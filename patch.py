#!/usr/bin/env python3
"""Verify a supported Slayers Royal disc dump and apply the English patch."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
from typing import Iterator


CHUNK_SIZE = 1024 * 1024
MAGIC = b"SLRXOR1\0"
HEADER = struct.Struct("<8sIQQ")
RECORD = struct.Struct("<QI")
REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "release_manifest.json"


class PatchError(RuntimeError):
    pass


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_file(label: str, path: Path, expected: dict[str, object]) -> None:
    if not path.is_file():
        raise PatchError(f"{label} does not exist: {path}")
    actual_hash, actual_size = sha256_file(path)
    expected_hash = str(expected["sha256"])
    expected_size = int(expected["size"])
    if actual_size != expected_size or actual_hash != expected_hash:
        raise PatchError(
            f"{label} is not the supported source file:\n"
            f"  path:          {path}\n"
            f"  expected size: {expected_size}\n"
            f"  actual size:   {actual_size}\n"
            f"  expected hash: {expected_hash}\n"
            f"  actual hash:   {actual_hash}"
        )
    print(f"verified {label}: {actual_hash}")


def resolve_patch_part(relative_path: str) -> Path:
    path = (REPO_ROOT / relative_path).resolve()
    try:
        path.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise PatchError(f"patch path leaves repository: {relative_path}") from exc
    return path


def verify_patch_parts(spec: dict[str, object]) -> list[Path]:
    parts: list[Path] = []
    stored_hash = hashlib.sha256()
    stored_size = 0
    for item in spec["parts"]:
        if not isinstance(item, dict):
            raise PatchError("invalid patch part entry")
        path = resolve_patch_part(str(item["path"]))
        verify_file(f"patch part {path.name}", path, item)
        parts.append(path)
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                stored_hash.update(chunk)
                stored_size += len(chunk)

    if stored_size != int(spec["stored_size"]):
        raise PatchError("combined patch-part size does not match the manifest")
    if stored_hash.hexdigest() != str(spec["stored_sha256"]):
        raise PatchError("combined patch-part hash does not match the manifest")
    return parts


def iter_stored_chunks(parts: list[Path]) -> Iterator[bytes]:
    for path in parts:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_SIZE):
                yield chunk


def iter_xz_chunks(parts: list[Path]) -> Iterator[bytes]:
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    ended = False
    for compressed in iter_stored_chunks(parts):
        if ended:
            raise PatchError("unexpected data after the XZ patch stream")
        pending = compressed
        while True:
            try:
                decoded = decoder.decompress(pending, max_length=CHUNK_SIZE)
            except lzma.LZMAError as exc:
                raise PatchError(f"invalid XZ patch stream: {exc}") from exc
            pending = b""
            if decoded:
                yield decoded
            if decoder.eof:
                if decoder.unused_data:
                    raise PatchError("unexpected data after the XZ patch stream")
                ended = True
                break
            if decoder.needs_input:
                break

    if not ended:
        while not decoder.needs_input and not decoder.eof:
            decoded = decoder.decompress(b"", max_length=CHUNK_SIZE)
            if decoded:
                yield decoded
        if not decoder.eof:
            raise PatchError("truncated XZ patch stream")


class DeltaReader:
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = iter(chunks)
        self._buffer = bytearray()
        self._hash = hashlib.sha256()
        self._size = 0

    def _next_chunk(self) -> bool:
        try:
            chunk = next(self._chunks)
        except StopIteration:
            return False
        self._hash.update(chunk)
        self._size += len(chunk)
        self._buffer.extend(chunk)
        return True

    def read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            if not self._next_chunk():
                raise PatchError("truncated XOR patch container")
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result

    def finish(self) -> tuple[str, int]:
        if self._buffer:
            raise PatchError("unexpected data after XOR patch terminator")
        while self._next_chunk():
            if self._buffer:
                raise PatchError("unexpected data after XOR patch terminator")
        return self._hash.hexdigest(), self._size


def copy_source_mask(source: Path, destination: Path, target_size: int) -> None:
    source_size = source.stat().st_size
    if source_size == 0:
        raise PatchError("cannot patch from an empty source file")
    copied = 0
    with source.open("rb") as original, destination.open("wb") as output:
        while copied < target_size:
            source_offset = copied % source_size
            amount = min(CHUNK_SIZE, target_size - copied, source_size - source_offset)
            original.seek(source_offset)
            chunk = original.read(amount)
            if len(chunk) != amount:
                raise PatchError("could not read source file while preparing output")
            output.write(chunk)
            copied += amount


def apply_xor_delta(
    source: Path,
    destination: Path,
    spec: dict[str, object],
    parts: list[Path],
    target: dict[str, object],
) -> None:
    if str(spec["storage"]) != "xz":
        raise PatchError(f"unsupported patch storage: {spec['storage']}")

    reader = DeltaReader(iter_xz_chunks(parts))
    magic, block_size, source_size, target_size = HEADER.unpack(
        reader.read_exact(HEADER.size)
    )
    if magic != MAGIC:
        raise PatchError("invalid XOR patch magic")
    if block_size == 0 or block_size > 16 * 1024 * 1024:
        raise PatchError("invalid XOR patch block size")
    if source_size != source.stat().st_size:
        raise PatchError("XOR patch source size does not match the supplied file")
    if target_size != int(target["size"]):
        raise PatchError("XOR patch target size does not match the manifest")

    copy_source_mask(source, destination, target_size)
    previous_end = 0
    with destination.open("r+b") as output:
        while True:
            offset, length = RECORD.unpack(reader.read_exact(RECORD.size))
            if length == 0:
                if offset != target_size:
                    raise PatchError("invalid XOR patch terminator")
                break
            if length > block_size:
                raise PatchError("XOR record exceeds declared block size")
            if offset < previous_end or offset + length > target_size:
                raise PatchError("invalid or overlapping XOR record")
            delta = reader.read_exact(length)
            output.seek(offset)
            current = output.read(length)
            if len(current) != length:
                raise PatchError("could not read output block during XOR application")
            changed = (
                int.from_bytes(current, "little") ^ int.from_bytes(delta, "little")
            ).to_bytes(length, "little")
            output.seek(offset)
            output.write(changed)
            previous_end = offset + length

    delta_hash, delta_size = reader.finish()
    if delta_size != int(spec["delta_size"]):
        raise PatchError("XOR container size does not match the manifest")
    if delta_hash != str(spec["delta_sha256"]):
        raise PatchError("XOR container hash does not match the manifest")


def temporary_output(output_dir: Path, name: str) -> Path:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{name}.", suffix=".tmp", dir=output_dir
    )
    os.close(descriptor)
    path = Path(temporary)
    path.unlink()
    return path


def load_manifest() -> dict[str, object]:
    try:
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PatchError(f"cannot read {MANIFEST_PATH}: {exc}") from exc
    if document.get("schema") != 1:
        raise PatchError("unsupported release manifest schema")
    return document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the Slayers Royal English translation patch."
    )
    parser.add_argument("--bin", required=True, type=Path, help="original sr.bin")
    parser.add_argument("--cue", required=True, type=Path, help="original sr.cue")
    parser.add_argument(
        "--output-dir", type=Path, default=REPO_ROOT / "output"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify source and patch files without creating output",
    )
    parser.add_argument(
        "--force", action="store_true", help="replace existing output files"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = load_manifest()
        sources = manifest["source"]
        targets = manifest["target"]
        patches = manifest["patches"]
        assert isinstance(sources, dict)
        assert isinstance(targets, dict)
        assert isinstance(patches, dict)

        inputs = {"bin": args.bin.resolve(), "cue": args.cue.resolve()}
        for kind, path in inputs.items():
            verify_file(f"source {kind.upper()}", path, sources[kind])
        patch_parts = {
            kind: verify_patch_parts(patches[kind]) for kind in ("bin", "cue")
        }

        if args.verify_only:
            print("source and patch files are valid")
            return 0

        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        required_space = sum(int(targets[kind]["size"]) for kind in ("bin", "cue"))
        if shutil.disk_usage(output_dir).free < required_space + 64 * 1024 * 1024:
            raise PatchError("not enough free space in the output directory")

        final_paths = {
            kind: output_dir / str(targets[kind]["name"])
            for kind in ("bin", "cue")
        }
        for path in final_paths.values():
            if path.exists() and not args.force:
                raise PatchError(f"output already exists (use --force): {path}")

        temporary_paths = {
            kind: temporary_output(output_dir, final_paths[kind].name)
            for kind in ("bin", "cue")
        }
        try:
            for kind in ("bin", "cue"):
                print(f"applying {kind.upper()} XOR patch...")
                apply_xor_delta(
                    inputs[kind],
                    temporary_paths[kind],
                    patches[kind],
                    patch_parts[kind],
                    targets[kind],
                )
                verify_file(
                    f"patched {kind.upper()}", temporary_paths[kind], targets[kind]
                )
            for kind in ("bin", "cue"):
                os.replace(temporary_paths[kind], final_paths[kind])
        finally:
            for path in temporary_paths.values():
                path.unlink(missing_ok=True)

        print("patch complete:")
        for kind in ("bin", "cue"):
            print(f"  {final_paths[kind]}")
        return 0
    except (PatchError, KeyError, TypeError, AssertionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
