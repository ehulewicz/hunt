from __future__ import annotations

import asyncio
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlparse

import httpx

SOURCE_NAME = "speedyapply/college-jobs"
GITHUB_ROOT = "https://github.com/speedyapply"
RAW_ROOT = "https://raw.githubusercontent.com/speedyapply"


@dataclass(frozen=True, slots=True)
class Feed:
    path: str
    career_stage: str
    region: str
    repository: str = "2027-SWE-College-Jobs"

    @property
    def raw_url(self) -> str:
        return f"{RAW_ROOT}/{self.repository}/main/{self.path}"

    @property
    def discovery_url(self) -> str:
        return f"{GITHUB_ROOT}/{self.repository}/blob/main/{self.path}"


FEEDS = (
    Feed("README.md", "internship", "usa"),
    Feed("README.md", "internship", "usa", repository="2027-AI-College-Jobs"),
)

US_REGION_CODES = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    }
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
    category: str
    normalized_locations: tuple[str, ...]
    career_stage: str
    feed_region: str
    source_posted_at: date | None
    source_age_days: int | None


class SpeedyApplySource:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, *, today: date | None = None) -> list[SourceListing]:
        resolved_today = today or date.today()

        async def fetch_feed(feed: Feed) -> list[SourceListing]:
            response = await self._client.get(feed.raw_url)
            response.raise_for_status()
            return parse_feed(response.text, feed, today=resolved_today)

        fetched = await asyncio.gather(*(fetch_feed(feed) for feed in FEEDS))
        return deduplicate([listing for feed_listings in fetched for listing in feed_listings])


def parse_feed(markdown: str, feed: Feed, *, today: date) -> list[SourceListing]:
    listings: list[SourceListing] = []
    category = "other"
    for raw_line in markdown.splitlines():
        marker_category = category_from_marker(raw_line)
        if marker_category is not None:
            category = marker_category
            continue
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
        if re.search(r"ph\.?d", title, re.IGNORECASE):
            continue

        raw_location = clean_cell(cells[2]) or None
        location = canonical_us_location(raw_location)
        if feed.region == "usa" and location is None:
            continue
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
                category=category,
                normalized_locations=normalize_locations(location),
                career_stage=feed.career_stage,
                feed_region=feed.region,
                source_posted_at=source_posted_at,
                source_age_days=source_age_days,
            )
        )
    return listings


def category_from_marker(line: str) -> str | None:
    normalized = line.strip().casefold()
    if normalized == "<!-- table_faang_start -->":
        return "faang"
    if normalized == "<!-- table_quant_start -->":
        return "quant"
    if normalized == "<!-- table_start -->":
        return "other"
    return None


def normalize_locations(location: str | None) -> tuple[str, ...]:
    if not location:
        return ()
    normalized = re.sub(r"\s+\+\d+\s*$", "", location).strip()
    if not normalized:
        return ()
    if normalized.casefold().startswith("remote · "):
        remainder = normalized[len("remote · ") :].strip()
        return tuple(part for part in ("Remote", remainder) if part)
    if normalized.casefold() == "remote":
        return ("Remote",)
    return (normalized,)


def canonical_us_location(location: str | None) -> str | None:
    if not location:
        return None
    visible = re.sub(r"\s+\+\d+\s*$", "", location).strip()
    if not visible:
        return None
    normalized = visible.casefold()
    if normalized == "remote":
        return "Remote"
    if normalized in {"usa", "united states"}:
        return "United States"
    if normalized.startswith("remote - "):
        remainder = visible[len("remote - ") :].strip()
        if remainder.casefold() in {"usa", "united states"}:
            return "Remote"
        city_state = canonical_city_state(remainder)
        return f"Remote · {city_state}" if city_state else None
    return canonical_city_state(visible)


def canonical_city_state(location: str) -> str | None:
    match = re.fullmatch(r"(?P<city>[^,]+),\s*(?P<region>[A-Za-z]{2})", location.strip())
    if match is None:
        return None
    region = match.group("region").upper()
    if region not in US_REGION_CODES:
        return None
    city = re.sub(r"\s+", " ", match.group("city")).strip()
    return f"{city}, {region}" if city else None


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
        by_external_id.setdefault(listing.external_id, listing)
    return list(by_external_id.values())
