"""Command-line interface for Royal 1 localization authoring."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import patch as english_patch

from . import __version__
from .disc import (
    ENGLISH_PROG_SHA256,
    SOURCE_PROG_SHA256,
    DiscError,
    read_prog,
    replace_extent_in_place,
    sha256_file,
    stamp_data_preparer,
    verify_source_bin,
)
from .glyphs import GlyphError, allocate_glyphs, load_language, patch_runtime_font
from .po import PoError, read_po, write_po
from .script import (
    ScriptError,
    catalog_entries,
    copy_dialogue_entries,
    rebuild_dialogue,
    source_inventory,
    validate_catalog,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
LANGUAGE_DIR = Path(__file__).resolve().parent / "languages"


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _resolve_language(locale: str, workspace: Path | None = None) -> Path:
    if workspace is not None:
        local = workspace / "language.json"
        if local.is_file():
            return local
    bundled = LANGUAGE_DIR / f"{locale}.json"
    if not bundled.is_file():
        raise GlyphError(f"unknown locale {locale!r}; missing {bundled}")
    return bundled


def export_catalog(args: argparse.Namespace) -> int:
    source_bin = args.bin.resolve()
    verify_source_bin(source_bin)
    prog = read_prog(source_bin, SOURCE_PROG_SHA256)
    language_path = _resolve_language(args.locale)
    language = load_language(language_path)
    output = args.output.resolve()
    po_path = output / "dialogue.po"
    language_output = output / "language.json"
    inventory_path = output / "source_inventory.json"
    existing = [path for path in (po_path, language_output, inventory_path) if path.exists()]
    if existing and not args.force:
        raise FileExistsError(
            "localization workspace already exists; use --force only if overwriting it is intentional: "
            + ", ".join(map(str, existing))
        )
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(language_path, language_output)
    entries = catalog_entries(prog)
    write_po(po_path, entries, str(language["locale"]))
    inventory = source_inventory(prog)
    inventory.update(
        {
            "toolkit_version": __version__,
            "locale": language["locale"],
            "catalog": "dialogue.po",
        }
    )
    _json(inventory_path, inventory)
    print(
        f"exported {inventory['translatable_segment_count']} source-text segments from "
        f"{inventory['record_count']} records to {po_path}"
    )
    print("Japanese source text exists only in this generated local workspace.")
    return 0


def _load_workspace(
    source_bin: Path,
    workspace: Path,
    locale: str,
    allow_incomplete: bool,
) -> tuple[bytes, dict[str, object], list[object], dict[str, object]]:
    verify_source_bin(source_bin)
    prog = read_prog(source_bin, SOURCE_PROG_SHA256)
    language = load_language(_resolve_language(locale, workspace))
    if language["locale"] != locale:
        raise GlyphError(
            f"workspace language is {language['locale']!r}, not requested locale {locale!r}"
        )
    po_path = workspace / "dialogue.po"
    if not po_path.is_file():
        raise FileNotFoundError(f"missing translation catalog: {po_path}")
    catalog = read_po(po_path)
    translations = validate_catalog(prog, catalog, allow_incomplete)
    counts = {
        "total": len(catalog),
        "translated": sum(bool(entry.translation) for entry in catalog),
        "untranslated": sum(not entry.translation for entry in catalog),
    }
    return prog, language, catalog, {"translations": translations, "counts": counts}


def validate_workspace(args: argparse.Namespace) -> int:
    _prog, language, _catalog, state = _load_workspace(
        args.bin.resolve(),
        args.workspace.resolve(),
        args.locale,
        args.allow_incomplete,
    )
    counts = state["counts"]
    assert isinstance(counts, dict)
    print(
        f"validated {language['name']} catalog: {counts['translated']} translated, "
        f"{counts['untranslated']} untranslated, {counts['total']} total"
    )
    return 0


def _apply_english_base(source_bin: Path, destination: Path) -> None:
    manifest = english_patch.load_manifest()
    sources = manifest["source"]
    targets = manifest["target"]
    patches = manifest["patches"]
    if not isinstance(sources, dict) or not isinstance(targets, dict) or not isinstance(patches, dict):
        raise english_patch.PatchError("invalid English release manifest")
    english_patch.verify_file("source BIN", source_bin, sources["bin"])
    parts = english_patch.verify_patch_parts(patches["bin"])
    english_patch.apply_xor_delta(
        source_bin, destination, patches["bin"], parts, targets["bin"]
    )
    english_patch.verify_file("English base BIN", destination, targets["bin"])


def build_disc(args: argparse.Namespace) -> int:
    source_bin = args.bin.resolve()
    workspace = args.workspace.resolve()
    source_prog, language, catalog, state = _load_workspace(
        source_bin, workspace, args.locale, args.allow_incomplete
    )
    translations = state["translations"]
    counts = state["counts"]
    assert isinstance(translations, dict) and isinstance(counts, dict)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = str(language["output_stem"])
    output_bin = output_dir / f"{stem}.bin"
    output_cue = output_dir / f"{stem}.cue"
    for path in (output_bin, output_cue):
        if path.exists() and not args.force:
            raise FileExistsError(f"output already exists (use --force): {path}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{stem}.", suffix=".tmp", dir=output_dir
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        print("applying the verified English release as the localization base...")
        _apply_english_base(source_bin, temporary)
        english_prog = read_prog(temporary, ENGLISH_PROG_SHA256)
        charmap, compact, glyph_report = allocate_glyphs(
            source_prog,
            english_prog,
            [entry.translation for entry in catalog],
            language,
            [
                entry.translation
                for entry in catalog
                if entry.context.startswith("dialogue/03E/")
            ],
        )
        print(
            f"allocated {glyph_report['new_character_cells']} locale characters and "
            f"{glyph_report['compact_cells']} compact text cells"
        )
        localized_source = rebuild_dialogue(
            source_prog, translations, charmap, compact
        )
        localized_prog = copy_dialogue_entries(localized_source, english_prog)
        build_dir = workspace / "build"
        localized_prog, font_report = patch_runtime_font(
            localized_prog, charmap, compact, build_dir
        )
        replace_extent_in_place(temporary, 229020, localized_prog)
        stamp_data_preparer(temporary, str(language["data_preparer"]))
        final_hash, final_size = sha256_file(temporary)
        os.replace(temporary, output_bin)
        cue_text = (
            f'FILE "{output_bin.name}" BINARY\r\n'
            "  TRACK 01 MODE2/2352\r\n"
            "    INDEX 01 00:00:00\r\n"
        )
        output_cue.write_bytes(cue_text.encode("ascii"))
        report = {
            "schema": 1,
            "toolkit_version": __version__,
            "locale": language["locale"],
            "language": language["name"],
            "base": "canonical English release",
            "catalog": counts,
            "glyphs": glyph_report,
            "font": font_report,
            "output": {
                "bin": output_bin.name,
                "size": final_size,
                "sha256": final_hash,
                "cue": output_cue.name,
            },
        }
        _json(build_dir / "glyph_map.json", glyph_report)
        _json(build_dir / "build_report.json", report)
        print(f"wrote {output_bin} ({final_hash})")
        print(f"wrote {output_cue}")
        if counts["untranslated"]:
            print(
                "warning: diagnostic build retains source dialogue for "
                f"{counts['untranslated']} untranslated segments"
            )
        return 0
    finally:
        temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Export and build manual Slayers Royal localizations."
    )
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)

    export = commands.add_parser(
        "export", help="export verified Japanese story text to a new PO workspace"
    )
    export.add_argument("--bin", type=Path, required=True, help="original sr.bin")
    export.add_argument("--locale", default="ru")
    export.add_argument(
        "--output", type=Path, default=REPO_ROOT / "localization-work" / "ru"
    )
    export.add_argument("--force", action="store_true")
    export.set_defaults(handler=export_catalog)

    validate = commands.add_parser(
        "validate", help="validate source IDs, translations, pages, and line lengths"
    )
    validate.add_argument("--bin", type=Path, required=True, help="original sr.bin")
    validate.add_argument("--locale", default="ru")
    validate.add_argument("--workspace", type=Path, required=True)
    validate.add_argument("--allow-incomplete", action="store_true")
    validate.set_defaults(handler=validate_workspace)

    build = commands.add_parser(
        "build", help="build a localized BIN/CUE over the canonical English base"
    )
    build.add_argument("--bin", type=Path, required=True, help="original sr.bin")
    build.add_argument("--locale", default="ru")
    build.add_argument("--workspace", type=Path, required=True)
    build.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / "localization-output"
    )
    build.add_argument("--allow-incomplete", action="store_true")
    build.add_argument("--force", action="store_true")
    build.set_defaults(handler=build_disc)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.handler(args))
    except (
        DiscError,
        GlyphError,
        PoError,
        ScriptError,
        english_patch.PatchError,
        FileExistsError,
        FileNotFoundError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
