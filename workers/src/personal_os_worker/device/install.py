"""`os device install`: make this Windows + WSL machine a Hunt device, once.

Three things outside WSL, all for the signed-in Windows user: a Task Scheduler
logon task that starts `os device serve` in this distro, hidden; Windows
`tailscale serve`, which publishes the agent's loopback port to the tailnet
over HTTPS (tailnet only, never Funnel); and the device's identity. A logon
task needs a signed-in Windows session, so a machine that reboots to the
lock screen has no agent until someone signs in.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Callable

from personal_os_worker.device.identity import AGENT_PORT, DeviceError, tailscale_exe

TASK_NAME = "Personal OS device"
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"

Run = Callable[..., subprocess.CompletedProcess[str]]


def encoded(script: str) -> str:
    """PowerShell's -EncodedCommand form, so no quoting survives a hop to be misread."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def serve_command(distro: str) -> str:
    """What the logon task runs: the agent, in a login shell so PATH is the usual one."""
    return f"wsl.exe -d {distro} --exec bash -lc 'exec os device serve'"


def task_script(distro: str) -> str:
    hidden = f"-NoProfile -WindowStyle Hidden -EncodedCommand {encoded(serve_command(distro))}"
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
            f"-Argument '{hidden}'",
            '$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\\$env:USERNAME"',
            "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) "
            "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew "
            "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)",
            f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger "
            "-Settings $settings -Description 'Starts the Personal OS Hunt agent in WSL' "
            "-Force | Out-Null",
            f"Start-ScheduledTask -TaskName '{TASK_NAME}'",
        ]
    )


def wsl_distro() -> str:
    distro = os.environ.get("WSL_DISTRO_NAME", "").strip()
    if not distro:
        raise DeviceError("WSL_DISTRO_NAME is not set; run this inside WSL")
    return distro


def register_logon_task(distro: str, run: Run = subprocess.run) -> None:
    """Start the agent at every Windows sign-in, and now."""
    result = run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         encoded(task_script(distro))],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        cwd="/mnt/c",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise DeviceError(
            "Could not register the logon task: " + (detail[0] if detail else "PowerShell failed")
        )


def serve_on_tailnet(run: Run = subprocess.run) -> None:
    """Publish the agent's loopback port to the tailnet over HTTPS, in the background."""
    executable = tailscale_exe()
    if executable is None:
        raise DeviceError("Tailscale for Windows is not installed")
    command = [executable, "serve", "--bg", "--https=443", f"http://127.0.0.1:{AGENT_PORT}"]
    try:
        result = run(command, check=False, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        # Serve waits for an approval it prints a link to, which capture hides.
        raise DeviceError(
            "tailscale serve did not finish in 60s; run 'tailscale.exe serve --bg --https=443 "
            f"http://127.0.0.1:{AGENT_PORT}' in a terminal to see what it is waiting for"
        ) from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise DeviceError(
            "tailscale serve failed: " + (detail[-1] if detail else "no output")
            + ". Turn on HTTPS certificates for the tailnet in the Tailscale admin console."
        )
