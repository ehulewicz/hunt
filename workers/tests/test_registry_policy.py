"""The policy a learned file must pass before it is ever imported."""

from pathlib import Path

import pytest

from personal_os_worker.jobs.registry import policy

K = 'Kind(structure="s", name="k", selector="s", reads="value", strategies=(), sites=("a.com",))'
DECLARATIONS = {
    "kinds": K,
    "strategies": 'Strategy(structure="s", name="s", kind="k", operate=go, sites=("a.com",))',
    "formats": 'Format(name="f", fact_keys=("x",), render=go, sites=("a.com",))',
    "questions": (
        'QuestionReader(structure="s", name="q", group_selector="g", '
        'question_selector="h", sites=("a.com",))'
    ),
    "routes": 'Route(sites=("a.com",), goal="posting", run=go)',
}


def good_source(category: str, site: str = "a.com") -> str:
    return (
        "from personal_os_worker.jobs.registry import api\n\n"
        "def go(*args):\n    return args[0].text()\n\n"
        f"DECL = api.{DECLARATIONS[category].replace('a.com', site)}\n"
    )


@pytest.mark.parametrize("category", list(DECLARATIONS))
def test_one_file_per_category_passes(category: str) -> None:
    assert policy.check_source(good_source(category), category=category) == []


@pytest.mark.parametrize("category", list(DECLARATIONS))
def test_a_site_the_file_does_not_name_is_refused(category: str) -> None:
    assert policy.check_source(good_source(category), category=category, site="b.com")


def test_a_route_must_name_exactly_one_site() -> None:
    source = good_source("routes").replace('sites=("a.com",)', 'sites=("a.com", "b.com")')
    assert policy.check_source(source, category="routes")


def test_a_declaration_without_structure_is_refused() -> None:
    source = good_source("kinds").replace('structure="s", ', "")
    assert policy.check_source(source, category="kinds")


def test_proven_must_be_a_boolean_literal() -> None:
    # A proven file (proven=True) loads; only candidates must stay False, and
    # that is the queue's candidate check, not the policy's.
    source = good_source("kinds").replace("sites=", "proven=True, sites=")
    assert policy.check_source(source, category="kinds") == []
    source = good_source("kinds").replace("sites=", "proven=1, sites=")
    assert policy.check_source(source, category="kinds")


def test_check_file_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "x.py"
    path.write_text(good_source("kinds"))
    assert policy.check_file(path, category="kinds") == []


HEADER = "from personal_os_worker.jobs.registry.api import Kind\n"

BAD = {
    "import os": f"import os\n{HEADER}{K.replace('Kind(', 'K = Kind(', 1)}",
    "import json": f"import json\n{HEADER}async def go():\n    return 1\nK = {K}",
    "two declarations": f"{HEADER}K = {K}\nL = {K}",
    "no declaration": "import re\nX = re.compile('x')",
    "sites not literal": (
        f"{HEADER}SITE = 'a.com'\nK = {K.replace('sites=(\"a.com\",)', 'sites=SITE')}"
    ),
    "type builtin": f"{HEADER}K = {K}\nY = type(1)",
    "underscore name": f"{HEADER}def _go():\n    return 1\nK = {K}",
    "underscore attribute": f"{HEADER}x = 'a'.__class__\nK = {K}",
    "eval": f"{HEADER}K = {K}\nY = eval('1')",
    "open": f"{HEADER}K = {K}\nY = open('f')",
    "getattr": f"{HEADER}K = {K}\nY = getattr(str, 'upper')",
    ".format": f"{HEADER}K = {K}\nY = '{{0}}'.format(1)",
    "while": f"{HEADER}def go():\n    while True:\n        break\nK = {K}",
    "class": f"{HEADER}class X:\n    pass\nK = {K}",
    "global": f"{HEADER}def go():\n    global y\nK = {K}",
    "async": f"{HEADER}async def go():\n    pass\nK = {K}",
    "exit": f"{HEADER}K = {K}\nexit()",
    "private attr": f"{HEADER}K = {K}\nY = 'x'._private",
    "format_map": f"{HEADER}K = {K}\nY = '{{a}}'.format_map({{'a': 1}})",
}


@pytest.mark.parametrize("name", list(BAD))
def test_each_rule_is_refused(name: str) -> None:
    assert policy.check_source(BAD[name], category="kinds"), name


def test_a_dropped_site_or_example_is_refused() -> None:
    """A revision keeps every earlier site; dropping one is a violation."""
    source = good_source("kinds").replace(
        'sites=("a.com",)', 'sites=()'
    )
    assert policy.check_source(source, category="kinds", site="a.com")
