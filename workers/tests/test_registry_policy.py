"""The learned-file policy: what may and may not be imported."""

from __future__ import annotations

import pytest

from personal_os_worker.jobs.registry import policy

DECLARATIONS = {
    "kinds": 'Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "strategies": 'Strategy(name="s", kind="k", operate=go, site="a.com")',
    "formats": 'Format(name="f", fact_keys=("x",), render=go, site="a.com")',
    "questions": 'QuestionReader(name="q", group_selector="g", '
        'question_selector="h", site="a.com")',
    "routes": 'Route(site="a.com", goal="posting", run=go)',
}


def good_source(category: str, site: str = "a.com") -> str:
    return (
        "from personal_os_worker.jobs.registry import api\n\n"
        "def go(*args):\n    return args[0].text()\n\n"
        f"DECL = api.{DECLARATIONS[category]}\n"
    )


@pytest.mark.parametrize("category", list(DECLARATIONS))
def test_one_file_per_category_passes(category: str) -> None:
    assert policy.check_source(good_source(category), category=category) == []


@pytest.mark.parametrize("category", list(DECLARATIONS))
def test_a_wrong_site_is_refused(category: str) -> None:
    assert policy.check_source(good_source(category), category=category, site="b.com")


BAD = {
    "import os": "import os\nfrom personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "import json": "import json\nfrom personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "underscore name": "from personal_os_worker.jobs.registry.api import Kind\n"
        'def _go():\n    return 1\n'
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "underscore attribute": "from personal_os_worker.jobs.registry.api import Kind\n"
        'x = "a".__class__\n'
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "eval": "from personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")\n'
        'Y = eval("1")',
    "open": "from personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")\n'
        'Y = open("f")',
    "getattr": "from personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")\n'
        'Y = getattr(str, "upper")',
    ".format": "from personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")\n'
        'Y = "{0}".format(1)',
    "while": "from personal_os_worker.jobs.registry.api import Kind\n"
        "def go():\n    while True:\n        break\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "class": "from personal_os_worker.jobs.registry.api import Kind\n"
        "class Widget:\n    pass\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "global": "from personal_os_worker.jobs.registry.api import Kind\n"
        "def go():\n    global X\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "async def": "from personal_os_worker.jobs.registry.api import Kind\n"
        "async def go():\n    return 1\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")',
    "two declarations": "from personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")\n'
        'L = Kind(name="l", selector="s", reads="value", strategies=(), site="a.com")',
    "no declaration": "import re\nX = re.compile('x')",
    "site not literal": "from personal_os_worker.jobs.registry.api import Kind\n"
        'SITE = "a.com"\n'
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site=SITE)',
    "type builtin": "from personal_os_worker.jobs.registry.api import Kind\n"
        'K = Kind(name="k", selector="s", reads="value", strategies=(), site="a.com")\n'
        'Y = type(1)',
}


@pytest.mark.parametrize("name", list(BAD))
def test_a_failing_file_per_rule(name: str) -> None:
    assert policy.check_source(BAD[name], category="kinds"), name


def test_too_long_is_refused() -> None:
    long_file = "import re\n" + "\n".join(f"X{i} = re.compile('x')" for i in range(125))
    assert any(
        "120" in violation
        for violation in policy.check_source(long_file, category="kinds")
    )


def test_check_file_reads_the_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "a.py"
    path.write_text(good_source("kinds"))
    assert policy.check_file(path, category="kinds") == []
