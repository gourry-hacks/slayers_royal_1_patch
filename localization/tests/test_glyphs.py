from __future__ import annotations

import unittest

from localization.glyphs import choose_compact_tokens


class GlyphTests(unittest.TestCase):
    def test_compact_tokens_are_frequency_ordered_and_deterministic(self) -> None:
        self.assertEqual(
            choose_compact_tokens(["мама мыла раму", "мама"], 3),
            ["ма", "ам", "а "],
        )

    def test_compact_tokens_require_repetition(self) -> None:
        self.assertEqual(choose_compact_tokens(["абвг"], 8), [])


if __name__ == "__main__":
    unittest.main()
