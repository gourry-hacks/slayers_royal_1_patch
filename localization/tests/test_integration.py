from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path

from localization.disc import SOURCE_PROG_SHA256, read_prog
from localization.glyphs import BASE_CHAR_TO_GLYPH
from localization.script import (
    catalog_entries,
    parse_all,
    rebuild_dialogue,
    source_inventory,
    validate_catalog,
)


SOURCE_BIN = os.environ.get("SLAYERS_ROYAL_BIN")


@unittest.skipUnless(SOURCE_BIN, "set SLAYERS_ROYAL_BIN for source-disc integration tests")
class SourceDiscIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prog = read_prog(Path(str(SOURCE_BIN)), SOURCE_PROG_SHA256)

    def test_complete_source_inventory(self) -> None:
        inventory = source_inventory(self.prog)
        self.assertEqual(inventory["scene_count"], 29)
        self.assertEqual(inventory["special_bank_count"], 1)
        self.assertEqual(inventory["record_count"], 2784)
        self.assertEqual(inventory["segment_count"], 4552)
        self.assertEqual(inventory["translatable_segment_count"], 4514)

    def test_every_pointer_rebuilds_with_short_cyrillic_text(self) -> None:
        catalog = [
            replace(entry, translation="Тест") for entry in catalog_entries(self.prog)
        ]
        translations = validate_catalog(self.prog, catalog)
        charmap = {
            **BASE_CHAR_TO_GLYPH,
            "Т": 0x001,
            "е": 0x002,
            "с": 0x003,
            "т": 0x004,
        }
        rebuilt = rebuild_dialogue(
            self.prog,
            translations,
            charmap,
            {"Те": 0x005, "ес": 0x006, "ст": 0x007},
        )
        self.assertEqual(len(rebuilt), len(self.prog))
        self.assertEqual(len(parse_all(rebuilt)), 30)


if __name__ == "__main__":
    unittest.main()
