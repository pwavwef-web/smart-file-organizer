from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

from . import notifier
from .config import Settings, load_settings
from .models import PlannedMove
from .organizer import Organizer


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.json"


def _display(moves: list[PlannedMove], *, applied: bool = False) -> None:
    if not moves:
        print("No eligible files found.")
        return
    for item in moves:
        info = item.classification
        skipped = item.note == "duplicate skipped"
        if applied:
            verb = "SKIPPED" if skipped else "MOVED"
        else:
            verb = "WOULD SKIP" if skipped else "WOULD MOVE"
        tag = " [duplicate]" if item.note else ""
        print(f"[{verb}]{tag} {item.source}")
        print(f"         -> {item.destination}")
        detail = f"         {info.category} | {info.confidence:.0%} | {info.source} | {info.reason}"
        if item.note:
            detail += f" | {item.note}"
        print(detail)


def _summary(moves: list[PlannedMove], organizer: Organizer) -> None:
    counts = Counter(item.classification.category for item in moves if item.note != "duplicate skipped")
    if counts:
        print("\nSummary by category:")
        for category, count in counts.most_common():
            print(f"  {count:>4}  {category}")
    extras = []
    if organizer.last_skipped_in_place:
        extras.append(f"{organizer.last_skipped_in_place} already in place")
    if organizer.last_duplicates:
        extras.append(f"{organizer.last_duplicates} duplicate(s) detected")
    if extras:
        print("Skipped: " + ", ".join(extras))


def _notify_moves(moves: list[PlannedMove], settings: Settings) -> None:
    if not settings.notifications.enabled:
        return
    applied = [item for item in moves if item.note != "duplicate skipped"]
    if not applied:
        return
    home = Path.home()
    limit = settings.notifications.max_files
    if len(applied) == 1:
        item = applied[0]
        title = "Smart Sorter"
        message = f"Moved {item.source.name}\n-> {notifier.friendly_folder(item.destination.parent, home)}"
    else:
        lines = [
            f"{item.source.name} -> {notifier.friendly_folder(item.destination.parent, home)}"
            for item in applied[:limit]
        ]
        if len(applied) > limit:
            lines.append(f"...and {len(applied) - limit} more")
        title = f"Smart Sorter - {len(applied)} files sorted"
        message = "\n".join(lines)
    notifier.notify(title, message)


def _configured_folders(settings: Settings, selected: str | None) -> list[Path]:
    return [Path(selected).expanduser().resolve()] if selected else list(settings.inbox_folders)


def _scan_once(organizer: Organizer, folders: list[Path], recursive: bool, minimum_age: int) -> list[PlannedMove]:
    files: list[Path] = []
    for folder in folders:
        files.extend(organizer.discover(folder, recursive=recursive, minimum_age=minimum_age))
    return organizer.plan(files)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview, sort, watch, and undo personal file organization.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan once; preview unless --apply is supplied")
    scan.add_argument("--folder", help="Scan one folder instead of configured inboxes")
    scan.add_argument("--recursive", action="store_true", help="Include subfolders")
    scan.add_argument("--apply", action="store_true", help="Actually move the proposed files")

    watch = subparsers.add_parser("watch", help="Poll configured inboxes; preview unless --apply is supplied")
    watch.add_argument("--apply", action="store_true", help="Automatically move newly eligible files")

    subparsers.add_parser("undo", help="Undo the latest non-undone move batch")
    subparsers.add_parser("check", help="Check configuration, folders, roots, and Gemini availability")
    return parser


def _run_check(settings: Settings, config_path: Path) -> int:
    print(f"Config: {config_path.resolve()}")
    print(f"Duplicate policy: {settings.on_duplicate}   Default root: {settings.default_location}")
    print("\nDestination roots (resolved):")
    for name, path in sorted(settings.locations.items()):
        marker = "ready" if path.exists() else "will be created"
        print(f"  {{{name}}} -> {path} ({marker})")
    print("\nInboxes:")
    for folder in settings.inbox_folders:
        print(f"  {folder} ({'ready' if folder.exists() else 'not found; skipped'})")
    if settings.gemini.enabled:
        state = "ready" if os.environ.get("GEMINI_API_KEY") else "enabled, but GEMINI_API_KEY is not set"
        print(f"\nGemini: {state}  (model {settings.gemini.model})")
        print(f"Gemini content subfolders: {'on' if settings.gemini.suggest_subfolder else 'off'}")
    else:
        print("\nGemini: disabled")
    notify = settings.notifications
    print(f"Notifications: {'on' if notify.enabled else 'off'} (up to {notify.max_files} files per toast)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    organizer = Organizer(settings)

    if args.command == "check":
        return _run_check(settings, args.config)

    if args.command == "undo":
        batch_id, count, warnings = organizer.undo_last_batch()
        if batch_id is None:
            print("Nothing is available to undo.")
            return 0
        print(f"Restored {count} file(s) from batch {batch_id}.")
        for warning in warnings:
            print(f"WARNING: {warning}")
        return 0 if not warnings else 1

    if args.command == "scan":
        folders = _configured_folders(settings, args.folder)
        recursive = args.recursive or (settings.recursive and not args.folder)
        moves = _scan_once(organizer, folders, recursive, minimum_age=0)
        if args.apply:
            batch_id, results = organizer.apply(moves)
            _display(results, applied=True)
            _summary(results, organizer)
            _notify_moves(results, settings)
            moved = sum(1 for item in results if item.note != "duplicate skipped")
            print(f"\nApplied batch {batch_id}: {moved} file(s) moved. Run 'undo' to restore it.")
        else:
            _display(moves)
            _summary(moves, organizer)
            print("\nPreview only: nothing was changed. Add --apply when the destinations look right.")
        if organizer.classifier.last_gemini_error:
            print(f"WARNING: Gemini was unavailable; local fallback used: {organizer.classifier.last_gemini_error}")
        return 0

    seen: dict[str, tuple[int, int]] = {}
    mode = "LIVE MOVE" if args.apply else "PREVIEW"
    print(f"Watching {len(settings.inbox_folders)} inbox(es) in {mode} mode. Press Ctrl+C to stop.")
    try:
        while True:
            files: list[Path] = []
            for folder in settings.inbox_folders:
                files.extend(
                    organizer.discover(
                        folder,
                        recursive=settings.recursive,
                        minimum_age=settings.minimum_age_seconds,
                    )
                )
            fresh_paths: list[Path] = []
            for path in files:
                try:
                    signature = (path.stat().st_size, path.stat().st_mtime_ns)
                except OSError:
                    continue
                key = str(path)
                if seen.get(key) != signature:
                    seen[key] = signature
                    fresh_paths.append(path)
            fresh = organizer.plan(fresh_paths)
            if fresh:
                if args.apply:
                    _, results = organizer.apply(fresh)
                    _notify_moves(results, settings)
                    _display(results, applied=True)
                else:
                    _display(fresh, applied=False)
            time.sleep(settings.scan_interval_seconds)
    except KeyboardInterrupt:
        print("\nWatcher stopped.")
        return 0
