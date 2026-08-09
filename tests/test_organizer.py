from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from smart_sorter.config import load_settings
from smart_sorter.organizer import Organizer


class OrganizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inbox = self.root / "inbox"
        self.library = self.root / "library"
        self.roots = self.root / "roots"
        self.inbox.mkdir()
        # Redirect the Windows known-folder tokens into the temp tree so tests
        # never touch the real Documents/Pictures/Videos/Music folders.
        self.locations = {
            "Documents": str(self.library / "Documents"),
            "Pictures": str(self.roots / "Pictures"),
            "Videos": str(self.roots / "Videos"),
            "Music": str(self.roots / "Music"),
        }
        self.config = {
            "inbox_folders": [str(self.inbox)],
            "library_root": str(self.library),
            "locations": self.locations,
            "log_file": str(self.root / "history.jsonl"),
            "gemini": {"enabled": False},
            "rules": [
                {
                    "name": "CVs",
                    "destination": "Documents\\Career\\CVs",
                    "keywords": ["curriculum vitae", "work experience"],
                    "extensions": [".txt"],
                    "priority": 100,
                },
                {
                    "name": "Indigen World",
                    "destination": "{Pictures}\\Projects\\Indigen World",
                    "keywords": ["indigen world"],
                    "extensions": [],
                    "priority": 90,
                },
            ],
        }
        self.organizer = Organizer(self._settings(self.config))

    def _settings(self, config: dict, name: str = "config.json"):
        path = self.root / name
        path.write_text(json.dumps(config), encoding="utf-8")
        return load_settings(path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_cv_content_rule(self) -> None:
        cv = self.inbox / "Kwame.txt"
        cv.write_text("Curriculum Vitae Work Experience Education", encoding="utf-8")
        move = self.organizer.plan([cv])[0]
        self.assertEqual(move.classification.category, "CVs")
        self.assertIn("Career", str(move.destination))

    def test_project_filename_rule_uses_pictures_root(self) -> None:
        screenshot = self.inbox / "Screenshot - Indigen World app.png"
        screenshot.write_bytes(b"not-an-image-needed-for-local-rule")
        move = self.organizer.plan([screenshot])[0]
        self.assertEqual(move.classification.category, "Indigen World")
        self.assertTrue(str(move.destination).startswith(str((self.roots / "Pictures").resolve())))

    def test_video_routes_to_videos_known_folder(self) -> None:
        clip = self.inbox / "holiday.mp4"
        clip.write_bytes(b"fake-video")
        move = self.organizer.plan([clip])[0]
        self.assertEqual(move.classification.category, "Video")
        self.assertEqual(move.destination.parent, (self.roots / "Videos").resolve())

    def test_audio_routes_to_music_known_folder(self) -> None:
        song = self.inbox / "track.mp3"
        song.write_bytes(b"fake-audio")
        move = self.organizer.plan([song])[0]
        self.assertEqual(move.classification.category, "Audio")
        self.assertEqual(move.destination.parent, (self.roots / "Music").resolve())

    def test_photo_gets_date_subfolders(self) -> None:
        photo = self.inbox / "IMG_2031.jpg"
        photo.write_bytes(b"\xff\xd8\xff\xd9not-real-exif")  # JPEG magic, no EXIF -> mtime
        stamp = datetime(2023, 7, 15, 9, 0, 0).timestamp()
        os.utime(photo, (stamp, stamp))
        move = self.organizer.plan([photo])[0]
        self.assertEqual(move.classification.category, "Photos")
        self.assertIn(os.path.join("Photos", "2023", "2023-07"), str(move.destination))

    def test_expanded_taxonomy(self) -> None:
        cases = {"script.py": "Code", "novel.epub": "Ebooks", "ubuntu.iso": "Disk Images", "font.ttf": "Fonts"}
        for name, category in cases.items():
            f = self.inbox / name
            f.write_bytes(b"data")
            move = self.organizer.plan([f])[0]
            self.assertEqual(move.classification.category, category, name)

    def test_apply_collision_and_undo(self) -> None:
        first = self.inbox / "notes.txt"
        first.write_text("ordinary notes", encoding="utf-8")
        destination_dir = self.library / "Documents" / "General"
        destination_dir.mkdir(parents=True)
        (destination_dir / "notes.txt").write_text("existing different content", encoding="utf-8")

        moves = self.organizer.plan([first])
        self.assertEqual(moves[0].destination.name, "notes (1).txt")
        _, results = self.organizer.apply(moves)
        moved = [item for item in results if item.note != "duplicate skipped"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].destination.name, "notes (1).txt")
        self.assertFalse(first.exists())

        _, restored, warnings = self.organizer.undo_last_batch()
        self.assertEqual(restored, 1)
        self.assertEqual(warnings, [])
        self.assertTrue(first.exists())

    def test_identical_duplicate_routed_to_duplicates(self) -> None:
        content = b"exactly the same bytes"
        incoming = self.inbox / "report.pdf"
        incoming.write_bytes(content)
        target_dir = self.library / "Documents" / "PDFs"
        target_dir.mkdir(parents=True)
        (target_dir / "report.pdf").write_bytes(content)  # identical, already sorted

        move = self.organizer.plan([incoming])[0]
        self.assertEqual(self.organizer.last_duplicates, 1)
        self.assertIn("Duplicates", str(move.destination))

    def test_already_sorted_file_is_skipped(self) -> None:
        general = self.library / "Documents" / "General"
        general.mkdir(parents=True)
        resident = general / "memo.txt"
        resident.write_text("just a memo", encoding="utf-8")
        moves = self.organizer.plan([resident])
        self.assertEqual(moves, [])
        self.assertEqual(self.organizer.last_skipped_in_place, 1)

    def test_partial_download_is_ignored(self) -> None:
        partial = self.inbox / "large-video.mp4.crdownload"
        partial.write_bytes(b"incomplete")
        discovered = self.organizer.discover(self.inbox, recursive=False)
        self.assertNotIn(partial.resolve(), discovered)

    def test_ignore_patterns_and_hidden_files_are_skipped(self) -> None:
        cfg = {**self.config, "ignore_patterns": ["*.bak"]}
        org = Organizer(self._settings(cfg, "ignore.json"))
        hidden = self.inbox / ".env"
        backup = self.inbox / "notes.bak"
        keep = self.inbox / "notes.txt"
        hidden.write_text("secret", encoding="utf-8")
        backup.write_text("backup", encoding="utf-8")
        keep.write_text("keep", encoding="utf-8")
        discovered = org.discover(self.inbox, recursive=False)
        self.assertEqual(discovered, [keep.resolve()])

    def test_include_hidden_allows_dotfiles(self) -> None:
        cfg = {**self.config, "include_hidden": True}
        org = Organizer(self._settings(cfg, "hidden.json"))
        hidden = self.inbox / ".env"
        hidden.write_text("API_KEY=x", encoding="utf-8")
        self.assertIn(hidden.resolve(), org.discover(self.inbox, recursive=False))

    def test_low_confidence_routes_to_review_location(self) -> None:
        cfg = {**self.config, "minimum_confidence": 0.9, "review_location": "{Library}\\Needs Review\\{category}"}
        org = Organizer(self._settings(cfg, "review.json"))
        unknown = self.inbox / "mystery.bin"
        unknown.write_bytes(b"x")
        move = org.plan([unknown])[0]
        self.assertEqual(move.classification.category, "Other")
        self.assertIn(os.path.join("Needs Review", "Other"), str(move.destination))
        self.assertIn("confidence floor", move.classification.reason)

    def test_destination_cannot_escape_root(self) -> None:
        unsafe = dict(self.config)
        unsafe["rules"] = [{"name": "Unsafe", "destination": "..\\outside", "keywords": ["x"]}]
        with self.assertRaises(ValueError):
            self._settings(unsafe, "unsafe.json")

    def test_unknown_root_token_rejected(self) -> None:
        bad = dict(self.config)
        bad["rules"] = [{"name": "Bad", "destination": "{Nowhere}\\x", "keywords": ["x"]}]
        with self.assertRaises(ValueError):
            self._settings(bad, "badroot.json")

    def test_unknown_format_token_rejected(self) -> None:
        bad = dict(self.config)
        bad["rules"] = [{"name": "Bad", "destination": "{Documents}\\{bogus}", "keywords": ["x"]}]
        with self.assertRaises(ValueError):
            self._settings(bad, "badtoken.json")

    def _gemini_organizer(self, name: str) -> Organizer:
        cfg = dict(self.config)
        cfg["gemini"] = {"enabled": True, "suggest_subfolder": True, "send_images": True}
        org = Organizer(self._settings(cfg, name))
        org.classifier.api_key = "test-key"  # bypass the env-var requirement
        return org

    def test_gemini_content_subfolder(self) -> None:
        org = self._gemini_organizer("gsub.json")
        org.classifier._gemini_answer = lambda path, text: {
            "category": "Documents", "confidence": 0.9, "reason": "a report", "subfolder": "Tax 2026"
        }
        f = self.inbox / "statement.pdf"
        f.write_bytes(b"x")
        move = org.plan([f])[0]
        self.assertEqual(move.classification.source, "Gemini")
        self.assertIn(os.path.join("General", "Tax 2026"), str(move.destination))

    def test_screenshot_pinned_with_gemini_folder(self) -> None:
        org = self._gemini_organizer("gshot.json")
        # Gemini tries to bucket it as Code, but a screenshot must stay a screenshot.
        org.classifier._gemini_answer = lambda path, text: {
            "category": "Code", "confidence": 0.8, "reason": "terminal output", "subfolder": "Console"
        }
        f = self.inbox / "Screenshot 2026-08-08 console.png"
        f.write_bytes(b"x")
        move = org.plan([f])[0]
        self.assertEqual(move.classification.category, "Screenshots")
        self.assertTrue(str(move.destination).startswith(str((self.roots / "Pictures" / "Screenshots").resolve())))
        self.assertEqual(move.destination.parent.name, "Console")

    def test_gemini_subfolder_sanitised(self) -> None:
        org = self._gemini_organizer("gsan.json")
        org.classifier._gemini_answer = lambda path, text: {
            "category": "Images", "confidence": 0.7, "reason": "x", "subfolder": "../../evil\\path"
        }
        f = self.inbox / "pic.png"
        f.write_bytes(b"x")
        move = org.plan([f])[0]
        self.assertNotIn("..", str(move.destination))
        self.assertNotIn("evil\\path", str(move.destination))

    def _skip_organizer(self, name: str) -> Organizer:
        cfg = dict(self.config)
        cfg["on_duplicate"] = "skip"
        return Organizer(self._settings(cfg, name))

    def test_duplicate_skip_reports_skipped_not_moved(self) -> None:
        # Finding #1: a skip-policy duplicate must not be reported as moved.
        import io
        from contextlib import redirect_stdout

        from smart_sorter import cli

        org = self._skip_organizer("skip.json")
        content = b"identical duplicate bytes"
        incoming = self.inbox / "report.pdf"
        incoming.write_bytes(content)
        target_dir = self.library / "Documents" / "PDFs"
        target_dir.mkdir(parents=True)
        (target_dir / "report.pdf").write_bytes(content)  # identical, already sorted

        moves = org.plan([incoming])
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].note, "duplicate skipped")

        _, results = org.apply(moves)
        self.assertTrue(incoming.exists())  # never moved
        self.assertEqual(sum(1 for r in results if r.note != "duplicate skipped"), 0)

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cli._display(results, applied=True)
        output = buffer.getvalue()
        self.assertIn("[SKIPPED]", output)
        self.assertNotIn("[MOVED]", output)

    def test_apply_reports_actual_destination_on_late_clash(self) -> None:
        # Finding #4: a clash that appears between plan and apply must be
        # reflected in the returned destination, not the stale planned one.
        doc = self.inbox / "memo.txt"
        doc.write_text("brand new memo", encoding="utf-8")
        moves = self.organizer.plan([doc])
        planned = moves[0].destination
        self.assertEqual(planned.name, "memo.txt")

        planned.parent.mkdir(parents=True, exist_ok=True)
        planned.write_text("something else", encoding="utf-8")  # late collision

        _, results = self.organizer.apply(moves)
        moved = [r for r in results if r.note != "duplicate skipped"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].destination.name, "memo (1).txt")
        self.assertNotEqual(moved[0].destination, planned)
        self.assertEqual(planned.read_text(encoding="utf-8"), "something else")
        self.assertEqual(moved[0].destination.read_text(encoding="utf-8"), "brand new memo")

    def test_undo_skips_missing_file_and_reaches_older_batch(self) -> None:
        # Finding #2: a missing sorted file must not permanently block undo
        # of older batches.
        alpha = self.inbox / "alpha.txt"
        alpha.write_text("alpha", encoding="utf-8")
        _, res_a = self.organizer.apply(self.organizer.plan([alpha]))

        beta = self.inbox / "beta.txt"
        beta.write_text("beta", encoding="utf-8")
        _, res_b = self.organizer.apply(self.organizer.plan([beta]))
        dest_b = [r for r in res_b if r.note != "duplicate skipped"][0].destination

        dest_b.unlink()  # the latest batch's sorted file goes missing

        batch_b, restored_b, warnings_b = self.organizer.undo_last_batch()
        self.assertEqual(restored_b, 0)
        self.assertTrue(warnings_b)

        batch_a, restored_a, warnings_a = self.organizer.undo_last_batch()
        self.assertEqual(restored_a, 1)
        self.assertNotEqual(batch_a, batch_b)
        self.assertEqual(warnings_a, [])
        self.assertTrue(alpha.exists())

    def test_move_rolls_back_when_log_write_fails(self) -> None:
        # Finding #3: if the undo log cannot be written, the file must not be
        # left moved with no way to restore it.
        doc = self.inbox / "gamma.txt"
        doc.write_text("gamma", encoding="utf-8")
        moves = self.organizer.plan([doc])
        planned = moves[0].destination

        def boom(record: dict) -> None:
            raise OSError("log is unwritable")

        self.organizer._append_log = boom  # type: ignore[assignment]
        with self.assertRaises(OSError):
            self.organizer.apply(moves)

        self.assertTrue(doc.exists())       # source restored
        self.assertFalse(planned.exists())  # nothing left behind

    def test_notification_toggle_and_format(self) -> None:
        from smart_sorter import cli
        from smart_sorter import notifier as notmod
        from smart_sorter.models import Classification, PlannedMove

        captured: list[tuple[str, str]] = []
        original = notmod.notify
        notmod.notify = lambda title, message: captured.append((title, message))
        try:
            src = self.inbox / "clip.mp4"
            dest = Path.home() / "Videos" / "clip.mp4"
            move = PlannedMove(src, dest, Classification("Video", "{Videos}", 0.65, "ext", "extension fallback"))

            off = self._settings({**self.config, "notifications": {"enabled": False}}, "noff.json")
            cli._notify_moves([move], off)
            self.assertEqual(captured, [])

            on = self._settings({**self.config, "notifications": {"enabled": True}}, "non.json")
            cli._notify_moves([move], on)
            self.assertEqual(len(captured), 1)
            self.assertIn("clip.mp4", captured[0][1])
            self.assertIn("Videos", captured[0][1])
        finally:
            notmod.notify = original

    def test_cli_scan_json_limit_extension_and_category_filters(self) -> None:
        import io
        from contextlib import redirect_stdout

        from smart_sorter import cli

        (self.inbox / "a.txt").write_text("ordinary notes", encoding="utf-8")
        (self.inbox / "b.mp4").write_bytes(b"video")
        (self.inbox / "c.pdf").write_bytes(b"pdf")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main([
                "--config",
                str(self.root / "config.json"),
                "scan",
                "--only-ext",
                "txt,mp4",
                "--category",
                "Video",
                "--limit",
                "2",
                "--json",
            ])
        payload = json.loads(buffer.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["moves"]), 1)
        self.assertEqual(payload["moves"][0]["category"], "Video")

    def test_history_command_reports_batches(self) -> None:
        first = self.inbox / "history.txt"
        first.write_text("history", encoding="utf-8")
        self.organizer.apply(self.organizer.plan([first]))
        history = self.organizer.history(limit=1)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["count"], 1)
        self.assertIn("Documents", history[0]["categories"])


if __name__ == "__main__":
    unittest.main()
