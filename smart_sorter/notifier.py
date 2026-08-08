from __future__ import annotations

import os
import subprocess
from pathlib import Path


# Builds and shows a Windows toast via the WinRT API. Title/body are passed as
# environment variables (never interpolated into the script) so arbitrary file
# names cannot break or inject into the PowerShell command.
_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$AppId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $xml.GetElementsByTagName('text')
$t.Item(0).AppendChild($xml.CreateTextNode($env:SS_TITLE)) | Out-Null
$t.Item(1).AppendChild($xml.CreateTextNode($env:SS_MSG)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId).Show($toast)
"""


def notify(title: str, message: str) -> None:
    """Show a best-effort Windows notification. Never raises."""
    if os.name != "nt":
        return
    try:
        env = dict(os.environ, SS_TITLE=title[:120], SS_MSG=message[:600])
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
            env=env,
            timeout=20,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def friendly_folder(path: Path, home: Path) -> str:
    """A short folder label for notifications, relative to the user's home."""
    try:
        return str(path.relative_to(home))
    except ValueError:
        return str(path)
