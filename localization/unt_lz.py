#!/usr/bin/env python3
"""Encode and decode the small LZ stream used by Slayers Royal resources."""

from __future__ import annotations

from collections import defaultdict, deque


RAW_MODE = 0
LZ_MODE = 1
WINDOW_SIZE = 0x800
MIN_MATCH = 3
MAX_MATCH = 34


def decompress(data: bytes) -> tuple[bytes, int]:
    if len(data) < 5:
        raise ValueError("resource is too short")

    mode = data[0]
    output_size = int.from_bytes(data[1:5], "little")
    if mode == RAW_MODE:
        end = 5 + output_size
        if end > len(data):
            raise ValueError("truncated raw resource")
        return data[5:end], end
    if mode != LZ_MODE:
        raise ValueError(f"unsupported resource mode {mode}")

    source = 5
    output = bytearray()
    while len(output) < output_size:
        if source + 2 > len(data):
            raise ValueError("truncated LZ flags")
        flags = int.from_bytes(data[source : source + 2], "little")
        source += 2

        for _ in range(16):
            if len(output) >= output_size:
                break
            if flags & 1:
                if source + 2 > len(data):
                    raise ValueError("truncated LZ match")
                code = int.from_bytes(data[source : source + 2], "little")
                source += 2
                distance = (code & 0x7FF) + 1
                length = (code >> 11) + MIN_MATCH
                if distance > len(output):
                    raise ValueError("LZ match precedes output")
                for _ in range(length):
                    if len(output) >= output_size:
                        break
                    output.append(output[-distance])
            else:
                if source >= len(data):
                    raise ValueError("truncated LZ literal")
                output.append(data[source])
                source += 1
            flags >>= 1

    return bytes(output), source


def compress(data: bytes) -> bytes:
    positions: dict[bytes, deque[int]] = defaultdict(deque)

    def add_position(pos: int) -> None:
        if pos + MIN_MATCH > len(data):
            return
        key = data[pos : pos + MIN_MATCH]
        candidates = positions[key]
        oldest = pos - WINDOW_SIZE
        while candidates and candidates[0] < oldest:
            candidates.popleft()
        candidates.append(pos)

    output = bytearray([LZ_MODE])
    output.extend(len(data).to_bytes(4, "little"))
    cursor = 0

    while cursor < len(data):
        flags = 0
        tokens = bytearray()

        for bit in range(16):
            if cursor >= len(data):
                break

            best_pos = -1
            best_length = 0
            if cursor + MIN_MATCH <= len(data):
                key = data[cursor : cursor + MIN_MATCH]
                candidates = positions.get(key)
                if candidates:
                    oldest = cursor - WINDOW_SIZE
                    while candidates and candidates[0] < oldest:
                        candidates.popleft()
                    for candidate in reversed(candidates):
                        limit = min(MAX_MATCH, len(data) - cursor)
                        length = MIN_MATCH
                        while length < limit and data[candidate + length] == data[cursor + length]:
                            length += 1
                        if length > best_length:
                            best_pos = candidate
                            best_length = length
                            if length == limit:
                                break

            if best_length >= MIN_MATCH:
                flags |= 1 << bit
                distance = cursor - best_pos
                code = (distance - 1) | ((best_length - MIN_MATCH) << 11)
                tokens.extend(code.to_bytes(2, "little"))
                old_cursor = cursor
                cursor += best_length
                for pos in range(old_cursor, cursor):
                    add_position(pos)
            else:
                tokens.append(data[cursor])
                add_position(cursor)
                cursor += 1

        output.extend(flags.to_bytes(2, "little"))
        output.extend(tokens)

    return bytes(output)
