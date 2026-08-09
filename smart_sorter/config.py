from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .destinations import validate_template
from .known_folders import known_folder_map


@dataclass(frozen=True)
class Rule:
    name: str
    destination: str
    keywords: tuple[str, ...]
    extensions: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class GeminiSettings:
    enabled: bool
    model: str
    send_images: bool
    send_pdf_files: bool
    send_document_text: bool
    suggest_subfolder: bool
    max_file_bytes: int


@dataclass(frozen=True)
class NotificationSettings:
    enabled: bool
    max_files: int


@dataclass(frozen=True)
class Settings:
    inbox_folders: tuple[Path, ...]
    library_root: Path
    locations: dict[str, Path]
    default_location: str
    on_duplicate: str
    minimum_confidence: float
    review_location: str
    minimum_age_seconds: int
    scan_interval_seconds: int
    recursive: bool
    ignored_extensions: tuple[str, ...]
    ignore_patterns: tuple[str, ...]
    include_hidden: bool
    log_file: Path
    gemini: GeminiSettings
    notifications: NotificationSettings
    rules: tuple[Rule, ...]


_DUPLICATE_POLICIES = {"version", "skip", "separate"}


def _expanded_path(value: str, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    return (base / expanded).resolve() if not expanded.is_absolute() else expanded.resolve()


def _build_location_map(raw: dict[str, Any], base: Path, library_root: Path) -> dict[str, Path]:
    """Known Windows folders, overlaid with config `locations`, plus Library."""
    root_map: dict[str, Path] = dict(known_folder_map())
    root_map["Library"] = library_root
    for name, value in raw.get("locations", {}).items():
        root_map[str(name)] = _expanded_path(str(value), base)
    return root_map


def load_settings(path: Path) -> Settings:
    path = path.resolve()
    with path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = json.load(handle)

    base = path.parent
    library_root = _expanded_path(raw["library_root"], base)
    locations = _build_location_map(raw, base, library_root)
    default_location = str(raw.get("default_location", "Library"))
    if default_location not in locations:
        raise ValueError(f"default_location '{default_location}' is not a known root")

    on_duplicate = str(raw.get("on_duplicate", "separate")).casefold()
    if on_duplicate not in _DUPLICATE_POLICIES:
        raise ValueError(f"on_duplicate must be one of {sorted(_DUPLICATE_POLICIES)}")
    minimum_confidence = max(0.0, min(1.0, float(raw.get("minimum_confidence", 0.0))))
    review_location = validate_template(
        str(raw.get("review_location", "{Library}\\Review\\{category}")),
        root_tokens=set(locations),
        default_root=default_location,
    )

    root_tokens = set(locations)
    rules = tuple(
        Rule(
            name=item["name"],
            destination=validate_template(
                str(item["destination"]), root_tokens=root_tokens, default_root=default_location
            ),
            keywords=tuple(str(word).casefold() for word in item.get("keywords", [])),
            extensions=tuple(
                extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
                for extension in item.get("extensions", [])
            ),
            priority=int(item.get("priority", 50)),
        )
        for item in raw.get("rules", [])
    )

    gemini_raw = raw.get("gemini", {})
    notifications_raw = raw.get("notifications", {})
    return Settings(
        inbox_folders=tuple(_expanded_path(item, base) for item in raw["inbox_folders"]),
        library_root=library_root,
        locations=locations,
        default_location=default_location,
        on_duplicate=on_duplicate,
        minimum_confidence=minimum_confidence,
        review_location=review_location,
        minimum_age_seconds=max(0, int(raw.get("minimum_age_seconds", 10))),
        scan_interval_seconds=max(2, int(raw.get("scan_interval_seconds", 15))),
        recursive=bool(raw.get("recursive", False)),
        ignored_extensions=tuple(
            extension.casefold() if extension.startswith(".") else f".{extension.casefold()}"
            for extension in raw.get(
                "ignored_extensions",
                [".crdownload", ".download", ".opdownload", ".part", ".partial", ".tmp"],
            )
        ),
        ignore_patterns=tuple(str(pattern).casefold() for pattern in raw.get("ignore_patterns", [])),
        include_hidden=bool(raw.get("include_hidden", False)),
        log_file=_expanded_path(raw.get("log_file", ".smart-sorter/history.jsonl"), base),
        gemini=GeminiSettings(
            enabled=bool(gemini_raw.get("enabled", True)),
            model=str(gemini_raw.get("model", "gemini-3.6-flash")),
            send_images=bool(gemini_raw.get("send_images", True)),
            send_pdf_files=bool(gemini_raw.get("send_pdf_files", True)),
            send_document_text=bool(gemini_raw.get("send_document_text", True)),
            suggest_subfolder=bool(gemini_raw.get("suggest_subfolder", False)),
            max_file_bytes=max(1, int(gemini_raw.get("max_file_bytes", 12_000_000))),
        ),
        notifications=NotificationSettings(
            enabled=bool(notifications_raw.get("enabled", True)),
            max_files=max(1, int(notifications_raw.get("max_files", 5))),
        ),
        rules=tuple(sorted(rules, key=lambda rule: rule.priority, reverse=True)),
    )
