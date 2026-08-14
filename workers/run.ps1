$ErrorActionPreference = "Stop"

$workerPath = (wsl.exe wslpath -u $PSScriptRoot).Trim()
if (-not $workerPath) {
  throw "Could not resolve the WSL worker path"
}

wsl.exe --cd $workerPath bash ./run.sh
exit $LASTEXITCODE
