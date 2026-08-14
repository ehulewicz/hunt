#!/usr/bin/env bash
set -euo pipefail

worker_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chrome_profile="${XDG_DATA_HOME:-$HOME/.local/share}/personal-os/chrome"
cdp_url="http://127.0.0.1:9222"

mkdir -p "$chrome_profile"
if ! curl --silent --fail "$cdp_url/json/version" >/dev/null 2>&1; then
  /usr/bin/google-chrome \
    --remote-debugging-port=9222 \
    --remote-debugging-address=127.0.0.1 \
    --user-data-dir="$chrome_profile" \
    --no-first-run \
    --no-default-browser-check \
    >/tmp/personal-os-chrome.log 2>&1 &
  for _ in {1..50}; do
    curl --silent --fail "$cdp_url/json/version" >/dev/null 2>&1 && break
    sleep 0.2
  done
fi

cd "$worker_dir"
if ! UV_CACHE_DIR=.uv-cache uv run personal-os-worker doctor; then
  echo "Runner preflight has failures; continuing so hosted outages can recover automatically." >&2
fi
exec env UV_CACHE_DIR=.uv-cache uv run personal-os-worker run
