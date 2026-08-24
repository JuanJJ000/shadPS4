#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Prove byte-exact Thin1DThin deswizzle/retile for Second Son BITMAPs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

import second_son_xpps_bitmap_descriptors as bitmaps
import second_son_xpps_eboot_registry as registry
import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_xpps_thin1d_roundtrip"
SCHEMA_VERSION = 1
PROOF_CLASS = "xpps_thin1d_roundtrip"
MICROTILE_WIDTH = 8
MICROTILE_HEIGHT = 8
MICROTILE_ELEMENTS = MICROTILE_WIDTH * MICROTILE_HEIGHT
MAX_DESCRIPTORS = 256
MAX_MIPS = 4096
MAX_MIP_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_SHADER_BYTES = 64 * 1024
HOST_SHADER_PATHS = (
    (32, "src/video_core/host_shaders/detilers/micro_32bpp.comp"),
    (64, "src/video_core/host_shaders/detilers/micro_64bpp.comp"),
    (128, "src/video_core/host_shaders/detilers/micro_128bpp.comp"),
)
RMORT_BLOCK = re.compile(
    rb"const\s+uint\s+rmort\s*\[\s*16\s*\]\s*=\s*\{(?P<body>.*?)\}\s*;",
    re.DOTALL,
)
HEX_WORD = re.compile(rb"0[xX][0-9a-fA-F]{1,8}")
U64_LIMIT = 1 << 64
NON_CLAIMS = (
    "pixel_channel_interpretation",
    "alpha_semantics",
    "color_space",
    "logical_edge_cropping",
    "decoded_image_correctness",
    "artwork_identity",
    "editable_image_roundtrip",
    "safe_replacement",
    "xpps_rebuild",
    "runtime_overlay_activation",
    "injection_support",
    "game_runtime_behavior",
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


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise probe.ProbeError(f"{label} is not a boolean")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise probe.ProbeError(f"{label} is not a string")
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


def _thin_morton_index(x: int, y: int) -> int:
    if x < 0 or x >= MICROTILE_WIDTH or y < 0 or y >= MICROTILE_HEIGHT:
        raise probe.ProbeError("thin Morton coordinate is outside one 8x8 microtile")
    return (
        (x & 1)
        | ((y & 1) << 1)
        | (((x >> 1) & 1) << 2)
        | (((y >> 1) & 1) << 3)
        | (((x >> 2) & 1) << 4)
        | (((y >> 2) & 1) << 5)
    )


def _parse_shader_lut(source: bytes, label: str) -> tuple[int, ...]:
    matches = list(RMORT_BLOCK.finditer(source))
    if len(matches) != 1:
        raise probe.ProbeError(f"{label} must contain exactly one rmort[16] table")
    body = matches[0].group("body")
    tokens = HEX_WORD.findall(body)
    if len(tokens) != 16:
        raise probe.ProbeError(f"{label} rmort table must contain exactly 16 u32 words")
    remainder = HEX_WORD.sub(b"", body)
    if re.fullmatch(rb"[\s,]*", remainder) is None:
        raise probe.ProbeError(f"{label} rmort table contains unsupported syntax")
    words = tuple(int(token, 16) for token in tokens)
    if any(word < 0 or word > 0xFFFFFFFF for word in words):
        raise probe.ProbeError(f"{label} rmort table contains a non-u32 word")
    return words


def _read_shader_lut(
    stack: ExitStack, shader_root: Path, element_bits: int, relative_path: str
) -> tuple[tuple[int, ...], dict[str, object]]:
    stream = registry._open_regular(
        stack,
        shader_root / relative_path,
        f"{element_bits}-bit host detiler source",
    )
    before = os.fstat(stream.fileno())
    source = registry._read_bounded(
        stream, MAX_SHADER_BYTES, f"{element_bits}-bit host detiler source"
    )
    after = os.fstat(stream.fileno())
    if registry._identity(after) != registry._identity(before):
        raise probe.ProbeError(
            f"{element_bits}-bit host detiler source changed while reading"
        )
    return _parse_shader_lut(source, f"{element_bits}-bit host detiler"), {
        "element_bits": element_bits,
        "logical_name": Path(relative_path).name,
        "sha256": hashlib.sha256(source).hexdigest(),
    }


def _load_host_permutation(
    shader_root: Path,
) -> tuple[list[tuple[int, int]], dict[str, object]]:
    sources: list[dict[str, object]] = []
    tables: list[tuple[int, ...]] = []
    with ExitStack() as stack:
        for element_bits, relative_path in HOST_SHADER_PATHS:
            words, source = _read_shader_lut(
                stack, shader_root, element_bits, relative_path
            )
            tables.append(words)
            sources.append(source)
    if any(table != tables[0] for table in tables[1:]):
        raise probe.ProbeError("32/64/128-bit host detiler rmort tables disagree")

    packed_words = struct.pack("<16I", *tables[0])
    coordinates: list[tuple[int, int]] = []
    for packed in packed_words:
        coordinates.append(((packed >> 4) & 0xF, packed & 0xF))
    if len(coordinates) != MICROTILE_ELEMENTS:
        raise probe.ProbeError("host detiler rmort table has an incomplete permutation")
    expected_coordinates = {
        (x, y) for y in range(MICROTILE_HEIGHT) for x in range(MICROTILE_WIDTH)
    }
    if set(coordinates) != expected_coordinates:
        raise probe.ProbeError(
            "host detiler rmort coordinates are incomplete or duplicated"
        )
    for tiled_index, (x, y) in enumerate(coordinates):
        if _thin_morton_index(x, y) != tiled_index:
            raise probe.ProbeError(
                "host detiler rmort table disagrees with thin Morton bit order"
            )
    coordinate_bytes = bytes(
        value for coordinate in coordinates for value in coordinate
    )
    return coordinates, {
        "all_sources_agree": True,
        "coordinate_count": len(coordinates),
        "coordinate_permutation_sha256": hashlib.sha256(coordinate_bytes).hexdigest(),
        "inverse_morton_lut_sha256": hashlib.sha256(packed_words).hexdigest(),
        "morton_bit_order": "x0,y0,x1,y1,x2,y2",
        "sources": sources,
    }


def _validate_mip_geometry(
    raw_mip: dict[str, object],
    *,
    mip_index: int,
    fields: dict[str, object],
    payload_absolute_start: int,
    payload_relative_start: int,
    expected_payload_offset: int,
    max_mip_bytes: int,
) -> tuple[dict[str, int], int]:
    observed_index = _require_int(raw_mip.get("index"), "BITMAP mip index")
    if observed_index != mip_index:
        raise probe.ProbeError("BITMAP mip indexes are not canonical")
    pitch = _require_int(fields.get("pitch"), "BITMAP descriptor pitch")
    height = _require_int(fields.get("height"), "BITMAP descriptor height")
    bits_per_element = _require_int(
        fields.get("bits_per_block"), "BITMAP element width"
    )
    if bits_per_element not in (32, 64, 128):
        raise probe.ProbeError(
            f"BITMAP element width {bits_per_element} is unsupported"
        )
    element_bytes = bits_per_element // 8
    is_block = _require_bool(fields.get("is_block_coded"), "BITMAP block-coding flag")
    pow2pad = _require_bool(fields.get("pow2pad"), "BITMAP power-of-two flag")

    logical_pitch = max(pitch >> mip_index, 1)
    logical_height = max(height >> mip_index, 1)
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
    mip_bytes = aligned_pitch * aligned_height * element_bytes
    alignment_iterations = 0
    while mip_bytes % 256:
        aligned_pitch += 8
        mip_bytes = aligned_pitch * aligned_height * element_bytes
        alignment_iterations += 1
        if alignment_iterations > 256:
            raise probe.ProbeError("microtile alignment did not converge")
    if mip_bytes < 1 or mip_bytes > max_mip_bytes:
        raise probe.ProbeError(
            f"BITMAP mip exceeds the {max_mip_bytes}-byte transform budget"
        )
    _, expected_payload_end = _checked_u64_range(
        expected_payload_offset, mip_bytes, f"BITMAP mip {mip_index} payload range"
    )
    expected_values = {
        "aligned_storage_height": aligned_height,
        "aligned_storage_pitch": aligned_pitch,
        "bytes": mip_bytes,
        "logical_height": logical_height,
        "logical_pitch": logical_pitch,
        "payload_offset_end": expected_payload_end,
        "payload_offset_start": expected_payload_offset,
        "storage_height": storage_height,
        "storage_pitch": storage_pitch,
    }
    for key, expected in expected_values.items():
        observed = _require_int(raw_mip.get(key), f"BITMAP mip {mip_index} {key}")
        if observed != expected:
            raise probe.ProbeError(f"BITMAP mip {mip_index} has inconsistent {key}")
    absolute_start = _require_int(
        raw_mip.get("absolute_start"), f"BITMAP mip {mip_index} absolute start"
    )
    absolute_end = _require_int(
        raw_mip.get("absolute_end"), f"BITMAP mip {mip_index} absolute end"
    )
    relative_start = _require_int(
        raw_mip.get("data_relative_start"),
        f"BITMAP mip {mip_index} relative start",
    )
    relative_end = _require_int(
        raw_mip.get("data_relative_end"), f"BITMAP mip {mip_index} relative end"
    )
    if (
        absolute_start != payload_absolute_start + expected_payload_offset
        or absolute_end != payload_absolute_start + expected_payload_end
        or relative_start != payload_relative_start + expected_payload_offset
        or relative_end != payload_relative_start + expected_payload_end
    ):
        raise probe.ProbeError(f"BITMAP mip {mip_index} has inconsistent source ranges")
    return {
        "absolute_start": absolute_start,
        "aligned_storage_height": aligned_height,
        "aligned_storage_pitch": aligned_pitch,
        "bytes": mip_bytes,
        "element_bits": bits_per_element,
        "element_bytes": element_bytes,
        "index": mip_index,
        "logical_height": logical_height,
        "logical_pitch": logical_pitch,
        "storage_height": storage_height,
        "storage_pitch": storage_pitch,
    }, expected_payload_end


def _validate_bitmap_report(
    report: dict[str, object],
    *,
    expected_xpps_sha256: str,
    expected_eboot_sha256: str,
    selected_row_index: int,
    max_descriptors: int,
    max_mips: int,
    max_mip_bytes: int,
    max_total_bytes: int,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    int,
    int,
]:
    if report.get("schema") != bitmaps.SCHEMA:
        raise probe.ProbeError("inherited BITMAP report has an unexpected schema")
    if report.get("version") != bitmaps.SCHEMA_VERSION:
        raise probe.ProbeError("inherited BITMAP report has an unexpected version")
    if report.get("proof_class") != bitmaps.PROOF_CLASS:
        raise probe.ProbeError("inherited BITMAP report has an unexpected proof class")

    xpps_identity = _require_dict(report.get("xpps"), "inherited XPPS identity")
    eboot_identity = _require_dict(report.get("eboot"), "inherited eboot identity")
    xpps_size = _require_int(xpps_identity.get("bytes"), "inherited XPPS bytes")
    eboot_size = _require_int(eboot_identity.get("bytes"), "inherited eboot bytes")
    if xpps_size < 1 or eboot_size < 1:
        raise probe.ProbeError("inherited source identity has an invalid byte size")
    if xpps_identity.get("sha256") != expected_xpps_sha256:
        raise probe.ProbeError("inherited BITMAP report has the wrong XPPS hash")
    if eboot_identity.get("sha256") != expected_eboot_sha256:
        raise probe.ProbeError("inherited BITMAP report has the wrong eboot hash")

    decoder = _require_dict(report.get("decoder_contract"), "BITMAP decoder contract")
    expected_decoder: dict[str, object] = {
        "array_mode": "Array1DTiledThin1",
        "microtile_height": MICROTILE_HEIGHT,
        "microtile_width": MICROTILE_WIDTH,
        "tile_mode": {"id": 13, "name": "Thin1DThin"},
    }
    for key, expected in expected_decoder.items():
        if decoder.get(key) != expected:
            raise probe.ProbeError(f"BITMAP decoder contract has inconsistent {key}")

    selected_row = _require_dict(
        report.get("selected_dic_row"), "inherited selected DIC row"
    )
    if (
        _require_int(selected_row.get("index"), "selected DIC row index")
        != selected_row_index
    ):
        raise probe.ProbeError("inherited BITMAP report selected the wrong DIC row")
    if (
        _require_int(selected_row.get("kind_class_high16"), "selected DIC row kind")
        != 2
    ):
        raise probe.ProbeError("inherited selected DIC row is not high-kind-2")

    layout = _require_dict(report.get("layout"), "BITMAP layout")
    data_start = _require_int(layout.get("data_start"), "BITMAP data start")
    data_size = _require_int(layout.get("data_size"), "BITMAP data size")
    _, data_end = _checked_u64_range(data_start, data_size, "BITMAP data region")
    if data_end > xpps_size:
        raise probe.ProbeError("BITMAP data region exceeds the inherited XPPS size")

    raw_descriptors = _require_list(report.get("descriptors"), "BITMAP descriptors")
    if not raw_descriptors or len(raw_descriptors) > max_descriptors:
        raise probe.ProbeError(
            f"BITMAP descriptor count is outside the 1 through {max_descriptors} budget"
        )
    descriptors: list[dict[str, object]] = []
    total_mips = 0
    total_bytes = 0
    previous_payload_end: int | None = None
    for descriptor_index, raw_descriptor_item in enumerate(raw_descriptors):
        item = _require_dict(raw_descriptor_item, "BITMAP descriptor item")
        descriptor = _require_dict(item.get("descriptor"), "BITMAP descriptor")
        fields = _require_dict(descriptor.get("fields"), "BITMAP descriptor fields")
        descriptor_sha = _require_string(
            descriptor.get("sha256"), "BITMAP descriptor SHA-256"
        )
        registry._validate_sha256(descriptor_sha, "BITMAP descriptor SHA-256")

        data_format = _require_dict(fields.get("data_format"), "BITMAP data format")
        format_id = _require_int(data_format.get("id"), "BITMAP data format ID")
        format_info = bitmaps.FORMAT_INFO.get(format_id)
        if format_info is None:
            raise probe.ProbeError(f"BITMAP data format {format_id} is unsupported")
        format_name, bits_per_element, is_block = format_info
        if data_format.get("name") != format_name:
            raise probe.ProbeError("BITMAP data format name disagrees with its ID")
        if fields.get("bits_per_block") != bits_per_element:
            raise probe.ProbeError("BITMAP element width disagrees with its format")
        if fields.get("is_block_coded") is not is_block:
            raise probe.ProbeError("BITMAP block geometry disagrees with its format")
        image_type = _require_dict(fields.get("image_type"), "BITMAP image type")
        image_type_id = _require_int(image_type.get("id"), "BITMAP image type ID")
        if image_type.get("name") != bitmaps.IMAGE_TYPES.get(image_type_id):
            raise probe.ProbeError("BITMAP image type name disagrees with its ID")
        tile_mode = _require_dict(fields.get("tile_mode"), "BITMAP tile mode")
        if tile_mode != {"id": 13, "name": "Thin1DThin"}:
            raise probe.ProbeError("BITMAP descriptor is not Thin1DThin")
        one_layer_fields = {
            "base_array": 0,
            "base_level": 0,
            "depth": 1,
            "last_array": 0,
            "sample_count": 1,
        }
        for key, expected in one_layer_fields.items():
            if _require_int(fields.get(key), f"BITMAP descriptor {key}") != expected:
                raise probe.ProbeError(f"BITMAP descriptor has unsupported {key}")

        payload = _require_dict(item.get("payload"), "BITMAP payload")
        payload_start = _require_int(
            payload.get("absolute_start"), "BITMAP payload absolute start"
        )
        payload_end = _require_int(
            payload.get("absolute_end"), "BITMAP payload absolute end"
        )
        payload_bytes = _require_int(payload.get("bytes"), "BITMAP payload bytes")
        relative_start = _require_int(
            payload.get("data_relative_start"), "BITMAP payload relative start"
        )
        relative_end = _require_int(
            payload.get("data_relative_end"), "BITMAP payload relative end"
        )
        _, expected_payload_end = _checked_u64_range(
            payload_start, payload_bytes, "BITMAP payload"
        )
        _, expected_relative_end = _checked_u64_range(
            relative_start, payload_bytes, "BITMAP relative payload"
        )
        if (
            payload_end != expected_payload_end
            or relative_end != expected_relative_end
            or payload_start != data_start + relative_start
            or payload_end > data_end
        ):
            raise probe.ProbeError("BITMAP payload has inconsistent ranges")
        if previous_payload_end is not None and payload_start < previous_payload_end:
            raise probe.ProbeError(
                "BITMAP payloads are not canonical and nonoverlapping"
            )
        previous_payload_end = payload_end
        owner = _require_dict(payload.get("owning_row"), "BITMAP payload owner")
        if _require_int(owner.get("kind_class_high16"), "BITMAP row kind") != 1:
            raise probe.ProbeError("BITMAP payload is not owned by a high-kind-1 row")
        payload_sha = _require_string(payload.get("sha256"), "BITMAP payload SHA-256")
        registry._validate_sha256(payload_sha, "BITMAP payload SHA-256")

        raw_mips = _require_list(payload.get("mips"), "BITMAP payload mips")
        level_count = _require_int(fields.get("level_count"), "BITMAP level count")
        if len(raw_mips) != level_count or not raw_mips:
            raise probe.ProbeError("BITMAP mip count disagrees with its descriptor")
        total_mips += len(raw_mips)
        if total_mips > max_mips:
            raise probe.ProbeError(
                f"BITMAP mip count exceeds the {max_mips}-mip budget"
            )
        normalized_mips: list[dict[str, int]] = []
        payload_offset = 0
        for mip_index, raw_mip_value in enumerate(raw_mips):
            raw_mip = _require_dict(raw_mip_value, f"BITMAP mip {mip_index}")
            normalized, payload_offset = _validate_mip_geometry(
                raw_mip,
                mip_index=mip_index,
                fields=fields,
                payload_absolute_start=payload_start,
                payload_relative_start=relative_start,
                expected_payload_offset=payload_offset,
                max_mip_bytes=max_mip_bytes,
            )
            normalized_mips.append(normalized)
        if payload_offset != payload_bytes:
            raise probe.ProbeError("BITMAP mips do not exactly cover their payload")
        total_bytes += payload_bytes
        if total_bytes > max_total_bytes:
            raise probe.ProbeError(
                f"BITMAP bytes exceed the {max_total_bytes}-byte transform budget"
            )
        descriptors.append(
            {
                "descriptor_index": descriptor_index,
                "descriptor_sha256": descriptor_sha,
                "element_bits": bits_per_element,
                "format_id": format_id,
                "format_name": format_name,
                "mips": normalized_mips,
                "payload_absolute_start": payload_start,
                "payload_bytes": payload_bytes,
                "payload_relative_start": relative_start,
                "payload_sha256": payload_sha,
            }
        )

    facts = _require_dict(report.get("facts"), "BITMAP facts")
    if _require_int(facts.get("distinct_bitmap_targets"), "BITMAP target count") != len(
        descriptors
    ):
        raise probe.ProbeError("BITMAP aggregate descriptor count is inconsistent")
    if (
        _require_int(facts.get("total_payload_bytes"), "BITMAP total bytes")
        != total_bytes
    ):
        raise probe.ProbeError("BITMAP aggregate byte count is inconsistent")
    inherited_identity = {
        "proof_class": report.get("proof_class"),
        "report_sha256": hashlib.sha256(bitmaps.encode_report(report)).hexdigest(),
        "schema": report.get("schema"),
        "selected_dic_row": selected_row,
        "version": report.get("version"),
    }
    return (
        xpps_identity,
        eboot_identity,
        inherited_identity,
        descriptors,
        total_mips,
        total_bytes,
    )


def _deswizzle_mip(
    tiled: bytes,
    *,
    pitch: int,
    height: int,
    element_bytes: int,
    host_coordinates: list[tuple[int, int]],
) -> bytearray:
    expected_size = pitch * height * element_bytes
    if (
        pitch < 1
        or height < 1
        or pitch % MICROTILE_WIDTH
        or height % MICROTILE_HEIGHT
        or len(tiled) != expected_size
        or element_bytes not in (4, 8, 16)
        or len(host_coordinates) != MICROTILE_ELEMENTS
    ):
        raise probe.ProbeError("Thin1D deswizzle received inconsistent geometry")
    linear = bytearray(expected_size)
    tiles_per_row = pitch // MICROTILE_WIDTH
    tiles_per_column = height // MICROTILE_HEIGHT
    for tile_y in range(tiles_per_column):
        for tile_x in range(tiles_per_row):
            tile_index = tile_y * tiles_per_row + tile_x
            tiled_tile_start = tile_index * MICROTILE_ELEMENTS * element_bytes
            for tiled_index, (local_x, local_y) in enumerate(host_coordinates):
                source = tiled_tile_start + tiled_index * element_bytes
                x = tile_x * MICROTILE_WIDTH + local_x
                y = tile_y * MICROTILE_HEIGHT + local_y
                destination = (y * pitch + x) * element_bytes
                linear[destination : destination + element_bytes] = tiled[
                    source : source + element_bytes
                ]
    return linear


def _retile_mip(
    linear: bytearray, *, pitch: int, height: int, element_bytes: int
) -> bytearray:
    expected_size = pitch * height * element_bytes
    if (
        pitch < 1
        or height < 1
        or pitch % MICROTILE_WIDTH
        or height % MICROTILE_HEIGHT
        or len(linear) != expected_size
        or element_bytes not in (4, 8, 16)
    ):
        raise probe.ProbeError("Thin1D retile received inconsistent geometry")
    tiled = bytearray(expected_size)
    tiles_per_row = pitch // MICROTILE_WIDTH
    for y in range(height):
        tile_y, local_y = divmod(y, MICROTILE_HEIGHT)
        for x in range(pitch):
            tile_x, local_x = divmod(x, MICROTILE_WIDTH)
            tile_index = tile_y * tiles_per_row + tile_x
            destination_element = tile_index * MICROTILE_ELEMENTS + _thin_morton_index(
                local_x, local_y
            )
            source = (y * pitch + x) * element_bytes
            destination = destination_element * element_bytes
            tiled[destination : destination + element_bytes] = linear[
                source : source + element_bytes
            ]
    return tiled


def prove_thin1d_roundtrip(
    xpps: Path,
    *,
    expected_xpps_sha256: str,
    row_index: int,
    eboot: Path,
    expected_eboot_sha256: str,
    max_descriptors: int = MAX_DESCRIPTORS,
    max_mips: int = MAX_MIPS,
    max_mip_bytes: int = MAX_MIP_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    resolver_overrides: dict[str, object] | None = None,
    shader_root: Path | None = None,
) -> dict[str, object]:
    registry._validate_sha256(expected_xpps_sha256, "expected XPPS SHA-256")
    registry._validate_sha256(expected_eboot_sha256, "expected eboot SHA-256")
    if max_descriptors < 1 or max_descriptors > MAX_DESCRIPTORS:
        raise probe.ProbeError(
            f"descriptor budget must be from 1 through {MAX_DESCRIPTORS}"
        )
    if max_mips < 1 or max_mips > MAX_MIPS:
        raise probe.ProbeError(f"mip budget must be from 1 through {MAX_MIPS}")
    if max_mip_bytes < 1 or max_mip_bytes > MAX_MIP_BYTES:
        raise probe.ProbeError(
            f"per-mip byte budget must be from 1 through {MAX_MIP_BYTES}"
        )
    if max_total_bytes < 1 or max_total_bytes > MAX_TOTAL_BYTES:
        raise probe.ProbeError(
            f"total byte budget must be from 1 through {MAX_TOTAL_BYTES}"
        )

    bitmap_report = bitmaps.classify_bitmap_descriptors(
        Path(xpps),
        expected_xpps_sha256=expected_xpps_sha256,
        row_index=row_index,
        eboot=Path(eboot),
        expected_eboot_sha256=expected_eboot_sha256,
        max_bitmap_entries=max_descriptors,
        max_total_payload_bytes=max_total_bytes,
        resolver_overrides=resolver_overrides,
    )
    (
        xpps_identity,
        eboot_identity,
        inherited_identity,
        descriptors,
        total_mips,
        total_bytes,
    ) = _validate_bitmap_report(
        bitmap_report,
        expected_xpps_sha256=expected_xpps_sha256,
        expected_eboot_sha256=expected_eboot_sha256,
        selected_row_index=row_index,
        max_descriptors=max_descriptors,
        max_mips=max_mips,
        max_mip_bytes=max_mip_bytes,
        max_total_bytes=max_total_bytes,
    )
    effective_shader_root = (
        Path(shader_root)
        if shader_root is not None
        else Path(__file__).resolve().parents[1]
    )
    host_coordinates, shader_contract = _load_host_permutation(effective_shader_root)

    public_descriptors: list[dict[str, object]] = []
    element_width_counts: Counter[str] = Counter()
    with ExitStack() as stack:
        xpps_stream = registry._open_regular(stack, Path(xpps), "XPPS source")
        eboot_stream = registry._open_regular(stack, Path(eboot), "eboot")
        xpps_before = os.fstat(xpps_stream.fileno())
        eboot_before = os.fstat(eboot_stream.fileno())
        if xpps_before.st_size != _require_int(
            xpps_identity.get("bytes"), "XPPS bytes"
        ):
            raise probe.ProbeError("XPPS size changed after inherited classification")
        if eboot_before.st_size != _require_int(
            eboot_identity.get("bytes"), "eboot bytes"
        ):
            raise probe.ProbeError("eboot size changed after inherited classification")
        if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError("XPPS hash changed after inherited classification")
        if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot hash changed after inherited classification")

        for descriptor in descriptors:
            element_bits = _require_int(
                descriptor.get("element_bits"), "normalized element width"
            )
            element_bytes = element_bits // 8
            element_width_counts[str(element_bits)] += 1
            payload_hash = hashlib.sha256()
            linear_chain_hash = hashlib.sha256()
            retiled_chain_hash = hashlib.sha256()
            public_mips: list[dict[str, object]] = []
            for raw_mip in _require_list(descriptor.get("mips"), "normalized mips"):
                mip = _require_dict(raw_mip, "normalized mip")
                mip_index = _require_int(mip.get("index"), "normalized mip index")
                mip_bytes = _require_int(mip.get("bytes"), "normalized mip bytes")
                tiled = _read_exact_at(
                    xpps_stream,
                    _require_int(mip.get("absolute_start"), "normalized mip start"),
                    mip_bytes,
                    f"BITMAP mip {mip_index}",
                )
                pitch = _require_int(
                    mip.get("aligned_storage_pitch"), "normalized mip pitch"
                )
                height = _require_int(
                    mip.get("aligned_storage_height"), "normalized mip height"
                )
                try:
                    linear = _deswizzle_mip(
                        tiled,
                        pitch=pitch,
                        height=height,
                        element_bytes=element_bytes,
                        host_coordinates=host_coordinates,
                    )
                    retiled = _retile_mip(
                        linear,
                        pitch=pitch,
                        height=height,
                        element_bytes=element_bytes,
                    )
                except MemoryError as error:
                    raise probe.ProbeError(
                        f"BITMAP mip {mip_index} transform allocation failed"
                    ) from error
                if retiled != tiled:
                    raise probe.ProbeError(
                        f"BITMAP mip {mip_index} failed byte-exact Thin1D roundtrip"
                    )
                payload_hash.update(tiled)
                linear_chain_hash.update(linear)
                retiled_chain_hash.update(retiled)
                tiled_sha = hashlib.sha256(tiled).hexdigest()
                retiled_sha = hashlib.sha256(retiled).hexdigest()
                if tiled_sha != retiled_sha:
                    raise probe.ProbeError(
                        f"BITMAP mip {mip_index} retiled hash disagrees with its source"
                    )
                public_mips.append(
                    {
                        "aligned_storage_height": height,
                        "aligned_storage_pitch": pitch,
                        "bytes": mip_bytes,
                        "element_bits": element_bits,
                        "exact_roundtrip": True,
                        "index": mip_index,
                        "linear_padded_sha256": hashlib.sha256(linear).hexdigest(),
                        "logical_height": _require_int(
                            mip.get("logical_height"), "normalized logical height"
                        ),
                        "logical_pitch": _require_int(
                            mip.get("logical_pitch"), "normalized logical pitch"
                        ),
                        "retiled_sha256": retiled_sha,
                        "storage_height": _require_int(
                            mip.get("storage_height"), "normalized storage height"
                        ),
                        "storage_pitch": _require_int(
                            mip.get("storage_pitch"), "normalized storage pitch"
                        ),
                        "tiled_sha256": tiled_sha,
                    }
                )
            inherited_payload_sha = _require_string(
                descriptor.get("payload_sha256"), "normalized payload SHA-256"
            )
            if payload_hash.hexdigest() != inherited_payload_sha:
                raise probe.ProbeError(
                    "BITMAP payload bytes disagree with the inherited payload hash"
                )
            if retiled_chain_hash.hexdigest() != inherited_payload_sha:
                raise probe.ProbeError(
                    "BITMAP retiled chain disagrees with the inherited payload hash"
                )
            public_descriptors.append(
                {
                    "descriptor_index": _require_int(
                        descriptor.get("descriptor_index"), "descriptor index"
                    ),
                    "descriptor_sha256": _require_string(
                        descriptor.get("descriptor_sha256"), "descriptor SHA-256"
                    ),
                    "element_bits": element_bits,
                    "format": {
                        "id": _require_int(descriptor.get("format_id"), "format ID"),
                        "name": _require_string(
                            descriptor.get("format_name"), "format name"
                        ),
                    },
                    "linear_padded_chain_sha256": linear_chain_hash.hexdigest(),
                    "mips": public_mips,
                    "payload_bytes": _require_int(
                        descriptor.get("payload_bytes"), "payload bytes"
                    ),
                    "payload_data_relative_start": _require_int(
                        descriptor.get("payload_relative_start"),
                        "payload relative start",
                    ),
                    "retiled_chain_sha256": retiled_chain_hash.hexdigest(),
                    "tiled_payload_sha256": inherited_payload_sha,
                }
            )

        if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError("XPPS changed during Thin1D roundtrip proof")
        if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot changed during Thin1D roundtrip proof")
        if registry._identity(os.fstat(xpps_stream.fileno())) != registry._identity(
            xpps_before
        ):
            raise probe.ProbeError(
                "XPPS identity changed during Thin1D roundtrip proof"
            )
        if registry._identity(os.fstat(eboot_stream.fileno())) != registry._identity(
            eboot_before
        ):
            raise probe.ProbeError(
                "eboot identity changed during Thin1D roundtrip proof"
            )

    return {
        "descriptors": public_descriptors,
        "eboot": eboot_identity,
        "facts": {
            "all_mips_byte_exact_roundtrip": True,
            "descriptor_count": len(public_descriptors),
            "element_width_descriptor_counts": dict(
                sorted(element_width_counts.items())
            ),
            "mip_count": total_mips,
            "total_payload_bytes": total_bytes,
        },
        "host_shader_contract": shader_contract,
        "inherited_bitmap_proof": inherited_identity,
        "non_claims": list(NON_CLAIMS),
        "proof_class": PROOF_CLASS,
        "schema": SCHEMA,
        "version": SCHEMA_VERSION,
        "warnings": [],
        "xpps": xpps_identity,
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prove byte-exact Thin1DThin deswizzle/retile for Second Son BITMAPs."
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
        report = prove_thin1d_roundtrip(
            args.input,
            expected_xpps_sha256=args.expected_xpps_sha256,
            row_index=args.row,
            eboot=args.eboot,
            expected_eboot_sha256=args.expected_eboot_sha256,
        )
        sys.stdout.buffer.write(encode_report(report))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_thin1d_roundtrip: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
