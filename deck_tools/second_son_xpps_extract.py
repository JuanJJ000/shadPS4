#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Extract one hash-guarded candidate row from an owned Second Son XPPS file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_xpps_extract"
SCHEMA_VERSION = 1
PROOF_CLASS = "bounded_extraction"
COPY_CHUNK_BYTES = 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
NON_CLAIMS = (
    "compression",
    "object_boundaries",
    "resource_identity",
    "runtime_acceptance",
    "safe_replacement",
    "texture_format",
)


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(COPY_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _copy_range(
    source: BinaryIO, destination: BinaryIO, *, absolute_start: int, size: int
) -> str:
    digest = hashlib.sha256()
    source.seek(absolute_start)
    remaining = size
    while remaining:
        chunk = source.read(min(COPY_CHUNK_BYTES, remaining))
        if not chunk:
            raise probe.ProbeError(
                f"short read while extracting: {remaining} bytes remained"
            )
        destination.write(chunk)
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _validate_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise probe.ProbeError(f"refusing to replace existing output: {path}")
    if not path.parent.is_dir():
        raise probe.ProbeError(f"output parent does not exist: {path.parent}")


def extract_row(
    source: Path, *, expected_sha256: str, row_index: int, output: Path
) -> dict[str, object]:
    source = Path(source)
    output = Path(output)
    if SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise probe.ProbeError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    if row_index < 0:
        raise probe.ProbeError("row index must be nonnegative")
    _validate_output(output)

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
    absolute_start = int(row["absolute_start"])
    absolute_end = int(row["absolute_end"])
    size = int(row["size"])
    source_size = int(source_info["bytes"])
    if absolute_start < 0 or absolute_end != absolute_start + size:
        raise probe.ProbeError("selected row has an inconsistent absolute range")
    if absolute_end > source_size:
        raise probe.ProbeError("selected row exceeds the source")

    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        temporary = Path(temporary_name)
        with source.open("rb") as source_stream, os.fdopen(descriptor, "wb") as output_stream:
            source_stat_before = os.fstat(source_stream.fileno())
            if source_stat_before.st_size != source_size:
                raise probe.ProbeError("source size changed after structure validation")
            payload_sha256 = _copy_range(
                source_stream,
                output_stream,
                absolute_start=absolute_start,
                size=size,
            )
            output_stream.flush()
            os.fsync(output_stream.fileno())
            source_sha256_after = _hash_stream(source_stream)
            source_stat_after = os.fstat(source_stream.fileno())
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
                raise probe.ProbeError("source changed between validation and publication")

        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, output)
        except FileExistsError as error:
            raise probe.ProbeError(f"refusing to replace existing output: {output}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return {
        "non_claims": list(NON_CLAIMS),
        "output": {
            "basename": output.name,
            "bytes": size,
            "sha256": payload_sha256,
        },
        "proof_class": PROOF_CLASS,
        "schema": SCHEMA,
        "selected_row": {
            "absolute_end": absolute_end,
            "absolute_start": absolute_start,
            "index": row_index,
            "kind_word": int(row["kind_word"]),
            "relative_offset": int(row["relative_offset"]),
            "size": size,
        },
        "source": {
            "basename": str(source_info["basename"]),
            "bytes": source_size,
            "sha256": expected_sha256,
        },
        "version": SCHEMA_VERSION,
        "warnings": [],
    }


def encode_manifest(manifest: dict[str, object]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract one hash-guarded candidate row from an owned Second Son XPPS file. "
            "The source is never modified and the deterministic manifest is written to stdout."
        )
    )
    parser.add_argument("input", type=Path, help="regular .xpps source")
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--row", required=True, type=int, help="zero-based candidate row")
    parser.add_argument("--output", required=True, type=Path, help="new payload output file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = extract_row(
            args.input,
            expected_sha256=args.expected_sha256,
            row_index=args.row,
            output=args.output,
        )
        sys.stdout.buffer.write(encode_manifest(manifest))
    except (OSError, probe.ProbeError) as error:
        print(f"second_son_xpps_extract: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
