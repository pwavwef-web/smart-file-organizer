from __future__ import annotations

import os
import subprocess
from pathlib import Path


_APP_ID = "SmartFileOrganizer.Notifications"
_POWERSHELL_APP_ID = (
    r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
)


# Builds and shows a Windows toast via the WinRT API. Title/body are passed as
# environment variables (never interpolated into the script) so arbitrary file
# names cannot break or inject into the PowerShell command.
_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime]
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text/><text/></binding></visual></toast>")
$t = $xml.GetElementsByTagName('text')
$t.Item(0).AppendChild($xml.CreateTextNode($env:SS_TITLE)) | Out-Null
$t.Item(1).AppendChild($xml.CreateTextNode($env:SS_MSG)) | Out-Null
if ($env:SS_LAUNCH) {
    $toastNode = $xml.SelectSingleNode('/toast')
    $toastNode.SetAttribute('activationType', 'protocol')
    $toastNode.SetAttribute('launch', $env:SS_LAUNCH)

    $actions = $xml.CreateElement('actions')
    $action = $xml.CreateElement('action')
    $action.SetAttribute('content', 'Open folder')
    $action.SetAttribute('arguments', $env:SS_LAUNCH)
    $action.SetAttribute('activationType', 'protocol')
    $actions.AppendChild($action) | Out-Null
    $toastNode.AppendChild($actions) | Out-Null
}
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:SS_APP_ID).Show($toast)
"""


_LEGACY_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $xml.GetElementsByTagName('text')
$t.Item(0).AppendChild($xml.CreateTextNode($env:SS_TITLE)) | Out-Null
$t.Item(1).AppendChild($xml.CreateTextNode($env:SS_MSG)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:SS_APP_ID).Show($toast)
"""


def _register_notification_identity() -> None:
    """Register the per-user identity required by modern interactive toasts."""
    import winreg

    key_path = rf"Software\Classes\AppUserModelId\{_APP_ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Smart File Organizer")
        winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)


def _show(script: str, env: dict[str, str]) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        timeout=20,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=True,
    )


def notify(title: str, message: str, *, open_folder: Path | None = None) -> None:
    """Show a best-effort Windows notification that can open a folder. Never raises."""
    if os.name != "nt":
        return
    try:
        launch_uri = open_folder.resolve().as_uri() if open_folder is not None else ""
        env = dict(
            os.environ,
            SS_TITLE=title[:120],
            SS_MSG=message[:600],
            SS_LAUNCH=launch_uri,
            SS_APP_ID=_APP_ID,
        )
        _register_notification_identity()
        _show(_TOAST_PS, env)
    except Exception:
        try:
            # Preserve the informational notification on locked-down systems,
            # even though this legacy fallback cannot support click activation.
            fallback_env = dict(env, SS_APP_ID=_POWERSHELL_APP_ID)
            _show(_LEGACY_TOAST_PS, fallback_env)
        except Exception:
            pass


def friendly_folder(path: Path, home: Path) -> str:
    """A short folder label for notifications, relative to the user's home."""
    try:
        return str(path.relative_to(home))
    except ValueError:
        return str(path)
