#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build a reversible Second Son XPPS copy from strictly compatible edited DDS."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

import second_son_xpps_dds_export as dds
import second_son_xpps_eboot_registry as registry
import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_xpps_dds_overlay"
SCHEMA_VERSION = 1
PROOF_CLASS = "xpps_dds_overlay"
OVERLAY_NAME = "overlay.xpps"
RECEIPT_NAME = "receipt.json"
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_EBOOT_BYTES = 128 * 1024 * 1024
MAX_EDIT_BYTES = 512 * 1024 * 1024
MAX_CHANGED_BYTES = 512 * 1024 * 1024
MAX_CHANGED_RANGES = 1_000_000
MAX_RECEIPT_BYTES = 16 * 1024 * 1024
NON_CLAIMS = (
    "artwork_identity",
    "channel_swizzle",
    "alpha_semantics",
    "color_space",
    "decoded_visual_correctness",
    "png_encoding",
    "compression_encoder_equivalence",
    "arbitrary_xpps_support",
    "in_place_retail_mutation",
    "runtime_activation",
    "game_runtime_behavior",
)


def _require_dict(value: object, label: str) -> dict[str, object]:
    return dds._require_dict(value, label)


def _require_list(value: object, label: str) -> list[object]:
    return dds._require_list(value, label)


def _require_int(value: object, label: str) -> int:
    return dds._require_int(value, label)


def _require_string(value: object, label: str) -> str:
    return dds._require_string(value, label)


def _validate_budget(value: int, maximum: int, label: str) -> None:
    if value < 1 or value > maximum:
        raise probe.ProbeError(f"{label} must be from 1 through {maximum}")


def _reject_constant(value: str) -> object:
    raise probe.ProbeError(f"JSON contains non-finite constant {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise probe.ProbeError(f"JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _parse_canonical_json(data: bytes, label: str) -> dict[str, object]:
    try:
        text = data.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise probe.ProbeError(f"{label} is not strict UTF-8 JSON: {error}") from error
    result = _require_dict(value, label)
    if dds.encode_manifest(result) != data:
        raise probe.ProbeError(f"{label} is not the canonical sorted encoding")
    return result


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_input_directory(path: Path, label: str) -> tuple[int, tuple[int, int]]:
    try:
        descriptor = os.open(Path(path), _directory_flags())
    except OSError as error:
        raise probe.ProbeError(
            f"cannot open {label} as a nonsymlink directory: {error.strerror}"
        ) from error
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise probe.ProbeError(f"{label} is not a directory")
    return descriptor, (info.st_dev, info.st_ino)


def _directory_binding_matches(
    path: Path, descriptor: int, identity: tuple[int, int]
) -> bool:
    info = os.fstat(descriptor)
    if (info.st_dev, info.st_ino) != identity:
        return False
    try:
        path_info = os.stat(Path(path), follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISDIR(path_info.st_mode) and (
        path_info.st_dev,
        path_info.st_ino,
    ) == identity


def _list_names(descriptor: int, label: str) -> list[str]:
    try:
        names = os.listdir(descriptor)
    except OSError as error:
        raise probe.ProbeError(f"cannot list {label}: {error.strerror}") from error
    if any(not name or "/" in name or "\x00" in name for name in names):
        raise probe.ProbeError(f"{label} contains an invalid basename")
    return sorted(names)


def _read_regular_at(
    directory_fd: int, name: str, maximum: int, label: str
) -> bytes:
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise probe.ProbeError(f"{label} has an invalid basename")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot open {label} as a nonsymlink file: {error.strerror}"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise probe.ProbeError(f"{label} is not a regular file")
        if before.st_size < 1 or before.st_size > maximum:
            raise probe.ProbeError(f"{label} exceeds its 1 through {maximum}-byte limit")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            data = registry._read_bounded(stream, maximum, label)
        after = os.fstat(descriptor)
        try:
            path_info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as error:
            raise probe.ProbeError(f"cannot restat {label}: {error.strerror}") from error
        identity = registry._identity(before)
        if (
            len(data) != before.st_size
            or registry._identity(after) != identity
            or not stat.S_ISREG(path_info.st_mode)
            or registry._identity(path_info) != identity
        ):
            raise probe.ProbeError(f"{label} changed during its bounded read")
        return data
    finally:
        os.close(descriptor)


def _read_source(
    stream: BinaryIO,
    expected_sha256: str,
    maximum: int,
    label: str,
) -> tuple[bytes, os.stat_result]:
    before = os.fstat(stream.fileno())
    if before.st_size < 1 or before.st_size > maximum:
        raise probe.ProbeError(f"{label} exceeds its 1 through {maximum}-byte limit")
    data = registry._read_bounded(stream, maximum, label)
    if len(data) != before.st_size:
        raise probe.ProbeError(f"{label} bounded read is short")
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise probe.ProbeError(f"{label} has the wrong expected SHA-256")
    return data, before


def _source_is_unchanged(
    stream: BinaryIO, before: os.stat_result, expected_sha256: str, label: str
) -> None:
    if registry._hash_stream(stream) != expected_sha256:
        raise probe.ProbeError(f"{label} changed during overlay construction")
    if registry._identity(os.fstat(stream.fileno())) != registry._identity(before):
        raise probe.ProbeError(f"{label} identity changed during overlay construction")


def _build_expected_export(
    xpps_data: bytes,
    *,
    xpps_identity: dict[str, object],
    eboot_identity: dict[str, object],
    descriptors: list[dict[str, object]],
    proof_identity: dict[str, object],
    shader_contract: dict[str, object],
    host_coordinates: list[tuple[int, int]],
) -> tuple[dict[str, object], dict[str, bytes]]:
    files: list[dict[str, object]] = []
    file_bytes: dict[str, bytes] = {}
    total_file_bytes = 0
    total_payload_bytes = 0
    total_mips = 0
    for descriptor in descriptors:
        descriptor_index = _require_int(
            descriptor.get("descriptor_index"), "descriptor index"
        )
        format_id = _require_int(descriptor.get("format_id"), "format ID")
        format_name = _require_string(descriptor.get("format_name"), "format name")
        format_info = _require_dict(descriptor.get("format_info"), "DDS format")
        image_type_id = _require_int(
            descriptor.get("image_type_id"), "image type ID"
        )
        image_type = dds.IMAGE_TYPE_DIMENSIONS[image_type_id]
        width = _require_int(descriptor.get("width"), "descriptor width")
        height = _require_int(descriptor.get("height"), "descriptor height")
        element_bytes = _require_int(
            descriptor.get("element_bits"), "element width"
        ) // 8
        payload = bytearray()
        public_mips: list[dict[str, object]] = []
        tiled_chain = hashlib.sha256()
        linear_chain = hashlib.sha256()
        for raw_mip in _require_list(descriptor.get("mips"), "descriptor mips"):
            mip = _require_dict(raw_mip, "descriptor mip")
            mip_index = _require_int(mip.get("index"), "mip index")
            start = _require_int(mip.get("absolute_start"), "mip absolute start")
            size = _require_int(mip.get("bytes"), "mip bytes")
            _, end = dds._checked_u64_range(start, size, f"BITMAP mip {mip_index}")
            if end > len(xpps_data):
                raise probe.ProbeError(f"BITMAP mip {mip_index} exceeds the XPPS")
            tiled = xpps_data[start:end]
            if hashlib.sha256(tiled).hexdigest() != _require_string(
                mip.get("tiled_sha256"), "proven tiled mip SHA-256"
            ):
                raise probe.ProbeError(f"BITMAP mip {mip_index} tiled hash changed")
            tiled_chain.update(tiled)
            pitch = _require_int(mip.get("aligned_storage_pitch"), "padded pitch")
            padded_height = _require_int(
                mip.get("aligned_storage_height"), "padded height"
            )
            linear = dds.roundtrip._deswizzle_mip(
                tiled,
                pitch=pitch,
                height=padded_height,
                element_bytes=element_bytes,
                host_coordinates=host_coordinates,
            )
            linear_sha = hashlib.sha256(linear).hexdigest()
            if linear_sha != _require_string(
                mip.get("linear_padded_sha256"), "proven padded-linear SHA-256"
            ):
                raise probe.ProbeError(f"BITMAP mip {mip_index} linear hash changed")
            linear_chain.update(linear)
            logical_width = max(width >> mip_index, 1)
            logical_height = max(height >> mip_index, 1)
            cropped, geometry = dds._crop_logical_mip(
                linear,
                padded_pitch=pitch,
                padded_height=padded_height,
                logical_width=logical_width,
                logical_height=logical_height,
                format_info=format_info,
            )
            file_start = dds.DDS_HEADER_BYTES + len(payload)
            _, file_end = dds._checked_u64_range(
                file_start, len(cropped), f"DDS mip {mip_index}"
            )
            payload.extend(cropped)
            public_mips.append(
                {
                    "bytes": len(cropped),
                    "cropped_linear_sha256": hashlib.sha256(cropped).hexdigest(),
                    "element_height": geometry["element_height"],
                    "element_width": geometry["element_width"],
                    "file_offset_end": file_end,
                    "file_offset_start": file_start,
                    "height": logical_height,
                    "index": mip_index,
                    "padded_linear_sha256": linear_sha,
                    "padded_storage_height": padded_height,
                    "padded_storage_pitch": pitch,
                    "row_bytes": geometry["row_bytes"],
                    "width": logical_width,
                }
            )
        inherited_payload_sha = _require_string(
            descriptor.get("payload_sha256"), "payload SHA-256"
        )
        inherited_linear_sha = _require_string(
            descriptor.get("linear_padded_chain_sha256"),
            "padded-linear chain SHA-256",
        )
        if tiled_chain.hexdigest() != inherited_payload_sha:
            raise probe.ProbeError("BITMAP payload hash changed")
        if linear_chain.hexdigest() != inherited_linear_sha:
            raise probe.ProbeError("BITMAP padded-linear chain hash changed")
        header = dds._build_dds_header(
            width=width,
            height=height,
            mip_count=len(public_mips),
            top_mip_bytes=_require_int(public_mips[0].get("bytes"), "top mip bytes"),
            format_id=format_id,
            image_type_id=image_type_id,
        )
        data = header + payload
        parsed = dds._parse_dds(data)
        filename = (
            f"bitmap_{descriptor_index:03d}_"
            f"{_require_int(descriptor.get('payload_relative_start'), 'payload start'):08x}_"
            f"{format_info['filename_tag']}.dds"
        )
        file_bytes[filename] = data
        files.append(
            {
                "basename": filename,
                "bytes": len(data),
                "dds": {key: value for key, value in parsed.items() if key != "mips"},
                "descriptor": {
                    "data_relative_payload_start": descriptor.get(
                        "payload_relative_start"
                    ),
                    "image_type": {"id": image_type_id, "name": image_type[0]},
                    "sha256": descriptor.get("descriptor_sha256"),
                    "padded_linear_chain_sha256": inherited_linear_sha,
                    "tiled_payload_sha256": inherited_payload_sha,
                    "xpps_format": {"id": format_id, "name": format_name},
                },
                "mips": public_mips,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        total_file_bytes += len(data)
        total_payload_bytes += len(payload)
        total_mips += len(public_mips)
    manifest = {
        "eboot": eboot_identity,
        "facts": {
            "all_dds_files_strictly_parsed": True,
            "all_logical_crops_bound_to_padded_linear_hashes": True,
            "dds_file_count": len(files),
            "mip_count": total_mips,
            "total_dds_file_bytes": total_file_bytes,
            "total_dds_payload_bytes": total_payload_bytes,
        },
        "files": files,
        "host_shader_contract": shader_contract,
        "inherited_proofs": proof_identity,
        "non_claims": list(dds.NON_CLAIMS),
        "proof_class": dds.PROOF_CLASS,
        "schema": dds.SCHEMA,
        "version": dds.SCHEMA_VERSION,
        "warnings": [],
        "xpps": xpps_identity,
    }
    return manifest, file_bytes


def _change_stats(before: bytes | bytearray, after: bytes | bytearray) -> tuple[int, int]:
    if len(before) != len(after):
        raise probe.ProbeError("change comparison received different byte lengths")
    changed_bytes = 0
    changed_ranges = 0
    changing = False
    for old, new in zip(before, after, strict=True):
        different = old != new
        if different:
            changed_bytes += 1
            if not changing:
                changed_ranges += 1
        changing = different
    return changed_bytes, changed_ranges


def _padding_identity(
    linear: bytes | bytearray,
    *,
    pitch: int,
    height: int,
    element_bytes: int,
    logical_row_bytes: int,
    logical_rows: int,
) -> tuple[int, str]:
    padded_row_bytes = pitch * element_bytes
    if len(linear) != padded_row_bytes * height:
        raise probe.ProbeError("padding proof received inconsistent padded geometry")
    digest = hashlib.sha256()
    padding_bytes = 0
    for row in range(height):
        row_start = row * padded_row_bytes
        logical_end = logical_row_bytes if row < logical_rows else 0
        padding = linear[row_start + logical_end : row_start + padded_row_bytes]
        digest.update(padding)
        padding_bytes += len(padding)
    return padding_bytes, digest.hexdigest()


def _verify_outside_targets(
    source: bytes, overlay: bytearray, ranges: list[tuple[int, int]]
) -> tuple[int, str]:
    digest_source = hashlib.sha256()
    digest_overlay = hashlib.sha256()
    outside_bytes = 0
    cursor = 0
    for start, end in sorted(ranges):
        if start < cursor or end < start or end > len(source):
            raise probe.ProbeError("changed XPPS target ranges overlap or exceed the source")
        source_gap = source[cursor:start]
        overlay_gap = overlay[cursor:start]
        if source_gap != overlay_gap:
            raise probe.ProbeError("XPPS bytes outside changed mip ranges were modified")
        digest_source.update(source_gap)
        digest_overlay.update(overlay_gap)
        outside_bytes += len(source_gap)
        cursor = end
    source_gap = source[cursor:]
    overlay_gap = overlay[cursor:]
    if source_gap != overlay_gap:
        raise probe.ProbeError("XPPS tail outside changed mip ranges was modified")
    digest_source.update(source_gap)
    digest_overlay.update(overlay_gap)
    outside_bytes += len(source_gap)
    if digest_source.digest() != digest_overlay.digest():
        raise probe.ProbeError("XPPS non-target aggregate hashes disagree")
    return outside_bytes, digest_source.hexdigest()


def _publish(
    output_dir: Path, overlay_data: bytes, receipt_data: bytes
) -> None:
    parent_fd: int | None = None
    output_fd: int | None = None
    output_name = ""
    output_identity = (0, 0)
    created_names: list[str] = []
    created_identities: dict[str, tuple[int, int, int, int, int]] = {}
    created_guards: dict[str, int] = {}
    try:
        parent_fd, output_fd, output_name, output_identity = (
            dds._open_fresh_output_directory(Path(output_dir))
        )
        written_overlay = dds._write_exclusive(
            output_fd,
            OVERLAY_NAME,
            overlay_data,
            created_names,
            created_identities,
            created_guards,
        )
        if written_overlay != overlay_data:
            raise probe.ProbeError("written XPPS overlay bytes changed")
        written_receipt = dds._write_exclusive(
            output_fd,
            RECEIPT_NAME,
            receipt_data,
            created_names,
            created_identities,
            created_guards,
        )
        if written_receipt != receipt_data:
            raise probe.ProbeError("written overlay receipt bytes changed")
        if sorted(dds._list_output_names(output_fd)) != sorted(created_names):
            raise probe.ProbeError("fresh overlay output contains unexpected entries")
        for name, identity in created_identities.items():
            path_info = os.stat(name, dir_fd=output_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(path_info.st_mode)
                or registry._identity(path_info) != identity
                or registry._identity(os.fstat(created_guards[name])) != identity
            ):
                raise probe.ProbeError("created overlay output path binding changed")
        if not dds._output_binding_matches(
            parent_fd, output_fd, output_name, output_identity
        ):
            raise probe.ProbeError("fresh overlay directory path binding changed")
    except BaseException as error:
        cleanup_error = None
        if parent_fd is not None and output_fd is not None:
            cleanup_error = dds._cleanup_fresh_output(
                parent_fd,
                output_fd,
                output_name,
                output_identity,
                created_names,
                created_identities,
                created_guards,
            )
        if cleanup_error:
            raise probe.ProbeError(
                f"{error}; fresh overlay cleanup incomplete: {cleanup_error}"
            ) from error
        raise
    finally:
        for descriptor in created_guards.values():
            try:
                os.close(descriptor)
            except OSError:
                pass
        if output_fd is not None:
            os.close(output_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def build_overlay(
    xpps: Path,
    *,
    expected_xpps_sha256: str,
    row_index: int,
    eboot: Path,
    expected_eboot_sha256: str,
    export_manifest: Path,
    edits_dir: Path,
    output_dir: Path,
    max_source_bytes: int = MAX_SOURCE_BYTES,
    max_edit_bytes: int = MAX_EDIT_BYTES,
    max_changed_bytes: int = MAX_CHANGED_BYTES,
    max_changed_ranges: int = MAX_CHANGED_RANGES,
    allow_identical_edits: bool = False,
    resolver_overrides: dict[str, object] | None = None,
    shader_root: Path | None = None,
) -> dict[str, object]:
    registry._validate_sha256(expected_xpps_sha256, "expected XPPS SHA-256")
    registry._validate_sha256(expected_eboot_sha256, "expected eboot SHA-256")
    _validate_budget(max_source_bytes, MAX_SOURCE_BYTES, "source byte budget")
    _validate_budget(max_edit_bytes, MAX_EDIT_BYTES, "edit byte budget")
    _validate_budget(max_changed_bytes, MAX_CHANGED_BYTES, "changed byte budget")
    _validate_budget(max_changed_ranges, MAX_CHANGED_RANGES, "changed range budget")
    manifest_path = Path(export_manifest)
    if manifest_path.name != dds.MANIFEST_NAME:
        raise probe.ProbeError("export manifest basename must be manifest.json")

    effective_shader_root = (
        Path(shader_root) if shader_root is not None else Path(__file__).resolve().parents[1]
    )
    host_coordinates, shader_contract = dds.roundtrip._load_host_permutation(
        effective_shader_root
    )
    roundtrip_report = dds.roundtrip.prove_thin1d_roundtrip(
        Path(xpps),
        expected_xpps_sha256=expected_xpps_sha256,
        row_index=row_index,
        eboot=Path(eboot),
        expected_eboot_sha256=expected_eboot_sha256,
        max_descriptors=dds.MAX_DESCRIPTORS,
        max_mips=dds.MAX_MIPS,
        max_mip_bytes=dds.MAX_MIP_BYTES,
        max_total_bytes=max_source_bytes,
        resolver_overrides=resolver_overrides,
        shader_root=effective_shader_root,
    )
    bitmap_report = dds.bitmaps.classify_bitmap_descriptors(
        Path(xpps),
        expected_xpps_sha256=expected_xpps_sha256,
        row_index=row_index,
        eboot=Path(eboot),
        expected_eboot_sha256=expected_eboot_sha256,
        max_bitmap_entries=dds.MAX_DESCRIPTORS,
        max_total_payload_bytes=max_source_bytes,
        resolver_overrides=resolver_overrides,
    )
    xpps_identity, eboot_identity, descriptors, proof_identity = dds._correlate_proofs(
        bitmap_report,
        roundtrip_report,
        expected_xpps_sha256=expected_xpps_sha256,
        expected_eboot_sha256=expected_eboot_sha256,
        row_index=row_index,
        max_descriptors=dds.MAX_DESCRIPTORS,
        max_mips=dds.MAX_MIPS,
        max_mip_bytes=dds.MAX_MIP_BYTES,
        max_total_source_bytes=max_source_bytes,
        current_shader_contract=shader_contract,
    )

    baseline_fd: int | None = None
    edits_fd: int | None = None
    baseline_identity = (0, 0)
    edits_identity = (0, 0)
    try:
        baseline_fd, baseline_identity = _open_input_directory(
            manifest_path.parent, "DDS export directory"
        )
        edits_fd, edits_identity = _open_input_directory(Path(edits_dir), "DDS edit directory")
        with ExitStack() as stack:
            xpps_stream = registry._open_regular(stack, Path(xpps), "XPPS source")
            eboot_stream = registry._open_regular(stack, Path(eboot), "eboot")
            xpps_data, xpps_before = _read_source(
                xpps_stream, expected_xpps_sha256, max_source_bytes, "XPPS source"
            )
            _, eboot_before = _read_source(
                eboot_stream, expected_eboot_sha256, MAX_EBOOT_BYTES, "eboot"
            )
            expected_manifest, baseline_files = _build_expected_export(
                xpps_data,
                xpps_identity=xpps_identity,
                eboot_identity=eboot_identity,
                descriptors=descriptors,
                proof_identity=proof_identity,
                shader_contract=shader_contract,
                host_coordinates=host_coordinates,
            )
            expected_manifest_data = dds.encode_manifest(expected_manifest)
            observed_manifest_data = _read_regular_at(
                baseline_fd,
                dds.MANIFEST_NAME,
                dds.MAX_MANIFEST_BYTES,
                "DDS export manifest",
            )
            observed_manifest = _parse_canonical_json(
                observed_manifest_data, "DDS export manifest"
            )
            if observed_manifest != expected_manifest or (
                observed_manifest_data != expected_manifest_data
            ):
                raise probe.ProbeError("DDS export manifest disagrees with current exact proofs")
            expected_baseline_names = sorted([dds.MANIFEST_NAME, *baseline_files])
            if _list_names(baseline_fd, "DDS export directory") != expected_baseline_names:
                raise probe.ProbeError("DDS export directory population is not exact")
            for name, expected in baseline_files.items():
                observed = _read_regular_at(
                    baseline_fd, name, len(expected), f"baseline DDS {name}"
                )
                if observed != expected:
                    raise probe.ProbeError(f"baseline DDS {name} disagrees with exact source")

            edit_names = _list_names(edits_fd, "DDS edit directory")
            if not edit_names or len(edit_names) > len(baseline_files):
                raise probe.ProbeError("DDS edit population is empty or exceeds the descriptor set")
            if any(name not in baseline_files for name in edit_names):
                raise probe.ProbeError("DDS edit directory contains an unknown basename")
            total_edit_bytes = 0
            edits: dict[str, bytes] = {}
            for name in edit_names:
                maximum = min(max_edit_bytes - total_edit_bytes, len(baseline_files[name]))
                if maximum < 1:
                    raise probe.ProbeError("DDS edits exceed the total edit byte budget")
                edit = _read_regular_at(edits_fd, name, maximum, f"edited DDS {name}")
                total_edit_bytes += len(edit)
                baseline_parsed = dds._parse_dds(baseline_files[name])
                edited_parsed = dds._parse_dds(edit)
                if edited_parsed != baseline_parsed:
                    raise probe.ProbeError(f"edited DDS {name} changed its strict structure")
                if edit[: dds.DDS_HEADER_BYTES] != baseline_files[name][: dds.DDS_HEADER_BYTES]:
                    raise probe.ProbeError(f"edited DDS {name} changed its canonical header")
                if edit == baseline_files[name] and not allow_identical_edits:
                    raise probe.ProbeError(f"edited DDS {name} is byte-identical to baseline")
                edits[name] = edit

            try:
                overlay = bytearray(xpps_data)
            except MemoryError as error:
                raise probe.ProbeError("XPPS overlay allocation failed") from error
            file_items = {
                _require_string(item.get("basename"), "DDS basename"): item
                for item in (
                    _require_dict(raw, "DDS file item")
                    for raw in _require_list(expected_manifest.get("files"), "DDS files")
                )
            }
            descriptor_by_index = {
                _require_int(item.get("descriptor_index"), "descriptor index"): item
                for item in descriptors
            }
            edit_receipts: list[dict[str, object]] = []
            target_ranges: list[tuple[int, int]] = []
            logical_changed_total = 0
            tiled_changed_total = 0
            for name in edit_names:
                baseline = baseline_files[name]
                edited = edits[name]
                item = _require_dict(file_items[name], "DDS manifest file")
                descriptor_index = int(name.split("_", 2)[1])
                descriptor = descriptor_by_index.get(descriptor_index)
                if descriptor is None:
                    raise probe.ProbeError("DDS basename has no proven descriptor")
                baseline_parsed = dds._parse_dds(baseline)
                parsed_mips = _require_list(baseline_parsed.get("mips"), "DDS parsed mips")
                source_mips = _require_list(descriptor.get("mips"), "descriptor mips")
                if len(parsed_mips) != len(source_mips):
                    raise probe.ProbeError("DDS and source mip populations disagree")
                element_bytes = _require_int(
                    descriptor.get("element_bits"), "descriptor element width"
                ) // 8
                mip_receipts: list[dict[str, object]] = []
                file_logical_changed = 0
                file_tiled_changed = 0
                for raw_parsed, raw_source in zip(parsed_mips, source_mips, strict=True):
                    parsed_mip = _require_dict(raw_parsed, "DDS parsed mip")
                    source_mip = _require_dict(raw_source, "source mip")
                    mip_index = _require_int(parsed_mip.get("index"), "mip index")
                    start = _require_int(source_mip.get("absolute_start"), "mip start")
                    size = _require_int(source_mip.get("bytes"), "mip bytes")
                    end = start + size
                    tiled = xpps_data[start:end]
                    pitch = _require_int(
                        source_mip.get("aligned_storage_pitch"), "padded pitch"
                    )
                    padded_height = _require_int(
                        source_mip.get("aligned_storage_height"), "padded height"
                    )
                    linear = dds.roundtrip._deswizzle_mip(
                        tiled,
                        pitch=pitch,
                        height=padded_height,
                        element_bytes=element_bytes,
                        host_coordinates=host_coordinates,
                    )
                    if hashlib.sha256(linear).hexdigest() != _require_string(
                        source_mip.get("linear_padded_sha256"),
                        "source padded-linear SHA-256",
                    ):
                        raise probe.ProbeError(f"source mip {mip_index} linear hash changed")
                    file_start = _require_int(
                        parsed_mip.get("file_offset_start"), "DDS mip file start"
                    )
                    file_end = _require_int(
                        parsed_mip.get("file_offset_end"), "DDS mip file end"
                    )
                    original_logical = baseline[file_start:file_end]
                    edited_logical = edited[file_start:file_end]
                    logical_changed, logical_ranges = _change_stats(
                        original_logical, edited_logical
                    )
                    patched_linear = bytearray(linear)
                    row_bytes = _require_int(parsed_mip.get("row_bytes"), "logical row bytes")
                    logical_rows = _require_int(
                        parsed_mip.get("element_height"), "logical row count"
                    )
                    padded_row_bytes = pitch * element_bytes
                    for row in range(logical_rows):
                        source_row = row * row_bytes
                        destination_row = row * padded_row_bytes
                        patched_linear[destination_row : destination_row + row_bytes] = (
                            edited_logical[source_row : source_row + row_bytes]
                        )
                    padding_bytes, source_padding_sha = _padding_identity(
                        linear,
                        pitch=pitch,
                        height=padded_height,
                        element_bytes=element_bytes,
                        logical_row_bytes=row_bytes,
                        logical_rows=logical_rows,
                    )
                    patched_padding_bytes, overlay_padding_sha = _padding_identity(
                        patched_linear,
                        pitch=pitch,
                        height=padded_height,
                        element_bytes=element_bytes,
                        logical_row_bytes=row_bytes,
                        logical_rows=logical_rows,
                    )
                    if (
                        patched_padding_bytes != padding_bytes
                        or overlay_padding_sha != source_padding_sha
                    ):
                        raise probe.ProbeError(f"mip {mip_index} padding bytes changed")
                    retiled = dds.roundtrip._retile_mip(
                        patched_linear,
                        pitch=pitch,
                        height=padded_height,
                        element_bytes=element_bytes,
                    )
                    verified_linear = dds.roundtrip._deswizzle_mip(
                        retiled,
                        pitch=pitch,
                        height=padded_height,
                        element_bytes=element_bytes,
                        host_coordinates=host_coordinates,
                    )
                    if verified_linear != patched_linear:
                        raise probe.ProbeError(f"mip {mip_index} edited retile is not reversible")
                    tiled_changed, tiled_ranges = _change_stats(tiled, retiled)
                    if tiled_changed != logical_changed:
                        raise probe.ProbeError(
                            f"mip {mip_index} changed-byte count was not preserved by tiling"
                        )
                    if logical_changed:
                        overlay[start:end] = retiled
                        target_ranges.append((start, end))
                    logical_changed_total += logical_changed
                    tiled_changed_total += tiled_changed
                    file_logical_changed += logical_changed
                    file_tiled_changed += tiled_changed
                    mip_receipts.append(
                        {
                            "index": mip_index,
                            "logical_bytes": len(original_logical),
                            "logical_changed_bytes": logical_changed,
                            "logical_changed_ranges": logical_ranges,
                            "overlay_padded_linear_sha256": hashlib.sha256(
                                patched_linear
                            ).hexdigest(),
                            "overlay_tiled_sha256": hashlib.sha256(retiled).hexdigest(),
                            "padding_bytes": padding_bytes,
                            "padding_sha256": source_padding_sha,
                            "source_padded_linear_sha256": hashlib.sha256(linear).hexdigest(),
                            "source_tiled_sha256": hashlib.sha256(tiled).hexdigest(),
                            "tiled_changed_bytes": tiled_changed,
                            "tiled_changed_ranges": tiled_ranges,
                            "xpps_range": {"bytes": size, "start": start},
                        }
                    )
                dds_changed, dds_ranges = _change_stats(baseline, edited)
                if dds_changed != file_logical_changed:
                    raise probe.ProbeError("DDS header or non-mip bytes changed")
                edit_receipts.append(
                    {
                        "basename": name,
                        "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
                        "dds_changed_bytes": dds_changed,
                        "dds_changed_ranges": dds_ranges,
                        "descriptor_sha256": _require_dict(
                            item.get("descriptor"), "DDS descriptor"
                        ).get("sha256"),
                        "edited_sha256": hashlib.sha256(edited).hexdigest(),
                        "mips": mip_receipts,
                        "tiled_changed_bytes": file_tiled_changed,
                    }
                )

            if logical_changed_total != tiled_changed_total:
                raise probe.ProbeError("logical and tiled aggregate changed-byte counts disagree")
            if not logical_changed_total and not allow_identical_edits:
                raise probe.ProbeError("DDS edit set is byte-identical to the baseline")
            outside_bytes, outside_sha = _verify_outside_targets(
                xpps_data, overlay, target_ranges
            )
            overlay_changed_bytes, overlay_changed_ranges = _change_stats(xpps_data, overlay)
            if overlay_changed_bytes != tiled_changed_total:
                raise probe.ProbeError("full XPPS changed-byte count is inconsistent")
            if overlay_changed_bytes > max_changed_bytes:
                raise probe.ProbeError("XPPS changes exceed the changed byte budget")
            if overlay_changed_ranges > max_changed_ranges:
                raise probe.ProbeError("XPPS changes exceed the changed range budget")
            _source_is_unchanged(
                xpps_stream, xpps_before, expected_xpps_sha256, "XPPS source"
            )
            _source_is_unchanged(
                eboot_stream, eboot_before, expected_eboot_sha256, "eboot"
            )
            if not _directory_binding_matches(
                manifest_path.parent, baseline_fd, baseline_identity
            ):
                raise probe.ProbeError("DDS export directory path binding changed")
            if not _directory_binding_matches(Path(edits_dir), edits_fd, edits_identity):
                raise probe.ProbeError("DDS edit directory path binding changed")

            overlay_data = bytes(overlay)
            receipt = {
                "eboot": eboot_identity,
                "edits": edit_receipts,
                "facts": {
                    "all_non_target_bytes_exact": True,
                    "all_padding_bytes_exact": True,
                    "all_retiled_mips_deswizzle_exact": True,
                    "changed_mip_count": len(target_ranges),
                    "edit_file_count": len(edit_receipts),
                    "logical_changed_bytes": logical_changed_total,
                    "outside_target_bytes": outside_bytes,
                    "outside_target_sha256": outside_sha,
                    "overlay_changed_bytes": overlay_changed_bytes,
                    "overlay_changed_ranges": overlay_changed_ranges,
                    "source_and_overlay_bytes": len(xpps_data),
                },
                "host_shader_contract": shader_contract,
                "inherited_dds_export": {
                    "manifest_sha256": hashlib.sha256(expected_manifest_data).hexdigest(),
                    "proofs": proof_identity,
                    "schema": dds.SCHEMA,
                    "version": dds.SCHEMA_VERSION,
                },
                "non_claims": list(NON_CLAIMS),
                "output": {
                    "basename": OVERLAY_NAME,
                    "bytes": len(overlay_data),
                    "sha256": hashlib.sha256(overlay_data).hexdigest(),
                },
                "proof_class": PROOF_CLASS,
                "schema": SCHEMA,
                "version": SCHEMA_VERSION,
                "warnings": [],
                "xpps": xpps_identity,
            }
            receipt_data = encode_receipt(receipt)
            if len(receipt_data) > MAX_RECEIPT_BYTES:
                raise probe.ProbeError("overlay receipt exceeds its byte limit")
        _publish(Path(output_dir), overlay_data, receipt_data)
        return receipt
    finally:
        if edits_fd is not None:
            os.close(edits_fd)
        if baseline_fd is not None:
            os.close(baseline_fd)


def encode_receipt(receipt: dict[str, object]) -> bytes:
    return (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a guarded Second Son XPPS copy from compatible edited DDS files."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--expected-xpps-sha256", required=True)
    parser.add_argument("--row", required=True, type=int)
    parser.add_argument("--eboot", required=True, type=Path)
    parser.add_argument("--expected-eboot-sha256", required=True)
    parser.add_argument("--export-manifest", required=True, type=Path)
    parser.add_argument("--edits-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        receipt = build_overlay(
            args.input,
            expected_xpps_sha256=args.expected_xpps_sha256,
            row_index=args.row,
            eboot=args.eboot,
            expected_eboot_sha256=args.expected_eboot_sha256,
            export_manifest=args.export_manifest,
            edits_dir=args.edits_dir,
            output_dir=args.output_dir,
        )
        sys.stdout.buffer.write(encode_receipt(receipt))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_dds_overlay: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
