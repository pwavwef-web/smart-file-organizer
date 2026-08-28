from __future__ import annotations

import argparse
import json
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


def _moves_payload(moves: list[PlannedMove], *, applied: bool = False) -> list[dict[str, object]]:
    return [
        {
            "action": "skipped" if item.note == "duplicate skipped" else "moved" if applied else "move",
            "source": str(item.source),
            "destination": str(item.destination),
            "category": item.classification.category,
            "confidence": item.classification.confidence,
            "classifier": item.classification.source,
            "reason": item.classification.reason,
            "note": item.note,
        }
        for item in moves
    ]


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
    notifier.notify(title, message, open_folder=applied[0].destination.parent)


def _configured_folders(settings: Settings, selected: str | None) -> list[Path]:
    return [Path(selected).expanduser().resolve()] if selected else list(settings.inbox_folders)


def _extension_set(values: list[str] | None) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        for item in value.split(","):
            cleaned = item.strip().casefold()
            if cleaned:
                result.add(cleaned if cleaned.startswith(".") else f".{cleaned}")
    return result


def _scan_files(
    organizer: Organizer,
    folders: list[Path],
    *,
    recursive: bool,
    minimum_age: int,
    only_ext: set[str],
    limit: int | None,
) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        files.extend(organizer.discover(folder, recursive=recursive, minimum_age=minimum_age))
    if only_ext:
        files = [path for path in files if path.suffix.casefold() in only_ext]
    return files[:limit] if limit is not None else files


def _filter_moves(moves: list[PlannedMove], categories: list[str] | None) -> list[PlannedMove]:
    wanted = {category.casefold() for category in categories or []}
    if not wanted:
        return moves
    return [move for move in moves if move.classification.category.casefold() in wanted]


def _print_stats(files: list[Path], moves: list[PlannedMove]) -> None:
    print("\nScan stats:")
    print(f"  Files considered: {len(files)}")
    print(f"  Planned actions:  {len(moves)}")
    for suffix, count in Counter(path.suffix.casefold() or "(none)" for path in files).most_common(8):
        print(f"  {suffix:>8}  {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview, sort, watch, and undo personal file organization.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan once; preview unless --apply is supplied")
    scan.add_argument("--folder", help="Scan one folder instead of configured inboxes")
    scan.add_argument("--recursive", action="store_true", help="Include subfolders")
    scan.add_argument("--min-age", type=int, default=0, help="Only include files older than N seconds")
    scan.add_argument("--limit", type=int, help="Process at most N discovered files")
    scan.add_argument("--only-ext", action="append", help="Only include extensions, e.g. pdf,jpg")
    scan.add_argument("--category", action="append", help="Only show/apply a planned category")
    scan.add_argument("--json", action="store_true", help="Print planned/applied actions as JSON")
    scan.add_argument("--stats", action="store_true", help="Print scan counts by extension")
    scan.add_argument("--apply", action="store_true", help="Actually move the proposed files")

    watch = subparsers.add_parser("watch", help="Poll configured inboxes; preview unless --apply is supplied")
    watch.add_argument("--folder", help="Watch one folder instead of configured inboxes")
    watch.add_argument("--recursive", action="store_true", help="Include subfolders")
    watch.add_argument("--only-ext", action="append", help="Only include extensions, e.g. pdf,jpg")
    watch.add_argument("--category", action="append", help="Only show/apply a planned category")
    watch.add_argument("--json", action="store_true", help="Print new actions as JSON lines")
    watch.add_argument("--apply", action="store_true", help="Automatically move newly eligible files")

    subparsers.add_parser("undo", help="Undo the latest non-undone move batch")
    history = subparsers.add_parser("history", help="Show recent move batches")
    history.add_argument("--limit", type=int, default=10, help="Number of batches to show")
    history.add_argument("--json", action="store_true", help="Print history as JSON")
    subparsers.add_parser("check", help="Check configuration, folders, roots, and Gemini availability")
    subparsers.add_parser("ui", help="Open the desktop interface")
    return parser


def _run_check(settings: Settings, config_path: Path) -> int:
    print(f"Config: {config_path.resolve()}")
    print(f"Duplicate policy: {settings.on_duplicate}   Default root: {settings.default_location}")
    print(f"Review below: {settings.minimum_confidence:.0%} -> {settings.review_location}")
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
    print(f"Hidden files: {'included' if settings.include_hidden else 'skipped'}")
    if settings.ignore_patterns:
        print(f"Ignore patterns: {', '.join(settings.ignore_patterns)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "ui":
        from .ui import run

        return run(args.config)

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

    if args.command == "history":
        batches = organizer.history(args.limit)
        if args.json:
            print(json.dumps(batches, indent=2))
            return 0
        if not batches:
            print("No move history found.")
            return 0
        for batch in batches:
            categories = batch.get("categories", {})
            if isinstance(categories, dict):
                category_text = ", ".join(f"{name}: {count}" for name, count in sorted(categories.items()))
            else:
                category_text = ""
            print(f"{batch.get('timestamp')}  {batch.get('batch_id')}  {batch.get('count')} file(s)")
            if category_text:
                print(f"  {category_text}")
        return 0

    if args.command == "scan":
        folders = _configured_folders(settings, args.folder)
        recursive = args.recursive or (settings.recursive and not args.folder)
        files = _scan_files(
            organizer,
            folders,
            recursive=recursive,
            minimum_age=max(0, args.min_age),
            only_ext=_extension_set(args.only_ext),
            limit=args.limit,
        )
        moves = _filter_moves(organizer.plan(files), args.category)
        if args.apply:
            batch_id, results = organizer.apply(moves)
            if args.json:
                print(json.dumps({"batch_id": batch_id, "moves": _moves_payload(results, applied=True)}, indent=2))
            else:
                _display(results, applied=True)
                _summary(results, organizer)
                if args.stats:
                    _print_stats(files, results)
            _notify_moves(results, settings)
            moved = sum(1 for item in results if item.note != "duplicate skipped")
            if not args.json:
                print(f"\nApplied batch {batch_id}: {moved} file(s) moved. Run 'undo' to restore it.")
        else:
            if args.json:
                print(json.dumps({"moves": _moves_payload(moves, applied=False)}, indent=2))
            else:
                _display(moves)
                _summary(moves, organizer)
                if args.stats:
                    _print_stats(files, moves)
                print("\nPreview only: nothing was changed. Add --apply when the destinations look right.")
        if organizer.classifier.last_gemini_error:
            print(f"WARNING: Gemini was unavailable; local fallback used: {organizer.classifier.last_gemini_error}")
        return 0

    seen: dict[str, tuple[int, int]] = {}
    mode = "LIVE MOVE" if args.apply else "PREVIEW"
    folders = _configured_folders(settings, args.folder)
    recursive = args.recursive or (settings.recursive and not args.folder)
    only_ext = _extension_set(args.only_ext)
    print(f"Watching {len(folders)} inbox(es) in {mode} mode. Press Ctrl+C to stop.")
    try:
        while True:
            files = _scan_files(
                organizer,
                folders,
                recursive=recursive,
                minimum_age=settings.minimum_age_seconds,
                only_ext=only_ext,
                limit=None,
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
            fresh = _filter_moves(organizer.plan(fresh_paths), args.category)
            if fresh:
                if args.apply:
                    _, results = organizer.apply(fresh)
                    _notify_moves(results, settings)
                    if args.json:
                        print(json.dumps({"moves": _moves_payload(results, applied=True)}))
                    else:
                        _display(results, applied=True)
                else:
                    if args.json:
                        print(json.dumps({"moves": _moves_payload(fresh, applied=False)}))
                    else:
                        _display(fresh, applied=False)
            time.sleep(settings.scan_interval_seconds)
    except KeyboardInterrupt:
        print("\nWatcher stopped.")
        return 0
