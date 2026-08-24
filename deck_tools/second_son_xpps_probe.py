#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read-only structural probe for owned inFAMOUS Second Son XPPS files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

SCHEMA = "shadps4.second_son_xpps_probe"
SCHEMA_VERSION = 1
PROOF_CLASS = "structure_probe"
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_FILES = 2048
MAX_ROWS_PER_FILE = 4096
MAX_TOTAL_ROWS = 32768
MAX_TAG_OFFSETS_PER_FILE = 256
MAX_TOTAL_TAG_OFFSETS = 32768
HASH_CHUNK_BYTES = 1024 * 1024
TABLE_ROW_BYTES = 40
PACKAGE_FIXED_BYTES = 48
MIN_FILE_BYTES = 64
TAGS = (b" DIC", b"KCAP", b"NAMS", b"PACK", b"SPS ")
NON_CLAIMS = (
    "chunk_semantics",
    "compression",
    "cross_title_compatibility",
    "object_identity",
    "runtime_acceptance",
    "safe_replacement",
    "texture_format",
)


class ProbeError(ValueError):
    """An input violates the bounded structure-probe contract."""


def _read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ProbeError(f"truncated {label}: expected {size} bytes, got {len(data)}")
    return data


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _scan_tags(path: Path) -> dict[str, list[int]]:
    offsets = {tag.decode("ascii"): [] for tag in TAGS}
    longest = max(map(len, TAGS))
    absolute = 0
    carry = b""
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            window = carry + chunk
            window_start = absolute - len(carry)
            for tag in TAGS:
                cursor = 0
                while len(offsets[tag.decode("ascii")]) < MAX_TAG_OFFSETS_PER_FILE:
                    found = window.find(tag, cursor)
                    if found < 0:
                        break
                    observed = window_start + found
                    if observed >= 0 and (not carry or found + len(tag) > len(carry)):
                        offsets[tag.decode("ascii")].append(observed)
                    cursor = found + 1
            absolute += len(chunk)
            carry = window[-(longest - 1) :] if longest > 1 else b""
    return {tag: values for tag, values in sorted(offsets.items()) if values}


def _regular_xpps(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise ProbeError(f"symlink inputs are refused: {path.name}")
    try:
        stat_result = path.stat()
    except FileNotFoundError as error:
        raise ProbeError(f"input does not exist: {path}") from error
    if not path.is_file():
        raise ProbeError(f"input is not a regular file: {path}")
    if path.suffix.lower() != ".xpps":
        raise ProbeError(f"input does not have the .xpps suffix: {path.name}")
    if stat_result.st_size > MAX_FILE_BYTES:
        raise ProbeError(
            f"input exceeds {MAX_FILE_BYTES} byte contract limit: {path.name}"
        )
    if stat_result.st_size < MIN_FILE_BYTES:
        raise ProbeError(f"input is too small for a PACK header: {path.name}")
    return stat_result


def probe_file(path: Path) -> dict[str, object]:
    path = Path(path)
    stat_result = _regular_xpps(path)
    file_size = stat_result.st_size

    with path.open("rb") as stream:
        header = _read_exact(stream, 64, "fixed header")
        if header[:4] != b"KCAP":
            raise ProbeError(
                f"wrong magic for {path.name}: expected KCAP, got {header[:4].hex()}"
            )

        header_words = list(struct.unpack_from("<15I", header, 4))
        package_header_offset = struct.unpack_from("<I", header, 24)[0]
        package_header_extent = struct.unpack_from("<I", header, 28)[0]
        data_start = struct.unpack_from("<I", header, 40)[0]
        data_size = struct.unpack_from("<I", header, 44)[0]

        if package_header_offset < 64:
            raise ProbeError("package header overlaps the fixed header")
        if package_header_offset + PACKAGE_FIXED_BYTES > file_size:
            raise ProbeError("package header is outside the input")
        if package_header_extent < PACKAGE_FIXED_BYTES:
            raise ProbeError("package header extent is smaller than its fixed prefix")

        package_end = package_header_offset + package_header_extent
        expected_package_end = data_start if data_size else file_size
        if package_end != expected_package_end:
            raise ProbeError(
                "package header extent does not end at the declared data/file boundary"
            )
        if data_size:
            if data_start < package_end:
                raise ProbeError("data starts inside the package header")
            if data_start + data_size != file_size:
                raise ProbeError("declared data does not exactly cover the file tail")
        elif data_start != 0:
            raise ProbeError("empty data region has a nonzero start")

        stream.seek(package_header_offset)
        package_words = list(
            struct.unpack("<12I", _read_exact(stream, PACKAGE_FIXED_BYTES, "package header"))
        )
        row_count = package_words[2]
        if row_count > MAX_ROWS_PER_FILE:
            raise ProbeError(
                f"candidate table exceeds {MAX_ROWS_PER_FILE} rows per file"
            )
        table_start = package_header_offset + PACKAGE_FIXED_BYTES
        table_size = row_count * TABLE_ROW_BYTES
        table_end = table_start + table_size
        if table_end > package_end:
            raise ProbeError("candidate table exceeds the package header extent")
        if row_count and not data_size:
            raise ProbeError("nonempty candidate table has no data region")

        rows: list[dict[str, object]] = []
        ranges: list[tuple[int, int, int]] = []
        stream.seek(table_start)
        for index in range(row_count):
            words = list(
                struct.unpack(
                    "<10I", _read_exact(stream, TABLE_ROW_BYTES, f"table row {index}")
                )
            )
            kind, size, relative_offset = words[:3]
            absolute_start = data_start + relative_offset
            absolute_end = absolute_start + size
            if relative_offset > data_size or size > data_size - relative_offset:
                raise ProbeError(f"table row {index} exceeds the declared data region")
            if absolute_end > file_size:
                raise ProbeError(f"table row {index} exceeds the input")
            rows.append(
                {
                    "absolute_end": absolute_end,
                    "absolute_start": absolute_start,
                    "index": index,
                    "kind_word": kind,
                    "opaque_words": words[3:],
                    "relative_offset": relative_offset,
                    "size": size,
                }
            )
            ranges.append((absolute_start, absolute_end, index))

    ordered = sorted(ranges)
    overlaps: list[tuple[int, int]] = []
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            overlaps.append((previous[2], current[2]))
    if overlaps:
        joined = ", ".join(f"{left}/{right}" for left, right in overlaps)
        raise ProbeError(f"candidate table rows overlap: {joined}")

    contiguous = True
    exact_coverage = not rows and data_size == 0
    if ordered:
        contiguous = ordered[0][0] == data_start
        contiguous &= all(left[1] == right[0] for left, right in zip(ordered, ordered[1:]))
        exact_coverage = contiguous and ordered[-1][1] == data_start + data_size

    return {
        "candidate_layout": {
            "data_size": data_size,
            "data_start": data_start,
            "package_header_extent": package_header_extent,
            "package_header_offset": package_header_offset,
            "package_header_words_le": package_words,
            "payload_rows": rows,
            "row_count": row_count,
            "row_size": TABLE_ROW_BYTES,
            "table_end": table_end,
            "table_start": table_start,
        },
        "facts": {
            "payload_ranges_contiguous": contiguous,
            "payload_ranges_exactly_cover_data": exact_coverage,
            "payload_ranges_non_overlapping": True,
        },
        "header_words_le_after_magic": header_words,
        "input": {
            "basename": path.name,
            "bytes": file_size,
            "sha256": _hash_file(path),
        },
        "magic_ascii": "KCAP",
        "structural_tag_offsets": _scan_tags(path),
        "warnings": [],
    }


def collect_inputs(path: Path, *, max_files: int = MAX_FILES) -> list[Path]:
    path = Path(path)
    if path.is_symlink():
        raise ProbeError(f"symlink inputs are refused: {path.name}")
    if path.is_file():
        _regular_xpps(path)
        return [path]
    if not path.is_dir():
        raise ProbeError(f"input is neither a regular XPPS file nor a directory: {path}")

    candidates: list[Path] = []
    with os.scandir(path) as entries:
        for entry in entries:
            if Path(entry.name).suffix.lower() != ".xpps":
                continue
            candidate = Path(entry.path)
            if entry.is_symlink():
                raise ProbeError(f"symlink inputs are refused: {entry.name}")
            if not entry.is_file(follow_symlinks=False):
                raise ProbeError(f"non-regular XPPS input is refused: {entry.name}")
            candidates.append(candidate)
            if len(candidates) > max_files:
                raise ProbeError(f"directory exceeds {max_files} XPPS files")
    if not candidates:
        raise ProbeError("directory contains no .xpps files")
    candidates.sort(key=lambda item: (item.name.casefold(), item.name))
    return candidates


def build_report(
    path: Path,
    *,
    max_files: int = MAX_FILES,
    max_total_rows: int = MAX_TOTAL_ROWS,
    max_total_tag_offsets: int = MAX_TOTAL_TAG_OFFSETS,
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    total_rows = 0
    total_tag_offsets = 0
    for candidate in collect_inputs(path, max_files=max_files):
        result = probe_file(candidate)
        candidate_layout = result["candidate_layout"]
        structural_tag_offsets = result["structural_tag_offsets"]
        assert isinstance(candidate_layout, dict)
        assert isinstance(structural_tag_offsets, dict)
        total_rows += int(candidate_layout["row_count"])
        total_tag_offsets += sum(len(offsets) for offsets in structural_tag_offsets.values())
        if total_rows > max_total_rows:
            raise ProbeError(f"input population exceeds {max_total_rows} total table rows")
        if total_tag_offsets > max_total_tag_offsets:
            raise ProbeError(
                f"input population exceeds {max_total_tag_offsets} total tag offsets"
            )
        files.append(result)
    return {
        "files": files,
        "non_claims": list(NON_CLAIMS),
        "proof_class": PROOF_CLASS,
        "schema": SCHEMA,
        "version": SCHEMA_VERSION,
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_new(path: Path, data: bytes) -> None:
    path = Path(path)
    parent = path.parent
    if not parent.is_dir():
        raise ProbeError(f"output parent does not exist: {parent}")
    if path.exists() or path.is_symlink():
        raise ProbeError(f"refusing to replace existing output: {path}")

    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise ProbeError(f"refusing to replace existing output: {path}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate candidate PACK table ranges in one owned Second Son .xpps file "
            "or a non-recursive directory. Inputs are never modified."
        )
    )
    parser.add_argument("input", type=Path, help="regular .xpps file or directory")
    parser.add_argument(
        "--output", type=Path, help="create a new JSON report instead of writing stdout"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        encoded = encode_report(build_report(args.input))
        if args.output is None:
            sys.stdout.buffer.write(encoded)
        else:
            write_new(args.output, encoded)
    except (OSError, ProbeError, struct.error) as error:
        print(f"second_son_xpps_probe: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
