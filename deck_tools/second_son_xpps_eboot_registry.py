#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Correlate bounded Second Son XPPS DIC hashes with an exact SELF eboot."""

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

import second_son_xpps_probe as probe
import second_son_xpps_targets as targets

SCHEMA = "shadps4.second_son_xpps_eboot_registry"
SCHEMA_VERSION = 1
PROOF_CLASS = "xpps_eboot_registry_correlator"
SELF_MAGIC = 0x1D3D154F
SELF_HEADER = struct.Struct("<IBBBBBBHHHIIHHI")
SELF_SEGMENT = struct.Struct("<QQQQ")
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
ELF_PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
ELF_MAGIC = b"\x7fELF"
ELF_CLASS_64 = 2
ELF_DATA_LITTLE_ENDIAN = 1
ELF_VERSION_CURRENT = 1
ELF_OSABI_FREEBSD = 9
EM_X86_64 = 62
ELF_TYPES_PS4 = frozenset((0xFE00, 0xFE10, 0xFE18))
PT_LOAD = 0x1
PT_SCE_RELRO = 0x61000010
SELF_FLAG_ENCRYPTED = 0x2
SELF_FLAG_COMPRESSED = 0x8
SELF_FLAG_BLOCKED = 0x800
MAX_EBOOT_BYTES = 128 * 1024 * 1024
MAX_SELF_SEGMENTS = 256
MAX_PROGRAM_HEADERS = 256
MAX_DISTINCT_HASHES = 128
MAX_SEARCH_PRODUCT_BYTES = 512 * 1024 * 1024
MAX_TOTAL_OCCURRENCES = 65_536
MAX_TOTAL_CANDIDATES = 4_096
HASH_CHUNK_BYTES = 1024 * 1024
NON_CLAIMS = (
    "dic_hash_is_name",
    "dic_hash_is_type_identifier",
    "elf_virtual_address_is_runtime_pointer",
    "object_identity",
    "opaque_registry_value_meaning",
    "resource_type",
    "safe_replacement",
    "texture_format",
)


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise probe.ProbeError(
            f"{label} must be exactly 64 lowercase hexadecimal characters"
        )


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_regular(stack: ExitStack, path: Path, label: str) -> BinaryIO:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot open {label} as a nonsymlink regular file: {error.strerror}"
        ) from error
    stream = stack.enter_context(os.fdopen(descriptor, "rb"))
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise probe.ProbeError(f"{label} is not a regular file")
    return stream


def _read_bounded(stream: BinaryIO, maximum: int, label: str) -> bytes:
    stream.seek(0)
    data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise probe.ProbeError(f"{label} exceeds the {maximum}-byte limit")
    return data


def _require_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise probe.ProbeError(f"{label} has an unexpected shape")
    return value


def _require_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise probe.ProbeError(f"{label} has an unexpected shape")
    return value


def _checked_range(start: int, size: int, limit: int, label: str) -> tuple[int, int]:
    if start < 0 or size < 0 or start > limit or size > limit - start:
        raise probe.ProbeError(f"{label} is outside the eboot")
    return start, start + size


def _reject_overlaps(
    mappings: list[dict[str, int]], start_key: str, end_key: str, label: str
) -> None:
    ordered = sorted(
        mappings, key=lambda mapping: (mapping[start_key], mapping[end_key])
    )
    for previous, current in zip(ordered, ordered[1:]):
        if current[start_key] < previous[end_key]:
            raise probe.ProbeError(
                f"usable SELF mappings overlap in {label}: "
                f"{previous['self_segment_index']}/{current['self_segment_index']}"
            )


def _parse_self_mappings(data: bytes) -> tuple[dict[str, object], list[dict[str, int]]]:
    if len(data) < SELF_HEADER.size:
        raise probe.ProbeError("eboot is truncated before the SELF header")
    fields = SELF_HEADER.unpack_from(data)
    (
        magic,
        version,
        mode,
        endian,
        attributes,
        category,
        program_type,
        _padding1,
        header_size,
        _meta_size,
        _declared_file_size,
        _padding2,
        segment_count,
        _unknown1a,
        _padding3,
    ) = fields
    if magic != SELF_MAGIC:
        raise probe.ProbeError("eboot does not have the PS4 SELF magic")
    expected_identity = (0, 1, 1, 0x12, 1, 1)
    observed_identity = (version, mode, endian, attributes, category, program_type)
    if observed_identity != expected_identity:
        raise probe.ProbeError("eboot has an unsupported SELF identity")
    if segment_count < 1 or segment_count > MAX_SELF_SEGMENTS:
        raise probe.ProbeError(
            f"SELF segment count must be from 1 through {MAX_SELF_SEGMENTS}"
        )

    segment_table_start = SELF_HEADER.size
    segment_table_size = segment_count * SELF_SEGMENT.size
    _, elf_offset = _checked_range(
        segment_table_start, segment_table_size, len(data), "SELF segment table"
    )
    _checked_range(elf_offset, ELF_HEADER.size, len(data), "embedded ELF header")
    elf_fields = ELF_HEADER.unpack_from(data, elf_offset)
    (
        ident,
        elf_type,
        machine,
        elf_version,
        _entry,
        program_header_offset,
        _section_header_offset,
        _flags,
        elf_header_size,
        program_header_entry_size,
        program_header_count,
        _section_header_entry_size,
        _section_header_count,
        _section_name_index,
    ) = elf_fields
    if ident[:4] != ELF_MAGIC:
        raise probe.ProbeError("embedded ELF magic is absent")
    if (
        ident[4] != ELF_CLASS_64
        or ident[5] != ELF_DATA_LITTLE_ENDIAN
        or ident[6] != ELF_VERSION_CURRENT
        or ident[7] != ELF_OSABI_FREEBSD
    ):
        raise probe.ProbeError("embedded ELF identity is unsupported")
    if elf_type not in ELF_TYPES_PS4 or machine != EM_X86_64 or elf_version != 1:
        raise probe.ProbeError("embedded ELF target identity is unsupported")
    if elf_header_size != ELF_HEADER.size:
        raise probe.ProbeError("embedded ELF header size is unsupported")
    if program_header_entry_size != ELF_PROGRAM_HEADER.size:
        raise probe.ProbeError("embedded ELF program-header size is unsupported")
    if program_header_count < 1 or program_header_count > MAX_PROGRAM_HEADERS:
        raise probe.ProbeError(
            f"ELF program-header count must be from 1 through {MAX_PROGRAM_HEADERS}"
        )
    if program_header_offset < ELF_HEADER.size:
        raise probe.ProbeError("embedded ELF program-header table overlaps its header")

    program_table_start = elf_offset + program_header_offset
    program_table_size = program_header_count * program_header_entry_size
    _, program_table_end = _checked_range(
        program_table_start,
        program_table_size,
        len(data),
        "embedded ELF program-header table",
    )
    if header_size > len(data):
        raise probe.ProbeError("SELF header size exceeds the eboot")
    if header_size < program_table_end:
        raise probe.ProbeError(
            "SELF header size does not cover the embedded ELF tables"
        )

    program_headers: list[dict[str, int]] = []
    for index in range(program_header_count):
        offset = program_table_start + index * program_header_entry_size
        values = ELF_PROGRAM_HEADER.unpack_from(data, offset)
        program_headers.append(
            {
                "index": index,
                "type": values[0],
                "flags": values[1],
                "file_offset": values[2],
                "virtual_address": values[3],
                "file_size": values[5],
                "memory_size": values[6],
            }
        )

    mappings: list[dict[str, int]] = []
    for index in range(segment_count):
        offset = segment_table_start + index * SELF_SEGMENT.size
        flags, file_offset, file_size, memory_size = SELF_SEGMENT.unpack_from(
            data, offset
        )
        _checked_range(file_offset, file_size, len(data), f"SELF segment {index}")
        if not flags & SELF_FLAG_BLOCKED:
            continue
        program_header_index = (flags >> 20) & 0xFFF
        if program_header_index >= len(program_headers):
            raise probe.ProbeError(
                f"SELF segment {index} references missing ELF program header "
                f"{program_header_index}"
            )
        if flags & (SELF_FLAG_ENCRYPTED | SELF_FLAG_COMPRESSED):
            continue
        program_header = program_headers[program_header_index]
        if program_header["type"] not in (PT_LOAD, PT_SCE_RELRO):
            continue
        mapped_size = program_header["file_size"]
        if mapped_size < 1:
            continue
        if mapped_size > program_header["memory_size"]:
            raise probe.ProbeError(
                f"ELF program header {program_header_index} file range exceeds its memory range"
            )
        if file_size < mapped_size:
            raise probe.ProbeError(
                f"SELF segment {index} is smaller than ELF program header "
                f"{program_header_index}"
            )
        if file_offset < header_size:
            raise probe.ProbeError(
                f"usable SELF segment {index} overlaps the SELF header"
            )
        self_start, self_end = _checked_range(
            file_offset, mapped_size, len(data), f"usable SELF segment {index}"
        )
        virtual_start = program_header["virtual_address"]
        virtual_end = virtual_start + mapped_size
        if virtual_end > (1 << 64):
            raise probe.ProbeError(
                f"SELF segment {index} ELF virtual range overflows u64"
            )
        mappings.append(
            {
                "bytes": mapped_size,
                "elf_file_offset": program_header["file_offset"],
                "elf_program_header_index": program_header_index,
                "elf_program_header_type": program_header["type"],
                "elf_virtual_address_end": virtual_end,
                "elf_virtual_address_start": virtual_start,
                "self_file_size": file_size,
                "self_memory_size": memory_size,
                "self_offset_end": self_end,
                "self_offset_start": self_start,
                "self_segment_flags": flags,
                "self_segment_index": index,
            }
        )
    if not mappings:
        raise probe.ProbeError("eboot has no usable uncompressed SELF-to-ELF mappings")
    _reject_overlaps(mappings, "self_offset_start", "self_offset_end", "file offsets")
    _reject_overlaps(
        mappings,
        "elf_virtual_address_start",
        "elf_virtual_address_end",
        "ELF virtual addresses",
    )

    public_mappings: list[dict[str, object]] = []
    for mapping in sorted(mappings, key=lambda item: item["self_offset_start"]):
        public_mappings.append(
            {
                "bytes": mapping["bytes"],
                "elf_file_offset": mapping["elf_file_offset"],
                "elf_program_header_index": mapping["elf_program_header_index"],
                "elf_program_header_type_hex": (
                    f"0x{mapping['elf_program_header_type']:08x}"
                ),
                "elf_virtual_address_end": mapping["elf_virtual_address_end"],
                "elf_virtual_address_start": mapping["elf_virtual_address_start"],
                "self_offset_end": mapping["self_offset_end"],
                "self_offset_start": mapping["self_offset_start"],
                "self_segment_flags_hex": f"0x{mapping['self_segment_flags']:x}",
                "self_segment_index": mapping["self_segment_index"],
            }
        )
    metadata: dict[str, object] = {
        "elf_header_offset": elf_offset,
        "elf_program_header_count": program_header_count,
        "self_header_size": header_size,
        "self_segment_count": segment_count,
        "usable_mappings": public_mappings,
    }
    return metadata, mappings


def _find_occurrences(data: bytes, pattern: bytes, maximum: int) -> list[int]:
    offsets: list[int] = []
    cursor = 0
    while True:
        found = data.find(pattern, cursor)
        if found < 0:
            return offsets
        offsets.append(found)
        if len(offsets) > maximum:
            raise probe.ProbeError(f"raw eboot occurrence budget exceeds {maximum}")
        cursor = found + 1


def _candidate_record(
    data: bytes, offset: int, mappings: list[dict[str, int]]
) -> dict[str, object] | None:
    if offset % 8:
        return None
    owners = [
        mapping
        for mapping in mappings
        if mapping["self_offset_start"] <= offset
        and offset + 16 <= mapping["self_offset_end"]
    ]
    if len(owners) > 1:
        raise probe.ProbeError(
            "one eboot occurrence belongs to overlapping SELF mappings"
        )
    if not owners:
        return None
    mapping = owners[0]
    virtual_address = mapping["elf_virtual_address_start"] + (
        offset - mapping["self_offset_start"]
    )
    if virtual_address % 8:
        return None
    registry_value = struct.unpack_from("<Q", data, offset + 8)[0]
    return {
        "alignment": {
            "elf_virtual_address_mod_8": virtual_address % 8,
            "self_offset_mod_8": offset % 8,
        },
        "elf_program_header_index": mapping["elf_program_header_index"],
        "elf_virtual_address": virtual_address,
        "opaque_registry_value_decimal": registry_value,
        "opaque_registry_value_hex": f"{registry_value:016x}",
        "self_offset": offset,
        "self_segment_index": mapping["self_segment_index"],
    }


def correlate_registry(
    xpps: Path,
    *,
    expected_xpps_sha256: str,
    row_index: int,
    eboot: Path,
    expected_eboot_sha256: str,
    max_distinct_hashes: int = MAX_DISTINCT_HASHES,
    max_search_product_bytes: int = MAX_SEARCH_PRODUCT_BYTES,
    max_total_occurrences: int = MAX_TOTAL_OCCURRENCES,
    max_total_candidates: int = MAX_TOTAL_CANDIDATES,
) -> dict[str, object]:
    _validate_sha256(expected_xpps_sha256, "expected XPPS SHA-256")
    _validate_sha256(expected_eboot_sha256, "expected eboot SHA-256")
    if max_distinct_hashes < 1 or max_distinct_hashes > MAX_DISTINCT_HASHES:
        raise probe.ProbeError(
            f"distinct-hash budget must be from 1 through {MAX_DISTINCT_HASHES}"
        )
    if (
        max_search_product_bytes < 1
        or max_search_product_bytes > MAX_SEARCH_PRODUCT_BYTES
    ):
        raise probe.ProbeError(
            f"search-product budget must be from 1 through {MAX_SEARCH_PRODUCT_BYTES}"
        )
    if max_total_occurrences < 1 or max_total_occurrences > MAX_TOTAL_OCCURRENCES:
        raise probe.ProbeError(
            f"occurrence budget must be from 1 through {MAX_TOTAL_OCCURRENCES}"
        )
    if max_total_candidates < 1 or max_total_candidates > MAX_TOTAL_CANDIDATES:
        raise probe.ProbeError(
            f"candidate budget must be from 1 through {MAX_TOTAL_CANDIDATES}"
        )

    target_report = targets.classify_targets(
        Path(xpps),
        expected_sha256=expected_xpps_sha256,
        row_index=row_index,
    )
    observations = _require_list(
        target_report.get("observations"), "target observations"
    )
    hash_counts: Counter[str] = Counter()
    for raw_observation in observations:
        observation = _require_dict(raw_observation, "target observation")
        dic = _require_dict(observation.get("dic"), "target DIC metadata")
        hash_word = str(dic.get("hash_word_hex"))
        if len(hash_word) != 16 or any(
            character not in "0123456789abcdef" for character in hash_word
        ):
            raise probe.ProbeError(
                "target classifier emitted a malformed DIC hash word"
            )
        hash_counts[hash_word] += 1
    if not hash_counts:
        raise probe.ProbeError("target classifier emitted no DIC hash words")
    if len(hash_counts) > max_distinct_hashes:
        raise probe.ProbeError(
            f"distinct DIC hash population exceeds {max_distinct_hashes}"
        )

    with ExitStack() as stack:
        xpps_stream = _open_regular(stack, Path(xpps), "XPPS source")
        eboot_stream = _open_regular(stack, Path(eboot), "eboot")
        xpps_before = os.fstat(xpps_stream.fileno())
        eboot_before = os.fstat(eboot_stream.fileno())
        if eboot_before.st_size > MAX_EBOOT_BYTES:
            raise probe.ProbeError(f"eboot exceeds the {MAX_EBOOT_BYTES}-byte limit")
        if _hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError(
                "XPPS source hash changed after target classification"
            )
        if _hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot SHA-256 mismatch")
        eboot_data = _read_bounded(eboot_stream, MAX_EBOOT_BYTES, "eboot")
        if len(eboot_data) * len(hash_counts) > max_search_product_bytes:
            raise probe.ProbeError(
                f"eboot/hash search product exceeds {max_search_product_bytes} bytes"
            )
        self_metadata, mappings = _parse_self_mappings(eboot_data)

        correlations: list[dict[str, object]] = []
        total_occurrences = 0
        total_candidates = 0
        status_counts: Counter[str] = Counter()
        for hash_word, xpps_entry_count in sorted(hash_counts.items()):
            pattern = struct.pack("<Q", int(hash_word, 16))
            offsets = _find_occurrences(
                eboot_data, pattern, max_total_occurrences - total_occurrences
            )
            total_occurrences += len(offsets)
            candidates: list[dict[str, object]] = []
            for offset in offsets:
                candidate = _candidate_record(eboot_data, offset, mappings)
                if candidate is not None:
                    candidates.append(candidate)
                    total_candidates += 1
                    if total_candidates > max_total_candidates:
                        raise probe.ProbeError(
                            f"aligned mapped candidate budget exceeds {max_total_candidates}"
                        )
            if not offsets:
                status = "absent"
            elif not candidates:
                status = "unaligned_or_unmapped"
            elif len(candidates) == 1:
                status = "unique_aligned_record"
            else:
                status = "ambiguous_aligned_records"
            status_counts[status] += 1
            correlations.append(
                {
                    "aligned_mapped_record_count": len(candidates),
                    "candidates": candidates,
                    "dic_hash_word_hex": hash_word,
                    "raw_eboot_occurrence_count": len(offsets),
                    "status": status,
                    "xpps_dic_entry_count": xpps_entry_count,
                }
            )

        if _hash_stream(xpps_stream) != expected_xpps_sha256:
            raise probe.ProbeError("XPPS source changed during registry correlation")
        if _hash_stream(eboot_stream) != expected_eboot_sha256:
            raise probe.ProbeError("eboot changed during registry correlation")
        if _identity(os.fstat(xpps_stream.fileno())) != _identity(xpps_before):
            raise probe.ProbeError(
                "XPPS source identity changed during registry correlation"
            )
        if _identity(os.fstat(eboot_stream.fileno())) != _identity(eboot_before):
            raise probe.ProbeError("eboot identity changed during registry correlation")

    target_source = _require_dict(target_report.get("source"), "target source metadata")
    selected_row = _require_dict(
        target_report.get("selected_dic_row"), "selected DIC row"
    )
    return {
        "correlations": correlations,
        "eboot": {
            "basename": Path(eboot).name,
            "bytes": eboot_before.st_size,
            "sha256": expected_eboot_sha256,
        },
        "facts": {
            "all_hashes_unique_aligned_records": (
                status_counts["unique_aligned_record"] == len(hash_counts)
            ),
            "distinct_dic_hash_words": len(hash_counts),
            "status_counts": dict(sorted(status_counts.items())),
            "total_aligned_mapped_records": total_candidates,
            "total_raw_eboot_occurrences": total_occurrences,
            "total_xpps_dic_entries": sum(hash_counts.values()),
        },
        "non_claims": list(NON_CLAIMS),
        "proof_class": PROOF_CLASS,
        "schema": SCHEMA,
        "selected_dic_row": selected_row,
        "self_elf": self_metadata,
        "version": SCHEMA_VERSION,
        "warnings": [],
        "xpps": {
            "basename": str(target_source["basename"]),
            "bytes": int(target_source["bytes"]),
            "sha256": expected_xpps_sha256,
        },
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Correlate bounded Second Son XPPS DIC hashes with an exact SELF eboot."
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
        report = correlate_registry(
            args.input,
            expected_xpps_sha256=args.expected_xpps_sha256,
            row_index=args.row,
            eboot=args.eboot,
            expected_eboot_sha256=args.expected_eboot_sha256,
        )
        sys.stdout.buffer.write(encode_report(report))
    except (OSError, probe.ProbeError, struct.error) as error:
        print(f"second_son_xpps_eboot_registry: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
