from personal_os_worker.jobs.registry.api import Route


def reach(site_handle, title, company):
    """A careers page that gates postings behind a Roles link and a search box."""
    if not site_handle.click_text("Roles"):
        return False
    site_handle.wait(300)
    return site_handle.search(title)


ROUTE = Route(
    site="fixture.example",
    goal="posting",
    run=reach,
)
