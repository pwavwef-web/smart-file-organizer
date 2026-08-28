from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from smart_sorter import notifier


class NotifierTests(unittest.TestCase):
    def test_notify_adds_destination_folder_uri(self) -> None:
        destination = Path("C:/Users/Test User/Documents/Sorted Files")

        with patch.object(notifier.os, "name", "nt"), patch.object(
            notifier, "_register_notification_identity"
        ) as register, patch.object(notifier.subprocess, "run") as run:
            notifier.notify("Moved", "report.pdf -> Sorted Files", open_folder=destination)

        env = run.call_args.kwargs["env"]
        register.assert_called_once_with()
        self.assertEqual(env["SS_LAUNCH"], destination.resolve().as_uri())
        self.assertEqual(env["SS_APP_ID"], notifier._APP_ID)
        self.assertIn("template='ToastGeneric'", notifier._TOAST_PS)
        self.assertIn("activationType', 'protocol", notifier._TOAST_PS)
        self.assertIn("arguments', $env:SS_LAUNCH", notifier._TOAST_PS)
        self.assertIn("content', 'Open folder", notifier._TOAST_PS)

    def test_notify_without_folder_has_no_launch_uri(self) -> None:
        with patch.object(notifier.os, "name", "nt"), patch.object(
            notifier, "_register_notification_identity"
        ), patch.object(notifier.subprocess, "run") as run:
            notifier.notify("Title", "Message")

        self.assertEqual(run.call_args.kwargs["env"]["SS_LAUNCH"], "")

    def test_notify_falls_back_when_interactive_toast_fails(self) -> None:
        with patch.object(notifier.os, "name", "nt"), patch.object(
            notifier, "_register_notification_identity"
        ), patch.object(
            notifier.subprocess,
            "run",
            side_effect=[notifier.subprocess.CalledProcessError(1, "powershell"), None],
        ) as run:
            notifier.notify("Title", "Message", open_folder=Path("C:/Sorted"))

        self.assertEqual(run.call_count, 2)
        self.assertIs(run.call_args_list[1].args[0][-1], notifier._LEGACY_TOAST_PS)
        self.assertEqual(
            run.call_args_list[1].kwargs["env"]["SS_APP_ID"], notifier._POWERSHELL_APP_ID
        )


if __name__ == "__main__":
    unittest.main()
