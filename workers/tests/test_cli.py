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


def test_category_filters_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hunt", "listings", "--faang", "--quant"])


def test_locations_accumulate() -> None:
    args = build_parser().parse_args(
        ["hunt", "listings", "--location", "Atlanta, GA", "--location", "Remote"]
    )
    assert args.location == ["Atlanta, GA", "Remote"]


def test_prep_takes_several_references() -> None:
    assert build_parser().parse_args(["hunt", "prep", "3", "7", "a1b2c3d4"]).refs == [
        "3",
        "7",
        "a1b2c3d4",
    ]


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
