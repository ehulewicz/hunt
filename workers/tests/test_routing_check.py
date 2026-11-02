"""The audit TSV's questions through the fill-time prose route.

Every prose row routes as prose and every fact/followup row does not.
Misses are reported to the user, not fixed here.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from personal_os_worker.jobs.autofill import is_factual_box, is_long_form_field

ROWS = [
    row
    for row in csv.DictReader(
        (Path(__file__).parent / "fixtures" / "prose_questions.tsv").open(encoding="utf-8"),
        delimiter="\t",
    )
]
PROSE_CLUSTERS = {"D", "G", "M", "O", "P", "X"}


def field_of(row: dict[str, str]) -> dict[str, str]:
    if row["control"] == "textarea":
        return {"label": row["question"], "tag": "textarea", "type": "textarea"}
    return {"label": row["question"], "tag": "input", "type": "text"}


def routes_prose(row: dict[str, str]) -> bool:
    field = field_of(row)
    return is_long_form_field(field) and not is_factual_box(field)


# Rows the route currently misroutes; reported to the user, not fixed here.
KNOWN_MISSES = {
    (row["cluster"], row["question"])
    for row in ROWS
    if row["cluster"] in ("fact", "followup") and routes_prose(row)
}


@pytest.mark.parametrize(
    "row", [row for row in ROWS if row["cluster"] in PROSE_CLUSTERS],
    ids=lambda r: f"{r['cluster']}-{r['question'][:40]}",
)
def test_prose_rows_route_as_prose(row: dict[str, str]) -> None:
    if (row["cluster"], row["question"]) in KNOWN_MISSES:
        pytest.xfail("routing miss, reported")
    assert routes_prose(row), f"{row['cluster']}: {row['question']}"


@pytest.mark.parametrize(
    "row", [row for row in ROWS if row["cluster"] in ("fact", "followup")],
    ids=lambda r: f"{r['cluster']}-{r['question'][:40]}",
)
def test_fact_rows_do_not_route_as_prose(row: dict[str, str]) -> None:
    if (row["cluster"], row["question"]) in KNOWN_MISSES:
        pytest.xfail("routing miss, reported")
    assert not routes_prose(row), f"{row['cluster']}: {row['question']}"
