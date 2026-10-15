import httpx
import pytest

from personal_os_worker import cli
from personal_os_worker.cli import build_parser, main
from personal_os_worker.core import commands as services
from personal_os_worker.core.auth import LoginError, Session
from personal_os_worker.core.config import CliConfig
from personal_os_worker.jobs import commands as hunt


def test_help_describes_the_personal_os_cli(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "Personal OS local command line" in output
    for command in ("status", "stop", "api", "chrome", "login", "doctor", "hunt"):
        assert command in output


def test_login_starts_the_automation_browser_with_cdp_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    config = CliConfig("http://127.0.0.1:8000", "https://example.supabase.co", "key")
    calls: list[tuple[str, object]] = []
    session = Session("access", "refresh", 4_000_000_000)
    monkeypatch.setattr(cli, "load_cli_config", lambda: config)
    monkeypatch.setattr(services, "ensure_chrome", lambda url: calls.append(("chrome", url)))
    monkeypatch.setattr(
        services,
        "login",
        lambda auth: calls.append(("login", auth)) or session,
    )
    monkeypatch.setattr(services, "save_session", lambda _: tmp_path / "session.json")

    assert cli.run_login() == 0
    assert [name for name, _ in calls] == ["chrome", "login"]


def test_connected_signs_in_by_itself_when_there_is_no_usable_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    config = CliConfig("http://127.0.0.1:8000", "https://example.supabase.co", "key")
    calls: list[str] = []
    session = Session("fresh-token", "refresh", 4_000_000_000)
    monkeypatch.setattr(services, "load_cli_config", lambda: config)
    monkeypatch.setattr(services.service, "ensure_running", lambda url: calls.append("api"))

    def no_session(auth: object) -> Session:
        raise LoginError("Not signed in.")

    monkeypatch.setattr(services, "active_session", no_session)
    monkeypatch.setattr(services, "ensure_chrome", lambda url: calls.append("chrome"))
    monkeypatch.setattr(services, "login", lambda auth: calls.append("login") or session)
    monkeypatch.setattr(services, "save_session", lambda _: tmp_path / "session.json")

    assert services.connected() == (config, "fresh-token")
    assert calls == ["api", "chrome", "login"]


def test_api_errors_exit_with_the_backend_detail_instead_of_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8000/v1/jobs/autofill/runs")
    response = httpx.Response(409, request=request, json={"detail": "Listing is closed"})

    def fail(_: object) -> int:
        raise httpx.HTTPStatusError("conflict", request=request, response=response)

    monkeypatch.setattr(cli, "_dispatch", fail)

    with pytest.raises(SystemExit, match=r"API request failed \(409\).+Listing is closed"):
        main(["status"])


def test_everyday_commands_have_short_spellings() -> None:
    from personal_os_worker.cli import expand_shorthand

    assert expand_shorthand(["hunt"]) == ["hunt", "list"]
    assert expand_shorthand(["hunt", "20"]) == ["hunt", "list", "20"]
    assert expand_shorthand(["hunt", "--faang"]) == ["hunt", "list", "--faang"]
    assert expand_shorthand(["hunt", "--ats", "lever"]) == ["hunt", "list", "--ats", "lever"]
    assert expand_shorthand(["hunt", "20", "--ats=ashby"]) == ["hunt", "list", "20", "--ats=ashby"]
    assert expand_shorthand(["hunt", "resume"]) == ["hunt", "resume", "edit", "--open"]
    # anything already spelled out is left alone
    assert expand_shorthand(["hunt", "apply", "7"]) == ["hunt", "apply", "7"]
    assert expand_shorthand(["hunt", "inbox"]) == ["hunt", "inbox"]
    assert expand_shorthand(["hunt", "resume", "build"]) == ["hunt", "resume", "build"]
    assert expand_shorthand(["status"]) == ["status"]
    assert expand_shorthand(["hunt", "-h"]) == ["hunt", "-h"]  # help is the hunt help
    assert build_parser().parse_args(expand_shorthand(["hunt", "20"])).limit == 20
    with pytest.raises(SystemExit, match=r"unknown command 'aps'.*apps"):
        expand_shorthand(["hunt", "aps"])


def test_hunt_help_lists_only_the_public_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["hunt", "-h"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "os hunt [N]" in output
    assert (
        "{today,sync,open,apps,skipped,inbox,patterns,facts,prose,archive,resume,apply,"
        "repair,validate}" in output
    )
    assert "\n    answers " not in output  # the library command is gone
    assert "queue" not in output
    assert "{list," not in output and "\n    list " not in output


def test_jobs_live_under_the_hunt_domain() -> None:
    args = build_parser().parse_args(["hunt", "list", "5", "--faang"])
    assert (args.command, args.hunt_command) == ("hunt", "list")
    assert args.faang is True and args.quant is False and args.limit == 5
    assert args.all_roles is False and args.family == [] and args.ats is None
    assert build_parser().parse_args(["hunt", "list", "--ats", "workday"]).ats == "workday"


def test_today_is_a_listing_view_with_roles_and_ats_switches() -> None:
    args = build_parser().parse_args(["hunt", "today"])
    assert args.all_roles is False and args.ats is None
    assert build_parser().parse_args(["hunt", "today", "--all-roles"]).all_roles is True
    assert build_parser().parse_args(["hunt", "today", "--ats", "greenhouse"]).ats == "greenhouse"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "today", "5"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "today", "--ats", "icims"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "easy"])


def test_any_displayed_listing_can_open_its_direct_or_discovery_link() -> None:
    direct = build_parser().parse_args(["hunt", "open", "12"])
    source = build_parser().parse_args(["hunt", "open", "12", "--source"])

    assert (direct.hunt_command, direct.ref, direct.source) == ("open", "12", False)
    assert source.source is True


def test_listing_limit_has_no_upper_bound_and_rejects_non_positive_values() -> None:
    assert build_parser().parse_args(["hunt", "list", "500"]).limit == 500
    assert build_parser().parse_args(["hunt", "list"]).limit == 10
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "list", "0"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "list", "--limit", "5"])


def test_category_filters_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "list", "--faang", "--quant"])


def test_locations_accumulate() -> None:
    args = build_parser().parse_args(
        ["hunt", "list", "--location", "Atlanta, GA", "--location", "Remote"]
    )
    assert args.location == ["Atlanta, GA", "Remote"]


def test_role_families_accumulate_and_all_roles_is_explicit() -> None:
    args = build_parser().parse_args(
        [
            "hunt",
            "list",
            "--family",
            "software_engineering",
            "--family",
            "ml_engineering",
        ]
    )
    assert args.family == ["software_engineering", "ml_engineering"]
    assert build_parser().parse_args(["hunt", "list", "--all-roles"]).all_roles is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "list", "--family", "data_science"])


def test_retired_workflow_commands_are_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "queue"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "prep", "1"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "run"])


def test_apps_lists_or_marks_one_with_a_lifecycle_status() -> None:
    listed = build_parser().parse_args(["hunt", "apps"])
    marked = build_parser().parse_args(["hunt", "apps", "2", "oa"])
    assert (listed.ref, listed.status) == (None, None)
    assert (marked.ref, marked.status) == ("2", "oa")
    for status in ("applying", "applied", "oa_completed", "interview", "offer", "rejected"):
        assert build_parser().parse_args(["hunt", "apps", "1", status]).status == status
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "apps", "1", "queued"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "apps", "1", "submitted"])


def test_resume_build_edit_and_import_are_cli_commands() -> None:
    build = build_parser().parse_args(
        ["hunt", "resume", "build", "--input", "/tmp/x.json", "--output", "/tmp/main.pdf"]
    )
    assert (build.hunt_command, build.resume_command, build.input, build.output) == (
        "resume",
        "build",
        "/tmp/x.json",
        "/tmp/main.pdf",
    )
    edit = build_parser().parse_args(["hunt", "resume", "edit", "--open", "--interval", "0.25"])
    assert edit.resume_command == "edit" and edit.open is True
    assert edit.interval == 0.25
    imported = build_parser().parse_args(["hunt", "resume", "import"])
    assert imported.resume_command == "import" and imported.input is None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "resume", "watch"])


def test_autofill_requires_a_saved_listing_reference_and_has_no_lentra_provider() -> None:
    args = build_parser().parse_args(["hunt", "apply", "7", "--dry-run"])
    assert args.ref == "7" and args.dry_run is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "apply", "7", "--provider", "lentra"])


def test_autofill_accepts_today_with_an_ats_filter() -> None:
    args = build_parser().parse_args(
        ["hunt", "apply", "today", "--ats", "greenhouse", "--dry-run", "--no-claude"]
    )
    assert args.ref == "today" and args.ats == "greenhouse"
    assert args.dry_run is True and args.no_claude is True
    assert build_parser().parse_args(["hunt", "apply", "today"]).ats is None
    bare = build_parser().parse_args(["hunt", "apply", "--ats", "ashby"])
    assert bare.ref is None and bare.ats == "ashby"
    assert build_parser().parse_args(["hunt", "apply"]).ref is None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "apply", "today", "--ats", "icims"])


def test_bare_apply_and_apply_today_use_the_first_row_on_any_or_the_chosen_ats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeApi:
        def __init__(self, config: object, token: str) -> None:
            calls.append(("connected", (config, token)))

        def listings(self, *, role_families: list[str], limit: int) -> dict[str, object]:
            calls.append(("listings", (role_families, limit)))
            return {
                "items": [
                    {
                        "id": "old-greenhouse",
                        "company_name": "Stale",
                        "title": "Engineer",
                        "application_url": "https://boards.greenhouse.io/stale/jobs/9",
                        "source_age_days": 4,
                    },
                    {
                        "id": "generic-first",
                        "company_name": "Generic",
                        "title": "Unsupported",
                        "application_url": "https://example.com/jobs/1",
                        "source_age_days": 0,
                    },
                    {
                        "id": "workday-first",
                        "company_name": "Big",
                        "title": "Engineer",
                        "application_url": "https://big.wd5.myworkdayjobs.com/en-US/jobs/1",
                        "source_age_days": 0,
                    },
                    {
                        "id": "ashby-first",
                        "company_name": "Acme",
                        "title": "Engineer",
                        "application_url": "https://jobs.ashbyhq.com/acme/1",
                        "source_age_days": 1,
                    },
                    {
                        "id": "greenhouse-first",
                        "company_name": "Later",
                        "title": "Engineer",
                        "application_url": "https://job-boards.greenhouse.io/later/jobs/2",
                        "source_age_days": 1,
                    },
                ]
            }

        def close(self) -> None:
            calls.append(("close", None))

    monkeypatch.setattr(hunt, "connected", lambda: ("config", "token"))
    monkeypatch.setattr(hunt, "WorkerApi", FakeApi)
    monkeypatch.setattr(
        hunt,
        "apply_posting",
        lambda posting_id, *, dry_run, no_claude, repair, **_: calls.append(
            ("apply", (posting_id, dry_run, no_claude))
        ),
    )

    assert main(["hunt", "apply", "today", "--dry-run", "--no-claude"]) == 0
    assert calls[1] == (
        "listings",
        (["software_engineering", "data_engineering", "ml_engineering"], 500),
    )
    # Every site is filled from its controls: the first row of today wins, whatever its ATS.
    assert calls[-1] == ("apply", ("generic-first", True, True))

    assert main(["hunt", "apply", "today", "--ats", "workday", "--dry-run"]) == 0
    assert calls[-1] == ("apply", ("workday-first", True, False))
    assert main(["hunt", "apply", "today", "--ats", "greenhouse", "--dry-run"]) == 0
    assert calls[-1] == ("apply", ("greenhouse-first", True, False))

    with pytest.raises(SystemExit, match="Nothing new today on lever"):
        main(["hunt", "apply", "today", "--ats", "lever"])

    # Without 'today' the whole board counts, in board order.
    assert main(["hunt", "apply", "--dry-run"]) == 0
    assert calls[-3] == (
        "listings",
        (["software_engineering", "data_engineering", "ml_engineering"], 1000),
    )
    assert calls[-1] == ("apply", ("old-greenhouse", True, False))
    assert main(["hunt", "apply", "--ats", "ashby"]) == 0
    assert calls[-1] == ("apply", ("ashby-first", False, False))
    with pytest.raises(SystemExit, match="do not give a row"):
        main(["hunt", "apply", "3", "--ats", "ashby"])
    with pytest.raises(SystemExit, match="needs a listing row"):
        main(["hunt", "apply", "--done"])


def test_skipped_lists_and_restores_by_row(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeApi:
        def __init__(self, config: object, token: str) -> None:
            self.rows = [
                {"id": "app-1", "status": "applied", "company_name": "Acme", "title": "A"},
                {"id": "app-2", "status": "skipped", "company_name": "Beta", "title": "B"},
            ]

        def applications(self) -> list[dict[str, object]]:
            return list(self.rows)

        def archive_application(self, application_id: str) -> None:
            calls.append(("archive", application_id))
            self.rows = [row for row in self.rows if row["id"] != application_id]

        def close(self) -> None:
            return None

    monkeypatch.setattr(hunt, "connected", lambda: ("config", "token"))
    monkeypatch.setattr(hunt, "WorkerApi", FakeApi)
    assert build_parser().parse_args(["hunt", "skipped"]).action is None
    assert main(["hunt", "skipped"]) == 0
    assert main(["hunt", "skipped", "restore", "1"]) == 0
    assert calls == [("archive", "app-2")]
    with pytest.raises(SystemExit, match="needs a row"):
        main(["hunt", "skipped", "restore"])


def test_apply_done_records_an_application_made_elsewhere() -> None:
    assert build_parser().parse_args(["hunt", "apply", "1", "--done"]).done is True
    assert build_parser().parse_args(["hunt", "apply", "1"]).done is False


def test_autofill_no_claude_flag_defaults_off() -> None:
    assert build_parser().parse_args(["hunt", "apply", "7"]).no_claude is False
    assert build_parser().parse_args(["hunt", "apply", "7", "--no-claude"]).no_claude is True


def test_autofill_failure_commands_preserve_the_documented_syntax() -> None:
    listed = build_parser().parse_args(["hunt", "apply", "failures", "list"])
    verified = build_parser().parse_args(["hunt", "apply", "failures", "verify", "2", "9"])
    assert (listed.ref, listed.failure_action) == ("failures", "list")
    assert (verified.failure_ref, verified.verify_listing_ref) == ("2", "9")


def test_stop_defaults_to_everything_and_is_not_forced() -> None:
    args = build_parser().parse_args(["stop"])
    assert args.target == "all" and args.force is False
    assert build_parser().parse_args(["stop", "chrome", "--force"]).force is True


def test_pairing_is_gone() -> None:
    """Sign-in replaced the paired worker credential, so no pair command remains."""
    commands = {
        name
        for action in build_parser()._subparsers._group_actions  # type: ignore[union-attr]
        for name in action.choices
    }
    assert "pair" not in commands


@pytest.mark.parametrize(
    ("flags", "lifecycle", "expected"),
    [
        ({"submitted": True}, ("update", "applied"), "submitted"),
        ({"skipped": True}, ("update", "skipped"), "skipped"),
        ({"interrupted": True}, ("archive", None), "stopped"),
    ],
)
def test_clean_terminal_outcome_persists_before_closing_one_page(
    flags: dict[str, bool], lifecycle: tuple[str, str | None], expected: str
) -> None:
    from personal_os_worker.jobs.commands import finalize_application_outcome

    calls: list[tuple[str, object]] = []

    class Api:
        def complete_autofill_run(self, *_: object, **payload: object) -> None:
            calls.append(("complete", payload["application_status"]))

        def update_application(self, *_: object, status: str) -> None:
            calls.append(("update", status))

        def archive_application(self, *_: object) -> None:
            calls.append(("archive", None))

    class Page:
        def is_closed(self) -> bool:
            return False

        def close(self) -> None:
            calls.append(("close", None))

    result = finalize_application_outcome(
        Api(),  # type: ignore[arg-type]
        run={"company_name": "Acme", "application_id": "app-1"},
        run_id="run-1",
        outcome={"final_url": "https://example.test", "answers": [], **flags},
        page=Page(),
    )
    assert result == expected
    assert calls == [("complete", lifecycle[1] or "archived"), ("close", None)]


def test_persistence_failure_keeps_the_session_page_for_recovery() -> None:
    from personal_os_worker.jobs.commands import finalize_application_outcome

    calls: list[str] = []

    class Api:
        def complete_autofill_run(self, *_: object, **__: object) -> None:
            calls.append("complete")
            raise RuntimeError("persistence failed")

    class Page:
        def is_closed(self) -> bool:
            return False

        def close(self) -> None:
            calls.append("close")

    with pytest.raises(RuntimeError, match="persistence failed"):
        finalize_application_outcome(
            Api(),  # type: ignore[arg-type]
            run={"company_name": "Acme", "application_id": "app-1"},
            run_id="run-1",
            outcome={"final_url": "https://example.test", "answers": []},
            page=Page(),
        )
    assert calls == ["complete"]




def test_the_learning_commands_parse() -> None:
    assert build_parser().parse_args(["hunt", "inbox"]).hunt_command == "inbox"
    patterns = build_parser().parse_args(["hunt", "patterns", "--all"])
    assert patterns.hunt_command == "patterns" and patterns.every is True
    prose = build_parser().parse_args(
        ["hunt", "prose", "add", "--question", "Why us?", "--file", "a.md"]
    )
    assert (prose.action, prose.question, prose.file) == ("add", "Why us?", "a.md")
    assert build_parser().parse_args(["hunt", "prose", "approve", "2"]).ref == "2"
    archive = build_parser().parse_args(["hunt", "archive", "prose", "--markdown", "/tmp/a.md"])
    assert (archive.section, archive.markdown) == ("prose", "/tmp/a.md")
    assert build_parser().parse_args(["hunt", "archive"]).section == "answers"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "answers", "list"])


def test_repair_and_validate_are_commands_and_apply_takes_repair() -> None:
    repair = build_parser().parse_args(["hunt", "repair", "--watch"])
    assert (repair.hunt_command, repair.watch, repair.until) == ("repair", True, None)
    assert build_parser().parse_args(["hunt", "validate"]).hunt_command == "validate"
    apply = build_parser().parse_args(["hunt", "apply", "7", "--repair"])
    assert apply.repair is True
    with pytest.raises(SystemExit, match="--done takes no other flags"):
        main(["hunt", "apply", "7", "--done", "--repair"])
