from __future__ import annotations

from personal_os_worker.jobs.browser import looks_like_application_form


def field(**kwargs: object) -> dict[str, str | bool | None]:
    base: dict[str, str | bool | None] = {"tag": "input", "type": "text", "label": "", "name": ""}
    base.update(kwargs)  # type: ignore[arg-type]
    return base


def test_a_resume_upload_is_enough() -> None:
    assert looks_like_application_form([field(type="file", label="Resume")]) is True


def test_typical_contact_fields_count_as_a_form() -> None:
    assert (
        looks_like_application_form(
            [
                field(label="First Name"),
                field(label="Last Name"),
                field(label="Email", type="email"),
            ]
        )
        is True
    )


def test_a_careers_index_with_only_a_search_box_is_not_a_form() -> None:
    """The Zipline case: a roles listing page that answered 200 and looked fine."""
    assert looks_like_application_form([field(label="Search roles")]) is False


def test_filter_dropdowns_do_not_count() -> None:
    assert (
        looks_like_application_form(
            [
                field(label="Search roles"),
                {"tag": "select", "type": None, "label": "Location", "name": "loc"},
                {"tag": "select", "type": None, "label": "Departments", "name": "dept"},
            ]
        )
        is False
    )


def test_a_page_with_no_inputs_is_not_a_form() -> None:
    assert looks_like_application_form([]) is False


def test_two_contact_fields_are_not_enough() -> None:
    assert looks_like_application_form([field(label="Email"), field(label="Name")]) is False
