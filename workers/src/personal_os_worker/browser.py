from __future__ import annotations

from typing import Any, cast

from playwright.sync_api import Browser, Page, Playwright, sync_playwright


class VisibleBrowser:
    def __init__(self, cdp_url: str) -> None:
        self._cdp_url = cdp_url
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> VisibleBrowser:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_url)
        return self

    def __exit__(self, *_: object) -> None:
        if self._playwright is not None:
            self._playwright.stop()

    def capture_application(self, url: str) -> dict[str, Any]:
        if self._browser is None:
            raise RuntimeError("Visible browser is not connected")
        context = self._browser.contexts[0]
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1500)
        result = {
            "url": page.url,
            "title": page.title(),
            "description": page.locator("body").inner_text(timeout=15_000)[:50000],
            "fields": inspect_fields(page),
        }
        # Deliberately leave the company page open in the dedicated visible profile.
        return result


def inspect_fields(page: Page) -> list[dict[str, str | bool | None]]:
    result = page.locator("input, textarea, select").evaluate_all(
        """
        elements => elements.map((element, index) => {
          const id = element.id;
          const explicit = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
          const wrapping = element.closest('label');
          return {
            index,
            tag: element.tagName.toLowerCase(),
            type: element.getAttribute('type'),
            name: element.getAttribute('name'),
            label: (
              explicit?.textContent
              || wrapping?.textContent
              || element.getAttribute('aria-label')
              || ''
            ).trim(),
            required: element.required === true,
          };
        })
        """
    )
    return cast(list[dict[str, str | bool | None]], result)
