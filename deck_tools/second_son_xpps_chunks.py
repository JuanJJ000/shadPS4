#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Classify a bounded high-kind-2 chunk row in an owned Second Son XPPS file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
from pathlib import Path
from typing import BinaryIO

import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_xpps_chunks"
SCHEMA_VERSION = 1
PROOF_CLASS = "chunk_structure_classifier"
MAX_CHUNKS = 65536
MAX_DIC_ENTRIES = 65536
HASH_CHUNK_BYTES = 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
NON_CLAIMS = (
    "compression",
    "hash_to_name_mapping",
    "object_boundaries",
    "object_header_layout",
    "resource_identity",
    "runtime_acceptance",
    "safe_replacement",
    "texture_format",
)


def _read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise probe.ProbeError(
            f"truncated {label}: expected {size} bytes, got {len(data)}"
        )
    return data


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _tag_ascii(tag: bytes) -> str | None:
    return tag.decode("ascii") if all(0x20 <= byte <= 0x7E for byte in tag) else None


def classify_chunks(
    source: Path,
    *,
    expected_sha256: str,
    row_index: int,
    max_chunks: int = MAX_CHUNKS,
    max_dic_entries: int = MAX_DIC_ENTRIES,
) -> dict[str, object]:
    source = Path(source)
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise probe.ProbeError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    if row_index < 0:
        raise probe.ProbeError("row index must be nonnegative")
    if max_chunks < 1 or max_dic_entries < 1:
        raise probe.ProbeError("chunk and DIC entry budgets must be positive")

    structure = probe.probe_file(source)
    source_info = structure["input"]
    candidate_layout = structure["candidate_layout"]
    assert isinstance(source_info, dict)
    assert isinstance(candidate_layout, dict)
    observed_sha256 = str(source_info["sha256"])
    if observed_sha256 != expected_sha256:
        raise probe.ProbeError(
            f"source SHA-256 mismatch: expected {expected_sha256}, observed {observed_sha256}"
        )

    rows = candidate_layout["payload_rows"]
    assert isinstance(rows, list)
    if row_index >= len(rows):
        raise probe.ProbeError(
            f"row index {row_index} is outside the candidate table of {len(rows)} rows"
        )
    row = rows[row_index]
    assert isinstance(row, dict)
    kind_word = int(row["kind_word"])
    kind_class = kind_word >> 16
    kind_flags = kind_word & 0xFFFF
    if kind_class != 2:
        raise probe.ProbeError(
            f"row {row_index} has observed high kind {kind_class}, expected 2"
        )

    row_start = int(row["absolute_start"])
    row_end = int(row["absolute_end"])
    row_size = int(row["size"])
    source_size = int(source_info["bytes"])
    data_start = int(candidate_layout["data_start"])
    data_size = int(candidate_layout["data_size"])
    if row_start < data_start or row_end != row_start + row_size:
        raise probe.ProbeError("selected row has an inconsistent data range")
    if row_end > data_start + data_size or row_end > source_size:
        raise probe.ProbeError("selected row exceeds the validated data region")

    chunks: list[dict[str, object]] = []
    total_dic_entries = 0
    with source.open("rb") as stream:
        source_stat_before = os.fstat(stream.fileno())
        if source_stat_before.st_size != source_size:
            raise probe.ProbeError("source size changed after structure validation")
        position = row_start
        while position < row_end:
            if len(chunks) >= max_chunks:
                raise probe.ProbeError(f"chunk stream exceeds {max_chunks} chunks")
            if row_end - position < 8:
                raise probe.ProbeError("truncated chunk prefix at the selected row end")
            stream.seek(position)
            prefix = _read_exact(stream, 8, f"chunk {len(chunks)} prefix")
            tag = prefix[:4]
            content_size = struct.unpack_from("<I", prefix, 4)[0]
            content_start = position + 8
            content_end = content_start + content_size
            if content_end > row_end:
                raise probe.ProbeError(f"chunk {len(chunks)} exceeds the selected row")
            if content_size == 0 and content_end != row_end:
                raise probe.ProbeError("zero-size chunk is not terminal")

            chunk: dict[str, object] = {
                "content_end": content_end,
                "content_size": content_size,
                "content_start": content_start,
                "index": len(chunks),
                "prefix_start": position,
                "tag_ascii": _tag_ascii(tag),
                "tag_hex": tag.hex(),
            }
            if tag == b" DIC":
                if content_size < 8:
                    raise probe.ProbeError("DIC content is too small for count/reserved words")
                stream.seek(content_start)
                count, reserved = struct.unpack(
                    "<II", _read_exact(stream, 8, "DIC count/reserved")
                )
                if count > max_dic_entries - total_dic_entries:
                    raise probe.ProbeError(
                        f"DIC population exceeds {max_dic_entries} total entries"
                    )
                expected_content_size = 8 + count * 16
                if content_size != expected_content_size:
                    raise probe.ProbeError(
                        "DIC content size does not exactly match its entry count"
                    )
                entries: list[dict[str, object]] = []
                for entry_index in range(count):
                    relative_offset, hash_word = struct.unpack(
                        "<QQ", _read_exact(stream, 16, f"DIC entry {entry_index}")
                    )
                    if relative_offset >= data_size:
                        raise probe.ProbeError(
                            f"DIC entry {entry_index} offset exceeds the data region"
                        )
                    absolute_offset = data_start + relative_offset
                    if absolute_offset >= source_size:
                        raise probe.ProbeError(
                            f"DIC entry {entry_index} absolute offset exceeds the source"
                        )
                    entries.append(
                        {
                            "absolute_offset": absolute_offset,
                            "hash_word_hex": f"{hash_word:016x}",
                            "index": entry_index,
                            "relative_offset": relative_offset,
                        }
                    )
                total_dic_entries += count
                chunk["dic"] = {
                    "count": count,
                    "entries": entries,
                    "reserved_word": reserved,
                }
            chunks.append(chunk)
            position = content_end

        source_sha256_after = _hash_stream(stream)
        source_stat_after = os.fstat(stream.fileno())
        stable_identity = (
            source_stat_before.st_dev,
            source_stat_before.st_ino,
            source_stat_before.st_size,
            source_stat_before.st_mtime_ns,
            source_stat_before.st_ctime_ns,
        ) == (
            source_stat_after.st_dev,
            source_stat_after.st_ino,
            source_stat_after.st_size,
            source_stat_after.st_mtime_ns,
            source_stat_after.st_ctime_ns,
        )
        if source_sha256_after != expected_sha256 or not stable_identity:
            raise probe.ProbeError("source changed between validation and classification")

    return {
        "chunks": chunks,
        "facts": {
            "chunk_stream_exactly_covers_row": position == row_end,
            "dic_offsets_inside_data": True,
            "total_chunks": len(chunks),
            "total_dic_entries": total_dic_entries,
        },
        "non_claims": list(NON_CLAIMS),
        "proof_class": PROOF_CLASS,
        "schema": SCHEMA,
        "selected_row": {
            "absolute_end": row_end,
            "absolute_start": row_start,
            "index": row_index,
            "kind_class_high16": kind_class,
            "kind_flags_low16": kind_flags,
            "kind_word": kind_word,
            "size": row_size,
        },
        "source": {
            "basename": str(source_info["basename"]),
            "bytes": source_size,
            "sha256": expected_sha256,
        },
        "version": SCHEMA_VERSION,
        "warnings": [],
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Classify a validated high-kind-2 chunk row in an owned Second Son XPPS file. "
            "The source is never modified and no payload bytes are emitted."
        )
    )
    parser.add_argument("input", type=Path, help="regular .xpps source")
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--row", required=True, type=int, help="zero-based candidate row")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = classify_chunks(
            args.input,
            expected_sha256=args.expected_sha256,
            row_index=args.row,
        )
        sys.stdout.buffer.write(encode_report(report))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_chunks: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
