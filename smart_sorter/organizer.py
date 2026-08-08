from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .classifier import FileClassifier
from .config import Settings
from .dates import capture_datetime
from .destinations import RenderContext, resolve_destination
from .extractors import extract_text
from .models import PlannedMove


_COMPONENT_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_below(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _safe_component(value: str) -> str:
    cleaned = _COMPONENT_RE.sub("", value).strip().strip(".")
    return cleaned or "Other"


def _available_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _digest(path: Path) -> str | None:
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def _same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    digest_a = _digest(a)
    return digest_a is not None and digest_a == _digest(b)


class Organizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.classifier = FileClassifier(settings)
        self.last_skipped_in_place = 0
        self.last_duplicates = 0

    def discover(self, folder: Path, *, recursive: bool, minimum_age: int = 0) -> list[Path]:
        if not folder.exists():
            return []
        iterator: Iterable[Path] = folder.rglob("*") if recursive else folder.iterdir()
        now = time.time()
        files: list[Path] = []
        for path in iterator:
            try:
                if not path.is_file() or path.is_symlink() or path.name.startswith("~$"):
                    continue
                if path.suffix.casefold() in self.settings.ignored_extensions:
                    continue
                if _is_below(path, self.settings.library_root):
                    continue
                if now - path.stat().st_mtime < minimum_age:
                    continue
                files.append(path.resolve())
            except OSError:
                continue
        return sorted(files, key=lambda item: str(item).casefold())

    def _resolve_target(self, source: Path, classification) -> Path:
        ctx = RenderContext(
            when=capture_datetime(source),
            ext=source.suffix,
            category=classification.category,
            source=classification.source,
        )
        try:
            return resolve_destination(
                classification.destination,
                source.name,
                root_map=self.settings.locations,
                default_root=self.settings.default_location,
                ctx=ctx,
            )
        except (KeyError, ValueError):
            # A malformed template must never crash sorting; land it safely.
            return self.settings.locations["Library"] / "Other" / source.name

    def plan(self, paths: Iterable[Path]) -> list[PlannedMove]:
        planned: list[PlannedMove] = []
        reserved: set[Path] = set()
        self.last_skipped_in_place = 0
        self.last_duplicates = 0
        for source in paths:
            source = source.resolve()
            classification = self.classifier.classify(source, extract_text(source))
            target = self._resolve_target(source, classification)

            # Already sorted: destination folder is where the file already lives.
            if target.parent == source.parent:
                self.last_skipped_in_place += 1
                continue

            note = ""
            final = target
            if target.exists():
                if _same_content(source, target):
                    self.last_duplicates += 1
                    policy = self.settings.on_duplicate
                    if policy == "skip":
                        planned.append(PlannedMove(source, target, classification, note="duplicate skipped"))
                        continue
                    if policy == "separate":
                        dup_dir = (
                            self.settings.locations["Library"]
                            / "Duplicates"
                            / _safe_component(classification.category)
                        )
                        final = _available_path(dup_dir / source.name)
                        note = "duplicate -> Duplicates"
                    else:  # version
                        final = _available_path(target)
                        note = "duplicate (kept copy)"
                else:
                    final = _available_path(target)  # name clash, different content
            while final in reserved:
                final = _available_path(final.with_name(f"{final.stem} (planned){final.suffix}"))
            reserved.add(final)
            planned.append(PlannedMove(source, final, classification, note=note))
        return planned

    def apply(self, moves: Iterable[PlannedMove]) -> tuple[str, list[PlannedMove]]:
        """Move the planned files and return what actually happened.

        The result mirrors the input so callers can report the truth:
        skip-policy duplicates are passed through unmoved (note
        "duplicate skipped"), and every moved item carries its *actual*
        destination, which may differ from the planned one when a name
        clash appears between planning and applying.
        """
        batch_id = str(uuid.uuid4())
        results: list[PlannedMove] = []
        for item in moves:
            if item.note == "duplicate skipped":
                results.append(item)
                continue
            if not item.source.exists():
                continue
            destination = _available_path(item.destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item.source), str(destination))
            record = {
                "event": "move",
                "operation_id": str(uuid.uuid4()),
                "batch_id": batch_id,
                "timestamp": _utc_now(),
                "source": str(item.source),
                "destination": str(destination),
                "category": item.classification.category,
                "reason": item.classification.reason,
                "classifier": item.classification.source,
                "note": item.note,
            }
            try:
                self._append_log(record)
            except OSError:
                # Never leave a moved file without an undo record: put it back
                # before surfacing the failure so state stays consistent.
                shutil.move(str(destination), str(item.source))
                raise
            applied = item if destination == item.destination else replace(item, destination=destination)
            results.append(applied)
        return batch_id, results

    def undo_last_batch(self) -> tuple[str | None, int, list[str]]:
        records = self._read_log()
        undone = {record.get("operation_id") for record in records if record.get("event") == "undo"}
        candidates = [
            record
            for record in records
            if record.get("event") == "move" and record.get("operation_id") not in undone
        ]
        if not candidates:
            return None, 0, []
        batch_id = str(candidates[-1]["batch_id"])
        batch = [record for record in candidates if record.get("batch_id") == batch_id]
        restored = 0
        warnings: list[str] = []
        for record in reversed(batch):
            source = Path(str(record["source"]))
            destination = Path(str(record["destination"]))
            if not destination.exists():
                warnings.append(f"Missing sorted file: {destination}")
                # Record it as handled so a file we can no longer restore does
                # not keep this batch "live" and block undo of older batches.
                self._append_log(
                    {
                        "event": "undo",
                        "operation_id": record["operation_id"],
                        "batch_id": batch_id,
                        "timestamp": _utc_now(),
                        "status": "missing",
                        "from": str(destination),
                    }
                )
                continue
            restore_to = _available_path(source)
            restore_to.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(restore_to))
            self._append_log(
                {
                    "event": "undo",
                    "operation_id": record["operation_id"],
                    "batch_id": batch_id,
                    "timestamp": _utc_now(),
                    "from": str(destination),
                    "restored_to": str(restore_to),
                }
            )
            restored += 1
        return batch_id, restored, warnings

    def _append_log(self, record: dict[str, object]) -> None:
        self.settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.settings.log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_log(self) -> list[dict[str, object]]:
        if not self.settings.log_file.exists():
            return []
        records: list[dict[str, object]] = []
        with self.settings.log_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records
