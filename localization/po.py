"""Small, dependency-free PO reader/writer for the toolkit's catalog subset."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


class PoError(ValueError):
    pass


@dataclass(frozen=True)
class PoEntry:
    context: str
    source: str
    translation: str = ""
    comments: tuple[str, ...] = field(default_factory=tuple)


def _quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
    )
    return f'"{escaped}"'


def write_po(path: Path, entries: list[PoEntry], language: str) -> None:
    if len({entry.context for entry in entries}) != len(entries):
        raise PoError("catalog contains duplicate contexts")
    lines = [
        '# Slayers Royal manual-translation catalog.',
        'msgid ""',
        'msgstr ""',
        '"Project-Id-Version: slayers-royal-localization-1\\n"',
        f'"Language: {language}\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
    ]
    for entry in entries:
        for comment in entry.comments:
            lines.append(f"#. {comment}")
        lines.extend(
            [
                f"msgctxt {_quote(entry.context)}",
                f"msgid {_quote(entry.source)}",
                f"msgstr {_quote(entry.translation)}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _literal(line: str, line_number: int) -> str:
    try:
        value = ast.literal_eval(line)
    except (SyntaxError, ValueError) as exc:
        raise PoError(f"invalid PO string on line {line_number}") from exc
    if not isinstance(value, str):
        raise PoError(f"PO value on line {line_number} is not a string")
    return value


def read_po(path: Path) -> list[PoEntry]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    entries: list[PoEntry] = []
    comments: list[str] = []
    values: dict[str, str] = {}
    active: str | None = None

    def finish() -> None:
        nonlocal comments, values, active
        if not values:
            comments = []
            active = None
            return
        context = values.get("msgctxt")
        source = values.get("msgid")
        translation = values.get("msgstr")
        if source == "" and context is None:
            comments = []
            values = {}
            active = None
            return
        if context is None or source is None or translation is None:
            raise PoError("every catalog entry needs msgctxt, msgid, and msgstr")
        entries.append(PoEntry(context, source, translation, tuple(comments)))
        comments = []
        values = {}
        active = None

    for number, raw in enumerate([*lines, ""], 1):
        line = raw.strip()
        if not line:
            finish()
            continue
        if line.startswith("#."):
            comments.append(line[2:].strip())
            continue
        if line.startswith("#"):
            continue
        matched = False
        for keyword in ("msgctxt", "msgid", "msgstr"):
            prefix = keyword + " "
            if line.startswith(prefix):
                active = keyword
                if keyword in values:
                    raise PoError(f"duplicate {keyword} on line {number}")
                values[keyword] = _literal(line[len(prefix) :], number)
                matched = True
                break
        if matched:
            continue
        if line.startswith('"') and active is not None:
            values[active] += _literal(line, number)
            continue
        raise PoError(f"unsupported PO syntax on line {number}: {raw!r}")

    contexts = [entry.context for entry in entries]
    if len(contexts) != len(set(contexts)):
        duplicate = next(item for item in contexts if contexts.count(item) > 1)
        raise PoError(f"duplicate msgctxt {duplicate!r}")
    return entries
