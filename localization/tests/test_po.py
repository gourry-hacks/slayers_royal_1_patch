from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from localization.po import PoEntry, PoError, read_po, write_po


class PoTests(unittest.TestCase):
    def test_round_trip_unicode_controls_and_comments(self) -> None:
        entries = [
            PoEntry(
                "dialogue/03B/E000/000",
                "日本語\n二行目",
                "Первая\nстрока\fЕщё",
                ("Context with a quote: \"test\"",),
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dialogue.po"
            write_po(path, entries, "ru")
            self.assertEqual(read_po(path), entries)

    def test_duplicate_context_is_rejected(self) -> None:
        entries = [PoEntry("same", "one"), PoEntry("same", "two")]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PoError):
                write_po(Path(directory) / "bad.po", entries, "ru")


if __name__ == "__main__":
    unittest.main()
