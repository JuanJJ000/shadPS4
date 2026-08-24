#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Prove bounded Second Son XPPS BITMAP descriptors and tiled payload ranges."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from collections import Counter, defaultdict
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

import second_son_xpps_eboot_registry as registry
import second_son_xpps_eboot_type_names as type_names
import second_son_xpps_probe as probe
import second_son_xpps_targets as targets

SCHEMA = "shadps4.second_son_xpps_bitmap_descriptors"
SCHEMA_VERSION = 1
PROOF_CLASS = "xpps_bitmap_descriptor_classifier"
BITMAP_NAME = "BITMAP"
DESCRIPTOR_OFFSET_FROM_TARGET = 8
DESCRIPTOR = struct.Struct("<QQQQ")
MAX_BITMAP_ENTRIES = 256
MAX_TOTAL_PAYLOAD_BYTES = 512 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
U64_LIMIT = 1 << 64

IMAGE_TYPES = {
    8: "Color1D",
    9: "Color2D",
}
FORMAT_INFO = {
    10: ("Format8_8_8_8", 32, False),
    35: ("FormatBc1", 64, True),
    37: ("FormatBc3", 128, True),
    38: ("FormatBc4", 64, True),
    39: ("FormatBc5", 128, True),
}
NUMBER_FORMATS = {0: "Unorm"}
TILE_MODES = {13: "Thin1DThin"}
RESOLVER_OVERRIDE_KEYS = frozenset(
    {
        "hash_lookup_guest",
        "hash_lookup_bytes",
        "expected_hash_lookup_sha256",
        "name_lookup_guest",
        "name_lookup_bytes",
        "expected_name_lookup_sha256",
        "name_count_guest",
        "name_count_bytes",
        "expected_name_count_sha256",
        "hash_table_guest",
        "hash_record_count",
        "name_table_guest",
        "name_table_count",
        "max_dynamic_bytes",
        "max_relocation_bytes",
        "max_relocations",
    }
)
NON_CLAIMS = (
    "deswizzle_order",
    "pixel_channel_interpretation",
    "alpha_semantics",
    "color_space",
    "artwork_identity",
    "decoded_image",
    "safe_replacement",
    "byte_exact_retile",
    "container_rebuild",
    "runtime_overlay_activation",
    "injection_support",
)


def _require_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise probe.ProbeError(f"{label} has an unexpected shape")
    return value


def _require_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise probe.ProbeError(f"{label} has an unexpected shape")
    return value


def _require_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise probe.ProbeError(f"{label} is not an integer")
    return value


def _checked_u64_range(start: int, size: int, label: str) -> tuple[int, int]:
    if start < 0 or size < 0 or start > U64_LIMIT or size > U64_LIMIT - start:
        raise probe.ProbeError(f"{label} overflows a u64 range")
    return start, start + size


def _read_exact_at(stream: BinaryIO, offset: int, size: int, label: str) -> bytes:
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise probe.ProbeError(
            f"truncated {label}: expected {size} bytes, got {len(data)}"
        )
    return data


def _hash_range(stream: BinaryIO, offset: int, size: int, label: str) -> str:
    digest = hashlib.sha256()
    stream.seek(offset)
    remaining = size
    while remaining:
        chunk = stream.read(min(remaining, HASH_CHUNK_BYTES))
        if not chunk:
            raise probe.ProbeError(f"truncated {label} while hashing")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _parse_rows(structure: dict[str, object]) -> tuple[int, int, list[dict[str, int]]]:
    source = _require_dict(structure.get("input"), "probe source metadata")
    layout = _require_dict(structure.get("candidate_layout"), "probe candidate layout")
    source_size = _require_int(source.get("bytes"), "probe source bytes")
    data_start = _require_int(layout.get("data_start"), "probe data start")
    data_size = _require_int(layout.get("data_size"), "probe data size")
    _, data_end = _checked_u64_range(data_start, data_size, "XPPS data region")
    if data_end > source_size:
        raise probe.ProbeError("XPPS data region exceeds the source")

    rows: list[dict[str, int]] = []
    for index, raw in enumerate(
        _require_list(layout.get("payload_rows"), "probe payload rows")
    ):
        row = _require_dict(raw, f"probe payload row {index}")
        observed_index = _require_int(row.get("index"), f"payload row {index} index")
        if observed_index != index:
            raise probe.ProbeError("probe payload row indexes are not canonical")
        start = _require_int(row.get("absolute_start"), f"payload row {index} start")
        end = _require_int(row.get("absolute_end"), f"payload row {index} end")
        size = _require_int(row.get("size"), f"payload row {index} size")
        relative = _require_int(
            row.get("relative_offset"), f"payload row {index} relative offset"
        )
        kind_word = _require_int(row.get("kind_word"), f"payload row {index} kind word")
        if start != data_start + relative or end != start + size:
            raise probe.ProbeError(f"payload row {index} has inconsistent ranges")
        if start < data_start or end > data_end or size < 1:
            raise probe.ProbeError(f"payload row {index} exceeds the XPPS data region")
        rows.append(
            {
                "absolute_end": end,
                "absolute_start": start,
                "index": index,
                "kind_class_high16": kind_word >> 16,
                "kind_flags_low16": kind_word & 0xFFFF,
                "kind_word": kind_word,
                "relative_end": relative + size,
                "relative_start": relative,
                "size": size,
            }
        )
    if not rows:
        raise probe.ProbeError("probe emitted no payload rows")
    for previous, current in zip(rows, rows[1:]):
        if previous["absolute_end"] != current["absolute_start"]:
            raise probe.ProbeError(
                "payload rows do not exactly and contiguously cover data"
            )
    if rows[0]["absolute_start"] != data_start or rows[-1]["absolute_end"] != data_end:
        raise probe.ProbeError(
            "payload rows do not cover the complete XPPS data region"
        )
    return data_start, data_size, rows


def _owner_for_absolute_range(
    rows: list[dict[str, int]], start: int, size: int, label: str
) -> dict[str, int]:
    _, end = _checked_u64_range(start, size, label)
    owners = [
        row
        for row in rows
        if row["absolute_start"] <= start and end <= row["absolute_end"]
    ]
    if len(owners) != 1:
        raise probe.ProbeError(f"{label} must fit in exactly one validated XPPS row")
    return owners[0]


def _decode_descriptor(raw: bytes) -> dict[str, object]:
    if len(raw) != DESCRIPTOR.size:
        raise probe.ProbeError("BITMAP descriptor has an unexpected byte length")
    word0, word1, word2, word3 = DESCRIPTOR.unpack(raw)

    base_address = word0 & ((1 << 38) - 1)
    mtype_l2 = (word0 >> 38) & 0x3
    min_lod = (word0 >> 40) & 0xFFF
    data_format = (word0 >> 52) & 0x3F
    number_format = (word0 >> 58) & 0xF
    mtype = (word0 >> 62) & 0x3

    width = (word1 & 0x3FFF) + 1
    height = ((word1 >> 14) & 0x3FFF) + 1
    perf_modulation = (word1 >> 28) & 0x7
    interlaced = (word1 >> 31) & 0x1
    dst_select = [
        (word1 >> 32) & 0x7,
        (word1 >> 35) & 0x7,
        (word1 >> 38) & 0x7,
        (word1 >> 41) & 0x7,
    ]
    base_level = (word1 >> 44) & 0xF
    last_level = (word1 >> 48) & 0xF
    tile_mode = (word1 >> 52) & 0x1F
    pow2pad = (word1 >> 57) & 0x1
    mtype2 = (word1 >> 58) & 0x1
    atc = (word1 >> 59) & 0x1
    image_type = (word1 >> 60) & 0xF

    depth = (word2 & 0x1FFF) + 1
    pitch = ((word2 >> 13) & 0x3FFF) + 1
    base_array = (word2 >> 32) & 0x1FFF
    last_array = (word2 >> 45) & 0x1FFF
    if ((word2 >> 27) & 0x1F) or ((word2 >> 58) & 0x3F):
        raise probe.ProbeError("BITMAP descriptor third word has nonzero reserved bits")

    min_lod_warn = word3 & 0xFFF
    counter_bank_id = (word3 >> 12) & 0xFF
    lod_hw_count_enabled = (word3 >> 20) & 0x1
    compression_enabled = (word3 >> 21) & 0x1
    alpha_is_on_msb = (word3 >> 22) & 0x1
    color_transform = (word3 >> 23) & 0x1
    alternate_tile_mode = (word3 >> 24) & 0x1
    if word3 >> 25:
        raise probe.ProbeError(
            "BITMAP descriptor fourth word has nonzero reserved bits"
        )

    format_info = FORMAT_INFO.get(data_format)
    if format_info is None:
        raise probe.ProbeError(
            f"BITMAP descriptor data format {data_format} is unsupported"
        )
    format_name, bits_per_block, is_block_coded = format_info
    type_name = IMAGE_TYPES.get(image_type)
    if type_name is None:
        raise probe.ProbeError(
            f"BITMAP descriptor image type {image_type} is unsupported"
        )
    number_name = NUMBER_FORMATS.get(number_format)
    if number_name is None:
        raise probe.ProbeError(
            f"BITMAP descriptor number format {number_format} is unsupported"
        )
    tile_name = TILE_MODES.get(tile_mode)
    if tile_name is None:
        raise probe.ProbeError(
            f"BITMAP descriptor tile mode {tile_mode} is unsupported"
        )
    if base_address == 0:
        raise probe.ProbeError("BITMAP descriptor has a zero serialized base field")
    if min_lod != 0 or base_level != 0:
        raise probe.ProbeError(
            "BITMAP descriptor does not begin at base mip level zero"
        )
    if last_level < base_level:
        raise probe.ProbeError("BITMAP descriptor mip-level range is reversed")
    if pitch < width:
        raise probe.ProbeError("BITMAP descriptor pitch is smaller than its width")
    if image_type == 8 and height != 1:
        raise probe.ProbeError("Color1D BITMAP descriptor height is not one")
    if depth != 1 or base_array != 0 or last_array != 0:
        raise probe.ProbeError("BITMAP descriptor is not a one-layer, one-depth image")
    if compression_enabled or alternate_tile_mode:
        raise probe.ProbeError(
            "BITMAP descriptor Neo compression or alternate tiling is unsupported"
        )

    return {
        "alpha_is_on_msb": bool(alpha_is_on_msb),
        "alternate_tile_mode": bool(alternate_tile_mode),
        "atc": bool(atc),
        "base_address_serialized_relative": base_address,
        "base_array": base_array,
        "base_level": base_level,
        "bits_per_block": bits_per_block,
        "color_transform": bool(color_transform),
        "compression_enabled": bool(compression_enabled),
        "counter_bank_id": counter_bank_id,
        "data_format": {"id": data_format, "name": format_name},
        "depth": depth,
        "destination_select": dst_select,
        "height": height,
        "image_type": {"id": image_type, "name": type_name},
        "interlaced": bool(interlaced),
        "is_block_coded": is_block_coded,
        "last_array": last_array,
        "last_level": last_level,
        "level_count": last_level + 1,
        "lod_hw_count_enabled": bool(lod_hw_count_enabled),
        "min_lod": min_lod,
        "min_lod_warn": min_lod_warn,
        "mtype": mtype,
        "mtype2": mtype2,
        "mtype_l2": mtype_l2,
        "number_format": {"id": number_format, "name": number_name},
        "perf_modulation": perf_modulation,
        "pitch": pitch,
        "pow2pad": bool(pow2pad),
        "sample_count": 1,
        "tile_mode": {"id": tile_mode, "name": tile_name},
        "width": width,
    }


def _mip_layout(fields: dict[str, object]) -> tuple[list[dict[str, int]], int]:
    pitch = _require_int(fields.get("pitch"), "descriptor pitch")
    height = _require_int(fields.get("height"), "descriptor height")
    level_count = _require_int(fields.get("level_count"), "descriptor level count")
    bits_per_block = _require_int(
        fields.get("bits_per_block"), "descriptor bits per block"
    )
    is_block = fields.get("is_block_coded") is True
    pow2pad = fields.get("pow2pad") is True
    if level_count < 1 or level_count > 16:
        raise probe.ProbeError("BITMAP descriptor level count is outside 1 through 16")

    mips: list[dict[str, int]] = []
    payload_offset = 0
    for mip in range(level_count):
        logical_pitch = max(pitch >> mip, 1)
        logical_height = max(height >> mip, 1)
        storage_pitch = logical_pitch
        storage_height = logical_height
        if is_block:
            storage_pitch = max((storage_pitch + 3) // 4, 1)
            storage_height = max((storage_height + 3) // 4, 1)
        if pow2pad:
            storage_pitch = 1 << (storage_pitch - 1).bit_length()
            storage_height = 1 << (storage_height - 1).bit_length()
        aligned_pitch = (storage_pitch + 7) & ~7
        aligned_height = (storage_height + 7) & ~7
        size = (aligned_pitch * aligned_height * bits_per_block + 7) // 8
        align_iterations = 0
        while size % 256:
            aligned_pitch += 8
            size = (aligned_pitch * aligned_height * bits_per_block + 7) // 8
            align_iterations += 1
            if align_iterations > 256:
                raise probe.ProbeError("microtile alignment did not converge")
        if size < 1 or size > MAX_TOTAL_PAYLOAD_BYTES:
            raise probe.ProbeError("one BITMAP mip exceeds the payload-size contract")
        _, mip_end = _checked_u64_range(payload_offset, size, f"BITMAP mip {mip}")
        guest_pitch = aligned_pitch
        guest_height = aligned_height
        if is_block:
            guest_pitch = max(aligned_pitch * 4, 32)
            guest_height = max(aligned_height * 4, 32)
        mips.append(
            {
                "aligned_storage_height": aligned_height,
                "aligned_storage_pitch": aligned_pitch,
                "bytes": size,
                "guest_height_texels": guest_height,
                "guest_pitch_texels": guest_pitch,
                "index": mip,
                "logical_height": logical_height,
                "logical_pitch": logical_pitch,
                "payload_offset_end": mip_end,
                "payload_offset_start": payload_offset,
                "storage_height": storage_height,
                "storage_pitch": storage_pitch,
            }
        )
        payload_offset = mip_end
    return mips, payload_offset


def classify_bitmap_descriptors(
    xpps: Path,
    *,
    expected_xpps_sha256: str,
    row_index: int,
    eboot: Path,
    expected_eboot_sha256: str,
    max_bitmap_entries: int = MAX_BITMAP_ENTRIES,
    max_total_payload_bytes: int = MAX_TOTAL_PAYLOAD_BYTES,
    resolver_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    registry._validate_sha256(expected_xpps_sha256, "expected XPPS SHA-256")
    registry._validate_sha256(expected_eboot_sha256, "expected eboot SHA-256")
    if max_bitmap_entries < 1 or max_bitmap_entries > MAX_BITMAP_ENTRIES:
        raise probe.ProbeError(
            f"BITMAP-entry budget must be from 1 through {MAX_BITMAP_ENTRIES}"
        )
    if max_total_payload_bytes < 1 or max_total_payload_bytes > MAX_TOTAL_PAYLOAD_BYTES:
        raise probe.ProbeError(
            f"payload-byte budget must be from 1 through {MAX_TOTAL_PAYLOAD_BYTES}"
        )
    overrides = dict(resolver_overrides or {})
    unknown_overrides = sorted(set(overrides) - RESOLVER_OVERRIDE_KEYS)
    if unknown_overrides:
        raise probe.ProbeError(
            f"unknown synthetic resolver override: {unknown_overrides[0]}"
        )

    name_report = type_names.resolve_type_names(
        Path(xpps),
        expected_xpps_sha256=expected_xpps_sha256,
        row_index=row_index,
        eboot=Path(eboot),
        expected_eboot_sha256=expected_eboot_sha256,
        **overrides,
    )
    name_resolutions = _require_list(
        name_report.get("resolutions"), "type-name resolutions"
    )
    bitmap_hashes: set[str] = set()
    expected_hash_counts: dict[str, int] = {}
    for raw_resolution in name_resolutions:
        resolution = _require_dict(raw_resolution, "type-name resolution")
        if resolution.get("name") != BITMAP_NAME:
            continue
        hash_word = str(resolution.get("dic_hash_word_hex"))
        if len(hash_word) != 16 or any(
            character not in "0123456789abcdef" for character in hash_word
        ):
            raise probe.ProbeError("BITMAP resolution has a malformed DIC hash")
        bitmap_hashes.add(hash_word)
        expected_hash_counts[hash_word] = _require_int(
            resolution.get("xpps_dic_entry_count"), "BITMAP DIC entry count"
        )
    if not bitmap_hashes:
        raise probe.ProbeError("exact type-name proof contains no BITMAP resolution")

    target_report = targets.classify_targets(
        Path(xpps),
        expected_sha256=expected_xpps_sha256,
        row_index=row_index,
    )
    structure = probe.probe_file(Path(xpps))
    source_metadata = _require_dict(structure.get("input"), "probe source metadata")
    if source_metadata.get("sha256") != expected_xpps_sha256:
        raise probe.ProbeError("probe source hash differs from the exact XPPS hash")
    data_start, data_size, rows = _parse_rows(structure)
    source_size = _require_int(source_metadata.get("bytes"), "probe source bytes")

    observations = _require_list(
        target_report.get("observations"), "target observations"
    )
    selected: list[dict[str, object]] = []
    observed_hash_counts: Counter[str] = Counter()
    for raw_observation in observations:
        observation = _require_dict(raw_observation, "target observation")
        dic = _require_dict(observation.get("dic"), "target DIC metadata")
        hash_word = str(dic.get("hash_word_hex"))
        if hash_word in bitmap_hashes:
            selected.append(observation)
            observed_hash_counts[hash_word] += 1
    if not selected:
        raise probe.ProbeError("target classifier emitted no proven BITMAP entries")
    if len(selected) > max_bitmap_entries:
        raise probe.ProbeError(
            f"BITMAP population exceeds the {max_bitmap_entries}-entry budget"
        )
    if dict(observed_hash_counts) != expected_hash_counts:
        raise probe.ProbeError(
            "BITMAP target population disagrees with the exact type-name proof"
        )

    grouped: defaultdict[int, list[dict[str, object]]] = defaultdict(list)
    for observation in selected:
        dic = _require_dict(observation.get("dic"), "target DIC metadata")
        target = _require_int(dic.get("absolute_offset"), "BITMAP target offset")
        grouped[target].append(observation)

    descriptors: list[dict[str, object]] = []
    total_payload_bytes = 0
    with ExitStack() as stack:
        xpps_stream = registry._open_regular(stack, Path(xpps), "XPPS source")
        eboot_stream = registry._open_regular(stack, Path(eboot), "eboot")
        xpps_before = os.fstat(xpps_stream.fileno())
        eboot_before = os.fstat(eboot_stream.fileno())
        if xpps_before.st_size != source_size:
            raise probe.ProbeError("XPPS size changed after inherited classification")
        if eboot_before.st_size > registry.MAX_EBOOT_BYTES:
            raise probe.ProbeError(
                f"eboot exceeds the {registry.MAX_EBOOT_BYTES}-byte limit"
            )
        if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError("XPPS hash changed after inherited classification")
        if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot hash changed after inherited classification")

        for target, aliases in sorted(grouped.items()):
            target_owner = _owner_for_absolute_range(
                rows,
                target,
                DESCRIPTOR_OFFSET_FROM_TARGET + DESCRIPTOR.size,
                "BITMAP target and descriptor",
            )
            if target_owner["kind_class_high16"] != 0:
                raise probe.ProbeError(
                    "BITMAP descriptor target is not in a high-kind-0 row"
                )
            descriptor_offset = target + DESCRIPTOR_OFFSET_FROM_TARGET
            raw_descriptor = _read_exact_at(
                xpps_stream,
                descriptor_offset,
                DESCRIPTOR.size,
                "BITMAP descriptor",
            )
            fields = _decode_descriptor(raw_descriptor)
            mips, payload_bytes = _mip_layout(fields)
            total_payload_bytes += payload_bytes
            if total_payload_bytes > max_total_payload_bytes:
                raise probe.ProbeError(
                    f"BITMAP payload population exceeds {max_total_payload_bytes} bytes"
                )
            relative_start = _require_int(
                fields.get("base_address_serialized_relative"),
                "serialized BITMAP base field",
            )
            _, relative_end = _checked_u64_range(
                relative_start, payload_bytes, "BITMAP serialized payload range"
            )
            if relative_end > data_size:
                raise probe.ProbeError(
                    "BITMAP payload range exceeds the XPPS data region"
                )
            absolute_start = data_start + relative_start
            absolute_end = data_start + relative_end
            payload_owner = _owner_for_absolute_range(
                rows, absolute_start, payload_bytes, "BITMAP payload"
            )
            if payload_owner["kind_class_high16"] != 1:
                raise probe.ProbeError("BITMAP payload is not in a high-kind-1 row")

            public_mips: list[dict[str, int]] = []
            for mip in mips:
                public_mip = dict(mip)
                public_mip["absolute_end"] = absolute_start + mip["payload_offset_end"]
                public_mip["absolute_start"] = (
                    absolute_start + mip["payload_offset_start"]
                )
                public_mip["data_relative_end"] = (
                    relative_start + mip["payload_offset_end"]
                )
                public_mip["data_relative_start"] = (
                    relative_start + mip["payload_offset_start"]
                )
                public_mips.append(public_mip)

            entry_indexes: list[int] = []
            alias_hashes: list[str] = []
            for alias in aliases:
                dic = _require_dict(alias.get("dic"), "target DIC metadata")
                entry_indexes.append(
                    _require_int(dic.get("dic_entry_index"), "DIC entry index")
                )
                alias_hashes.append(str(dic.get("hash_word_hex")))
            descriptors.append(
                {
                    "aliases": {
                        "count": len(aliases),
                        "dic_entry_indexes": sorted(entry_indexes),
                        "dic_hash_words": sorted(set(alias_hashes)),
                    },
                    "descriptor": {
                        "absolute_offset": descriptor_offset,
                        "bytes": DESCRIPTOR.size,
                        "fields": fields,
                        "sha256": hashlib.sha256(raw_descriptor).hexdigest(),
                    },
                    "payload": {
                        "absolute_end": absolute_end,
                        "absolute_start": absolute_start,
                        "bytes": payload_bytes,
                        "data_relative_end": relative_end,
                        "data_relative_start": relative_start,
                        "mips": public_mips,
                        "owning_row": payload_owner,
                        "sha256": _hash_range(
                            xpps_stream,
                            absolute_start,
                            payload_bytes,
                            "BITMAP payload",
                        ),
                    },
                    "target": {
                        "absolute_offset": target,
                        "offset_in_row": target - target_owner["absolute_start"],
                        "owning_row": target_owner,
                    },
                }
            )

        ordered_payloads = sorted(
            descriptors,
            key=lambda item: _require_int(
                _require_dict(item.get("payload"), "BITMAP payload").get(
                    "data_relative_start"
                ),
                "BITMAP payload start",
            ),
        )
        contiguous_adjacencies = 0
        for index, item in enumerate(ordered_payloads):
            payload = _require_dict(item.get("payload"), "BITMAP payload")
            if index + 1 == len(ordered_payloads):
                payload["contiguous_with_next"] = None
                payload["next_payload_gap_bytes"] = None
                continue
            next_payload = _require_dict(
                ordered_payloads[index + 1].get("payload"), "next BITMAP payload"
            )
            end = _require_int(payload.get("data_relative_end"), "BITMAP payload end")
            next_start = _require_int(
                next_payload.get("data_relative_start"), "next BITMAP payload start"
            )
            if next_start < end:
                raise probe.ProbeError("distinct BITMAP payload ranges overlap")
            gap = next_start - end
            payload["contiguous_with_next"] = gap == 0
            payload["next_payload_gap_bytes"] = gap
            contiguous_adjacencies += gap == 0
        descriptors = ordered_payloads

        if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError("XPPS changed during BITMAP classification")
        if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot changed during BITMAP classification")
        if registry._identity(os.fstat(xpps_stream.fileno())) != registry._identity(
            xpps_before
        ):
            raise probe.ProbeError("XPPS identity changed during BITMAP classification")
        if registry._identity(os.fstat(eboot_stream.fileno())) != registry._identity(
            eboot_before
        ):
            raise probe.ProbeError(
                "eboot identity changed during BITMAP classification"
            )

    format_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    for item in descriptors:
        descriptor = _require_dict(item.get("descriptor"), "BITMAP descriptor")
        fields = _require_dict(descriptor.get("fields"), "BITMAP descriptor fields")
        data_format = _require_dict(fields.get("data_format"), "BITMAP data format")
        image_type = _require_dict(fields.get("image_type"), "BITMAP image type")
        format_counts[str(data_format.get("name"))] += 1
        type_counts[str(image_type.get("name"))] += 1

    xpps_identity = _require_dict(name_report.get("xpps"), "type-name XPPS identity")
    eboot_identity = _require_dict(name_report.get("eboot"), "type-name eboot identity")
    selected_row = _require_dict(
        name_report.get("selected_dic_row"), "type-name selected DIC row"
    )
    return {
        "decoder_contract": {
            "array_mode": "Array1DTiledThin1",
            "descriptor_bytes": DESCRIPTOR.size,
            "descriptor_offset_from_target": DESCRIPTOR_OFFSET_FROM_TARGET,
            "microtile_height": 8,
            "microtile_width": 8,
            "payload_alignment_bytes": 256,
            "serialized_base_interpretation": "xpps_data_relative_byte_offset",
            "tile_mode": {"id": 13, "name": "Thin1DThin"},
        },
        "descriptors": descriptors,
        "eboot": eboot_identity,
        "facts": {
            "bitmap_dic_entries": len(selected),
            "contiguous_payload_adjacencies": contiguous_adjacencies,
            "data_format_counts": dict(sorted(format_counts.items())),
            "distinct_bitmap_targets": len(descriptors),
            "image_type_counts": dict(sorted(type_counts.items())),
            "payload_ranges_nonoverlapping": True,
            "total_payload_bytes": total_payload_bytes,
        },
        "layout": {
            "data_size": data_size,
            "data_start": data_start,
            "row_count": len(rows),
        },
        "non_claims": list(NON_CLAIMS),
        "proof_class": PROOF_CLASS,
        "schema": SCHEMA,
        "selected_dic_row": selected_row,
        "type_name_proof": {
            "bitmap_dic_hash_words": sorted(bitmap_hashes),
            "proof_class": name_report.get("proof_class"),
            "schema": name_report.get("schema"),
            "version": name_report.get("version"),
        },
        "version": SCHEMA_VERSION,
        "warnings": [],
        "xpps": xpps_identity,
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prove bounded Second Son XPPS BITMAP descriptors and tiled payload ranges."
        )
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--expected-xpps-sha256", required=True)
    parser.add_argument("--row", required=True, type=int)
    parser.add_argument("--eboot", required=True, type=Path)
    parser.add_argument("--expected-eboot-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = classify_bitmap_descriptors(
            args.input,
            expected_xpps_sha256=args.expected_xpps_sha256,
            row_index=args.row,
            eboot=args.eboot,
            expected_eboot_sha256=args.expected_eboot_sha256,
        )
        sys.stdout.buffer.write(encode_report(report))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_bitmap_descriptors: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
