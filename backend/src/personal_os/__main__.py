"""Entry point for running the API locally.

The hosted container invokes uvicorn directly; this exists so the local Jobs
workflow can start the API from any directory.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="personal-os-api",
        description="Run the Personal OS API on the loopback interface.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Restart on source changes")
    args = parser.parse_args(arguments)

    import uvicorn

    uvicorn.run(
        "personal_os.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
