import httpx
import pytest

from personal_os_worker import cli
from personal_os_worker.auth import LoginError, Session
from personal_os_worker.cli import build_parser, main
from personal_os_worker.commands import services
from personal_os_worker.config import CliConfig


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
    monkeypatch.setattr(
        services, "ensure_chrome", lambda url: calls.append(("chrome", url))
    )
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
    assert expand_shorthand(["hunt", "answers"]) == ["hunt", "answers", "list"]
    assert expand_shorthand(["hunt", "resume"]) == ["hunt", "resume", "watch", "--open"]
    # anything already spelled out is left alone
    assert expand_shorthand(["hunt", "apply", "7"]) == ["hunt", "apply", "7"]
    assert expand_shorthand(["hunt", "answers", "add"]) == ["hunt", "answers", "add"]
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
    assert "{today,easy,sync,open,apps,queue,answers,resume,apply}" in output
    assert "{list," not in output and "\n    list " not in output


def test_jobs_live_under_the_hunt_domain() -> None:
    args = build_parser().parse_args(["hunt", "list", "5", "--faang"])
    assert (args.command, args.hunt_command) == ("hunt", "list")
    assert args.faang is True and args.quant is False and args.limit == 5
    assert args.all_roles is False and args.family == []


def test_today_is_a_listing_view_with_only_the_roles_switch() -> None:
    assert build_parser().parse_args(["hunt", "today"]).all_roles is False
    assert build_parser().parse_args(["hunt", "today", "--all-roles"]).all_roles is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "today", "5"])


def test_easy_takes_a_row_limit_like_the_board() -> None:
    assert build_parser().parse_args(["hunt", "easy"]).limit == 50
    assert build_parser().parse_args(["hunt", "easy", "12"]).limit == 12


def test_any_displayed_listing_can_open_its_direct_or_discovery_link() -> None:
    direct = build_parser().parse_args(["hunt", "open", "12"])
    source = build_parser().parse_args(["hunt", "open", "12", "--source"])

    assert (direct.hunt_command, direct.ref, direct.source) == ("open", "12", False)
    assert source.source is True


def test_listing_limit_has_no_upper_bound_and_rejects_non_positive_values() -> None:
    assert build_parser().parse_args(["hunt", "list", "500"]).limit == 500
    assert build_parser().parse_args(["hunt", "list"]).limit == 50
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


def test_queue_lists_adds_and_empties() -> None:
    bare = build_parser().parse_args(["hunt", "queue"])
    added = build_parser().parse_args(["hunt", "queue", "add", "3", "7", "a1b2c3d4"])
    emptied = build_parser().parse_args(["hunt", "queue", "empty"])
    assert (bare.action, bare.refs) == (None, [])
    assert (added.action, added.refs) == ("add", ["3", "7", "a1b2c3d4"])
    assert (emptied.action, emptied.refs) == ("empty", [])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "prep", "1"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "run"])


def test_apps_lists_or_marks_one_with_a_lifecycle_status() -> None:
    listed = build_parser().parse_args(["hunt", "apps"])
    marked = build_parser().parse_args(["hunt", "apps", "2", "oa"])
    assert (listed.ref, listed.status) == (None, None)
    assert (marked.ref, marked.status) == ("2", "oa")
    for status in ("applied", "oa_completed", "interview", "offer", "rejected"):
        assert build_parser().parse_args(["hunt", "apps", "1", status]).status == status
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "apps", "1", "queued"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "apps", "1", "submitted"])


def test_resume_build_and_watch_are_cli_commands() -> None:
    build = build_parser().parse_args(["hunt", "resume", "build", "--output", "/tmp/main.pdf"])
    assert (build.hunt_command, build.resume_command, build.output) == (
        "resume",
        "build",
        "/tmp/main.pdf",
    )
    watch = build_parser().parse_args(["hunt", "resume", "watch", "--open", "--interval", "0.25"])
    assert watch.resume_command == "watch" and watch.open is True
    assert watch.interval == 0.25


def test_autofill_requires_a_saved_listing_reference_and_has_no_lentra_provider() -> None:
    args = build_parser().parse_args(["hunt", "apply", "7", "--dry-run"])
    assert args.ref == "7" and args.dry_run is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "apply", "7", "--provider", "lentra"])


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


def test_answer_library_is_manageable_without_the_web_app() -> None:
    listed = build_parser().parse_args(["hunt", "answers", "list"])
    added = build_parser().parse_args(
        [
            "hunt",
            "answers",
            "add",
            "--question",
            "Why us?",
            "--file",
            "answer.md",
            "--reuse",
            "contextual",
        ]
    )
    edited = build_parser().parse_args(
        ["hunt", "answers", "edit", "2", "--text", "Updated", "--approve"]
    )
    assert listed.answer_command == "list"
    assert added.file == "answer.md" and added.draft is False
    assert added.reuse == "contextual"
    assert edited.ref == "2" and edited.approved is True


def test_answer_reuse_choices_include_pattern() -> None:
    added = build_parser().parse_args(
        [
            "hunt",
            "answers",
            "add",
            "--question",
            r"consider me for other (job )?opportunities",
            "--text",
            "Yes",
            "--reuse",
            "pattern",
        ]
    )
    edited = build_parser().parse_args(
        ["hunt", "answers", "edit", "3", "--reuse", "pattern"]
    )
    assert added.reuse == "pattern"
    assert edited.reuse == "pattern"
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["hunt", "answers", "add", "--question", "Q", "--text", "A", "--reuse", "bogus"]
        )


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
