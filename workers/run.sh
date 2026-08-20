#!/usr/bin/env bash
set -euo pipefail

worker_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cdp_url="http://127.0.0.1:9333"

# Jobs drives the Windows Chrome build, not the WSLg one: it renders properly and
# keeps a real persistent profile. Reaching its CDP port from WSL requires
# networkingMode=mirrored in C:\Users\eahtr\.wslconfig, because Chrome binds
# remote debugging to loopback only.
chrome_exe="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
chrome_profile_windows='C:\Users\eahtr\personal-os-chrome'
chrome_profile_wsl="/mnt/c/Users/eahtr/personal-os-chrome"

mkdir -p "$chrome_profile_wsl"
if ! curl --silent --fail "$cdp_url/json/version" >/dev/null 2>&1; then
  if [[ ! -x "$chrome_exe" ]]; then
    echo "Windows Chrome not found at $chrome_exe" >&2
    exit 1
  fi
  # A dedicated profile is required: Chrome 136+ serves 404 on /json/* when
  # remote debugging is pointed at the default user data directory.
  "$chrome_exe" \
    --remote-debugging-port=9333 \
    --user-data-dir="$chrome_profile_windows" \
    --no-first-run \
    --no-default-browser-check \
    >/tmp/personal-os-chrome.log 2>&1 &
  for _ in {1..50}; do
    curl --silent --fail "$cdp_url/json/version" >/dev/null 2>&1 && break
    sleep 0.2
  done
fi

if ! curl --silent --fail "$cdp_url/json/version" >/dev/null 2>&1; then
  echo "Chrome started but its CDP endpoint is unreachable at $cdp_url." >&2
  echo "Check that networkingMode=mirrored is set in C:\\Users\\eahtr\\.wslconfig" >&2
  echo "and that 'wsl --shutdown' has been run since it was added." >&2
  exit 1
fi

cd "$worker_dir"
if ! UV_CACHE_DIR=.uv-cache uv run hunt doctor; then
  echo "Runner preflight has failures; continuing so hosted outages can recover automatically." >&2
fi
exec env UV_CACHE_DIR=.uv-cache uv run hunt run
