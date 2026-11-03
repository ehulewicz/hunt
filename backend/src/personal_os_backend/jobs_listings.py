"""Listings: the board, the sync that fills it, and a posting found by number, id prefix, or URL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from psycopg import AsyncConnection, sql

from personal_os_backend.database import StoreBase
from personal_os_backend.jobs_models import (
    DEFAULT_TECHNICAL_FAMILIES,
    JobListing,
    JobListingPage,
    ListingFromUrl,
    ListingSyncResponse,
)
from personal_os_backend.jobs_sources import SOURCE_NAME, SourceListing, classify_role_families

# Every open listing without an active application, numbered once: default
# technical families first, newest first, ties by id. Every role family stays on
# the board; `--family` and `--all-roles` are how a view narrows.
BOARD_QUERY = sql.SQL(
    """
    select posting.id, posting.created_at, company.name as company_name, posting.title,
           posting.location, posting.salary_text, posting.application_url,
           posting.discovery_url, posting.role_families,
           posting.source_category, posting.normalized_locations,
           posting.career_stage, posting.feed_region,
           posting.source_posted_at, posting.source_age_days,
           posting.status, application.id as application_id,
           application.status as application_status,
           row_number() over (
             order by case when posting.role_families && %s::text[] then 0 else 1 end,
                      posting.source_age_days asc nulls last,
                      posting.source_posted_at desc nulls last,
                      posting.created_at desc,
                      posting.id
           ) as "row"
    from app.jobs_postings posting
    join app.jobs_companies company on company.id = posting.company_id
    left join app.jobs_applications application
      on application.user_id = posting.user_id and application.posting_id = posting.id
     and application.status <> 'archived'
    where posting.user_id = %s
      and posting.status not in ('closed', 'archived')
      and posting.career_stage = 'internship'
      and posting.feed_region = 'usa'
      and application.id is null
    """
)


class AmbiguousReference(LookupError):
    """An id prefix matched more than one listing."""


class ListingsStore(Protocol):
    async def sync_listings(
        self, user_id: UUID, listings: Sequence[SourceListing]
    ) -> ListingSyncResponse: ...

    async def list_listings(
        self,
        user_id: UUID,
        *,
        category: str | None,
        locations: Sequence[str],
        role_families: Sequence[str],
        limit: int,
        offset: int,
    ) -> JobListingPage: ...

    async def get_listing(self, user_id: UUID, reference: str) -> JobListing | None: ...

    async def listing_from_url(
        self, user_id: UUID, item: ListingFromUrl
    ) -> JobListing | None: ...


class ListingsMixin(StoreBase):
    async def sync_listings(
        self, user_id: UUID, listings: Sequence[SourceListing]
    ) -> ListingSyncResponse:
        sync_started = datetime.now(UTC)
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            company_ids: dict[str, UUID] = {}
            if listings:
                company_ids = await _upsert_companies(connection, user_id, listings)
                await _upsert_postings(connection, user_id, listings, company_ids, sync_started)

            await connection.execute(
                """
                update app.jobs_postings posting
                set status = 'closed'
                where posting.user_id = %s
                  and posting.source = %s
                  and posting.last_seen_at < %s
                  and posting.status in ('saved', 'reviewing')
                """,
                (user_id, SOURCE_NAME, sync_started),
            )
            count_cursor = await connection.execute(
                """
                select count(*) as seen,
                       count(*) filter (where application_url is not null) as open
                from app.jobs_postings
                where user_id = %s and source = %s and last_seen_at = %s
                """,
                (user_id, SOURCE_NAME, sync_started),
            )
            counts = await count_cursor.fetchone()
        return ListingSyncResponse(
            source=SOURCE_NAME,
            seen=int(counts["seen"] if counts else 0),
            open=int(counts["open"] if counts else 0),
            synced_at=sync_started,
        )

    async def list_listings(
        self,
        user_id: UUID,
        *,
        category: str | None,
        locations: Sequence[str],
        role_families: Sequence[str],
        limit: int,
        offset: int,
    ) -> JobListingPage:
        """Board listings not yet applied to, each carrying its canonical row number.

        Every role family is on the board; nothing is discarded for its family.
        The number is computed over the whole board before any filter applies,
        so a listing keeps the same number in every view; filtered views show
        gaps. Default technical families come first so the everyday view is
        contiguous and `--all-roles` extends it rather than reshuffling it.
        """
        conditions: list[sql.Composable] = []
        params: list[Any] = [list(DEFAULT_TECHNICAL_FAMILIES), user_id]
        if category:
            conditions.append(sql.SQL("posting.source_category = %s"))
            params.append(category)
        if role_families:
            conditions.append(sql.SQL("posting.role_families && %s::text[]"))
            params.append(list(role_families))
        facet_conditions = list(conditions)
        facet_params = list(params)
        if locations:
            conditions.append(sql.SQL("posting.normalized_locations && %s::text[]"))
            params.append(list(locations))
        where = (
            sql.SQL("where ") + sql.SQL(" and ").join(conditions) if conditions else sql.SQL("")
        )

        def select(columns: str, tail: str = "") -> sql.Composed:
            return sql.SQL("with board as ({}) select {} from board posting {} {}").format(
                BOARD_QUERY, sql.SQL(columns), where, sql.SQL(tail)
            )

        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            location_cursor = await connection.execute(
                sql.SQL(
                    """
                    with board as ({})
                    select distinct normalized.location as location
                    from board posting
                    cross join lateral unnest(posting.normalized_locations)
                      as normalized(location)
                    {}
                    order by normalized.location
                    """
                ).format(
                    BOARD_QUERY,
                    sql.SQL("where ") + sql.SQL(" and ").join(facet_conditions)
                    if facet_conditions
                    else sql.SQL(""),
                ),
                facet_params,
            )
            location_rows = await location_cursor.fetchall()
            count_cursor = await connection.execute(select("count(*) as total"), params)
            count = await count_cursor.fetchone()
            rows_cursor = await connection.execute(
                select("posting.*", 'order by posting."row" limit %s offset %s'),
                (*params, limit, offset),
            )
            rows = await rows_cursor.fetchall()
        return JobListingPage(
            items=[JobListing.model_validate(row) for row in rows],
            total=int(count["total"] if count else 0),
            limit=limit,
            offset=offset,
            location_options=[str(row["location"]) for row in location_rows],
        )

    async def get_listing(self, user_id: UUID, reference: str) -> JobListing | None:
        """A listing by its canonical row number, or by a prefix of its id.

        A row number resolves against the same board the listing views show. An
        id prefix resolves against every listing, applied-to ones included, so
        a hidden listing can still be opened; several matches are ambiguous.
        """
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            if reference.isdigit():
                cursor = await connection.execute(
                    sql.SQL("with board as ({}) select posting.* from board posting "
                            'where posting."row" = %s').format(BOARD_QUERY),
                    (list(DEFAULT_TECHNICAL_FAMILIES), user_id, int(reference)),
                )
                row = await cursor.fetchone()
                return JobListing.model_validate(row) if row else None
            prefix = reference.replace("-", "").casefold()
            if len(prefix) < 4 or any(char not in "0123456789abcdef" for char in prefix):
                return None
            cursor = await connection.execute(
                """
                select posting.id, posting.created_at, company.name as company_name, posting.title,
                       posting.location, posting.salary_text, posting.application_url,
                       posting.discovery_url, posting.role_families,
                       posting.source_category, posting.normalized_locations,
                       posting.career_stage, posting.feed_region,
                       posting.source_posted_at, posting.source_age_days,
                       posting.status, application.id as application_id,
                       application.status as application_status
                from app.jobs_postings posting
                join app.jobs_companies company on company.id = posting.company_id
                left join app.jobs_applications application
                  on application.user_id = posting.user_id
                 and application.posting_id = posting.id
                 and application.status <> 'archived'
                where posting.user_id = %s
                  and replace(posting.id::text, '-', '') like %s
                limit 2
                """,
                (user_id, f"{prefix}%"),
            )
            rows = await cursor.fetchall()
            if len(rows) > 1:
                raise AmbiguousReference(reference)
            return JobListing.model_validate(rows[0]) if rows else None

    async def listing_from_url(
        self, user_id: UUID, item: ListingFromUrl
    ) -> JobListing | None:
        """The posting at this URL, or a new manual one; None when it needs naming.

        A referral link or any posting off the board is applied to by its URL.
        The URL matches a posting's application or discovery URL exactly, else
        with its query string dropped when that names one posting. A URL no
        posting has becomes a `manual` posting once a company and title are
        given; the worker reads them off the page.
        """
        url = str(item.url).strip()
        base = url.split("#", 1)[0].split("?", 1)[0]
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                """
                select id::text from app.jobs_postings
                where user_id = %s and (application_url = %s or discovery_url = %s)
                order by status in ('closed', 'archived'), created_at desc
                limit 1
                """,
                (user_id, url, url),
            )
            found = await cursor.fetchone()
            if found is None:
                cursor = await connection.execute(
                    """
                    select id::text from app.jobs_postings
                    where user_id = %s
                      and (split_part(split_part(application_url, '#', 1), '?', 1) = %s
                           or split_part(split_part(discovery_url, '#', 1), '?', 1) = %s)
                    limit 2
                    """,
                    (user_id, base, base),
                )
                matches = await cursor.fetchall()
                found = matches[0] if len(matches) == 1 else None
            if found is None:
                if not (item.company and item.title):
                    return None
                found = await _insert_manual_posting(
                    connection, user_id, url, item.company, item.title
                )
        return await self.get_listing(user_id, str(found["id"]))


async def _upsert_companies(
    connection: AsyncConnection[Any], user_id: UUID, listings: Sequence[SourceListing]
) -> dict[str, UUID]:
    """Upsert every listed company, keeping a known website, and return each one's id."""
    companies: dict[str, str | None] = {}
    for listing in listings:
        current_url = companies.get(listing.company)
        companies[listing.company] = current_url or listing.company_url

    async with connection.cursor() as cursor:
        await cursor.executemany(
            """
            insert into app.jobs_companies (user_id, name, website_url)
            values (%s, %s, %s)
            on conflict (user_id, name) do update
            set website_url = coalesce(
              excluded.website_url, app.jobs_companies.website_url
            )
            """,
            [
                (user_id, company_name, company_url)
                for company_name, company_url in companies.items()
            ],
        )
    company_cursor = await connection.execute(
        """
        select id, name
        from app.jobs_companies
        where user_id = %s and name = any(%s::text[])
        """,
        (user_id, list(companies)),
    )
    company_ids = {
        str(row["name"]): UUID(str(row["id"]))
        for row in await company_cursor.fetchall()
    }
    if company_ids.keys() != companies.keys():
        raise RuntimeError("Company upsert did not return every id")
    return company_ids


async def _upsert_postings(
    connection: AsyncConnection[Any],
    user_id: UUID,
    listings: Sequence[SourceListing],
    company_ids: dict[str, UUID],
    sync_started: datetime,
) -> None:
    """Upsert each listing as a posting last seen at this sync."""
    async with connection.cursor() as cursor:
        await cursor.executemany(
            """
            insert into app.jobs_postings (
              user_id, company_id, title, location, employment_type,
              source, source_url, external_id, salary_text, status,
              discovered_at, application_url, discovery_url, role_families,
              source_category, normalized_locations, career_stage,
              feed_region, source_posted_at, source_age_days,
              last_seen_at
            )
            values (
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s, %s,
              %s, %s,
              %s
            )
            on conflict (user_id, source, external_id)
            where source is not null and external_id is not null
            do update set
              company_id = excluded.company_id,
              title = excluded.title,
              location = excluded.location,
              employment_type = excluded.employment_type,
              source_url = excluded.source_url,
              salary_text = excluded.salary_text,
              status = case
                when app.jobs_postings.status in ('saved', 'reviewing', 'closed')
                  then excluded.status
                else app.jobs_postings.status
              end,
              application_url = excluded.application_url,
              discovery_url = excluded.discovery_url,
              role_families = excluded.role_families,
              source_category = excluded.source_category,
              normalized_locations = excluded.normalized_locations,
              career_stage = excluded.career_stage,
              feed_region = excluded.feed_region,
              source_posted_at = excluded.source_posted_at,
              source_age_days = excluded.source_age_days,
              last_seen_at = excluded.last_seen_at
            """,
            [
                (
                    user_id,
                    company_ids[listing.company],
                    listing.title,
                    listing.location,
                    listing.career_stage,
                    SOURCE_NAME,
                    listing.application_url,
                    listing.external_id,
                    listing.salary_text,
                    "saved" if listing.application_url else "closed",
                    sync_started,
                    listing.application_url,
                    listing.discovery_url,
                    list(listing.role_families),
                    listing.category,
                    list(listing.normalized_locations),
                    listing.career_stage,
                    listing.feed_region,
                    listing.source_posted_at,
                    listing.source_age_days,
                    sync_started,
                )
                for listing in listings
            ],
        )


async def _insert_manual_posting(
    connection: AsyncConnection[Any], user_id: UUID, url: str, company_name: str, title: str
) -> Any:
    """Insert a `manual` posting at this URL under its company; return the new posting's id row."""
    company_cursor = await connection.execute(
        """
        insert into app.jobs_companies (user_id, name)
        values (%s, %s)
        on conflict (user_id, name) do update set name = excluded.name
        returning id
        """,
        (user_id, company_name.strip()),
    )
    company = await company_cursor.fetchone()
    if company is None:
        raise RuntimeError("Company upsert returned no id")
    now = datetime.now(UTC)
    posting_cursor = await connection.execute(
        """
        insert into app.jobs_postings (
          user_id, company_id, title, source, source_url, external_id, status,
          discovered_at, application_url, discovery_url, role_families,
          source_category, normalized_locations, career_stage, feed_region,
          last_seen_at
        )
        values (%s, %s, %s, 'manual', %s, %s, 'saved', %s, %s, %s, %s,
                'other', '{}', 'internship', 'usa', %s)
        on conflict (user_id, source, external_id)
        where source is not null and external_id is not null
        do update set title = excluded.title
        returning id::text
        """,
        (
            user_id, company["id"], title.strip(), url, url, now, url, url,
            list(classify_role_families(title)), now,
        ),
    )
    found = await posting_cursor.fetchone()
    if found is None:
        raise RuntimeError("Posting insert returned no id")
    return found
