"""Verified MODE2/2352 disc extraction and sector replacement."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


RAW_SECTOR_SIZE = 2352
USER_DATA_OFFSET = 24
USER_DATA_SIZE = 2048
PROG_LBA = 229020
PROG_SIZE = 8_822_784
SOURCE_BIN_SIZE = 712_300_848
SOURCE_BIN_SHA256 = "89760d728f0580dba1c6176f024d3cd6f8fc105b79bd1c27a819208fa0b4d0fe"
SOURCE_PROG_SHA256 = "9e1334077df2e7a71e3bcb4df7ee1d9e6a2f2c51dd431b53022504c47c1c12b8"
ENGLISH_PROG_SHA256 = "50ef7feac9569a1979f85d99d593676c6a62b8cf3d9273022c9eb516a908aec6"


class DiscError(RuntimeError):
    pass


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def verify_source_bin(path: Path) -> None:
    digest, size = sha256_file(path)
    if size != SOURCE_BIN_SIZE or digest != SOURCE_BIN_SHA256:
        raise DiscError(
            "unsupported source BIN: "
            f"expected {SOURCE_BIN_SIZE} bytes / {SOURCE_BIN_SHA256}, "
            f"found {size} bytes / {digest}"
        )


def read_extent(path: Path, lba: int, size: int) -> bytes:
    output = bytearray()
    remaining = size
    with path.open("rb") as image:
        sector = lba
        while remaining:
            amount = min(USER_DATA_SIZE, remaining)
            image.seek(sector * RAW_SECTOR_SIZE + USER_DATA_OFFSET)
            block = image.read(amount)
            if len(block) != amount:
                raise DiscError(f"disc ends while reading LBA {sector}")
            output.extend(block)
            remaining -= amount
            sector += 1
    return bytes(output)


def read_prog(path: Path, expected_hash: str | None = None) -> bytes:
    data = read_extent(path, PROG_LBA, PROG_SIZE)
    digest = hashlib.sha256(data).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise DiscError(f"unexpected PROG.UNT hash {digest}; expected {expected_hash}")
    return data


class CdChecksums:
    """EDC/ECC generator for PlayStation Mode 2 Form 1 sectors."""

    def __init__(self) -> None:
        self.ecc_f = [0] * 256
        self.ecc_b = [0] * 256
        self.edc = [0] * 256
        for value in range(256):
            forward = ((value << 1) ^ (0x11D if value & 0x80 else 0)) & 0xFF
            self.ecc_f[value] = forward
            self.ecc_b[value ^ forward] = value
            crc = value
            for _ in range(8):
                crc = (crc >> 1) ^ (0xD8018001 if crc & 1 else 0)
            self.edc[value] = crc

    def compute_edc(self, data: bytes) -> bytes:
        crc = 0
        for value in data:
            crc = (crc >> 8) ^ self.edc[(crc ^ value) & 0xFF]
        return crc.to_bytes(4, "little")

    def compute_ecc(
        self,
        source: bytes,
        major_count: int,
        minor_count: int,
        major_mult: int,
        minor_inc: int,
    ) -> bytes:
        address = b"\0\0\0\0"
        length = major_count * minor_count
        output = bytearray(major_count * 2)
        for major in range(major_count):
            index = (major >> 1) * major_mult + (major & 1)
            ecc_a = 0
            ecc_b = 0
            for _ in range(minor_count):
                value = address[index] if index < 4 else source[index - 4]
                index = (index + minor_inc) % length
                ecc_a ^= value
                ecc_b ^= value
                ecc_a = self.ecc_f[ecc_a]
            ecc_a = self.ecc_b[self.ecc_f[ecc_a] ^ ecc_b]
            output[major] = ecc_a
            output[major + major_count] = ecc_a ^ ecc_b
        return bytes(output)

    def repair_mode2_form1(self, sector: bytearray) -> None:
        if len(sector) != RAW_SECTOR_SIZE or sector[15] != 2 or sector[18] & 0x20:
            raise DiscError("target sector is not MODE2/2352 Form 1")
        sector[0x818:0x81C] = self.compute_edc(sector[0x10:0x818])
        sector[0x81C:0x8C8] = self.compute_ecc(sector[0x10:], 86, 24, 2, 86)
        sector[0x8C8:0x930] = self.compute_ecc(sector[0x10:], 52, 43, 86, 88)


def replace_extent_in_place(path: Path, lba: int, payload: bytes) -> None:
    if len(payload) % USER_DATA_SIZE:
        raise DiscError("replacement extent must contain complete 2048-byte sectors")
    checksums = CdChecksums()
    with path.open("r+b") as image:
        for index, offset in enumerate(range(0, len(payload), USER_DATA_SIZE)):
            sector_lba = lba + index
            image.seek(sector_lba * RAW_SECTOR_SIZE)
            sector = bytearray(image.read(RAW_SECTOR_SIZE))
            if len(sector) != RAW_SECTOR_SIZE:
                raise DiscError(f"disc ends while writing LBA {sector_lba}")
            sector[USER_DATA_OFFSET : USER_DATA_OFFSET + USER_DATA_SIZE] = payload[
                offset : offset + USER_DATA_SIZE
            ]
            checksums.repair_mode2_form1(sector)
            image.seek(sector_lba * RAW_SECTOR_SIZE)
            image.write(sector)


def stamp_data_preparer(path: Path, value: str) -> None:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise DiscError("ISO data-preparer metadata must be ASCII") from exc
    if not encoded or len(encoded) > 128:
        raise DiscError("ISO data-preparer metadata must contain 1-128 ASCII bytes")
    checksums = CdChecksums()
    with path.open("r+b") as image:
        image.seek(16 * RAW_SECTOR_SIZE)
        sector = bytearray(image.read(RAW_SECTOR_SIZE))
        if len(sector) != RAW_SECTOR_SIZE:
            raise DiscError("disc ends before the ISO primary volume descriptor")
        start = USER_DATA_OFFSET + 446
        sector[start : start + 128] = encoded.ljust(128, b" ")
        checksums.repair_mode2_form1(sector)
        image.seek(16 * RAW_SECTOR_SIZE)
        image.write(sector)


def copy_disc(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
