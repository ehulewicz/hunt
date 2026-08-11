from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx

SOURCE_NAME = "speedyapply/2027-SWE-College-Jobs"
SOURCE_ROOT = "https://github.com/speedyapply/2027-SWE-College-Jobs/blob/main"
RAW_ROOT = "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main"


@dataclass(frozen=True, slots=True)
class Feed:
    path: str
    career_stage: str
    region: str

    @property
    def raw_url(self) -> str:
        return f"{RAW_ROOT}/{self.path}"

    @property
    def discovery_url(self) -> str:
        return f"{SOURCE_ROOT}/{self.path}"


FEEDS = (
    Feed("README.md", "internship", "usa"),
    Feed("NEW_GRAD_USA.md", "new_grad", "usa"),
    Feed("INTERN_INTL.md", "internship", "international"),
    Feed("NEW_GRAD_INTL.md", "new_grad", "international"),
)


@dataclass(frozen=True, slots=True)
class SourceListing:
    company: str
    company_url: str | None
    title: str
    location: str | None
    salary_text: str | None
    application_url: str | None
    discovery_url: str
    external_id: str
    role_families: tuple[str, ...]
    career_stage: str
    feed_region: str
    source_posted_at: date | None
    source_age_days: int | None


class SpeedyApplySource:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, *, today: date | None = None) -> list[SourceListing]:
        fetched: list[SourceListing] = []
        resolved_today = today or date.today()
        for feed in FEEDS:
            response = await self._client.get(feed.raw_url)
            response.raise_for_status()
            fetched.extend(parse_feed(response.text, feed, today=resolved_today))
        return deduplicate(fetched)


def parse_feed(markdown: str, feed: Feed, *, today: date) -> list[SourceListing]:
    listings: list[SourceListing] = []
    for raw_line in markdown.splitlines():
        if not raw_line.startswith("|"):
            continue
        cells = [cell.strip() for cell in raw_line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0].casefold() in {"company", "---"}:
            continue
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue

        company = clean_cell(cells[0])
        title = clean_cell(cells[1])
        if not company or not title:
            continue

        location = clean_cell(cells[2]) or None
        posting_cell = cells[-2]
        age_cell = cells[-1]
        salary_text = clean_cell(cells[-3]) or None if len(cells) >= 6 else None
        application_url = first_href(posting_cell)
        company_url = first_href(cells[0])
        age_match = re.search(r"(?P<days>\d+)\s*d", clean_cell(age_cell), re.IGNORECASE)
        source_age_days = int(age_match.group("days")) if age_match else None
        source_posted_at = (
            today - timedelta(days=source_age_days) if source_age_days is not None else None
        )
        stable_value = application_url or f"{feed.path}|{company}|{title}|{location or ''}"
        external_id = hashlib.sha256(stable_value.encode()).hexdigest()
        listings.append(
            SourceListing(
                company=company,
                company_url=company_url,
                title=title,
                location=location,
                salary_text=salary_text,
                application_url=application_url,
                discovery_url=feed.discovery_url,
                external_id=external_id,
                role_families=classify_role_families(title),
                career_stage=feed.career_stage,
                feed_region=feed.region,
                source_posted_at=source_posted_at,
                source_age_days=source_age_days,
            )
        )
    return listings


def classify_role_families(title: str) -> tuple[str, ...]:
    normalized = re.sub(r"[^a-z0-9+#]+", " ", title.casefold()).strip()
    families: list[str] = []

    if re.search(
        r"\b(data engineer|analytics engineer|data platform|data infrastructure|"
        r"etl|database engineer)\b",
        normalized,
    ):
        families.append("data_engineering")
    if re.search(
        r"\b(machine learning|ml engineer|ai engineer|artificial intelligence|"
        r"deep learning|computer vision|nlp|research engineer)\b",
        normalized,
    ):
        families.append("ml_engineering")
    if re.search(
        r"\b(data scientist|data science|applied scientist|decision scientist|"
        r"data analyst|quantitative analyst)\b",
        normalized,
    ):
        families.append("data_science")
    if re.search(
        r"\b(software|developer|development engineer|frontend|front end|backend|back end|"
        r"fullstack|full stack|web|mobile|ios|android|platform|infrastructure|cloud|devops|"
        r"site reliability|sre|systems|embedded|firmware|security engineer|automation|"
        r"quality engineer|test engineer)\b",
        normalized,
    ):
        families.append("software_engineering")

    return tuple(dict.fromkeys(families)) or ("other",)


def clean_cell(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value)
    without_markdown = re.sub(r"!?\[([^]]*)\]\([^)]+\)", r"\1", without_tags)
    return re.sub(r"\s+", " ", html.unescape(without_markdown)).strip()


def first_href(value: str) -> str | None:
    html_match = re.search(r'href=["\'](?P<url>https?://[^"\']+)', value, re.IGNORECASE)
    markdown_match = re.search(r"\[[^]]*\]\((?P<url>https?://[^)]+)\)", value)
    candidate = html_match or markdown_match
    if candidate is None:
        return None
    url = html.unescape(candidate.group("url")).strip()
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def deduplicate(listings: list[SourceListing]) -> list[SourceListing]:
    by_external_id: dict[str, SourceListing] = {}
    for listing in listings:
        by_external_id[listing.external_id] = listing
    return list(by_external_id.values())
