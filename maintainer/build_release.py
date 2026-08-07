#!/usr/bin/env python3
"""Regenerate the source-dependent XOR release patches and manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import struct
import tempfile


CHUNK_SIZE = 1024 * 1024
BLOCK_SIZE = 64 * 1024
PART_SIZE = 45 * 1024 * 1024
MAGIC = b"SLRXOR1\0"
HEADER = struct.Struct("<8sIQQ")
RECORD = struct.Struct("<QI")
REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    "source": {
        "bin": {
            "name": "sr.bin",
            "size": 712300848,
            "sha256": "89760d728f0580dba1c6176f024d3cd6f8fc105b79bd1c27a819208fa0b4d0fe",
        },
        "cue": {
            "name": "sr.cue",
            "size": 68,
            "sha256": "0f93f45114b7fc88b8f57c5449af0828e59699a8780849540a670df7c3a0aa08",
        },
    },
    "target": {
        "bin": {
            "name": "sr_patched.bin",
            "size": 712300848,
            "sha256": "d464bb3c08414f2f1ad284d934b1a96316b44940dadf0d8c5dd837a45bef2d8f",
        },
        "cue": {
            "name": "sr_patched.cue",
            "size": 76,
            "sha256": "c5384aae77bd17955acb4559d92422f7e11057619e83f55f7726fd975f5bfc84",
        },
    },
}


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify(path: Path, expected: dict[str, object]) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest, size = sha256_file(path)
    if size != int(expected["size"]) or digest != str(expected["sha256"]):
        raise ValueError(
            f"unexpected input {path}: size={size}, sha256={digest}"
        )


def read_source_mask(
    source, source_size: int, offset: int, length: int
) -> bytes:
    result = bytearray()
    while len(result) < length:
        position = (offset + len(result)) % source_size
        amount = min(length - len(result), source_size - position)
        source.seek(position)
        chunk = source.read(amount)
        if len(chunk) != amount:
            raise OSError("short read from source while building XOR patch")
        result.extend(chunk)
    return bytes(result)


def build_xor_container(
    source_path: Path, target_path: Path, stored_path: Path
) -> tuple[str, int, int]:
    source_size = source_path.stat().st_size
    target_size = target_path.stat().st_size
    delta_hash = hashlib.sha256()
    delta_size = 0
    record_count = 0

    with (
        source_path.open("rb") as source,
        target_path.open("rb") as target,
        lzma.open(
            stored_path,
            "wb",
            format=lzma.FORMAT_XZ,
            preset=9 | lzma.PRESET_EXTREME,
        ) as compressed,
    ):
        def emit(data: bytes) -> None:
            nonlocal delta_size
            compressed.write(data)
            delta_hash.update(data)
            delta_size += len(data)

        emit(HEADER.pack(MAGIC, BLOCK_SIZE, source_size, target_size))
        offset = 0
        while target_chunk := target.read(BLOCK_SIZE):
            mask = read_source_mask(source, source_size, offset, len(target_chunk))
            delta = (
                int.from_bytes(mask, "little")
                ^ int.from_bytes(target_chunk, "little")
            ).to_bytes(len(target_chunk), "little")
            if any(delta):
                emit(RECORD.pack(offset, len(delta)))
                emit(delta)
                record_count += 1
            offset += len(target_chunk)
        emit(RECORD.pack(target_size, 0))

    return delta_hash.hexdigest(), delta_size, record_count


def split_file(source: Path, output_dir: Path, prefix: str) -> list[Path]:
    parts: list[Path] = []
    with source.open("rb") as handle:
        index = 1
        while True:
            data = handle.read(PART_SIZE)
            if not data:
                break
            path = output_dir / f"{prefix}.part{index:02d}"
            path.write_bytes(data)
            parts.append(path)
            index += 1
    return parts


def part_records(paths: list[Path]) -> list[dict[str, object]]:
    records = []
    for path in paths:
        digest, size = sha256_file(path)
        records.append(
            {
                "path": f"patches/{path.name}",
                "size": size,
                "sha256": digest,
            }
        )
    return records


def patch_record(
    delta_hash: str,
    delta_size: int,
    record_count: int,
    stored: Path,
    parts: list[Path],
) -> dict[str, object]:
    stored_hash, stored_size = sha256_file(stored)
    return {
        "format": "SLRXOR1",
        "storage": "xz",
        "block_size": BLOCK_SIZE,
        "record_count": record_count,
        "delta_size": delta_size,
        "delta_sha256": delta_hash,
        "stored_size": stored_size,
        "stored_sha256": stored_hash,
        "parts": part_records(parts),
    }


def parse_args() -> argparse.Namespace:
    parent = REPO_ROOT.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-bin", type=Path, default=parent / "sr.bin")
    parser.add_argument("--source-cue", type=Path, default=parent / "sr.cue")
    parser.add_argument(
        "--target-bin", type=Path, default=parent / "patched" / "sr_patched.bin"
    )
    parser.add_argument(
        "--target-cue", type=Path, default=parent / "patched" / "sr_patched.cue"
    )
    parser.add_argument("--patch-version", default="2026-08-07")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    inputs = {
        "source": {"bin": args.source_bin.resolve(), "cue": args.source_cue.resolve()},
        "target": {"bin": args.target_bin.resolve(), "cue": args.target_cue.resolve()},
    }
    for side in ("source", "target"):
        for kind in ("bin", "cue"):
            verify(inputs[side][kind], EXPECTED[side][kind])

    patches_dir = REPO_ROOT / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-build-", dir=REPO_ROOT) as tmp:
        stage = Path(tmp)
        records: dict[str, dict[str, object]] = {}
        staged_parts: list[Path] = []
        for kind in ("bin", "cue"):
            stored = stage / f"sr_patched.{kind}.slrxor.xz"
            print(f"building {kind.upper()} XOR patch...")
            delta_hash, delta_size, record_count = build_xor_container(
                inputs["source"][kind], inputs["target"][kind], stored
            )
            parts = split_file(stored, stage, stored.name)
            records[kind] = patch_record(
                delta_hash, delta_size, record_count, stored, parts
            )
            staged_parts.extend(parts)

        manifest = {
            "schema": 1,
            "project": "Slayers Royal English Patch",
            "disc_id": "SLPS-01363",
            "patch_version": args.patch_version,
            "source": EXPECTED["source"],
            "target": EXPECTED["target"],
            "patches": records,
        }

        keep_names = {path.name for path in staged_parts}
        for old in patches_dir.glob("sr_patched.*"):
            if old.name not in keep_names:
                old.unlink()
        for path in staged_parts:
            os.replace(path, patches_dir / path.name)

        temporary_manifest = stage / "release_manifest.json"
        temporary_manifest.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary_manifest, REPO_ROOT / "release_manifest.json")

    print(f"wrote {REPO_ROOT / 'release_manifest.json'}")
    for path in sorted(patches_dir.iterdir()):
        digest, size = sha256_file(path)
        print(f"{path.name}: {size} bytes, SHA256 {digest}")


if __name__ == "__main__":
    main()
