#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Resolve exact Second Son XPPS registry IDs through guarded eboot evidence."""

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
import second_son_xpps_eboot_registry as registry
import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_xpps_eboot_type_names"
SCHEMA_VERSION = 1
PROOF_CLASS = "xpps_eboot_type_name_resolver"

HASH_LOOKUP_GUEST = 0x00643E60
HASH_LOOKUP_BYTES = 88
HASH_LOOKUP_SHA256 = "58c0c1f210701d9147373ac8ea1ddad7f5aecaf433bbaaa9abb1f47a90d7d9b0"
NAME_LOOKUP_GUEST = 0x0066A590
NAME_LOOKUP_BYTES = 28
NAME_LOOKUP_SHA256 = "3bb5f4ac084f3331d3c83dd92e10ec241f4150c32abd1e8670752b1975a69a92"
NAME_COUNT_GUEST = 0x00643EC0
NAME_COUNT_BYTES = 62
NAME_COUNT_SHA256 = "a12bd7bd60d4e72ff5948552e8d91427b2b28eb759593e39f53d0441a80a7d34"
HASH_TABLE_GUEST = 0x00E00EF0
HASH_RECORD_COUNT = 7_040
HASH_RECORD = struct.Struct("<qII")
NAME_TABLE_GUEST = 0x010A7510
NAME_TABLE_COUNT = 1_022
NAME_POINTER_BYTES = 8
MAX_NAME_BYTES = 128

PT_DYNAMIC = 0x2
PT_SCE_DYNLIBDATA = 0x61000000
DT_NULL = 0
DT_SCE_RELA = 0x6100002F
DT_SCE_RELASZ = 0x61000031
DT_SCE_RELAENT = 0x61000033
DYNAMIC_ENTRY = struct.Struct("<qQ")
RELOCATION_ENTRY = struct.Struct("<QQq")
R_X86_64_RELATIVE_INFO = 8
MAX_DYNAMIC_BYTES = 64 * 1024
MAX_DYNAMIC_ENTRIES = 4_096
MAX_RELOCATION_BYTES = 32 * 1024 * 1024
MAX_RELOCATIONS = 131_072
U64_LIMIT = 1 << 64

NON_CLAIMS = (
    "object_boundary",
    "descriptor_layout",
    "field_meaning",
    "texel_range",
    "texture_format",
    "dimensions",
    "mip_or_layer_layout",
    "shader_bytecode_boundary",
    "safe_replacement",
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


def _checked_u64_range(start: int, size: int, label: str) -> tuple[int, int]:
    if start < 0 or size < 0 or start > U64_LIMIT or size > U64_LIMIT - start:
        raise probe.ProbeError(f"{label} overflows a u64 range")
    return start, start + size


def _parse_full_self(
    data: bytes,
) -> tuple[
    dict[str, object],
    list[dict[str, int]],
    list[dict[str, int]],
    list[dict[str, int]],
]:
    metadata, mappings = registry._parse_self_mappings(data)
    fields = registry.SELF_HEADER.unpack_from(data)
    header_size = fields[8]
    segment_count = fields[12]
    segment_table_start = registry.SELF_HEADER.size
    elf_offset = segment_table_start + segment_count * registry.SELF_SEGMENT.size
    elf_fields = registry.ELF_HEADER.unpack_from(data, elf_offset)
    program_header_offset = elf_fields[5]
    program_header_count = elf_fields[10]
    program_table_start = elf_offset + program_header_offset

    program_headers: list[dict[str, int]] = []
    for index in range(program_header_count):
        offset = program_table_start + index * registry.ELF_PROGRAM_HEADER.size
        values = registry.ELF_PROGRAM_HEADER.unpack_from(data, offset)
        program_headers.append(
            {
                "alignment": values[7],
                "file_offset": values[2],
                "file_size": values[5],
                "flags": values[1],
                "index": index,
                "memory_size": values[6],
                "physical_address": values[4],
                "type": values[0],
                "virtual_address": values[3],
            }
        )

    self_segments: list[dict[str, int]] = []
    for index in range(segment_count):
        offset = segment_table_start + index * registry.SELF_SEGMENT.size
        flags, file_offset, file_size, memory_size = registry.SELF_SEGMENT.unpack_from(
            data, offset
        )
        registry._checked_range(
            file_offset, file_size, len(data), f"SELF segment {index}"
        )
        self_segments.append(
            {
                "file_offset": file_offset,
                "file_size": file_size,
                "flags": flags,
                "index": index,
                "memory_size": memory_size,
                "program_header_index": (flags >> 20) & 0xFFF,
            }
        )

    if header_size > len(data):
        raise probe.ProbeError("SELF header size exceeds the eboot")
    return metadata, mappings, program_headers, self_segments


def _map_guest_file_range(
    mappings: list[dict[str, int]], guest: int, size: int, label: str
) -> tuple[dict[str, int], int]:
    _, end = _checked_u64_range(guest, size, label)
    owners = [
        mapping
        for mapping in mappings
        if mapping["elf_virtual_address_start"] <= guest
        and end <= mapping["elf_virtual_address_end"]
    ]
    if len(owners) != 1:
        raise probe.ProbeError(
            f"{label} must map through exactly one file-backed load mapping"
        )
    owner = owners[0]
    self_offset = owner["self_offset_start"] + (
        guest - owner["elf_virtual_address_start"]
    )
    return owner, self_offset


def _find_load_memory_owner(
    program_headers: list[dict[str, int]], guest: int, size: int, label: str
) -> dict[str, int]:
    _, end = _checked_u64_range(guest, size, label)
    owners: list[dict[str, int]] = []
    for header in program_headers:
        if header["type"] != registry.PT_LOAD:
            continue
        if header["file_size"] > header["memory_size"]:
            raise probe.ProbeError(
                f"ELF load program header {header['index']} file range exceeds "
                "its memory range"
            )
        start = header["virtual_address"]
        _, memory_end = _checked_u64_range(
            start,
            header["memory_size"],
            f"ELF program header {header['index']} memory range",
        )
        if start <= guest and end <= memory_end:
            owners.append(header)
    if len(owners) != 1:
        raise probe.ProbeError(f"{label} must fit in exactly one ELF load memory range")
    return owners[0]


def _map_logical_file_range(
    data: bytes,
    program_headers: list[dict[str, int]],
    self_segments: list[dict[str, int]],
    logical_offset: int,
    size: int,
    label: str,
) -> tuple[dict[str, int], dict[str, int], int]:
    _, logical_end = _checked_u64_range(logical_offset, size, label)
    owners: list[tuple[dict[str, int], dict[str, int], int]] = []
    for segment in self_segments:
        flags = segment["flags"]
        if not flags & registry.SELF_FLAG_BLOCKED:
            continue
        if flags & (registry.SELF_FLAG_ENCRYPTED | registry.SELF_FLAG_COMPRESSED):
            continue
        program_index = segment["program_header_index"]
        if program_index >= len(program_headers):
            raise probe.ProbeError(
                f"SELF segment {segment['index']} references a missing program header"
            )
        header = program_headers[program_index]
        header_start = header["file_offset"]
        _, header_end = _checked_u64_range(
            header_start,
            header["file_size"],
            f"ELF program header {program_index} file range",
        )
        if not (header_start <= logical_offset and logical_end <= header_end):
            continue
        relative = logical_offset - header_start
        if relative > segment["file_size"] or size > segment["file_size"] - relative:
            continue
        physical = segment["file_offset"] + relative
        registry._checked_range(physical, size, len(data), label)
        owners.append((segment, header, physical))
    if len(owners) != 1:
        raise probe.ProbeError(
            f"{label} must map through exactly one uncompressed blocked SELF segment"
        )
    return owners[0]


def _guard_code(
    data: bytes,
    mappings: list[dict[str, int]],
    *,
    guest: int,
    size: int,
    expected_sha256: str,
    label: str,
) -> dict[str, object]:
    registry._validate_sha256(expected_sha256, f"expected {label} SHA-256")
    if size < 1 or size > 4_096:
        raise probe.ProbeError(f"{label} byte length is outside the guarded limit")
    owner, self_offset = _map_guest_file_range(mappings, guest, size, label)
    observed = hashlib.sha256(data[self_offset : self_offset + size]).hexdigest()
    if observed != expected_sha256:
        raise probe.ProbeError(f"{label} SHA-256 mismatch")
    return {
        "bytes": size,
        "elf_program_header_index": owner["elf_program_header_index"],
        "guest_address": guest,
        "self_offset": self_offset,
        "sha256": expected_sha256,
    }


def _validate_hash_table(
    data: bytes,
    mappings: list[dict[str, int]],
    *,
    guest: int,
    count: int,
) -> tuple[dict[int, dict[str, int]], dict[str, object]]:
    if count < 1 or count > HASH_RECORD_COUNT:
        raise probe.ProbeError(
            f"hash-record count must be from 1 through {HASH_RECORD_COUNT}"
        )
    size = count * HASH_RECORD.size
    owner, self_offset = _map_guest_file_range(
        mappings, guest, size, "complete hash registry"
    )
    records: dict[int, dict[str, int]] = {}
    previous: int | None = None
    for index in range(count):
        offset = self_offset + index * HASH_RECORD.size
        signed_key, registry_id, reserved = HASH_RECORD.unpack_from(data, offset)
        if reserved != 0:
            raise probe.ProbeError(
                f"hash registry record {index} has a nonzero reserved u32"
            )
        if previous is not None and signed_key <= previous:
            raise probe.ProbeError(
                "hash registry keys are not strictly increasing as signed u64 values"
            )
        previous = signed_key
        unsigned_key = signed_key & (U64_LIMIT - 1)
        records[unsigned_key] = {
            "guest_address": guest + index * HASH_RECORD.size,
            "index": index,
            "registry_id": registry_id,
            "self_offset": offset,
        }
    return records, {
        "bytes": size,
        "elf_program_header_index": owner["elf_program_header_index"],
        "guest_address": guest,
        "record_bytes": HASH_RECORD.size,
        "record_count": count,
        "reserved_u32_zero_for_all_records": True,
        "self_offset": self_offset,
        "signed_keys_strictly_increasing": True,
    }


def _parse_dynamic_relocations(
    data: bytes,
    program_headers: list[dict[str, int]],
    self_segments: list[dict[str, int]],
    *,
    max_dynamic_bytes: int,
    max_relocation_bytes: int,
    max_relocations: int,
) -> tuple[list[tuple[int, int, int]], dict[str, object]]:
    if max_dynamic_bytes < DYNAMIC_ENTRY.size or max_dynamic_bytes > MAX_DYNAMIC_BYTES:
        raise probe.ProbeError(
            f"dynamic-table budget must be from {DYNAMIC_ENTRY.size} through "
            f"{MAX_DYNAMIC_BYTES}"
        )
    if (
        max_relocation_bytes < RELOCATION_ENTRY.size
        or max_relocation_bytes > MAX_RELOCATION_BYTES
    ):
        raise probe.ProbeError(
            f"relocation-byte budget must be from {RELOCATION_ENTRY.size} through "
            f"{MAX_RELOCATION_BYTES}"
        )
    if max_relocations < 1 or max_relocations > MAX_RELOCATIONS:
        raise probe.ProbeError(
            f"relocation-count budget must be from 1 through {MAX_RELOCATIONS}"
        )

    dynamics = [header for header in program_headers if header["type"] == PT_DYNAMIC]
    dynlibs = [
        header for header in program_headers if header["type"] == PT_SCE_DYNLIBDATA
    ]
    if len(dynamics) != 1:
        raise probe.ProbeError(
            "eboot must contain exactly one PT_DYNAMIC program header"
        )
    if len(dynlibs) != 1:
        raise probe.ProbeError(
            "eboot must contain exactly one PT_SCE_DYNLIBDATA program header"
        )
    dynamic = dynamics[0]
    dynlib = dynlibs[0]
    dynamic_size = dynamic["file_size"]
    if (
        dynamic_size < DYNAMIC_ENTRY.size
        or dynamic_size > max_dynamic_bytes
        or dynamic_size % DYNAMIC_ENTRY.size
    ):
        raise probe.ProbeError("PT_DYNAMIC size is malformed or exceeds its budget")
    dynamic_segment, _, dynamic_physical = _map_logical_file_range(
        data,
        program_headers,
        self_segments,
        dynamic["file_offset"],
        dynamic_size,
        "PT_DYNAMIC table",
    )
    dynamic_values: defaultdict[int, list[int]] = defaultdict(list)
    terminated = False
    entry_count = 0
    for offset in range(0, dynamic_size, DYNAMIC_ENTRY.size):
        tag, value = DYNAMIC_ENTRY.unpack_from(data, dynamic_physical + offset)
        entry_count += 1
        if entry_count > MAX_DYNAMIC_ENTRIES:
            raise probe.ProbeError(
                f"dynamic entry population exceeds {MAX_DYNAMIC_ENTRIES}"
            )
        if tag == DT_NULL:
            terminated = True
            break
        dynamic_values[tag].append(value)
    if not terminated:
        raise probe.ProbeError("PT_DYNAMIC has no bounded DT_NULL terminator")

    required = {
        DT_SCE_RELA: "DT_SCE_RELA",
        DT_SCE_RELASZ: "DT_SCE_RELASZ",
        DT_SCE_RELAENT: "DT_SCE_RELAENT",
    }
    resolved: dict[int, int] = {}
    for tag, name in required.items():
        values = dynamic_values.get(tag, [])
        if len(values) != 1:
            raise probe.ProbeError(f"PT_DYNAMIC must contain exactly one {name}")
        resolved[tag] = values[0]

    rela_offset = resolved[DT_SCE_RELA]
    rela_size = resolved[DT_SCE_RELASZ]
    rela_entry_size = resolved[DT_SCE_RELAENT]
    if rela_entry_size != RELOCATION_ENTRY.size:
        raise probe.ProbeError(
            f"DT_SCE_RELAENT must be exactly {RELOCATION_ENTRY.size} bytes"
        )
    if rela_size < RELOCATION_ENTRY.size or rela_size % RELOCATION_ENTRY.size:
        raise probe.ProbeError("DT_SCE_RELASZ is empty or not entry-aligned")
    relocation_count = rela_size // RELOCATION_ENTRY.size
    if rela_size > max_relocation_bytes:
        raise probe.ProbeError(
            f"relocation table exceeds the {max_relocation_bytes}-byte budget"
        )
    if relocation_count > max_relocations:
        raise probe.ProbeError(
            f"relocation population exceeds the {max_relocations}-entry budget"
        )
    if (
        rela_offset > dynlib["file_size"]
        or rela_size > dynlib["file_size"] - rela_offset
    ):
        raise probe.ProbeError("SCE relocation table exceeds PT_SCE_DYNLIBDATA")
    relocation_logical = dynlib["file_offset"] + rela_offset
    relocation_segment, _, relocation_physical = _map_logical_file_range(
        data,
        program_headers,
        self_segments,
        relocation_logical,
        rela_size,
        "SCE relocation table",
    )
    relocations = [
        RELOCATION_ENTRY.unpack_from(
            data, relocation_physical + index * RELOCATION_ENTRY.size
        )
        for index in range(relocation_count)
    ]
    proof: dict[str, object] = {
        "dynamic_entry_count_through_null": entry_count,
        "dynamic_physical_offset": dynamic_physical,
        "dynamic_program_header_index": dynamic["index"],
        "dynamic_self_segment_index": dynamic_segment["index"],
        "dynlib_program_header_index": dynlib["index"],
        "rela_entry_bytes": rela_entry_size,
        "rela_logical_offset": relocation_logical,
        "rela_physical_offset": relocation_physical,
        "rela_self_segment_index": relocation_segment["index"],
        "relocation_bytes": rela_size,
        "relocation_count": relocation_count,
    }
    return relocations, proof


def resolve_type_names(
    xpps: Path,
    *,
    expected_xpps_sha256: str,
    row_index: int,
    eboot: Path,
    expected_eboot_sha256: str,
    hash_lookup_guest: int = HASH_LOOKUP_GUEST,
    hash_lookup_bytes: int = HASH_LOOKUP_BYTES,
    expected_hash_lookup_sha256: str = HASH_LOOKUP_SHA256,
    name_lookup_guest: int = NAME_LOOKUP_GUEST,
    name_lookup_bytes: int = NAME_LOOKUP_BYTES,
    expected_name_lookup_sha256: str = NAME_LOOKUP_SHA256,
    name_count_guest: int = NAME_COUNT_GUEST,
    name_count_bytes: int = NAME_COUNT_BYTES,
    expected_name_count_sha256: str = NAME_COUNT_SHA256,
    hash_table_guest: int = HASH_TABLE_GUEST,
    hash_record_count: int = HASH_RECORD_COUNT,
    name_table_guest: int = NAME_TABLE_GUEST,
    name_table_count: int = NAME_TABLE_COUNT,
    max_dynamic_bytes: int = MAX_DYNAMIC_BYTES,
    max_relocation_bytes: int = MAX_RELOCATION_BYTES,
    max_relocations: int = MAX_RELOCATIONS,
) -> dict[str, object]:
    registry._validate_sha256(expected_xpps_sha256, "expected XPPS SHA-256")
    registry._validate_sha256(expected_eboot_sha256, "expected eboot SHA-256")
    if name_table_count < 1 or name_table_count > NAME_TABLE_COUNT:
        raise probe.ProbeError(
            f"name-table count must be from 1 through {NAME_TABLE_COUNT}"
        )

    inherited = registry.correlate_registry(
        Path(xpps),
        expected_xpps_sha256=expected_xpps_sha256,
        row_index=row_index,
        eboot=Path(eboot),
        expected_eboot_sha256=expected_eboot_sha256,
    )
    correlations = _require_list(inherited.get("correlations"), "correlations")
    if not correlations:
        raise probe.ProbeError("registry correlator emitted no correlations")

    with ExitStack() as stack:
        xpps_stream = registry._open_regular(stack, Path(xpps), "XPPS source")
        eboot_stream = registry._open_regular(stack, Path(eboot), "eboot")
        xpps_before = os.fstat(xpps_stream.fileno())
        eboot_before = os.fstat(eboot_stream.fileno())
        if eboot_before.st_size > registry.MAX_EBOOT_BYTES:
            raise probe.ProbeError(
                f"eboot exceeds the {registry.MAX_EBOOT_BYTES}-byte limit"
            )
        if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError("XPPS source hash changed after correlation")
        if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot hash changed after correlation")
        data = registry._read_bounded(eboot_stream, registry.MAX_EBOOT_BYTES, "eboot")
        self_metadata, mappings, program_headers, self_segments = _parse_full_self(data)

        hash_lookup = _guard_code(
            data,
            mappings,
            guest=hash_lookup_guest,
            size=hash_lookup_bytes,
            expected_sha256=expected_hash_lookup_sha256,
            label="hash lookup function",
        )
        name_lookup = _guard_code(
            data,
            mappings,
            guest=name_lookup_guest,
            size=name_lookup_bytes,
            expected_sha256=expected_name_lookup_sha256,
            label="name lookup function",
        )
        name_count = _guard_code(
            data,
            mappings,
            guest=name_count_guest,
            size=name_count_bytes,
            expected_sha256=expected_name_count_sha256,
            label="name-count loop function",
        )
        table_records, hash_table = _validate_hash_table(
            data,
            mappings,
            guest=hash_table_guest,
            count=hash_record_count,
        )
        name_table_bytes = name_table_count * NAME_POINTER_BYTES
        name_table_owner = _find_load_memory_owner(
            program_headers,
            name_table_guest,
            name_table_bytes,
            "complete name-pointer table",
        )

        used: list[dict[str, object]] = []
        used_slots: dict[int, dict[str, object]] = {}
        for raw_correlation in correlations:
            correlation = _require_dict(raw_correlation, "correlation")
            hash_word = str(correlation.get("dic_hash_word_hex"))
            if len(hash_word) != 16 or any(
                character not in "0123456789abcdef" for character in hash_word
            ):
                raise probe.ProbeError("correlator emitted a malformed hash word")
            if correlation.get("status") != "unique_aligned_record":
                raise probe.ProbeError(
                    f"DIC hash {hash_word} lacks one unique aligned registry record"
                )
            candidates = _require_list(
                correlation.get("candidates"), "correlation candidates"
            )
            if len(candidates) != 1:
                raise probe.ProbeError(
                    f"DIC hash {hash_word} has an unexpected candidate population"
                )
            candidate = _require_dict(candidates[0], "registry candidate")
            hash_value = int(hash_word, 16)
            table_record = table_records.get(hash_value)
            if table_record is None:
                raise probe.ProbeError(
                    f"DIC hash {hash_word} is absent from the guarded hash registry"
                )
            registry_id = table_record["registry_id"]
            candidate_value = candidate.get("opaque_registry_value_decimal")
            if not isinstance(candidate_value, int) or candidate_value != registry_id:
                raise probe.ProbeError(
                    f"DIC hash {hash_word} registry ID disagrees with the correlator"
                )
            if candidate.get("self_offset") != table_record["self_offset"]:
                raise probe.ProbeError(
                    f"DIC hash {hash_word} candidate is outside the guarded registry"
                )
            if registry_id >= name_table_count:
                raise probe.ProbeError(
                    f"DIC hash {hash_word} registry ID {registry_id} is outside "
                    f"the {name_table_count} name slots"
                )
            slot = name_table_guest + registry_id * NAME_POINTER_BYTES
            item: dict[str, object] = {
                "dic_hash_word_hex": hash_word,
                "hash_registry_record_guest": table_record["guest_address"],
                "hash_registry_record_index": table_record["index"],
                "hash_registry_record_self_offset": table_record["self_offset"],
                "name_slot_guest": slot,
                "registry_id": registry_id,
                "xpps_dic_entry_count": int(correlation.get("xpps_dic_entry_count", 0)),
            }
            used.append(item)
            used_slots[slot] = item

        relocations, relocation_proof = _parse_dynamic_relocations(
            data,
            program_headers,
            self_segments,
            max_dynamic_bytes=max_dynamic_bytes,
            max_relocation_bytes=max_relocation_bytes,
            max_relocations=max_relocations,
        )
        relocation_matches: defaultdict[int, list[tuple[int, int, int]]] = defaultdict(
            list
        )
        for index, (offset, info, addend) in enumerate(relocations):
            if offset in used_slots:
                relocation_matches[offset].append((index, info, addend))

        for item in used:
            slot = int(item["name_slot_guest"])
            matches = relocation_matches.get(slot, [])
            if len(matches) != 1:
                raise probe.ProbeError(
                    f"name slot 0x{slot:x} must have exactly one relocation"
                )
            relocation_index, info, addend = matches[0]
            if info != R_X86_64_RELATIVE_INFO:
                raise probe.ProbeError(
                    f"name slot 0x{slot:x} does not use exact relative relocation info 8"
                )
            if addend < 0:
                raise probe.ProbeError(
                    f"name slot 0x{slot:x} has a negative relocation target"
                )
            target_owner, target_self = _map_guest_file_range(
                mappings, addend, 1, f"name target for slot 0x{slot:x}"
            )
            available = min(
                MAX_NAME_BYTES,
                target_owner["elf_virtual_address_end"] - addend,
            )
            raw = data[target_self : target_self + available]
            terminator = raw.find(b"\0")
            if terminator < 0:
                raise probe.ProbeError(
                    f"name target for slot 0x{slot:x} is not terminated within "
                    f"{MAX_NAME_BYTES} bytes"
                )
            name_bytes = raw[:terminator]
            if not name_bytes:
                raise probe.ProbeError(f"name target for slot 0x{slot:x} is empty")
            if any(value < 0x20 or value > 0x7E for value in name_bytes):
                raise probe.ProbeError(
                    f"name target for slot 0x{slot:x} is not printable ASCII"
                )
            item["name"] = name_bytes.decode("ascii")
            item["name_target_guest"] = addend
            item["name_target_program_header_index"] = target_owner[
                "elf_program_header_index"
            ]
            item["name_target_self_offset"] = target_self
            item["relocation_index"] = relocation_index

        if registry._hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError("XPPS source changed during name resolution")
        if registry._hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot changed during name resolution")
        if registry._identity(os.fstat(xpps_stream.fileno())) != registry._identity(
            xpps_before
        ):
            raise probe.ProbeError(
                "XPPS source identity changed during name resolution"
            )
        if registry._identity(os.fstat(eboot_stream.fileno())) != registry._identity(
            eboot_before
        ):
            raise probe.ProbeError("eboot identity changed during name resolution")

    inherited_xpps = _require_dict(inherited.get("xpps"), "inherited XPPS source")
    inherited_eboot = _require_dict(inherited.get("eboot"), "inherited eboot source")
    selected_row = _require_dict(
        inherited.get("selected_dic_row"), "inherited selected DIC row"
    )
    ordered = sorted(used, key=lambda item: str(item["dic_hash_word_hex"]))
    name_counts = Counter(str(item["name"]) for item in ordered)
    return {
        "dynamic_relocation_proof": relocation_proof,
        "eboot": inherited_eboot,
        "facts": {
            "distinct_resolved_names": len(name_counts),
            "distinct_dic_hash_words": len(ordered),
            "name_counts_by_distinct_hash": dict(sorted(name_counts.items())),
            "total_xpps_dic_entries": sum(
                int(item["xpps_dic_entry_count"]) for item in ordered
            ),
        },
        "lookup_proof": {
            "hash_lookup_function": hash_lookup,
            "hash_registry": hash_table,
            "name_lookup_function": name_lookup,
            "name_count_loop_function": name_count,
            "name_pointer_table": {
                "bytes": name_table_bytes,
                "guest_address": name_table_guest,
                "pointer_bytes": NAME_POINTER_BYTES,
                "program_header_index": name_table_owner["index"],
                "slot_count": name_table_count,
            },
        },
        "non_claims": list(NON_CLAIMS),
        "proof_class": PROOF_CLASS,
        "resolutions": ordered,
        "schema": SCHEMA,
        "selected_dic_row": selected_row,
        "self_elf": self_metadata,
        "version": SCHEMA_VERSION,
        "warnings": [],
        "xpps": inherited_xpps,
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve exact Second Son XPPS registry IDs through guarded eboot evidence."
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
        report = resolve_type_names(
            args.input,
            expected_xpps_sha256=args.expected_xpps_sha256,
            row_index=args.row,
            eboot=args.eboot,
            expected_eboot_sha256=args.expected_eboot_sha256,
        )
        sys.stdout.buffer.write(encode_report(report))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_eboot_type_names: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
