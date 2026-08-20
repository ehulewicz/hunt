#!/usr/bin/env bash
# Kept as a convenience entry point. `hunt run` starts the API and Chrome itself,
# so this is only a thin wrapper for launching from Windows via run.ps1.
set -euo pipefail
exec hunt run "$@"
