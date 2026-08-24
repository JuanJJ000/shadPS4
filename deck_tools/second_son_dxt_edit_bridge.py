#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Normalize strict ImageMagick DXT1/DXT5 edits to proven DX10 DDS containers."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import struct
import sys
from pathlib import Path

import second_son_xpps_dds_export as dds
import second_son_xpps_dds_overlay as overlay
import second_son_xpps_eboot_registry as registry
import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_dxt_edit_bridge"
SCHEMA_VERSION = 1
PROOF_CLASS = "dxt_edit_bridge"
LEGACY_HEADER_BYTES = len(dds.DDS_MAGIC) + dds.DDS_HEADER.size
IMAGEMAGICK_RESERVED = b"IMAGEMAGICK\x00" + bytes(32)
MAX_EDIT_FILES = 256
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_RECEIPT_BYTES = 16 * 1024 * 1024
LEGACY_FORMATS = {
    35: (struct.unpack("<I", b"DXT1")[0], "DXT1"),
    37: (struct.unpack("<I", b"DXT5")[0], "DXT5"),
}
NON_CLAIMS = (
    "encoder_quality",
    "alpha_semantics",
    "color_space",
    "decoded_visual_correctness",
    "bc4_or_bc5_encoding",
    "runtime_activation",
    "game_runtime_behavior",
)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_dict(value: object, label: str) -> dict[str, object]:
    return dds._require_dict(value, label)


def _require_list(value: object, label: str) -> list[object]:
    return dds._require_list(value, label)


def _require_int(value: object, label: str) -> int:
    return dds._require_int(value, label)


def _require_string(value: object, label: str) -> str:
    return dds._require_string(value, label)


def _change_stats(before: bytes, after: bytes) -> tuple[int, int]:
    return overlay._change_stats(before, after)


def _parse_legacy_dxt(data: bytes, baseline: dict[str, object]) -> dict[str, object]:
    if len(data) < LEGACY_HEADER_BYTES or data[:4] != dds.DDS_MAGIC:
        raise probe.ProbeError("legacy DDS is truncated or has the wrong magic")
    fields = dds.DDS_HEADER.unpack_from(data, len(dds.DDS_MAGIC))
    baseline_format = _require_int(baseline.get("format_id"), "baseline format ID")
    legacy_format = LEGACY_FORMATS.get(baseline_format)
    if legacy_format is None:
        raise probe.ProbeError("baseline DDS is not bridgeable BC1 or BC3")
    fourcc, fourcc_name = legacy_format
    width = _require_int(baseline.get("width"), "baseline width")
    height = _require_int(baseline.get("height"), "baseline height")
    mip_count = _require_int(baseline.get("mip_count"), "baseline mip count")
    baseline_mips = _require_list(baseline.get("mips"), "baseline mips")
    if not baseline_mips or len(baseline_mips) != mip_count:
        raise probe.ProbeError("baseline DDS mip population is inconsistent")
    top_bytes = _require_int(
        _require_dict(baseline_mips[0], "baseline top mip").get("bytes"),
        "baseline top mip bytes",
    )
    expected_flags = (
        dds.DDSD_CAPS
        | dds.DDSD_HEIGHT
        | dds.DDSD_WIDTH
        | dds.DDSD_PIXELFORMAT
        | dds.DDSD_LINEARSIZE
    )
    expected_caps = dds.DDSCAPS_TEXTURE
    if mip_count > 1:
        expected_flags |= dds.DDSD_MIPMAPCOUNT
        expected_caps |= dds.DDSCAPS_COMPLEX | dds.DDSCAPS_MIPMAP
    if (
        fields[0] != dds.DDS_HEADER_SIZE
        or fields[1] != expected_flags
        or fields[2] != height
        or fields[3] != width
        or fields[4] != top_bytes
        or fields[5] != 0
        or fields[6] != mip_count
    ):
        raise probe.ProbeError("legacy DDS dimensions, flags, mips, or linear size changed")
    reserved = data[32:76]
    if reserved != IMAGEMAGICK_RESERVED:
        raise probe.ProbeError("legacy DDS lacks the exact ImageMagick reserved signature")
    if (
        fields[18] != dds.DDS_PIXEL_FORMAT_SIZE
        or fields[19] != dds.DDPF_FOURCC
        or fields[20] != fourcc
        or any(fields[index] for index in range(21, 26))
        or fields[26] != expected_caps
        or any(fields[index] for index in range(27, 31))
    ):
        raise probe.ProbeError("legacy DDS format, caps, face, or volume fields changed")
    payload_bytes = sum(
        _require_int(_require_dict(raw, "baseline mip").get("bytes"), "mip bytes")
        for raw in baseline_mips
    )
    expected_file_bytes = LEGACY_HEADER_BYTES + payload_bytes
    if len(data) < expected_file_bytes:
        raise probe.ProbeError("legacy DDS mip payload is truncated")
    if len(data) > expected_file_bytes:
        raise probe.ProbeError("legacy DDS has trailing bytes")
    public_mips: list[dict[str, int]] = []
    offset = LEGACY_HEADER_BYTES
    for raw in baseline_mips:
        mip = _require_dict(raw, "baseline mip")
        size = _require_int(mip.get("bytes"), "baseline mip bytes")
        public_mips.append(
            {
                "bytes": size,
                "file_offset_end": offset + size,
                "file_offset_start": offset,
                "index": _require_int(mip.get("index"), "baseline mip index"),
            }
        )
        offset += size
    if offset != len(data):
        raise probe.ProbeError("legacy DDS mip ranges do not end at EOF")
    return {
        "fourcc": fourcc_name,
        "format_id": baseline_format,
        "header_bytes": LEGACY_HEADER_BYTES,
        "height": height,
        "mip_count": mip_count,
        "mips": public_mips,
        "payload_bytes": payload_bytes,
        "width": width,
    }


def _publish(output_dir: Path, files: dict[str, bytes]) -> None:
    parent_fd: int | None = None
    output_fd: int | None = None
    output_name = ""
    output_identity = (0, 0)
    created_names: list[str] = []
    identities: dict[str, tuple[int, int, int, int, int]] = {}
    guards: dict[str, int] = {}
    try:
        parent_fd, output_fd, output_name, output_identity = (
            dds._open_fresh_output_directory(Path(output_dir))
        )
        for name in sorted(files):
            observed = dds._write_exclusive(
                output_fd,
                name,
                files[name],
                created_names,
                identities,
                guards,
            )
            if observed != files[name]:
                raise probe.ProbeError("normalized DDS changed during guarded write")
            dds._parse_dds(observed)
        if sorted(dds._list_output_names(output_fd)) != sorted(files):
            raise probe.ProbeError("normalized DDS output population is not exact")
        for name, identity in identities.items():
            path_info = os.stat(name, dir_fd=output_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(path_info.st_mode)
                or registry._identity(path_info) != identity
                or registry._identity(os.fstat(guards[name])) != identity
            ):
                raise probe.ProbeError("normalized DDS path binding changed")
        if not dds._output_binding_matches(
            parent_fd, output_fd, output_name, output_identity
        ):
            raise probe.ProbeError("normalized DDS directory path binding changed")
    except BaseException as error:
        cleanup_error = None
        if parent_fd is not None and output_fd is not None:
            cleanup_error = dds._cleanup_fresh_output(
                parent_fd,
                output_fd,
                output_name,
                output_identity,
                created_names,
                identities,
                guards,
            )
        if cleanup_error:
            raise probe.ProbeError(
                f"{error}; normalized DDS cleanup incomplete: {cleanup_error}"
            ) from error
        raise
    finally:
        for descriptor in guards.values():
            try:
                os.close(descriptor)
            except OSError:
                pass
        if output_fd is not None:
            os.close(output_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def bridge_edits(
    export_manifest: Path,
    *,
    expected_manifest_sha256: str,
    encoded_dir: Path,
    output_dir: Path,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> dict[str, object]:
    registry._validate_sha256(
        expected_manifest_sha256, "expected DDS manifest SHA-256"
    )
    if max_total_bytes < 1 or max_total_bytes > MAX_TOTAL_BYTES:
        raise probe.ProbeError(
            f"total byte budget must be from 1 through {MAX_TOTAL_BYTES}"
        )
    manifest_path = Path(export_manifest)
    if manifest_path.name != dds.MANIFEST_NAME:
        raise probe.ProbeError("export manifest basename must be manifest.json")
    baseline_fd: int | None = None
    encoded_fd: int | None = None
    try:
        baseline_fd, baseline_identity = overlay._open_input_directory(
            manifest_path.parent, "DDS export directory"
        )
        encoded_fd, encoded_identity = overlay._open_input_directory(
            Path(encoded_dir), "encoded DDS directory"
        )
        manifest_data = overlay._read_regular_at(
            baseline_fd,
            dds.MANIFEST_NAME,
            dds.MAX_MANIFEST_BYTES,
            "DDS export manifest",
        )
        if _hash(manifest_data) != expected_manifest_sha256:
            raise probe.ProbeError("DDS export manifest has the wrong expected hash")
        manifest = overlay._parse_canonical_json(manifest_data, "DDS export manifest")
        if (
            manifest.get("schema") != dds.SCHEMA
            or manifest.get("version") != dds.SCHEMA_VERSION
            or manifest.get("proof_class") != dds.PROOF_CLASS
        ):
            raise probe.ProbeError("DDS export manifest has the wrong exact contract")
        file_items: dict[str, dict[str, object]] = {}
        baseline_files: dict[str, bytes] = {}
        expected_baseline_names = [dds.MANIFEST_NAME]
        for raw in _require_list(manifest.get("files"), "DDS manifest files"):
            item = _require_dict(raw, "DDS manifest file")
            name = _require_string(item.get("basename"), "DDS basename")
            if name in file_items:
                raise probe.ProbeError("DDS export manifest repeats a basename")
            size = _require_int(item.get("bytes"), "baseline DDS bytes")
            expected_sha = _require_string(item.get("sha256"), "baseline DDS SHA-256")
            registry._validate_sha256(expected_sha, "baseline DDS SHA-256")
            data = overlay._read_regular_at(
                baseline_fd, name, size, f"baseline DDS {name}"
            )
            if len(data) != size or _hash(data) != expected_sha:
                raise probe.ProbeError(f"baseline DDS {name} disagrees with its manifest")
            parsed = dds._parse_dds(data)
            if {key: value for key, value in parsed.items() if key != "mips"} != item.get(
                "dds"
            ):
                raise probe.ProbeError(f"baseline DDS {name} structure changed")
            file_items[name] = item
            baseline_files[name] = data
            expected_baseline_names.append(name)
        if overlay._list_names(baseline_fd, "DDS export directory") != sorted(
            expected_baseline_names
        ):
            raise probe.ProbeError("DDS export directory population is not exact")
        encoded_names = overlay._list_names(encoded_fd, "encoded DDS directory")
        if (
            not encoded_names
            or len(encoded_names) > MAX_EDIT_FILES
            or any(name not in baseline_files for name in encoded_names)
        ):
            raise probe.ProbeError("encoded DDS population is empty, unknown, or too large")
        normalized_files: dict[str, bytes] = {}
        public_files: list[dict[str, object]] = []
        total_input_bytes = 0
        total_changed_bytes = 0
        total_changed_ranges = 0
        for name in encoded_names:
            baseline_data = baseline_files[name]
            baseline_parsed = dds._parse_dds(baseline_data)
            payload_bytes = _require_int(
                baseline_parsed.get("payload_bytes"), "baseline DDS payload bytes"
            )
            maximum = LEGACY_HEADER_BYTES + payload_bytes
            if total_input_bytes + maximum > max_total_bytes:
                raise probe.ProbeError("encoded DDS files exceed the total byte budget")
            encoded = overlay._read_regular_at(
                encoded_fd, name, maximum, f"encoded DDS {name}"
            )
            total_input_bytes += len(encoded)
            legacy = _parse_legacy_dxt(encoded, baseline_parsed)
            normalized = baseline_data[: dds.DDS_HEADER_BYTES] + encoded[
                LEGACY_HEADER_BYTES:
            ]
            normalized_parsed = dds._parse_dds(normalized)
            if normalized_parsed != baseline_parsed:
                raise probe.ProbeError(f"normalized DDS {name} changed its strict structure")
            changed_bytes, changed_ranges = _change_stats(baseline_data, normalized)
            if not changed_bytes:
                raise probe.ProbeError(f"encoded DDS {name} is byte-identical to baseline")
            mip_receipts: list[dict[str, object]] = []
            for base_raw, legacy_raw in zip(
                _require_list(baseline_parsed.get("mips"), "baseline mips"),
                _require_list(legacy.get("mips"), "legacy mips"),
                strict=True,
            ):
                base_mip = _require_dict(base_raw, "baseline mip")
                legacy_mip = _require_dict(legacy_raw, "legacy mip")
                base_start = _require_int(base_mip.get("file_offset_start"), "base mip start")
                base_end = _require_int(base_mip.get("file_offset_end"), "base mip end")
                edit_start = _require_int(
                    legacy_mip.get("file_offset_start"), "legacy mip start"
                )
                edit_end = _require_int(
                    legacy_mip.get("file_offset_end"), "legacy mip end"
                )
                before = baseline_data[base_start:base_end]
                after = encoded[edit_start:edit_end]
                mip_changed, mip_ranges = _change_stats(before, after)
                mip_receipts.append(
                    {
                        "bytes": len(before),
                        "changed_bytes": mip_changed,
                        "changed_ranges": mip_ranges,
                        "index": _require_int(base_mip.get("index"), "mip index"),
                        "normalized_sha256": _hash(after),
                        "source_sha256": _hash(before),
                    }
                )
            if sum(_require_int(item.get("changed_bytes"), "mip changed bytes") for item in mip_receipts) != changed_bytes:
                raise probe.ProbeError("normalized DDS changed-byte count is inconsistent")
            normalized_files[name] = normalized
            public_files.append(
                {
                    "basename": name,
                    "baseline_sha256": _hash(baseline_data),
                    "changed_bytes": changed_bytes,
                    "changed_ranges": changed_ranges,
                    "legacy": legacy,
                    "legacy_sha256": _hash(encoded),
                    "mips": mip_receipts,
                    "normalized_sha256": _hash(normalized),
                }
            )
            total_changed_bytes += changed_bytes
            total_changed_ranges += changed_ranges
        if not overlay._directory_binding_matches(
            manifest_path.parent, baseline_fd, baseline_identity
        ):
            raise probe.ProbeError("DDS export directory path binding changed")
        if not overlay._directory_binding_matches(
            Path(encoded_dir), encoded_fd, encoded_identity
        ):
            raise probe.ProbeError("encoded DDS directory path binding changed")
        receipt = {
            "facts": {
                "edit_file_count": len(public_files),
                "total_changed_bytes": total_changed_bytes,
                "total_changed_ranges": total_changed_ranges,
                "total_legacy_bytes": total_input_bytes,
                "total_normalized_bytes": sum(len(data) for data in normalized_files.values()),
            },
            "files": public_files,
            "inherited_dds_export": {
                "manifest_sha256": expected_manifest_sha256,
                "schema": dds.SCHEMA,
                "version": dds.SCHEMA_VERSION,
            },
            "non_claims": list(NON_CLAIMS),
            "proof_class": PROOF_CLASS,
            "schema": SCHEMA,
            "version": SCHEMA_VERSION,
            "warnings": [],
        }
        receipt_data = encode_receipt(receipt)
        if len(receipt_data) > MAX_RECEIPT_BYTES:
            raise probe.ProbeError("DXT bridge receipt exceeds its byte limit")
        _publish(Path(output_dir), normalized_files)
        return receipt
    finally:
        if encoded_fd is not None:
            os.close(encoded_fd)
        if baseline_fd is not None:
            os.close(baseline_fd)


def encode_receipt(receipt: dict[str, object]) -> bytes:
    return overlay.encode_receipt(receipt)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize strict ImageMagick DXT1/DXT5 edits to proven DX10 DDS."
    )
    parser.add_argument("--export-manifest", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--encoded-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = bridge_edits(
            args.export_manifest,
            expected_manifest_sha256=args.expected_manifest_sha256,
            encoded_dir=args.encoded_dir,
            output_dir=args.output_dir,
        )
        sys.stdout.buffer.write(encode_receipt(receipt))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_dxt_edit_bridge: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
