from datetime import date

import httpx

from personal_os.jobs_sources import (
    Feed,
    SpeedyApplySource,
    classify_role_families,
    normalize_locations,
    parse_feed,
)


def test_role_classification_is_broad_and_non_excluding() -> None:
    assert classify_role_families("Full-Stack Product Engineer Intern") == ("software_engineering",)
    assert classify_role_families("Data Platform ML Engineer") == (
        "data_engineering",
        "ml_engineering",
        "software_engineering",
    )
    assert classify_role_families("Technical Solutions Intern") == ("other",)


def test_speedyapply_rows_preserve_company_application_and_discovery_links() -> None:
    markdown = "\n".join(
        (
            "| Company | Position | Location | Salary | Posting | Age |",
            "|---|---|---|---|---|---|",
            '| <a href="https://company.example"><strong>Example Co</strong></a> '
            "| Fullstack Engineer Intern | Remote | $50/hr "
            '| <a href="https://company.example/careers/123"><img alt="Apply"/></a> | 0d |',
            '| <a href="https://another.example"><strong>Another</strong></a> '
            "| Technical Operations Intern | Atlanta, GA |  "
            '| <a href="https://another.example/jobs/456"><img alt="Apply"/></a> | 12d |',
        )
    )
    feed = Feed("README.md", "internship", "usa")

    listings = parse_feed(markdown, feed, today=date(2026, 8, 10))

    assert len(listings) == 2
    assert listings[0].company == "Example Co"
    assert listings[0].application_url == "https://company.example/careers/123"
    assert listings[0].discovery_url.endswith("/README.md")
    assert listings[0].role_families == ("software_engineering",)
    assert listings[0].source_posted_at == date(2026, 8, 10)
    assert listings[1].role_families == ("other",)
    assert listings[1].source_posted_at == date(2026, 7, 29)


def test_speedyapply_rows_preserve_github_category_and_normalized_locations() -> None:
    markdown = "\n".join(
        (
            "<!-- TABLE_FAANG_START -->",
            "| Company | Position | Location | Posting | Age |",
            "|---|---|---|---|---|",
            "| Example | AI Engineering Intern | Remote - San Francisco, CA +2 "
            "| [Apply](https://example.com/jobs/123) | 1d |",
            "<!-- TABLE_FAANG_END -->",
            "<!-- TABLE_QUANT_START -->",
            "| Quant Co | Data Science Co-op | New York City, NY +1 "
            "| [Apply](https://example.com/jobs/456) | 2d |",
            "<!-- TABLE_QUANT_END -->",
        )
    )

    listings = parse_feed(markdown, Feed("README.md", "internship", "usa"), today=date(2026, 8, 10))

    assert [listing.category for listing in listings] == ["faang", "quant"]
    assert listings[0].normalized_locations == ("Remote", "San Francisco, CA")
    assert listings[1].normalized_locations == ("New York City, NY",)


def test_location_normalization_removes_source_counts_without_inventing_hidden_places() -> None:
    assert normalize_locations("Mountain View, CA +29") == ("Mountain View, CA",)
    assert normalize_locations("Remote") == ("Remote",)
    assert normalize_locations(None) == ()


def test_us_feed_accepts_only_visible_us_locations_and_uses_canonical_display() -> None:
    markdown = "\n".join(
        (
            "<!-- TABLE_START -->",
            "| Company | Position | Location | Posting | Age |",
            "|---|---|---|---|---|",
            "| City Co | SWE Intern | Atlanta, GA +3 | [Apply](https://example.com/1) | 0d |",
            "| Remote Co | ML Intern | Remote - New York, NY +2 "
            "| [Apply](https://example.com/2) | 1d |",
            "| Anywhere Co | Data Intern | Remote - USA | [Apply](https://example.com/3) | 2d |",
            "| Country Co | SWE Co-op | United States | [Apply](https://example.com/4) | 3d |",
            "| London Co | SWE Intern | London, United Kingdom +4 "
            "| [Apply](https://example.com/5) | 4d |",
            "| Canada Co | SWE Intern | Remote - Canada | [Apply](https://example.com/6) | 5d |",
            "<!-- TABLE_END -->",
        )
    )

    listings = parse_feed(
        markdown,
        Feed("README.md", "internship", "usa"),
        today=date(2026, 8, 13),
    )

    assert [listing.location for listing in listings] == [
        "Atlanta, GA",
        "Remote · New York, NY",
        "Remote",
        "United States",
    ]
    assert [listing.normalized_locations for listing in listings] == [
        ("Atlanta, GA",),
        ("Remote", "New York, NY"),
        ("Remote",),
        ("United States",),
    ]


async def test_speedyapply_fetches_only_internship_feeds_from_both_repositories() -> None:
    requested_urls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        number = len(requested_urls)
        markdown = "\n".join(
            (
                "<!-- TABLE_START -->",
                "| Company | Position | Location | Posting | Age |",
                "|---|---|---|---|---|",
                f"| Company {number} | Software Engineering Intern | Remote "
                f"| [Apply](https://example.com/jobs/{number}) | 0d |",
                "<!-- TABLE_END -->",
            )
        )
        return httpx.Response(200, text=markdown)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        listings = await SpeedyApplySource(client).fetch(today=date(2026, 8, 10))

    assert len(requested_urls) == 2
    assert all("NEW_GRAD" not in url for url in requested_urls)
    assert any("2027-SWE-College-Jobs" in url for url in requested_urls)
    assert any("2027-AI-College-Jobs" in url for url in requested_urls)
    assert len(listings) == 2
    assert all(listing.career_stage == "internship" for listing in listings)
