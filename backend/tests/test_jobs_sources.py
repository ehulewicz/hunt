from datetime import date

from personal_os.jobs_sources import Feed, classify_role_families, parse_feed


def test_role_classification_is_broad_and_non_excluding() -> None:
    assert classify_role_families("Full-Stack Product Engineer Intern") == (
        "software_engineering",
    )
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
            "| <a href=\"https://company.example\"><strong>Example Co</strong></a> "
            "| Fullstack Engineer Intern | Remote | $50/hr "
            "| <a href=\"https://company.example/careers/123\"><img alt=\"Apply\"/></a> | 0d |",
            "| <a href=\"https://another.example\"><strong>Another</strong></a> "
            "| Technical Operations Intern | Atlanta, GA |  "
            "| <a href=\"https://another.example/jobs/456\"><img alt=\"Apply\"/></a> | 12d |",
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
