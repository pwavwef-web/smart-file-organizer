from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Classification:
    category: str
    destination: str
    confidence: float
    reason: str
    source: str


@dataclass(frozen=True)
class PlannedMove:
    source: Path
    destination: Path
    classification: Classification
    note: str = ""
