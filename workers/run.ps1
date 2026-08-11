$ErrorActionPreference = "Stop"

$chrome = Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) {
  throw "Google Chrome was not found at $chrome"
}

$profile = Join-Path $env:LOCALAPPDATA "PersonalOS\Chrome"
New-Item -ItemType Directory -Force -Path $profile | Out-Null

$listening = Get-NetTCPConnection -LocalPort 9222 -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
  Start-Process $chrome -ArgumentList @(
    "--remote-debugging-port=9222",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=$profile",
    "--no-first-run"
  )
  Start-Sleep -Seconds 2
}

$workerPath = (wsl.exe wslpath -u $PSScriptRoot).Trim()
if (-not $workerPath) {
  throw "Could not resolve the WSL worker path"
}

wsl.exe --cd $workerPath uv run personal-os-worker run
exit $LASTEXITCODE
