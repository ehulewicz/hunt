import pytest

from personal_os_worker.cli import build_parser, main


def test_help_describes_the_personal_os_cli(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "Personal OS local command line" in output
    for command in ("status", "stop", "api", "chrome", "login", "doctor", "hunt"):
        assert command in output


def test_jobs_live_under_the_hunt_domain() -> None:
    args = build_parser().parse_args(["hunt", "listings", "--faang", "--limit", "5"])
    assert (args.command, args.hunt_command) == ("hunt", "listings")
    assert args.faang is True and args.quant is False and args.limit == 5
    assert args.all_roles is False and args.family == []


def test_listing_limit_has_no_upper_bound_and_rejects_non_positive_values() -> None:
    assert build_parser().parse_args(["hunt", "listings", "--limit", "500"]).limit == 500
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "listings", "--limit", "0"])


def test_category_filters_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "listings", "--faang", "--quant"])


def test_locations_accumulate() -> None:
    args = build_parser().parse_args(
        ["hunt", "listings", "--location", "Atlanta, GA", "--location", "Remote"]
    )
    assert args.location == ["Atlanta, GA", "Remote"]


def test_role_families_accumulate_and_all_roles_is_explicit() -> None:
    args = build_parser().parse_args(
        [
            "hunt",
            "listings",
            "--family",
            "software_engineering",
            "--family",
            "ml_engineering",
        ]
    )
    assert args.family == ["software_engineering", "ml_engineering"]
    assert build_parser().parse_args(["hunt", "listings", "--all-roles"]).all_roles is True


def test_prep_takes_several_references() -> None:
    assert build_parser().parse_args(["hunt", "prep", "3", "7", "a1b2c3d4"]).refs == [
        "3",
        "7",
        "a1b2c3d4",
    ]


def test_resume_build_and_watch_are_cli_commands() -> None:
    build = build_parser().parse_args(
        ["hunt", "resume", "build", "--output", "/tmp/main.pdf"]
    )
    assert (build.hunt_command, build.resume_command, build.output) == (
        "resume",
        "build",
        "/tmp/main.pdf",
    )
    watch = build_parser().parse_args(
        ["hunt", "resume", "watch", "--open", "--interval", "0.25"]
    )
    assert watch.resume_command == "watch" and watch.open is True
    assert watch.interval == 0.25


def test_lentra_is_an_explicit_autofill_provider() -> None:
    args = build_parser().parse_args(["hunt", "autofill", "--provider", "lentra"])
    assert args.provider == "lentra"


def test_claude_tailoring_is_opt_in() -> None:
    plain = build_parser().parse_args(["hunt", "prep", "1"])
    tailored = build_parser().parse_args(["hunt", "prep", "1", "--tailor"])
    assert plain.tailor is False
    assert tailored.tailor is True


def test_answer_library_is_manageable_without_the_web_app() -> None:
    listed = build_parser().parse_args(["hunt", "answers", "list"])
    added = build_parser().parse_args(
        ["hunt", "answers", "add", "--question", "Why us?", "--file", "answer.md"]
    )
    edited = build_parser().parse_args(
        ["hunt", "answers", "edit", "2", "--text", "Updated", "--approve"]
    )
    assert listed.answer_command == "list"
    assert added.file == "answer.md" and added.draft is False
    assert edited.ref == "2" and edited.approved is True


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
