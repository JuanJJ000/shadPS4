#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Export proven Second Son XPPS BITMAP mip chains as guarded DDS files."""

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

import second_son_xpps_bitmap_descriptors as bitmaps
import second_son_xpps_eboot_registry as registry
import second_son_xpps_probe as probe
import second_son_xpps_thin1d_roundtrip as roundtrip

SCHEMA = "shadps4.second_son_xpps_dds_export"
SCHEMA_VERSION = 1
PROOF_CLASS = "xpps_dds_export"
MANIFEST_NAME = "manifest.json"
MAX_DESCRIPTORS = 256
MAX_MIPS = 4096
MAX_MIP_BYTES = 64 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_DDS_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
U64_LIMIT = 1 << 64

DDS_MAGIC = b"DDS "
DDS_HEADER = struct.Struct("<31I")
DDS_DX10_HEADER = struct.Struct("<5I")
DDS_HEADER_BYTES = len(DDS_MAGIC) + DDS_HEADER.size + DDS_DX10_HEADER.size
DDS_HEADER_SIZE = 124
DDS_PIXEL_FORMAT_SIZE = 32
DDS_FOURCC_DX10 = struct.unpack("<I", b"DX10")[0]

DDSD_CAPS = 0x00000001
DDSD_HEIGHT = 0x00000002
DDSD_WIDTH = 0x00000004
DDSD_PITCH = 0x00000008
DDSD_PIXELFORMAT = 0x00001000
DDSD_MIPMAPCOUNT = 0x00020000
DDSD_LINEARSIZE = 0x00080000
DDPF_FOURCC = 0x00000004
DDSCAPS_COMPLEX = 0x00000008
DDSCAPS_TEXTURE = 0x00001000
DDSCAPS_MIPMAP = 0x00400000
D3D10_RESOURCE_DIMENSION_TEXTURE1D = 2
D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3

DDS_FORMATS: dict[int, dict[str, object]] = {
    10: {
        "block_coded": False,
        "dxgi_format": 28,
        "dxgi_name": "DXGI_FORMAT_R8G8B8A8_UNORM",
        "element_bytes": 4,
        "filename_tag": "rgba8",
        "xpps_name": "Format8_8_8_8",
    },
    35: {
        "block_coded": True,
        "dxgi_format": 71,
        "dxgi_name": "DXGI_FORMAT_BC1_UNORM",
        "element_bytes": 8,
        "filename_tag": "bc1",
        "xpps_name": "FormatBc1",
    },
    37: {
        "block_coded": True,
        "dxgi_format": 77,
        "dxgi_name": "DXGI_FORMAT_BC3_UNORM",
        "element_bytes": 16,
        "filename_tag": "bc3",
        "xpps_name": "FormatBc3",
    },
    38: {
        "block_coded": True,
        "dxgi_format": 80,
        "dxgi_name": "DXGI_FORMAT_BC4_UNORM",
        "element_bytes": 8,
        "filename_tag": "bc4",
        "xpps_name": "FormatBc4",
    },
    39: {
        "block_coded": True,
        "dxgi_format": 83,
        "dxgi_name": "DXGI_FORMAT_BC5_UNORM",
        "element_bytes": 16,
        "filename_tag": "bc5",
        "xpps_name": "FormatBc5",
    },
}
DXGI_TO_FORMAT_ID = {
    int(format_info["dxgi_format"]): format_id
    for format_id, format_info in DDS_FORMATS.items()
}
IMAGE_TYPE_DIMENSIONS = {
    8: ("Color1D", D3D10_RESOURCE_DIMENSION_TEXTURE1D, "TEXTURE1D"),
    9: ("Color2D", D3D10_RESOURCE_DIMENSION_TEXTURE2D, "TEXTURE2D"),
}
NON_CLAIMS = (
    "channel_swizzle",
    "alpha_semantics",
    "color_space",
    "decoded_visual_correctness",
    "artwork_identity",
    "editability",
    "compression_encoder_equivalence",
    "padding_reconstruction_from_dds",
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


def _validate_format(
    format_id: int, format_name: str, element_bits: int
) -> dict[str, object]:
    format_info = DDS_FORMATS.get(format_id)
    if format_info is None:
        raise probe.ProbeError(f"XPPS format {format_id} has no DDS mapping")
    if format_info["xpps_name"] != format_name:
        raise probe.ProbeError("XPPS format name disagrees with its DDS mapping")
    if int(format_info["element_bytes"]) * 8 != element_bits:
        raise probe.ProbeError("XPPS element width disagrees with its DDS mapping")
    return format_info


def _mip_logical_layout(
    width: int, height: int, format_info: dict[str, object]
) -> tuple[int, int, int, int]:
    if width < 1 or height < 1:
        raise probe.ProbeError("DDS logical dimensions must be positive")
    element_width = width
    element_height = height
    if format_info["block_coded"] is True:
        element_width = max((width + 3) // 4, 1)
        element_height = max((height + 3) // 4, 1)
    element_bytes = int(format_info["element_bytes"])
    row_bytes = element_width * element_bytes
    mip_bytes = row_bytes * element_height
    if row_bytes < 1 or mip_bytes < 1:
        raise probe.ProbeError("DDS logical mip byte size is invalid")
    return element_width, element_height, row_bytes, mip_bytes


def _build_dds_header(
    *,
    width: int,
    height: int,
    mip_count: int,
    top_mip_bytes: int,
    format_id: int,
    image_type_id: int,
) -> bytes:
    format_info = DDS_FORMATS.get(format_id)
    image_type = IMAGE_TYPE_DIMENSIONS.get(image_type_id)
    if format_info is None or image_type is None:
        raise probe.ProbeError(
            "DDS header received an unsupported format or image type"
        )
    if width < 1 or height < 1 or mip_count < 1 or mip_count > 16:
        raise probe.ProbeError("DDS header dimensions or mip count are invalid")
    _, resource_dimension, _ = image_type
    if resource_dimension == D3D10_RESOURCE_DIMENSION_TEXTURE1D and height != 1:
        raise probe.ProbeError("DDS TEXTURE1D height must be one")
    _, _, top_row_bytes, expected_top_bytes = _mip_logical_layout(
        width, height, format_info
    )
    if top_mip_bytes != expected_top_bytes:
        raise probe.ProbeError("DDS top-mip byte size disagrees with its dimensions")

    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT
    pitch_or_linear = top_row_bytes
    if format_info["block_coded"] is True:
        flags |= DDSD_LINEARSIZE
        pitch_or_linear = top_mip_bytes
    else:
        flags |= DDSD_PITCH
    caps = DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
    values = [
        DDS_HEADER_SIZE,
        flags,
        height,
        width,
        pitch_or_linear,
        0,
        mip_count,
        *([0] * 11),
        DDS_PIXEL_FORMAT_SIZE,
        DDPF_FOURCC,
        DDS_FOURCC_DX10,
        0,
        0,
        0,
        0,
        0,
        caps,
        0,
        0,
        0,
        0,
    ]
    if len(values) != 31:
        raise probe.ProbeError("internal DDS header field count is invalid")
    dx10 = DDS_DX10_HEADER.pack(
        int(format_info["dxgi_format"]),
        resource_dimension,
        0,
        1,
        0,
    )
    return DDS_MAGIC + DDS_HEADER.pack(*values) + dx10


def _parse_dds(data: bytes) -> dict[str, object]:
    if len(data) < DDS_HEADER_BYTES:
        raise probe.ProbeError("DDS file is truncated before its DX10 header")
    if data[:4] != DDS_MAGIC:
        raise probe.ProbeError("DDS file has the wrong magic")
    fields = DDS_HEADER.unpack_from(data, len(DDS_MAGIC))
    if fields[0] != DDS_HEADER_SIZE or fields[18] != DDS_PIXEL_FORMAT_SIZE:
        raise probe.ProbeError("DDS header or pixel-format size is invalid")
    if fields[19] != DDPF_FOURCC or fields[20] != DDS_FOURCC_DX10:
        raise probe.ProbeError("DDS pixel format is not the exact DX10 FourCC form")
    if any(fields[index] for index in range(7, 18)):
        raise probe.ProbeError("DDS reserved header words are nonzero")
    if any(fields[index] for index in range(21, 26)):
        raise probe.ProbeError("DDS legacy pixel-format words are nonzero")
    if fields[5] != 0 or any(fields[index] for index in range(27, 31)):
        raise probe.ProbeError("DDS depth, caps2-4, or reserved2 is nonzero")

    dxgi_format, resource_dimension, misc_flag, array_size, misc_flags2 = (
        DDS_DX10_HEADER.unpack_from(data, len(DDS_MAGIC) + DDS_HEADER.size)
    )
    format_id = DXGI_TO_FORMAT_ID.get(dxgi_format)
    if format_id is None:
        raise probe.ProbeError(f"DDS DXGI format {dxgi_format} is unsupported")
    format_info = DDS_FORMATS[format_id]
    if resource_dimension not in (
        D3D10_RESOURCE_DIMENSION_TEXTURE1D,
        D3D10_RESOURCE_DIMENSION_TEXTURE2D,
    ):
        raise probe.ProbeError("DDS DX10 resource dimension is unsupported")
    if misc_flag != 0 or array_size != 1 or misc_flags2 != 0:
        raise probe.ProbeError("DDS DX10 misc flags or array size are unsupported")

    height = fields[2]
    width = fields[3]
    mip_count = fields[6]
    if width < 1 or height < 1 or mip_count < 1 or mip_count > 16:
        raise probe.ProbeError("DDS dimensions or mip count are invalid")
    if resource_dimension == D3D10_RESOURCE_DIMENSION_TEXTURE1D and height != 1:
        raise probe.ProbeError("DDS TEXTURE1D height is not one")
    expected_flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT
    expected_caps = DDSCAPS_TEXTURE
    if format_info["block_coded"] is True:
        expected_flags |= DDSD_LINEARSIZE
    else:
        expected_flags |= DDSD_PITCH
    if mip_count > 1:
        expected_flags |= DDSD_MIPMAPCOUNT
        expected_caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
    if fields[1] != expected_flags or fields[26] != expected_caps:
        raise probe.ProbeError("DDS flags or caps are not canonical")

    mip_ranges: list[dict[str, int]] = []
    offset = DDS_HEADER_BYTES
    top_pitch_or_linear = 0
    for mip_index in range(mip_count):
        mip_width = max(width >> mip_index, 1)
        mip_height = max(height >> mip_index, 1)
        element_width, element_height, row_bytes, mip_bytes = _mip_logical_layout(
            mip_width, mip_height, format_info
        )
        if mip_index == 0:
            top_pitch_or_linear = (
                mip_bytes if format_info["block_coded"] is True else row_bytes
            )
        start, end = _checked_u64_range(offset, mip_bytes, f"DDS mip {mip_index}")
        if end > len(data):
            raise probe.ProbeError(f"DDS mip {mip_index} is truncated")
        mip_ranges.append(
            {
                "bytes": mip_bytes,
                "element_height": element_height,
                "element_width": element_width,
                "file_offset_end": end,
                "file_offset_start": start,
                "height": mip_height,
                "index": mip_index,
                "row_bytes": row_bytes,
                "width": mip_width,
            }
        )
        offset = end
    if fields[4] != top_pitch_or_linear:
        raise probe.ProbeError("DDS top-level pitch or linear size is inconsistent")
    if offset != len(data):
        raise probe.ProbeError("DDS file has trailing bytes after its mip chain")
    dimension_name = (
        "TEXTURE1D"
        if resource_dimension == D3D10_RESOURCE_DIMENSION_TEXTURE1D
        else "TEXTURE2D"
    )
    return {
        "array_size": array_size,
        "dxgi_format": dxgi_format,
        "dxgi_name": format_info["dxgi_name"],
        "format_id": format_id,
        "header_bytes": DDS_HEADER_BYTES,
        "height": height,
        "mip_count": mip_count,
        "mips": mip_ranges,
        "payload_bytes": len(data) - DDS_HEADER_BYTES,
        "resource_dimension": resource_dimension,
        "resource_dimension_name": dimension_name,
        "width": width,
    }


def _crop_logical_mip(
    linear: bytearray,
    *,
    padded_pitch: int,
    padded_height: int,
    logical_width: int,
    logical_height: int,
    format_info: dict[str, object],
) -> tuple[bytes, dict[str, int]]:
    element_bytes = int(format_info["element_bytes"])
    expected_padded_bytes = padded_pitch * padded_height * element_bytes
    if padded_pitch < 1 or padded_height < 1 or len(linear) != expected_padded_bytes:
        raise probe.ProbeError("padded linear mip has inconsistent geometry")
    element_width, element_height, row_bytes, mip_bytes = _mip_logical_layout(
        logical_width, logical_height, format_info
    )
    if element_width > padded_pitch or element_height > padded_height:
        raise probe.ProbeError("logical DDS crop exceeds padded linear storage")
    padded_row_bytes = padded_pitch * element_bytes
    cropped = bytearray(mip_bytes)
    destination = 0
    for row in range(element_height):
        source = row * padded_row_bytes
        cropped[destination : destination + row_bytes] = linear[
            source : source + row_bytes
        ]
        destination += row_bytes
    if destination != mip_bytes:
        raise probe.ProbeError("logical DDS crop did not exactly fill its mip")
    return bytes(cropped), {
        "element_height": element_height,
        "element_width": element_width,
        "row_bytes": row_bytes,
    }


def _correlate_proofs(
    bitmap_report: dict[str, object],
    roundtrip_report: dict[str, object],
    *,
    expected_xpps_sha256: str,
    expected_eboot_sha256: str,
    row_index: int,
    max_descriptors: int,
    max_mips: int,
    max_mip_bytes: int,
    max_total_source_bytes: int,
    current_shader_contract: dict[str, object],
) -> tuple[
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    dict[str, object],
]:
    (
        xpps_identity,
        eboot_identity,
        _bitmap_identity,
        normalized_descriptors,
        normalized_mip_count,
        normalized_total_bytes,
    ) = roundtrip._validate_bitmap_report(
        bitmap_report,
        expected_xpps_sha256=expected_xpps_sha256,
        expected_eboot_sha256=expected_eboot_sha256,
        selected_row_index=row_index,
        max_descriptors=max_descriptors,
        max_mips=max_mips,
        max_mip_bytes=max_mip_bytes,
        max_total_bytes=max_total_source_bytes,
    )
    if roundtrip_report.get("schema") != roundtrip.SCHEMA:
        raise probe.ProbeError("roundtrip report has an unexpected schema")
    if roundtrip_report.get("version") != roundtrip.SCHEMA_VERSION:
        raise probe.ProbeError("roundtrip report has an unexpected version")
    if roundtrip_report.get("proof_class") != roundtrip.PROOF_CLASS:
        raise probe.ProbeError("roundtrip report has an unexpected proof class")
    if roundtrip_report.get("xpps") != xpps_identity:
        raise probe.ProbeError("roundtrip and BITMAP XPPS identities disagree")
    if roundtrip_report.get("eboot") != eboot_identity:
        raise probe.ProbeError("roundtrip and BITMAP eboot identities disagree")
    if roundtrip_report.get("host_shader_contract") != current_shader_contract:
        raise probe.ProbeError("roundtrip and current host-shader contracts disagree")

    bitmap_report_sha = hashlib.sha256(bitmaps.encode_report(bitmap_report)).hexdigest()
    inherited = _require_dict(
        roundtrip_report.get("inherited_bitmap_proof"),
        "roundtrip inherited BITMAP proof",
    )
    if inherited.get("report_sha256") != bitmap_report_sha:
        raise probe.ProbeError("roundtrip report binds a different BITMAP report")
    if inherited.get("selected_dic_row") != bitmap_report.get("selected_dic_row"):
        raise probe.ProbeError("roundtrip and BITMAP selected DIC rows disagree")

    facts = _require_dict(roundtrip_report.get("facts"), "roundtrip facts")
    if facts.get("all_mips_byte_exact_roundtrip") is not True:
        raise probe.ProbeError("roundtrip report does not prove every mip byte-exact")
    if _require_int(facts.get("descriptor_count"), "roundtrip descriptor count") != len(
        normalized_descriptors
    ):
        raise probe.ProbeError("roundtrip descriptor count is inconsistent")
    if (
        _require_int(facts.get("mip_count"), "roundtrip mip count")
        != normalized_mip_count
    ):
        raise probe.ProbeError("roundtrip mip count is inconsistent")
    if (
        _require_int(facts.get("total_payload_bytes"), "roundtrip payload bytes")
        != normalized_total_bytes
    ):
        raise probe.ProbeError("roundtrip payload byte count is inconsistent")

    roundtrip_descriptors = _require_list(
        roundtrip_report.get("descriptors"), "roundtrip descriptors"
    )
    bitmap_descriptors = _require_list(
        bitmap_report.get("descriptors"), "BITMAP descriptors"
    )
    if not (
        len(roundtrip_descriptors)
        == len(bitmap_descriptors)
        == len(normalized_descriptors)
    ):
        raise probe.ProbeError("inherited descriptor populations disagree")

    correlated: list[dict[str, object]] = []
    for index, normalized in enumerate(normalized_descriptors):
        roundtrip_item = _require_dict(
            roundtrip_descriptors[index], f"roundtrip descriptor {index}"
        )
        bitmap_item = _require_dict(
            bitmap_descriptors[index], f"BITMAP descriptor {index}"
        )
        descriptor = _require_dict(bitmap_item.get("descriptor"), "BITMAP descriptor")
        fields = _require_dict(descriptor.get("fields"), "BITMAP descriptor fields")
        data_format = _require_dict(fields.get("data_format"), "BITMAP data format")
        format_id = _require_int(data_format.get("id"), "BITMAP format ID")
        format_name = _require_string(data_format.get("name"), "BITMAP format name")
        element_bits = _require_int(normalized.get("element_bits"), "element width")
        format_info = _validate_format(format_id, format_name, element_bits)
        image_type = _require_dict(fields.get("image_type"), "BITMAP image type")
        image_type_id = _require_int(image_type.get("id"), "BITMAP image type ID")
        expected_image_type = IMAGE_TYPE_DIMENSIONS.get(image_type_id)
        if (
            expected_image_type is None
            or image_type.get("name") != expected_image_type[0]
        ):
            raise probe.ProbeError(
                "BITMAP image type has no exact DDS dimension mapping"
            )
        width = _require_int(fields.get("width"), "BITMAP width")
        height = _require_int(fields.get("height"), "BITMAP height")
        if width < 1 or height < 1:
            raise probe.ProbeError("BITMAP descriptor dimensions are invalid")
        if image_type_id == 8 and height != 1:
            raise probe.ProbeError("Color1D BITMAP height is not one")

        expected_roundtrip_values: dict[str, object] = {
            "descriptor_index": index,
            "descriptor_sha256": normalized.get("descriptor_sha256"),
            "element_bits": element_bits,
            "format": {"id": format_id, "name": format_name},
            "payload_bytes": normalized.get("payload_bytes"),
            "payload_data_relative_start": normalized.get("payload_relative_start"),
            "tiled_payload_sha256": normalized.get("payload_sha256"),
        }
        for key, expected in expected_roundtrip_values.items():
            if roundtrip_item.get(key) != expected:
                raise probe.ProbeError(
                    f"roundtrip descriptor {index} has inconsistent {key}"
                )
        linear_chain_sha = _require_string(
            roundtrip_item.get("linear_padded_chain_sha256"),
            f"roundtrip descriptor {index} linear chain SHA-256",
        )
        retiled_chain_sha = _require_string(
            roundtrip_item.get("retiled_chain_sha256"),
            f"roundtrip descriptor {index} retiled chain SHA-256",
        )
        registry._validate_sha256(linear_chain_sha, "roundtrip linear chain SHA-256")
        registry._validate_sha256(retiled_chain_sha, "roundtrip retiled chain SHA-256")
        if retiled_chain_sha != normalized.get("payload_sha256"):
            raise probe.ProbeError(
                f"roundtrip descriptor {index} retiled chain hash is inconsistent"
            )
        normalized_mips = _require_list(normalized.get("mips"), "normalized mips")
        roundtrip_mips = _require_list(roundtrip_item.get("mips"), "roundtrip mips")
        if len(normalized_mips) != len(roundtrip_mips):
            raise probe.ProbeError(f"roundtrip descriptor {index} mip count disagrees")
        correlated_mips: list[dict[str, object]] = []
        for mip_index, raw_normalized_mip in enumerate(normalized_mips):
            mip = _require_dict(raw_normalized_mip, f"normalized mip {mip_index}")
            roundtrip_mip = _require_dict(
                roundtrip_mips[mip_index], f"roundtrip mip {mip_index}"
            )
            expected_mip_values = {
                "aligned_storage_height": mip.get("aligned_storage_height"),
                "aligned_storage_pitch": mip.get("aligned_storage_pitch"),
                "bytes": mip.get("bytes"),
                "element_bits": element_bits,
                "exact_roundtrip": True,
                "index": mip_index,
                "logical_height": mip.get("logical_height"),
                "logical_pitch": mip.get("logical_pitch"),
                "storage_height": mip.get("storage_height"),
                "storage_pitch": mip.get("storage_pitch"),
            }
            for key, expected in expected_mip_values.items():
                if roundtrip_mip.get(key) != expected:
                    raise probe.ProbeError(
                        f"roundtrip descriptor {index} mip {mip_index} has inconsistent {key}"
                    )
            linear_sha = _require_string(
                roundtrip_mip.get("linear_padded_sha256"),
                f"roundtrip mip {mip_index} linear SHA-256",
            )
            tiled_sha = _require_string(
                roundtrip_mip.get("tiled_sha256"),
                f"roundtrip mip {mip_index} tiled SHA-256",
            )
            retiled_sha = _require_string(
                roundtrip_mip.get("retiled_sha256"),
                f"roundtrip mip {mip_index} retiled SHA-256",
            )
            registry._validate_sha256(linear_sha, "roundtrip linear SHA-256")
            registry._validate_sha256(tiled_sha, "roundtrip tiled SHA-256")
            registry._validate_sha256(retiled_sha, "roundtrip retiled SHA-256")
            if tiled_sha != retiled_sha:
                raise probe.ProbeError(
                    f"roundtrip descriptor {index} mip {mip_index} hashes disagree"
                )
            correlated_mip = dict(mip)
            correlated_mip["linear_padded_sha256"] = linear_sha
            correlated_mip["tiled_sha256"] = tiled_sha
            correlated_mips.append(correlated_mip)
        correlated.append(
            {
                "descriptor_index": index,
                "descriptor_sha256": normalized.get("descriptor_sha256"),
                "element_bits": element_bits,
                "format_id": format_id,
                "format_info": format_info,
                "format_name": format_name,
                "height": height,
                "image_type_id": image_type_id,
                "image_type_name": expected_image_type[0],
                "linear_padded_chain_sha256": linear_chain_sha,
                "mips": correlated_mips,
                "payload_bytes": normalized.get("payload_bytes"),
                "payload_relative_start": normalized.get("payload_relative_start"),
                "payload_sha256": normalized.get("payload_sha256"),
                "width": width,
            }
        )
    proof_identity = {
        "bitmap_report_sha256": bitmap_report_sha,
        "roundtrip_report_sha256": hashlib.sha256(
            roundtrip.encode_report(roundtrip_report)
        ).hexdigest(),
        "selected_dic_row": bitmap_report.get("selected_dic_row"),
    }
    return xpps_identity, eboot_identity, correlated, proof_identity


def _directory_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _open_fresh_output_directory(
    output_dir: Path,
) -> tuple[int, int, str, tuple[int, int]]:
    output_dir = Path(output_dir)
    name = output_dir.name
    if not name or name in (".", "..") or "/" in name:
        raise probe.ProbeError("output directory has an invalid final component")
    parent = output_dir.parent if str(output_dir.parent) else Path(".")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_fd = os.open(parent, directory_flags)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot open output parent as a nonsymlink directory: {error.strerror}"
        ) from error
    try:
        if not stat.S_ISDIR(os.fstat(parent_fd).st_mode):
            raise probe.ProbeError("output parent is not a directory")
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except OSError as error:
            raise probe.ProbeError(
                f"cannot create one fresh output directory: {error.strerror}"
            ) from error
        try:
            output_fd = os.open(name, directory_flags, dir_fd=parent_fd)
        except OSError as error:
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise probe.ProbeError(
                f"cannot open fresh output directory: {error.strerror}"
            ) from error
        info = os.fstat(output_fd)
        if not stat.S_ISDIR(info.st_mode):
            os.close(output_fd)
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise probe.ProbeError("fresh output path is not a directory")
        return parent_fd, output_fd, name, _directory_identity(info)
    except BaseException:
        os.close(parent_fd)
        raise


def _output_binding_matches(
    parent_fd: int, output_fd: int, name: str, identity: tuple[int, int]
) -> bool:
    if _directory_identity(os.fstat(output_fd)) != identity:
        return False
    try:
        path_info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISDIR(path_info.st_mode) and _directory_identity(path_info) == identity
    )


def _list_output_names(output_fd: int) -> list[str]:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fresh_fd = os.open(".", directory_flags, dir_fd=output_fd)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot reopen fresh output directory for listing: {error.strerror}"
        ) from error
    try:
        return os.listdir(fresh_fd)
    finally:
        os.close(fresh_fd)


def _write_all(file_descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    written_total = 0
    while written_total < len(data):
        written = os.write(file_descriptor, view[written_total:])
        if written <= 0:
            raise probe.ProbeError("output write made no forward progress")
        written_total += written


def _write_exclusive(
    output_fd: int,
    name: str,
    data: bytes,
    created_names: list[str],
    created_file_identities: dict[str, tuple[int, int, int, int, int]],
    created_file_guards: dict[str, int],
) -> bytes:
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or "\x00" in name
        or name in created_names
    ):
        raise probe.ProbeError("output basename is invalid or duplicated")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        file_descriptor = os.open(name, flags, 0o600, dir_fd=output_fd)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot create output file exclusively: {error.strerror}"
        ) from error
    created_names.append(name)
    created_file_guards[name] = file_descriptor
    try:
        created_info = os.fstat(file_descriptor)
        if not stat.S_ISREG(created_info.st_mode):
            raise probe.ProbeError("created output is not a regular file")
        created_file_identities[name] = registry._identity(created_info)
        _write_all(file_descriptor, data)
        os.fsync(file_descriptor)
        created_file_identities[name] = registry._identity(
            os.fstat(file_descriptor)
        )
    except BaseException:
        # The descriptor remains open as the cleanup authority. Holding it also
        # prevents an unlinked inode from being recycled beneath the same name.
        raise

    read_flags = (
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        read_descriptor = os.open(name, read_flags, dir_fd=output_fd)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot reopen output file read-only: {error.strerror}"
        ) from error
    try:
        info = os.fstat(read_descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != len(data):
            raise probe.ProbeError("written output has the wrong type or byte size")
        path_info = os.stat(name, dir_fd=output_fd, follow_symlinks=False)
        expected_identity = created_file_identities[name]
        if (
            not stat.S_ISREG(path_info.st_mode)
            or registry._identity(info) != expected_identity
            or registry._identity(path_info) != expected_identity
            or registry._identity(os.fstat(created_file_guards[name]))
            != expected_identity
        ):
            raise probe.ProbeError("written output path binding changed")
        with os.fdopen(os.dup(read_descriptor), "rb") as stream:
            observed = registry._read_bounded(
                stream, len(data), "written output verification"
            )
        if registry._identity(os.fstat(read_descriptor)) != registry._identity(info):
            raise probe.ProbeError("written output changed during verification")
    finally:
        os.close(read_descriptor)
    if observed != data:
        raise probe.ProbeError("written output bytes disagree with the in-memory file")
    return observed


def _cleanup_fresh_output(
    parent_fd: int,
    output_fd: int,
    name: str,
    identity: tuple[int, int],
    created_names: list[str],
    created_file_identities: dict[str, tuple[int, int, int, int, int]],
    created_file_guards: dict[str, int],
) -> str | None:
    failures: list[str] = []
    for created_name in reversed(created_names):
        expected_file_identity = created_file_identities.get(created_name)
        guard_descriptor = created_file_guards.get(created_name)
        try:
            guarded_identity = (
                registry._identity(os.fstat(guard_descriptor))
                if guard_descriptor is not None
                else None
            )
        except OSError as error:
            failures.append(f"cannot inspect guarded {created_name}: {error.strerror}")
            continue
        try:
            observed = os.stat(created_name, dir_fd=output_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as error:
            failures.append(f"cannot inspect {created_name}: {error.strerror}")
            continue
        if (
            guarded_identity is None
            or not stat.S_ISREG(observed.st_mode)
            or registry._identity(observed) != guarded_identity
            or (
                expected_file_identity is not None
                and expected_file_identity[:2] != guarded_identity[:2]
            )
        ):
            failures.append(f"created output binding changed for {created_name}")
            continue
        try:
            os.unlink(created_name, dir_fd=output_fd)
        except OSError as error:
            failures.append(f"cannot remove {created_name}: {error.strerror}")
    if not _output_binding_matches(parent_fd, output_fd, name, identity):
        failures.append("fresh output path binding changed")
    else:
        try:
            remaining = _list_output_names(output_fd)
        except (OSError, probe.ProbeError) as error:
            failures.append(f"cannot list fresh output: {error}")
            remaining = ["unknown"]
        if remaining:
            failures.append("fresh output contains untracked entries")
        else:
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except OSError as error:
                failures.append(
                    f"cannot remove fresh output directory: {error.strerror}"
                )
    return "; ".join(failures) if failures else None


def export_dds(
    xpps: Path,
    *,
    expected_xpps_sha256: str,
    row_index: int,
    eboot: Path,
    expected_eboot_sha256: str,
    output_dir: Path,
    max_descriptors: int = MAX_DESCRIPTORS,
    max_mips: int = MAX_MIPS,
    max_mip_bytes: int = MAX_MIP_BYTES,
    max_total_source_bytes: int = MAX_TOTAL_SOURCE_BYTES,
    max_total_dds_bytes: int = MAX_TOTAL_DDS_BYTES,
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
    if max_total_source_bytes < 1 or max_total_source_bytes > MAX_TOTAL_SOURCE_BYTES:
        raise probe.ProbeError(
            f"source byte budget must be from 1 through {MAX_TOTAL_SOURCE_BYTES}"
        )
    if max_total_dds_bytes < 1 or max_total_dds_bytes > MAX_TOTAL_DDS_BYTES:
        raise probe.ProbeError(
            f"DDS byte budget must be from 1 through {MAX_TOTAL_DDS_BYTES}"
        )

    effective_shader_root = (
        Path(shader_root)
        if shader_root is not None
        else Path(__file__).resolve().parents[1]
    )
    host_coordinates, current_shader_contract = roundtrip._load_host_permutation(
        effective_shader_root
    )
    roundtrip_report = roundtrip.prove_thin1d_roundtrip(
        Path(xpps),
        expected_xpps_sha256=expected_xpps_sha256,
        row_index=row_index,
        eboot=Path(eboot),
        expected_eboot_sha256=expected_eboot_sha256,
        max_descriptors=max_descriptors,
        max_mips=max_mips,
        max_mip_bytes=max_mip_bytes,
        max_total_bytes=max_total_source_bytes,
        resolver_overrides=resolver_overrides,
        shader_root=effective_shader_root,
    )
    bitmap_report = bitmaps.classify_bitmap_descriptors(
        Path(xpps),
        expected_xpps_sha256=expected_xpps_sha256,
        row_index=row_index,
        eboot=Path(eboot),
        expected_eboot_sha256=expected_eboot_sha256,
        max_bitmap_entries=max_descriptors,
        max_total_payload_bytes=max_total_source_bytes,
        resolver_overrides=resolver_overrides,
    )
    xpps_identity, eboot_identity, descriptors, proof_identity = _correlate_proofs(
        bitmap_report,
        roundtrip_report,
        expected_xpps_sha256=expected_xpps_sha256,
        expected_eboot_sha256=expected_eboot_sha256,
        row_index=row_index,
        max_descriptors=max_descriptors,
        max_mips=max_mips,
        max_mip_bytes=max_mip_bytes,
        max_total_source_bytes=max_total_source_bytes,
        current_shader_contract=current_shader_contract,
    )

    parent_fd: int | None = None
    output_fd: int | None = None
    output_name = ""
    output_identity = (0, 0)
    created_names: list[str] = []
    created_file_identities: dict[str, tuple[int, int, int, int, int]] = {}
    created_file_guards: dict[str, int] = {}
    manifest: dict[str, object] | None = None
    try:
        with ExitStack() as stack:
            xpps_stream = registry._open_regular(stack, Path(xpps), "XPPS source")
            eboot_stream = registry._open_regular(stack, Path(eboot), "eboot")
            xpps_before = os.fstat(xpps_stream.fileno())
            eboot_before = os.fstat(eboot_stream.fileno())
            if xpps_before.st_size != _require_int(
                xpps_identity.get("bytes"), "XPPS bytes"
            ):
                raise probe.ProbeError("XPPS size changed after inherited proofs")
            if eboot_before.st_size != _require_int(
                eboot_identity.get("bytes"), "eboot bytes"
            ):
                raise probe.ProbeError("eboot size changed after inherited proofs")
            if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
                raise probe.ProbeError("XPPS hash changed after inherited proofs")
            if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
                raise probe.ProbeError("eboot hash changed after inherited proofs")

            parent_fd, output_fd, output_name, output_identity = (
                _open_fresh_output_directory(Path(output_dir))
            )
            public_files: list[dict[str, object]] = []
            total_dds_file_bytes = 0
            total_dds_payload_bytes = 0
            total_mips = 0
            for descriptor in descriptors:
                descriptor_index = _require_int(
                    descriptor.get("descriptor_index"), "descriptor index"
                )
                format_id = _require_int(descriptor.get("format_id"), "format ID")
                format_name = _require_string(
                    descriptor.get("format_name"), "format name"
                )
                format_info = _require_dict(descriptor.get("format_info"), "DDS format")
                image_type_id = _require_int(
                    descriptor.get("image_type_id"), "image type ID"
                )
                image_type = IMAGE_TYPE_DIMENSIONS[image_type_id]
                base_width = _require_int(descriptor.get("width"), "descriptor width")
                base_height = _require_int(
                    descriptor.get("height"), "descriptor height"
                )
                element_bits = _require_int(
                    descriptor.get("element_bits"), "element width"
                )
                element_bytes = element_bits // 8
                dds_payload = bytearray()
                public_mips: list[dict[str, object]] = []
                tiled_payload_hash = hashlib.sha256()
                linear_payload_hash = hashlib.sha256()
                for raw_mip in _require_list(descriptor.get("mips"), "descriptor mips"):
                    mip = _require_dict(raw_mip, "descriptor mip")
                    mip_index = _require_int(mip.get("index"), "mip index")
                    tiled = _read_exact_at(
                        xpps_stream,
                        _require_int(mip.get("absolute_start"), "mip absolute start"),
                        _require_int(mip.get("bytes"), "mip bytes"),
                        f"BITMAP mip {mip_index}",
                    )
                    tiled_sha = hashlib.sha256(tiled).hexdigest()
                    if tiled_sha != _require_string(
                        mip.get("tiled_sha256"), "proven tiled mip SHA-256"
                    ):
                        raise probe.ProbeError(
                            f"BITMAP descriptor {descriptor_index} mip {mip_index} tiled hash changed"
                        )
                    tiled_payload_hash.update(tiled)
                    padded_pitch = _require_int(
                        mip.get("aligned_storage_pitch"), "padded pitch"
                    )
                    padded_height = _require_int(
                        mip.get("aligned_storage_height"), "padded height"
                    )
                    try:
                        linear = roundtrip._deswizzle_mip(
                            tiled,
                            pitch=padded_pitch,
                            height=padded_height,
                            element_bytes=element_bytes,
                            host_coordinates=host_coordinates,
                        )
                    except MemoryError as error:
                        raise probe.ProbeError(
                            f"BITMAP mip {mip_index} deswizzle allocation failed"
                        ) from error
                    linear_sha = hashlib.sha256(linear).hexdigest()
                    if linear_sha != _require_string(
                        mip.get("linear_padded_sha256"),
                        "proven padded-linear SHA-256",
                    ):
                        raise probe.ProbeError(
                            f"BITMAP descriptor {descriptor_index} mip {mip_index} linear hash changed"
                        )
                    linear_payload_hash.update(linear)
                    logical_width = max(base_width >> mip_index, 1)
                    logical_height = max(base_height >> mip_index, 1)
                    cropped, crop_geometry = _crop_logical_mip(
                        linear,
                        padded_pitch=padded_pitch,
                        padded_height=padded_height,
                        logical_width=logical_width,
                        logical_height=logical_height,
                        format_info=format_info,
                    )
                    file_offset_start = DDS_HEADER_BYTES + len(dds_payload)
                    _, file_offset_end = _checked_u64_range(
                        file_offset_start,
                        len(cropped),
                        f"DDS descriptor {descriptor_index} mip {mip_index}",
                    )
                    dds_payload.extend(cropped)
                    public_mips.append(
                        {
                            "bytes": len(cropped),
                            "cropped_linear_sha256": hashlib.sha256(
                                cropped
                            ).hexdigest(),
                            "element_height": crop_geometry["element_height"],
                            "element_width": crop_geometry["element_width"],
                            "file_offset_end": file_offset_end,
                            "file_offset_start": file_offset_start,
                            "height": logical_height,
                            "index": mip_index,
                            "padded_linear_sha256": linear_sha,
                            "padded_storage_height": padded_height,
                            "padded_storage_pitch": padded_pitch,
                            "row_bytes": crop_geometry["row_bytes"],
                            "width": logical_width,
                        }
                    )
                inherited_payload_sha = _require_string(
                    descriptor.get("payload_sha256"), "payload SHA-256"
                )
                if tiled_payload_hash.hexdigest() != inherited_payload_sha:
                    raise probe.ProbeError(
                        f"BITMAP descriptor {descriptor_index} payload hash changed"
                    )
                inherited_linear_chain_sha = _require_string(
                    descriptor.get("linear_padded_chain_sha256"),
                    "padded-linear chain SHA-256",
                )
                if linear_payload_hash.hexdigest() != inherited_linear_chain_sha:
                    raise probe.ProbeError(
                        f"BITMAP descriptor {descriptor_index} padded-linear chain hash changed"
                    )
                if not public_mips:
                    raise probe.ProbeError("DDS descriptor has no mips")
                header = _build_dds_header(
                    width=base_width,
                    height=base_height,
                    mip_count=len(public_mips),
                    top_mip_bytes=_require_int(
                        public_mips[0].get("bytes"), "top mip bytes"
                    ),
                    format_id=format_id,
                    image_type_id=image_type_id,
                )
                dds_data = header + dds_payload
                parsed_before_write = _parse_dds(dds_data)
                if parsed_before_write["mips"] != [
                    {
                        key: mip[key]
                        for key in (
                            "bytes",
                            "element_height",
                            "element_width",
                            "file_offset_end",
                            "file_offset_start",
                            "height",
                            "index",
                            "row_bytes",
                            "width",
                        )
                    }
                    for mip in public_mips
                ]:
                    raise probe.ProbeError(
                        "DDS strict parse-back mip geometry disagrees"
                    )
                total_dds_file_bytes += len(dds_data)
                total_dds_payload_bytes += len(dds_payload)
                total_mips += len(public_mips)
                if total_dds_file_bytes > max_total_dds_bytes:
                    raise probe.ProbeError(
                        f"DDS files exceed the {max_total_dds_bytes}-byte output budget"
                    )
                filename = (
                    f"bitmap_{descriptor_index:03d}_"
                    f"{_require_int(descriptor.get('payload_relative_start'), 'payload start'):08x}_"
                    f"{format_info['filename_tag']}.dds"
                )
                written = _write_exclusive(
                    output_fd,
                    filename,
                    dds_data,
                    created_names,
                    created_file_identities,
                    created_file_guards,
                )
                parsed_after_write = _parse_dds(written)
                if parsed_after_write != parsed_before_write:
                    raise probe.ProbeError("written DDS parse-back facts changed")
                public_files.append(
                    {
                        "basename": filename,
                        "bytes": len(written),
                        "dds": {
                            key: value
                            for key, value in parsed_after_write.items()
                            if key != "mips"
                        },
                        "descriptor": {
                            "data_relative_payload_start": descriptor.get(
                                "payload_relative_start"
                            ),
                            "image_type": {
                                "id": image_type_id,
                                "name": image_type[0],
                            },
                            "sha256": descriptor.get("descriptor_sha256"),
                            "padded_linear_chain_sha256": inherited_linear_chain_sha,
                            "tiled_payload_sha256": inherited_payload_sha,
                            "xpps_format": {
                                "id": format_id,
                                "name": format_name,
                            },
                        },
                        "mips": public_mips,
                        "sha256": hashlib.sha256(written).hexdigest(),
                    }
                )

            if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
                raise probe.ProbeError("XPPS changed during DDS export")
            if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
                raise probe.ProbeError("eboot changed during DDS export")
            if registry._identity(os.fstat(xpps_stream.fileno())) != registry._identity(
                xpps_before
            ):
                raise probe.ProbeError("XPPS identity changed during DDS export")
            if registry._identity(
                os.fstat(eboot_stream.fileno())
            ) != registry._identity(eboot_before):
                raise probe.ProbeError("eboot identity changed during DDS export")

            manifest = {
                "eboot": eboot_identity,
                "facts": {
                    "all_dds_files_strictly_parsed": True,
                    "all_logical_crops_bound_to_padded_linear_hashes": True,
                    "dds_file_count": len(public_files),
                    "mip_count": total_mips,
                    "total_dds_file_bytes": total_dds_file_bytes,
                    "total_dds_payload_bytes": total_dds_payload_bytes,
                },
                "files": public_files,
                "host_shader_contract": current_shader_contract,
                "inherited_proofs": proof_identity,
                "non_claims": list(NON_CLAIMS),
                "proof_class": PROOF_CLASS,
                "schema": SCHEMA,
                "version": SCHEMA_VERSION,
                "warnings": [],
                "xpps": xpps_identity,
            }
            manifest_bytes = encode_manifest(manifest)
            if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise probe.ProbeError(
                    f"DDS manifest exceeds the {MAX_MANIFEST_BYTES}-byte limit"
                )
            written_manifest = _write_exclusive(
                output_fd,
                MANIFEST_NAME,
                manifest_bytes,
                created_names,
                created_file_identities,
                created_file_guards,
            )
            if written_manifest != manifest_bytes:
                raise probe.ProbeError("written DDS manifest changed")
            observed_output_names = sorted(_list_output_names(output_fd))
            expected_output_names = sorted(created_names)
            if observed_output_names != expected_output_names:
                raise probe.ProbeError(
                    "fresh output directory contains unexpected entries"
                )
            for created_name, expected_identity in created_file_identities.items():
                observed = os.stat(
                    created_name, dir_fd=output_fd, follow_symlinks=False
                )
                if (
                    not stat.S_ISREG(observed.st_mode)
                    or registry._identity(observed) != expected_identity
                    or registry._identity(
                        os.fstat(created_file_guards[created_name])
                    )
                    != expected_identity
                ):
                    raise probe.ProbeError("created output file path binding changed")
            if not _output_binding_matches(
                parent_fd, output_fd, output_name, output_identity
            ):
                raise probe.ProbeError("fresh output directory path binding changed")
    except BaseException as error:
        cleanup_error = None
        if parent_fd is not None and output_fd is not None:
            cleanup_error = _cleanup_fresh_output(
                parent_fd,
                output_fd,
                output_name,
                output_identity,
                created_names,
                created_file_identities,
                created_file_guards,
            )
        if cleanup_error:
            raise probe.ProbeError(
                f"{error}; fresh output cleanup incomplete: {cleanup_error}"
            ) from error
        raise
    finally:
        for guard_descriptor in created_file_guards.values():
            try:
                os.close(guard_descriptor)
            except OSError:
                pass
        if output_fd is not None:
            os.close(output_fd)
        if parent_fd is not None:
            os.close(parent_fd)
    if manifest is None:
        raise probe.ProbeError("DDS export completed without a manifest")
    return manifest


def encode_manifest(manifest: dict[str, object]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export proven Second Son XPPS BITMAP mip chains as guarded DDS files."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--expected-xpps-sha256", required=True)
    parser.add_argument("--row", required=True, type=int)
    parser.add_argument("--eboot", required=True, type=Path)
    parser.add_argument("--expected-eboot-sha256", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = export_dds(
            args.input,
            expected_xpps_sha256=args.expected_xpps_sha256,
            row_index=args.row,
            eboot=args.eboot,
            expected_eboot_sha256=args.expected_eboot_sha256,
            output_dir=args.output_dir,
        )
        sys.stdout.buffer.write(encode_manifest(manifest))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_dds_export: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
