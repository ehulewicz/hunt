import argparse
from collections.abc import Sequence

from personal_os_worker import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal-os-worker",
        description="Personal OS worker scaffold; task execution is not implemented yet.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    build_parser().parse_args(arguments)
    return 0
