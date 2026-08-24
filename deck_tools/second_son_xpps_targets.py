#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Fingerprint bounded DIC target headers in an owned Second Son XPPS file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import sys
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

import second_son_xpps_chunks as chunks
import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_xpps_targets"
SCHEMA_VERSION = 1
PROOF_CLASS = "dic_target_fingerprint_classifier"
MAX_ENTRIES = 4096
PREDECESSOR_BYTES = 16
TARGET_BYTES = 64
HASH_CHUNK_BYTES = 1024 * 1024
NON_CLAIMS = (
    "dic_hash_meaning",
    "fixed_window_is_object_boundary",
    "object_header_layout",
    "object_identity",
    "predecessor_is_header",
    "resource_type",
    "safe_replacement",
    "texture_format",
)


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_regular(stack: ExitStack, source: Path) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot open source as a nonsymlink regular file: {error.strerror}"
        ) from error
    stream = stack.enter_context(os.fdopen(descriptor, "rb"))
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise probe.ProbeError("source is not a regular file")
    return stream


def _read_exact_at(stream: BinaryIO, offset: int, size: int, label: str) -> bytes:
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise probe.ProbeError(f"truncated {label}: expected {size} bytes, got {len(data)}")
    return data


def _require_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise probe.ProbeError(f"{label} has an unexpected shape")
    return value


def _require_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise probe.ProbeError(f"{label} has an unexpected shape")
    return value


def classify_targets(
    source: Path,
    *,
    expected_sha256: str,
    row_index: int,
    max_entries: int = MAX_ENTRIES,
) -> dict[str, object]:
    if max_entries < 1 or max_entries > MAX_ENTRIES:
        raise probe.ProbeError(f"entry budget must be from 1 through {MAX_ENTRIES}")

    source = Path(source)
    structure = probe.probe_file(source)
    classifier_report = chunks.classify_chunks(
        source,
        expected_sha256=expected_sha256,
        row_index=row_index,
        max_dic_entries=max_entries,
    )
    structure_input = _require_dict(structure.get("input"), "probe input metadata")
    layout = _require_dict(structure.get("candidate_layout"), "probe candidate layout")
    rows_raw = _require_list(layout.get("payload_rows"), "probe payload rows")
    source_size = int(structure_input["bytes"])
    if str(structure_input["sha256"]) != expected_sha256:
        raise probe.ProbeError("probe source hash differs from the expected SHA-256")

    rows: list[dict[str, int]] = []
    for index, raw_row in enumerate(rows_raw):
        row = _require_dict(raw_row, f"probe payload row {index}")
        kind_word = int(row["kind_word"])
        rows.append(
            {
                "absolute_end": int(row["absolute_end"]),
                "absolute_start": int(row["absolute_start"]),
                "index": index,
                "kind_class_high16": kind_word >> 16,
                "kind_flags_low16": kind_word & 0xFFFF,
                "kind_word": kind_word,
                "size": int(row["size"]),
            }
        )

    report_chunks = _require_list(classifier_report.get("chunks"), "classifier chunks")
    dic_entries: list[dict[str, object]] = []
    for raw_chunk in report_chunks:
        chunk = _require_dict(raw_chunk, "classifier chunk")
        if chunk.get("tag_ascii") != " DIC":
            continue
        dic = _require_dict(chunk.get("dic"), "classifier DIC")
        for raw_entry in _require_list(dic.get("entries"), "classifier DIC entries"):
            entry = _require_dict(raw_entry, "classifier DIC entry")
            dic_entries.append(
                {
                    "absolute_offset": int(entry["absolute_offset"]),
                    "chunk_index": int(chunk["index"]),
                    "dic_entry_index": int(entry["index"]),
                    "hash_word_hex": str(entry["hash_word_hex"]),
                    "relative_offset": int(entry["relative_offset"]),
                }
            )
    if not dic_entries:
        raise probe.ProbeError("selected row contains no DIC entries to fingerprint")
    if len(dic_entries) > max_entries:
        raise probe.ProbeError(f"DIC population exceeds {max_entries} entries")

    target_counts = Counter(int(entry["absolute_offset"]) for entry in dic_entries)
    sorted_targets = sorted(target_counts)
    neighbors: dict[int, tuple[int | None, int | None]] = {}
    for index, target in enumerate(sorted_targets):
        previous_delta = target - sorted_targets[index - 1] if index > 0 else None
        next_delta = sorted_targets[index + 1] - target if index + 1 < len(sorted_targets) else None
        neighbors[target] = (previous_delta, next_delta)

    observations: list[dict[str, object]] = []
    with ExitStack() as stack:
        stream = _open_regular(stack, source)
        before = os.fstat(stream.fileno())
        if before.st_size != source_size:
            raise probe.ProbeError("source size changed after inherited classification")
        if _hash_stream(stream) != expected_sha256:
            raise probe.ProbeError("source hash changed after inherited classification")

        for entry in dic_entries:
            target = int(entry["absolute_offset"])
            owners = [row for row in rows if row["absolute_start"] <= target < row["absolute_end"]]
            if len(owners) != 1:
                raise probe.ProbeError(
                    f"DIC entry {entry['dic_entry_index']} target does not belong to exactly one row"
                )
            owner = owners[0]
            if target - PREDECESSOR_BYTES < owner["absolute_start"]:
                raise probe.ProbeError(
                    f"DIC entry {entry['dic_entry_index']} has no complete predecessor window"
                )
            if target + TARGET_BYTES > owner["absolute_end"]:
                raise probe.ProbeError(
                    f"DIC entry {entry['dic_entry_index']} has no complete target window"
                )
            predecessor = _read_exact_at(
                stream, target - PREDECESSOR_BYTES, PREDECESSOR_BYTES, "predecessor window"
            )
            target_window = _read_exact_at(stream, target, TARGET_BYTES, "target window")
            predecessor_words = struct.unpack("<QQ", predecessor)
            target_words = struct.unpack("<QQ", target_window[:16])
            previous_delta, next_delta = neighbors[target]
            observations.append(
                {
                    "alias_count": target_counts[target],
                    "alignment": {
                        "absolute_mod_16": target % 16,
                        "absolute_mod_32": target % 32,
                        "absolute_mod_96": target % 96,
                    },
                    "containing_row": owner,
                    "dic": entry,
                    "neighbors": {
                        "next_unique_delta": next_delta,
                        "previous_unique_delta": previous_delta,
                    },
                    "predecessor": {
                        "bytes": PREDECESSOR_BYTES,
                        "opaque_u64_le_hex": [f"{word:016x}" for word in predecessor_words],
                        "sha256": hashlib.sha256(predecessor).hexdigest(),
                        "zero_bytes": predecessor.count(0),
                    },
                    "target": {
                        "absolute_offset": target,
                        "bytes": TARGET_BYTES,
                        "offset_in_row": target - owner["absolute_start"],
                        "opaque_first_u64_le_hex": [f"{word:016x}" for word in target_words],
                        "sha256": hashlib.sha256(target_window).hexdigest(),
                        "zero_bytes": target_window.count(0),
                    },
                }
            )

        after_hash = _hash_stream(stream)
        after = os.fstat(stream.fileno())
        if after_hash != expected_sha256 or _identity(after) != _identity(before):
            raise probe.ProbeError("source changed during target fingerprinting")

    selected_row = _require_dict(classifier_report.get("selected_row"), "selected classifier row")
    return {
        "facts": {
            "all_targets_have_fixed_windows": True,
            "all_targets_owned_by_exactly_one_row": True,
            "distinct_dic_hash_words": len({str(entry["hash_word_hex"]) for entry in dic_entries}),
            "distinct_target_offsets": len(sorted_targets),
            "max_alias_count": max(target_counts.values(), default=0),
            "total_dic_entries": len(dic_entries),
        },
        "non_claims": list(NON_CLAIMS),
        "observations": observations,
        "proof_class": PROOF_CLASS,
        "schema": SCHEMA,
        "selected_dic_row": selected_row,
        "source": {
            "basename": str(structure_input["basename"]),
            "bytes": source_size,
            "sha256": expected_sha256,
        },
        "version": SCHEMA_VERSION,
        "warnings": [],
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fingerprint bounded DIC target headers in one owned Second Son XPPS file."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--row", required=True, type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = classify_targets(
            args.input,
            expected_sha256=args.expected_sha256,
            row_index=args.row,
        )
        sys.stdout.buffer.write(encode_report(report))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_targets: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
