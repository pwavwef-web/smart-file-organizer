from __future__ import annotations

import unittest
from pathlib import Path

from smart_sorter.models import Classification, PlannedMove
from smart_sorter.ui import (
    category_summary,
    format_confidence,
    move_action_label,
    parse_extensions,
    parse_optional_int,
)


class UIHelperTests(unittest.TestCase):
    def _move(self, category: str, *, note: str = "") -> PlannedMove:
        classification = Classification(
            category=category,
            destination="{Documents}",
            confidence=0.76,
            reason="test",
            source="unit",
        )
        return PlannedMove(Path("source.txt"), Path("dest.txt"), classification, note=note)

    def test_parse_extensions_normalizes_commas(self) -> None:
        self.assertEqual(parse_extensions("pdf, .JPG,txt"), {".pdf", ".jpg", ".txt"})
        self.assertEqual(parse_extensions("  "), set())

    def test_parse_optional_int(self) -> None:
        self.assertEqual(parse_optional_int("12", minimum=1), 12)
        self.assertIsNone(parse_optional_int("", minimum=1))
        with self.assertRaises(ValueError):
            parse_optional_int("0", minimum=1)

    def test_move_action_label_respects_duplicate_skips(self) -> None:
        duplicate = self._move("PDFs", note="duplicate skipped")
        normal = self._move("Documents")
        self.assertEqual(move_action_label(duplicate, applied=False), "Skip duplicate")
        self.assertEqual(move_action_label(duplicate, applied=True), "Skipped")
        self.assertEqual(move_action_label(normal, applied=False), "Move")
        self.assertEqual(move_action_label(normal, applied=True), "Moved")

    def test_confidence_and_category_summary(self) -> None:
        moves = [self._move("PDFs"), self._move("PDFs"), self._move("Images", note="duplicate skipped")]
        self.assertEqual(format_confidence(0.756), "76%")
        self.assertEqual(category_summary(moves), "PDFs: 2")


if __name__ == "__main__":
    unittest.main()
