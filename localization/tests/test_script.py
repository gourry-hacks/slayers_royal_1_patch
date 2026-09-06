from __future__ import annotations

import unittest

from localization.script import ScriptError, parse_target


class ScriptLayoutTests(unittest.TestCase):
    def test_valid_manual_layout(self) -> None:
        self.assertEqual(
            parse_target("Очень даже\nнеплохо!", 0x00FF, "sample"),
            [["Очень даже", "неплохо!"]],
        )

    def test_fd_allows_continuation_page(self) -> None:
        self.assertEqual(
            parse_target("Первая\fВторая", 0x00FD, "sample"),
            [["Первая"], ["Вторая"]],
        )

    def test_ff_rejects_continuation_page(self) -> None:
        with self.assertRaisesRegex(ScriptError, "cannot add pages"):
            parse_target("Первая\fВторая", 0x00FF, "sample")

    def test_line_limit_counts_unicode_characters(self) -> None:
        with self.assertRaisesRegex(ScriptError, "16 characters"):
            parse_target("абвгдеёжзийклмно", 0x00FD, "sample")


if __name__ == "__main__":
    unittest.main()
