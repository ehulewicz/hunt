from __future__ import annotations

import argparse
import getpass
from collections.abc import Sequence

from personal_os_worker import __version__
from personal_os_worker.config import WorkerConfig, load_config, save_config
from personal_os_worker.doctor import run_doctor
from personal_os_worker.runner import run_worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal-os-worker",
        description="Foreground Personal OS Jobs worker. It never submits applications.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    pair = commands.add_parser("pair", help="Store a revocable worker token locally")
    pair.add_argument("--api-url", required=True)
    pair.add_argument("--cdp-url", default="http://127.0.0.1:9222")

    run = commands.add_parser("run", help="Claim and execute Jobs tasks in the foreground")
    run.add_argument("--once", action="store_true", help="Exit after one task or empty poll")
    commands.add_parser("doctor", help="Check pairing, Chrome, Claude, and PDF tools")
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if args.command == "pair":
        token = getpass.getpass("Worker token (input hidden): ").strip()
        if not token:
            raise SystemExit("Worker token is required")
        path = save_config(
            WorkerConfig(
                api_url=args.api_url.rstrip("/"),
                token=token,
                cdp_url=args.cdp_url,
            )
        )
        print(f"Worker paired. Configuration stored at {path}")
        return 0
    try:
        if args.command == "run":
            return run_worker(load_config(), once=args.once)
        if args.command == "doctor":
            checks = run_doctor(load_config())
            for check in checks:
                marker = "OK" if check.ok else "FAIL"
                print(f"[{marker}] {check.name}: {check.detail}")
            return 0 if all(check.ok for check in checks) else 1
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    return 2
