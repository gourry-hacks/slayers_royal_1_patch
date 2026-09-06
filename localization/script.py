"""Extract and rebuild Royal 1's pointer-based story-script resources."""

from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .po import PoEntry
from .sr_charmap import CHARMAP


SECTOR_SIZE = 0x800
RUNTIME_BASE = 0x00230000
SCENE_ENTRIES = tuple(range(0x03B, 0x058))
SPECIAL_ENTRIES = (0x139,)
DELIMITERS = {0x00FD, 0x00FF}
LINE_BREAK = 0x00FE
DONOR_ENTRY = 0x011

# These are the allocations used by the proven English build. Keeping the
# same table layout lets a localized script replace the story entries in the
# canonical English base without disturbing the other translated systems.
DEFAULT_EXPANSION_SECTORS = {
    0x03B: 1,
    0x03C: 4,
    0x03D: 3,
    0x040: 3,
    0x041: 2,
    0x042: 2,
    0x043: 1,
    0x044: 1,
    0x045: 2,
    0x046: 1,
    0x048: 1,
    0x04F: 2,
    0x050: 1,
    0x051: 1,
    0x052: 1,
    0x053: 2,
    0x054: 1,
    0x056: 1,
}


class ScriptError(ValueError):
    pass


@dataclass(frozen=True)
class ArchiveEntry:
    index: int
    start_sector: int
    sector_count: int

    @property
    def offset(self) -> int:
        return self.start_sector * SECTOR_SIZE

    @property
    def size(self) -> int:
        return self.sector_count * SECTOR_SIZE

    def extract(self, archive: bytes) -> bytes:
        return archive[self.offset : self.offset + self.size]


@dataclass(frozen=True)
class Segment:
    context: str
    start: int
    end: int
    words: tuple[int, ...]
    delimiter: int
    source: str


@dataclass(frozen=True)
class Record:
    key: str
    start: int
    allocation_end: int
    logical_end: int
    command: int | None
    segments: tuple[Segment, ...]


@dataclass(frozen=True)
class Scene:
    entry_index: int
    family: str
    body_start: int
    body_end: int
    table_offset: int
    table_end: int
    pointers: tuple[int, ...]
    records: tuple[Record, ...]


def read_entries(archive: bytes) -> list[ArchiveEntry]:
    if len(archive) % SECTOR_SIZE:
        raise ScriptError("PROG.UNT is not sector aligned")
    entries: list[ArchiveEntry] = []
    expected = 1
    for offset in range(0, SECTOR_SIZE, 4):
        start = int.from_bytes(archive[offset : offset + 2], "little")
        count = int.from_bytes(archive[offset + 2 : offset + 4], "little")
        if start != expected or count == 0:
            break
        entries.append(ArchiveEntry(len(entries), start, count))
        expected += count
    if not entries or expected * SECTOR_SIZE != len(archive):
        raise ScriptError("PROG.UNT has an invalid contiguous archive table")
    return entries


def words_be(data: bytes) -> list[int]:
    if len(data) % 2:
        raise ScriptError("script byte stream is not word aligned")
    return [
        int.from_bytes(data[offset : offset + 2], "big")
        for offset in range(0, len(data), 2)
    ]


def _decode(words: list[int]) -> str:
    text: list[str] = []
    for word in words:
        if word == 0:
            continue
        if word == LINE_BREAK:
            text.append("\n")
        elif word <= 0x03FF:
            value = CHARMAP.get(word)
            text.append(value if value is not None else f"<G:{word:04X}>")
        else:
            # Presentation and speaker controls are described in extracted
            # comments and are preserved structurally by the compiler.
            continue
    return "".join(text).strip("\n")


def _split_segments(
    entry_index: int,
    record_key: str,
    words: list[int],
    byte_start: int,
) -> tuple[Segment, ...]:
    segments: list[Segment] = []
    start = 0
    for index, word in enumerate(words):
        if word not in DELIMITERS:
            continue
        segment_words = words[start : index + 1]
        context = (
            f"dialogue/{entry_index:03X}/{record_key}/{len(segments):03d}"
        )
        segments.append(
            Segment(
                context=context,
                start=byte_start + start * 2,
                end=byte_start + (index + 1) * 2,
                words=tuple(segment_words),
                delimiter=word,
                source=_decode(segment_words[:-1]),
            )
        )
        start = index + 1
    if any(words[start:]):
        raise ScriptError(
            f"entry {entry_index:03X} record {record_key} has an incomplete tail"
        )
    return tuple(segments)


def _scene_header(data: bytes, entry_index: int) -> tuple[int, int, tuple[int, ...]]:
    table_offset = int.from_bytes(data[0x04:0x08], "big") - RUNTIME_BASE
    table_end = int.from_bytes(data[0x1C:0x20], "big") - RUNTIME_BASE
    if not (0x20 <= table_offset < table_end <= len(data)):
        raise ScriptError(f"entry {entry_index:03X} has an invalid pointer-table header")
    if (table_end - table_offset) % 4:
        raise ScriptError(f"entry {entry_index:03X} pointer table is not aligned")
    pointers = tuple(
        int.from_bytes(data[offset : offset + 4], "big") - RUNTIME_BASE
        for offset in range(table_offset, table_end, 4)
    )
    if not pointers or any(
        target < 0 or target >= table_offset or target % 2 for target in pointers
    ):
        raise ScriptError(f"entry {entry_index:03X} has an invalid pointer target")
    return table_offset, table_end, pointers


def parse_scene(entry: ArchiveEntry, archive: bytes) -> Scene:
    data = entry.extract(archive)
    table_offset, table_end, pointers = _scene_header(data, entry.index)
    body_start = min(pointers)
    tag_positions = [
        offset
        for offset in range(body_start, table_offset, 2)
        if 0xE000 <= int.from_bytes(data[offset : offset + 2], "big") <= 0xEFFF
    ]
    records: list[Record] = []
    if tag_positions:
        if tag_positions[0] != body_start:
            raise ScriptError(f"entry {entry.index:03X} has data before its first tag")
        for ordinal, start in enumerate(tag_positions):
            allocation_end = (
                tag_positions[ordinal + 1]
                if ordinal + 1 < len(tag_positions)
                else table_offset
            )
            logical_end = allocation_end
            while logical_end >= start + 4 and data[logical_end - 2 : logical_end] == b"\0\0":
                logical_end -= 2
            command = int.from_bytes(data[start : start + 2], "big")
            key = f"{command:04X}"
            payload = words_be(data[start + 2 : logical_end])
            segments = _split_segments(entry.index, key, payload, start + 2)
            if not segments:
                raise ScriptError(f"entry {entry.index:03X} {key} has no delimiter")
            records.append(
                Record(key, start, allocation_end, logical_end, command, segments)
            )
        family = "tagged"
    else:
        logical_end = table_offset
        while logical_end >= body_start + 2 and data[logical_end - 2 : logical_end] == b"\0\0":
            logical_end -= 2
        stream = words_be(data[body_start:logical_end])
        split = _split_segments(entry.index, f"U{entry.index:03X}", stream, body_start)
        for ordinal, segment in enumerate(split):
            key = f"U{entry.index:03X}-S{ordinal:03d}"
            context = f"dialogue/{entry.index:03X}/{key}/000"
            rewritten = Segment(
                context,
                segment.start,
                segment.end,
                segment.words,
                segment.delimiter,
                segment.source,
            )
            records.append(
                Record(key, segment.start, segment.end, segment.end, None, (rewritten,))
            )
        family = "untagged_linear"
    return Scene(
        entry.index,
        family,
        body_start,
        table_offset,
        table_offset,
        table_end,
        pointers,
        tuple(records),
    )


def parse_special(entry: ArchiveEntry, archive: bytes) -> Scene:
    data = entry.extract(archive)
    table_offset = int.from_bytes(data[0:4], "big") - RUNTIME_BASE
    if not (4 < table_offset < len(data) and table_offset % 4 == 0):
        raise ScriptError(f"entry {entry.index:03X} has an invalid special table")
    pointers: list[int] = []
    table_end = table_offset
    while table_end + 4 <= len(data):
        target = int.from_bytes(data[table_end : table_end + 4], "big") - RUNTIME_BASE
        if target < 4 or target >= table_offset or target % 2:
            break
        pointers.append(target)
        table_end += 4
    if not pointers:
        raise ScriptError(f"entry {entry.index:03X} has no special pointers")
    body_start = min(pointers)
    logical_end = table_offset
    while logical_end >= body_start + 2 and data[logical_end - 2 : logical_end] == b"\0\0":
        logical_end -= 2
    split = _split_segments(
        entry.index,
        f"U{entry.index:03X}",
        words_be(data[body_start:logical_end]),
        body_start,
    )
    records: list[Record] = []
    for ordinal, segment in enumerate(split):
        key = f"U{entry.index:03X}-S{ordinal:03d}"
        context = f"dialogue/{entry.index:03X}/{key}/000"
        rewritten = Segment(
            context,
            segment.start,
            segment.end,
            segment.words,
            segment.delimiter,
            segment.source,
        )
        records.append(Record(key, segment.start, segment.end, segment.end, None, (rewritten,)))
    return Scene(
        entry.index,
        "headerless_pointer_bank",
        body_start,
        logical_end,
        table_offset,
        table_end,
        tuple(pointers),
        tuple(records),
    )


def parse_all(prog: bytes) -> list[Scene]:
    entries = read_entries(prog)
    needed = max(*SCENE_ENTRIES, *SPECIAL_ENTRIES)
    if len(entries) <= needed:
        raise ScriptError("PROG.UNT does not contain the complete story inventory")
    scenes = [parse_scene(entries[index], prog) for index in SCENE_ENTRIES]
    scenes.extend(parse_special(entries[index], prog) for index in SPECIAL_ENTRIES)
    return scenes


def _speaker_hint(words: tuple[int, ...]) -> str | None:
    for control in words:
        if not 0x9000 <= control <= 0xDFFF:
            continue
        family = control & 0x0FFF
        if 0x100 <= family < 0x120:
            return "Lina"
        if 0x120 <= family < 0x140:
            return "Naga"
        if 0x140 <= family < 0x150:
            return "Gourry"
        if 0x160 <= family < 0x170:
            return "Zelgadis"
        if 0x180 <= family < 0x1A0:
            return "Amelia"
        if 0x1A0 <= family < 0x1C0:
            return "Sylphiel"
        if 0x1C0 <= family < 0x1E0:
            return "Lark"
        if 0x240 <= family < 0x260:
            return "Emilia"
    return None


def catalog_entries(prog: bytes) -> list[PoEntry]:
    entries: list[PoEntry] = []
    for scene in parse_all(prog):
        for record in scene.records:
            for segment in record.segments:
                if not segment.source:
                    continue
                controls = [f"{word:04X}" for word in segment.words if word > 0x03FF]
                comments = [
                    f"PROG entry 0x{scene.entry_index:03X}; {scene.family}; record {record.key}",
                    "Target layout: at most 3 lines per page and 15 characters per line.",
                ]
                if segment.delimiter == 0x00FD:
                    comments.append("Use \\f between additional pages when needed.")
                else:
                    comments.append("This FF segment cannot add another page; shorten to fit.")
                if speaker := _speaker_hint(segment.words):
                    comments.append(f"Probable speaker: {speaker}")
                if controls:
                    comments.append("Preserved engine controls: " + " ".join(controls))
                entries.append(
                    PoEntry(segment.context, segment.source, "", tuple(comments))
                )
    return entries


def source_inventory(prog: bytes) -> dict[str, object]:
    scenes = parse_all(prog)
    records = sum(len(scene.records) for scene in scenes)
    segments = sum(len(record.segments) for scene in scenes for record in scene.records)
    translatable_segments = sum(
        bool(segment.source)
        for scene in scenes
        for record in scene.records
        for segment in record.segments
    )
    return {
        "schema": 1,
        "source_prog_sha256": hashlib.sha256(prog).hexdigest(),
        "scene_count": len([scene for scene in scenes if scene.entry_index in SCENE_ENTRIES]),
        "special_bank_count": len([scene for scene in scenes if scene.entry_index in SPECIAL_ENTRIES]),
        "record_count": records,
        "segment_count": segments,
        "translatable_segment_count": translatable_segments,
        "entries": [
            {
                "entry": f"0x{scene.entry_index:03X}",
                "family": scene.family,
                "record_count": len(scene.records),
                "segment_count": sum(len(record.segments) for record in scene.records),
                "pointer_count": len(scene.pointers),
            }
            for scene in scenes
        ],
    }


def parse_target(value: str, delimiter: int, context: str) -> list[list[str]]:
    normalized = unicodedata.normalize("NFC", value.replace("\r", ""))
    if normalized != value.replace("\r", ""):
        raise ScriptError(f"{context}: translation must use NFC-normalized Unicode")
    pages = normalized.split("\f")
    if len(pages) > 1 and delimiter != 0x00FD:
        raise ScriptError(f"{context}: an FF-terminated segment cannot add pages")
    result: list[list[str]] = []
    for page_index, page in enumerate(pages):
        lines = page.split("\n")
        if not 1 <= len(lines) <= 3 or any(not line for line in lines):
            raise ScriptError(
                f"{context}: page {page_index + 1} needs 1-3 nonempty lines"
            )
        for line_index, line in enumerate(lines):
            if len(line) > 15:
                raise ScriptError(
                    f"{context}: page {page_index + 1}, line {line_index + 1} "
                    f"has {len(line)} characters; maximum is 15: {line!r}"
                )
        result.append(lines)
    return result


def validate_catalog(
    prog: bytes,
    catalog: list[PoEntry],
    allow_incomplete: bool = False,
) -> dict[str, PoEntry]:
    expected = {entry.context: entry for entry in catalog_entries(prog)}
    actual = {entry.context: entry for entry in catalog}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        raise ScriptError(
            f"catalog contexts differ from source: {len(missing)} missing, {len(extra)} extra"
        )
    blank = 0
    segment_by_context = {
        segment.context: segment
        for scene in parse_all(prog)
        for record in scene.records
        for segment in record.segments
    }
    for context, reference in expected.items():
        entry = actual[context]
        if entry.source != reference.source:
            raise ScriptError(f"{context}: msgid differs from the verified source")
        if not entry.translation:
            blank += 1
            continue
        segment = segment_by_context[context]
        parse_target(entry.translation, segment.delimiter, context)
    if blank and not allow_incomplete:
        raise ScriptError(
            f"catalog has {blank} untranslated segments; finish them or use --allow-incomplete for a diagnostic build"
        )
    return actual


def expand_entry(archive: bytes, target_index: int, sectors: int) -> bytes:
    if sectors <= 0:
        return archive
    entries = read_entries(archive)
    if DONOR_ENTRY >= target_index:
        raise ScriptError("dialogue donor must precede the expanded scene")
    amount = sectors * SECTOR_SIZE
    payloads = [entry.extract(archive) for entry in entries]
    donor = payloads[DONOR_ENTRY]
    if len(donor) - amount < SECTOR_SIZE or any(donor[-amount:]):
        raise ScriptError(
            f"PROG entry {DONOR_ENTRY:03X} lacks {sectors} verified zero sectors"
        )
    payloads[DONOR_ENTRY] = donor[:-amount]
    payloads[target_index] += bytes(amount)
    result = bytearray(archive[:SECTOR_SIZE])
    start_sector = 1
    for index, payload in enumerate(payloads):
        if len(payload) % SECTOR_SIZE:
            raise ScriptError(f"PROG entry {index:03X} is not sector aligned")
        count = len(payload) // SECTOR_SIZE
        offset = index * 4
        result[offset : offset + 2] = start_sector.to_bytes(2, "little")
        result[offset + 2 : offset + 4] = count.to_bytes(2, "little")
        result.extend(payload)
        start_sector += count
    if len(result) != len(archive):
        raise AssertionError("entry expansion changed PROG.UNT size")
    return bytes(result)


def expand_for_dialogue(prog: bytes) -> bytes:
    result = prog
    for entry_index in sorted(DEFAULT_EXPANSION_SECTORS):
        result = expand_entry(
            result, entry_index, DEFAULT_EXPANSION_SECTORS[entry_index]
        )
    return result


def _tokenize(text: str, compact: Mapping[str, int]) -> list[str]:
    best: list[list[str] | None] = [None] * (len(text) + 1)
    best[0] = []
    for offset in range(len(text)):
        prefix = best[offset]
        if prefix is None:
            continue
        candidates = [text[offset]]
        pair = text[offset : offset + 2]
        if len(pair) == 2 and pair in compact:
            candidates.append(pair)
        for token in candidates:
            end = offset + len(token)
            candidate = [*prefix, token]
            current = best[end]
            if current is None or len(candidate) < len(current) or (
                len(candidate) == len(current) and tuple(candidate) < tuple(current)
            ):
                best[end] = candidate
    if best[-1] is None:
        raise ScriptError(f"cannot tokenize target text {text!r}")
    return best[-1]


def _control_events(words: tuple[int, ...]) -> list[tuple[int, int, int, int]]:
    events: list[tuple[int, int, int, int]] = []
    line = 0
    column = 0
    ordinal = 0
    for word in words[:-1]:
        if word == 0:
            continue
        if word == LINE_BREAK:
            line += 1
            column = 0
        elif word <= 0x03FF:
            column += 1
        else:
            events.append((line, column, ordinal, word))
            ordinal += 1
    return events


def _encode_line(
    text: str,
    charmap: Mapping[str, int],
    compact: Mapping[str, int],
    events: list[tuple[int, int]],
    context: str,
) -> list[int]:
    by_offset: dict[int, list[int]] = {}
    for offset, control in events:
        by_offset.setdefault(min(offset, len(text)), []).append(control)
    output: list[int] = []
    cursor = 0
    for boundary in sorted({*by_offset, len(text)}):
        if boundary < cursor:
            continue
        for token in _tokenize(text[cursor:boundary], compact):
            try:
                output.append(charmap[token] if len(token) == 1 else compact[token])
            except KeyError as exc:
                raise ScriptError(
                    f"{context}: no glyph mapping for {exc.args[0]!r}"
                ) from exc
        output.extend(by_offset.get(boundary, []))
        cursor = boundary
    return output


def _encode_segment(
    segment: Segment,
    translation: str,
    charmap: Mapping[str, int],
    compact: Mapping[str, int],
) -> bytes:
    pages = parse_target(translation, segment.delimiter, segment.context)
    source_events = _control_events(segment.words)
    source_line_lengths: dict[int, int] = Counter()
    line = 0
    for word in segment.words[:-1]:
        if word == LINE_BREAK:
            line += 1
        elif 0 < word <= 0x03FF:
            source_line_lengths[line] += 1
    target_first = pages[0]
    assigned: dict[int, list[tuple[int, int, int]]] = {}
    for source_line, source_column, ordinal, control in source_events:
        target_line = min(source_line, len(target_first) - 1)
        source_length = max(source_line_lengths.get(source_line, 0), 1)
        target_length = len(target_first[target_line])
        target_column = min(
            target_length,
            round(source_column * target_length / source_length),
        )
        assigned.setdefault(target_line, []).append(
            (target_column, ordinal, control)
        )

    output: list[int] = []
    for page_index, page in enumerate(pages):
        if page_index:
            output.append(segment.delimiter)
        for line_index, text in enumerate(page):
            if line_index:
                output.append(LINE_BREAK)
            events = []
            if page_index == 0:
                events = [
                    (column, control)
                    for column, _ordinal, control in sorted(
                        assigned.get(line_index, []), key=lambda item: (item[0], item[1])
                    )
                ]
            output.extend(_encode_line(text, charmap, compact, events, segment.context))
    output.append(segment.delimiter)
    original_controls = [word for word in segment.words if word > 0x03FF]
    new_controls = [word for word in output if word > 0x03FF]
    if new_controls != original_controls:
        raise ScriptError(f"{segment.context}: engine-control order changed")
    return b"".join(word.to_bytes(2, "big") for word in output)


def _translated_segment(
    segment: Segment,
    translations: Mapping[str, PoEntry],
    charmap: Mapping[str, int],
    compact: Mapping[str, int],
) -> bytes:
    entry = translations.get(segment.context)
    value = entry.translation if entry is not None else ""
    if not value:
        return b"".join(word.to_bytes(2, "big") for word in segment.words)
    return _encode_segment(segment, value, charmap, compact)


def _footer_end(data: bytes, table_end: int) -> int:
    nonzero = [offset for offset in range(table_end, len(data)) if data[offset]]
    return ((max(nonzero) + 4) // 4 * 4) if nonzero else table_end


def _patch_regular_scene(
    scene_data: bytes,
    scene: Scene,
    translations: Mapping[str, PoEntry],
    charmap: Mapping[str, int],
    compact: Mapping[str, int],
) -> bytes:
    rebuilt = bytearray()
    relocations: list[tuple[int, int, int, dict[int, int]]] = []
    for record in scene.records:
        new_start = scene.body_start + len(rebuilt)
        anchors = {0: 0}
        record_bytes = bytearray()
        if record.command is not None:
            record_bytes.extend(record.command.to_bytes(2, "big"))
        for segment in record.segments:
            anchors[segment.start - record.start] = len(record_bytes)
            record_bytes.extend(
                _translated_segment(segment, translations, charmap, compact)
            )
        rebuilt.extend(record_bytes)
        relocations.append(
            (record.start, record.logical_end, new_start, anchors)
        )
    rebuilt.extend(bytes((-(scene.body_start + len(rebuilt))) % 4))
    new_table = scene.body_start + len(rebuilt)
    footer_end = _footer_end(scene_data, scene.table_end)
    table_footer = bytearray(scene_data[scene.table_offset:footer_end])

    def relocate(target: int) -> int:
        for old_start, old_end, new_start, anchors in relocations:
            if old_start <= target < old_end:
                relative = target - old_start
                if relative not in anchors:
                    raise ScriptError(
                        f"entry {scene.entry_index:03X}: pointer 0x{target:04X} "
                        "does not target a record or segment boundary"
                    )
                return new_start + anchors[relative]
        raise ScriptError(
            f"entry {scene.entry_index:03X}: pointer 0x{target:04X} is unmapped"
        )

    for index, target in enumerate(scene.pointers):
        table_footer[index * 4 : index * 4 + 4] = (
            RUNTIME_BASE + relocate(target)
        ).to_bytes(4, "big")
    new_table_end = new_table + (scene.table_end - scene.table_offset)
    new_footer_end = new_table + len(table_footer)
    if new_footer_end > len(scene_data):
        raise ScriptError(
            f"entry {scene.entry_index:03X} exceeds its expanded allocation by "
            f"0x{new_footer_end - len(scene_data):X} bytes"
        )
    result = bytearray(scene_data)
    result[scene.body_start:] = bytes(len(result) - scene.body_start)
    result[scene.body_start:new_table] = rebuilt
    result[new_table:new_footer_end] = table_footer
    result[0x04:0x08] = (RUNTIME_BASE + new_table).to_bytes(4, "big")
    result[0x1C:0x20] = (RUNTIME_BASE + new_table_end).to_bytes(4, "big")
    return bytes(result)


def _patch_special_scene(
    scene_data: bytes,
    scene: Scene,
    translations: Mapping[str, PoEntry],
    charmap: Mapping[str, int],
    compact: Mapping[str, int],
) -> bytes:
    rebuilt = bytearray()
    anchors: dict[int, int] = {}
    for record in scene.records:
        segment = record.segments[0]
        anchors[record.start - scene.body_start] = len(rebuilt)
        rebuilt.extend(_translated_segment(segment, translations, charmap, compact))
    rebuilt.extend(bytes((-(scene.body_start + len(rebuilt))) % 4))
    new_table = scene.body_start + len(rebuilt)
    table = bytearray()
    for target in scene.pointers:
        relative = target - scene.body_start
        if relative not in anchors:
            raise ScriptError(
                f"entry {scene.entry_index:03X}: special pointer 0x{target:04X} is unmapped"
            )
        table.extend((RUNTIME_BASE + scene.body_start + anchors[relative]).to_bytes(4, "big"))
    end = new_table + len(table)
    if end > len(scene_data):
        raise ScriptError(
            f"entry {scene.entry_index:03X} exceeds its allocation by 0x{end - len(scene_data):X} bytes"
        )
    result = bytearray(len(scene_data))
    result[:4] = (RUNTIME_BASE + new_table).to_bytes(4, "big")
    result[scene.body_start:new_table] = rebuilt
    result[new_table:end] = table
    return bytes(result)


def rebuild_dialogue(
    source_prog: bytes,
    translations: Mapping[str, PoEntry],
    charmap: Mapping[str, int],
    compact: Mapping[str, int],
) -> bytes:
    result = expand_for_dialogue(source_prog)
    for entry_index in (*SCENE_ENTRIES, *SPECIAL_ENTRIES):
        entries = read_entries(result)
        entry = entries[entry_index]
        scene = (
            parse_scene(entry, result)
            if entry_index in SCENE_ENTRIES
            else parse_special(entry, result)
        )
        source = entry.extract(result)
        replacement = (
            _patch_regular_scene(
                source,
                scene,
                translations,
                charmap,
                compact if entry_index == 0x03E else {},
            )
            if entry_index in SCENE_ENTRIES
            else _patch_special_scene(source, scene, translations, charmap, {})
        )
        patched = bytearray(result)
        patched[entry.offset : entry.offset + entry.size] = replacement
        result = bytes(patched)
    return result


def copy_dialogue_entries(localized: bytes, english: bytes) -> bytes:
    localized_entries = read_entries(localized)
    english_entries = read_entries(english)
    if [
        (entry.start_sector, entry.sector_count) for entry in localized_entries
    ] != [
        (entry.start_sector, entry.sector_count) for entry in english_entries
    ]:
        raise ScriptError(
            "localized and English PROG archive layouts differ; expansion inventory changed"
        )
    result = bytearray(english)
    for index in (*SCENE_ENTRIES, *SPECIAL_ENTRIES):
        source = localized_entries[index]
        target = english_entries[index]
        result[target.offset : target.offset + target.size] = source.extract(localized)
    return bytes(result)
