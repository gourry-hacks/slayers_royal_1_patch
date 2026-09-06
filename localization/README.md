# Royal 1 Localization Toolkit

This toolkit turns the verified Japanese `SLPS-01363` disc into a manual
translation workspace and builds that workspace back into a BIN/CUE image.
The Japanese script is extracted from the contributor's own disc; it is not
stored in this repository.

## Current scope

Version 0.1 covers the complete pointer-based story-script inventory:

- 29 main scene resources (`PROG.UNT` entries `0x03B..0x057`)
- the separate `0x139` pointer bank
- 2,784 records, 4,552 segment slots, and 4,514 entries containing source text
- automatic pointer relocation, source-control preservation, and the same
  verified scene expansions used by the English release
- locale-specific 16x16 runtime glyphs and optional two-character compression
  cells

The localized story is installed over the canonical English release. Systems
not yet represented in the PO catalog—object inspection, battle text, menus,
bitmap UI, minigames, credits, and FMV captions—therefore remain English and
functional. Those domains are the next adapters to add; the catalog IDs and
glyph allocator are deliberately reusable for them.

An incomplete catalog can be built only with `--allow-incomplete`. That mode
is for pointer/font diagnostics, not play: newly allocated locale cells can
replace Japanese glyphs still referenced by untranslated story records.

## Requirements

- Python 3.10 or newer
- Pillow 10 or newer (`python3 -m pip install -r localization/requirements.txt`)
- the complete repository, including the English XOR patch parts
- the exact original raw `MODE2/2352` BIN listed in the main README
- about 1.7 GiB of temporary/output space

DejaVu Sans Condensed Bold or Liberation Sans Bold must be installed. DejaVu
Sans includes the Russian alphabet and is used by default.

## 1. Export a Russian workspace

```bash
python3 localize.py export \
  --bin "/path/to/sr.bin" \
  --locale ru \
  --output localization-work/ru
```

This verifies the complete source BIN and writes:

```text
localization-work/ru/
  dialogue.po
  language.json
  source_inventory.json
```

`msgctxt` is the stable build ID. Do not edit it. `msgid` is the text decoded
from the Japanese disc. Enter the Russian localization in `msgstr`.

Each visible line can contain at most 15 Unicode characters, with at most
three lines on a page. Use `\n` for an intentional line break. Entries whose
comments permit continuation can use `\f` between pages. For example:

```po
msgctxt "dialogue/03B/E000/000"
msgid "なかなかいけるじゃない."
msgstr "Очень даже\nнеплохо!"
```

The compiler preserves speaker/expression control words from the source; they
must not be copied into `msgstr`.

## 2. Validate while translating

```bash
python3 localize.py validate \
  --bin "/path/to/sr.bin" \
  --locale ru \
  --workspace localization-work/ru \
  --allow-incomplete
```

Remove `--allow-incomplete` for the release gate. Validation rejects changed
source IDs/text, duplicate contexts, non-NFC Unicode, overlong lines, invalid
page counts, and continuation pages on records that cannot support them.

## 3. Build the localized disc

```bash
python3 localize.py build \
  --bin "/path/to/sr.bin" \
  --locale ru \
  --workspace localization-work/ru \
  --output-dir localization-output/ru
```

The command verifies and applies the canonical English patch as a base,
rebuilds every translated scene and pointer table from the Japanese source,
adds the locale glyphs, repairs EDC/ECC for every replaced Form 1 sector, and
writes `slayers_royal_ru.bin` plus `slayers_royal_ru.cue`.

Build diagnostics are written under `localization-work/ru/build/`:

- `build_report.json` — source/target counts, output hash, and font capacity
- `glyph_map.json` — deterministic character/token-to-tile allocation
- `locale_glyphs.png` — enlarged review sheet for every new glyph
- `runtime_font.png` — the complete patched runtime atlas

The game does not receive Unicode support. UTF-8 is only the authoring format;
the builder maps each used character to a renderer-safe tile ID and rasterizes
that tile into the game's existing font atlas.

## Adding another language

Copy `localization/languages/ru.json` to a new locale code. Keep
`data_preparer` ASCII because ISO-9660 metadata cannot safely store Cyrillic.
`required_characters` reserves an alphabet even before every letter appears in
the PO. `compact_glyph_limit` controls how many frequent two-character cells
may be allocated to reduce storage in the fixed-runtime `0x03E` scene; other
scenes keep ordinary one-character typography. A build still fails rather
than overwriting another resource if a translation exceeds its proven
allocation.

Generated workspaces and disc images are ignored by Git. Commit only the
translated `dialogue.po` and language definition when intentionally adding a
locale to the project.
