# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_xpps_eboot_registry as registry
import second_son_xpps_probe as probe


def chunk(tag: bytes, content: bytes) -> bytes:
    return tag + struct.pack("<I", len(content)) + content


def dic_chunk(entries: list[tuple[int, int]]) -> bytes:
    content = struct.pack("<II", len(entries), 0)
    content += b"".join(
        struct.pack("<QQ", offset, hash_word) for offset, hash_word in entries
    )
    return chunk(b" DIC", content)


def make_xpps(path: Path, entries: list[tuple[int, int]]) -> None:
    rows = [
        (0x0FF00, bytes(range(256))),
        (0x1FF12, b"B" * 96),
        (0x2FF03, dic_chunk(entries) + chunk(b" DNE", b"")),
    ]
    package_header_offset = 152
    package_extent = probe.PACKAGE_FIXED_BYTES + len(rows) * probe.TABLE_ROW_BYTES
    data_start = package_header_offset + package_extent
    payload_size = sum(len(payload) for _, payload in rows)
    data = bytearray(data_start + payload_size)
    data[:4] = b"KCAP"
    struct.pack_into("<I", data, 24, package_header_offset)
    struct.pack_into("<I", data, 28, package_extent)
    struct.pack_into("<I", data, 40, data_start)
    struct.pack_into("<I", data, 44, payload_size)
    struct.pack_into("<I", data, package_header_offset + 8, len(rows))
    table_start = package_header_offset + probe.PACKAGE_FIXED_BYTES
    relative_offset = 0
    for index, (kind_word, payload) in enumerate(rows):
        struct.pack_into(
            "<10I",
            data,
            table_start + index * probe.TABLE_ROW_BYTES,
            kind_word,
            len(payload),
            relative_offset,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        absolute = data_start + relative_offset
        data[absolute : absolute + len(payload)] = payload
        relative_offset += len(payload)
    path.write_bytes(data)


def make_self(
    path: Path,
    records: list[tuple[int, int, int]],
    *,
    raw_patterns: list[tuple[int, int]] | None = None,
    overlapping_mappings: bool = False,
) -> None:
    segment_size = 256
    segment_count = 2 if overlapping_mappings else 1
    elf_offset = registry.SELF_HEADER.size + segment_count * registry.SELF_SEGMENT.size
    program_count = segment_count
    program_table_start = elf_offset + registry.ELF_HEADER.size
    data_start = 512
    second_start = data_start + 128
    file_end = data_start + segment_size
    if overlapping_mappings:
        file_end = second_start + segment_size
    data = bytearray(file_end)

    registry.SELF_HEADER.pack_into(
        data,
        0,
        registry.SELF_MAGIC,
        0,
        1,
        1,
        0x12,
        1,
        1,
        0,
        data_start,
        0,
        len(data),
        0,
        segment_count,
        0x22,
        0,
    )
    for index in range(segment_count):
        segment_start = data_start if index == 0 else second_start
        flags = registry.SELF_FLAG_BLOCKED | 0x4 | (index << 20)
        registry.SELF_SEGMENT.pack_into(
            data,
            registry.SELF_HEADER.size + index * registry.SELF_SEGMENT.size,
            flags,
            segment_start,
            segment_size,
            segment_size,
        )

    ident = bytearray(16)
    ident[:4] = registry.ELF_MAGIC
    ident[4] = registry.ELF_CLASS_64
    ident[5] = registry.ELF_DATA_LITTLE_ENDIAN
    ident[6] = registry.ELF_VERSION_CURRENT
    ident[7] = registry.ELF_OSABI_FREEBSD
    registry.ELF_HEADER.pack_into(
        data,
        elf_offset,
        bytes(ident),
        0xFE10,
        registry.EM_X86_64,
        1,
        0,
        registry.ELF_HEADER.size,
        0,
        0,
        registry.ELF_HEADER.size,
        registry.ELF_PROGRAM_HEADER.size,
        program_count,
        0,
        0,
        0,
    )
    for index in range(program_count):
        virtual_address = 0x400000 + index * 0x100000
        registry.ELF_PROGRAM_HEADER.pack_into(
            data,
            program_table_start + index * registry.ELF_PROGRAM_HEADER.size,
            registry.PT_LOAD,
            4,
            0x1000 + index * segment_size,
            virtual_address,
            0,
            segment_size,
            segment_size,
            0x4000,
        )

    for offset, hash_word, opaque_value in records:
        struct.pack_into("<QQ", data, data_start + offset, hash_word, opaque_value)
    for offset, hash_word in raw_patterns or []:
        struct.pack_into("<Q", data, data_start + offset, hash_word)
    path.write_bytes(data)


class SecondSonXppsEbootRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.xpps = self.root / "source.xpps"
        self.eboot = self.root / "eboot.bin"
        self.hash_one = 0x1111222233334444
        self.hash_two = 0x5555666677778888
        self.entries = [(32, self.hash_one), (128, self.hash_two)]
        self.rewrite_xpps(self.entries)
        self.rewrite_eboot([(32, self.hash_one, 7), (80, self.hash_two, 11)])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite_xpps(self, entries: list[tuple[int, int]]) -> None:
        make_xpps(self.xpps, entries)
        self.xpps_hash = hashlib.sha256(self.xpps.read_bytes()).hexdigest()

    def rewrite_eboot(
        self,
        records: list[tuple[int, int, int]],
        *,
        raw_patterns: list[tuple[int, int]] | None = None,
        overlapping_mappings: bool = False,
    ) -> None:
        make_self(
            self.eboot,
            records,
            raw_patterns=raw_patterns,
            overlapping_mappings=overlapping_mappings,
        )
        self.eboot_hash = hashlib.sha256(self.eboot.read_bytes()).hexdigest()

    def correlate(self, **kwargs: object) -> dict[str, object]:
        return registry.correlate_registry(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            **kwargs,
        )

    def test_unique_records_are_mapped_and_deterministic(self) -> None:
        xpps_before = self.xpps.read_bytes()
        eboot_before = self.eboot.read_bytes()
        first = self.correlate()
        second = self.correlate()
        self.assertEqual(registry.encode_report(first), registry.encode_report(second))
        self.assertEqual(self.xpps.read_bytes(), xpps_before)
        self.assertEqual(self.eboot.read_bytes(), eboot_before)
        self.assertTrue(first["facts"]["all_hashes_unique_aligned_records"])
        self.assertEqual(first["facts"]["distinct_dic_hash_words"], 2)
        record = first["correlations"][0]["candidates"][0]
        self.assertEqual(record["self_offset"], 544)
        self.assertEqual(record["elf_virtual_address"], 0x400020)
        self.assertEqual(record["opaque_registry_value_decimal"], 7)
        self.assertNotIn(str(self.root), registry.encode_report(first).decode())

    def test_duplicate_absent_and_unaligned_occurrences_are_distinguished(self) -> None:
        hash_three = 0x9999AAAABBBBCCCC
        self.rewrite_xpps([(32, self.hash_one), (96, self.hash_two), (160, hash_three)])
        self.rewrite_eboot(
            [(32, self.hash_one, 7), (80, self.hash_one, 8)],
            raw_patterns=[(1, self.hash_two)],
        )
        report = self.correlate()
        statuses = {
            item["dic_hash_word_hex"]: item["status"] for item in report["correlations"]
        }
        self.assertEqual(statuses[f"{self.hash_one:016x}"], "ambiguous_aligned_records")
        self.assertEqual(statuses[f"{self.hash_two:016x}"], "unaligned_or_unmapped")
        self.assertEqual(statuses[f"{hash_three:016x}"], "absent")

    def test_complete_record_must_remain_inside_the_segment(self) -> None:
        self.rewrite_xpps([(32, self.hash_one)])
        self.rewrite_eboot([], raw_patterns=[(248, self.hash_one)])
        report = self.correlate()
        correlation = report["correlations"][0]
        self.assertEqual(correlation["raw_eboot_occurrence_count"], 1)
        self.assertEqual(correlation["aligned_mapped_record_count"], 0)
        self.assertEqual(correlation["status"], "unaligned_or_unmapped")

    def test_malformed_and_overlapping_self_mappings_are_refused(self) -> None:
        malformed = bytearray(self.eboot.read_bytes())
        struct.pack_into("<I", malformed, 0, 0)
        self.eboot.write_bytes(malformed)
        self.eboot_hash = hashlib.sha256(malformed).hexdigest()
        with self.assertRaisesRegex(probe.ProbeError, "SELF magic"):
            self.correlate()

        self.rewrite_eboot([])
        malformed = bytearray(self.eboot.read_bytes())
        struct.pack_into("<H", malformed, 12, len(malformed) + 1)
        self.eboot.write_bytes(malformed)
        self.eboot_hash = hashlib.sha256(malformed).hexdigest()
        with self.assertRaisesRegex(probe.ProbeError, "SELF header size exceeds"):
            self.correlate()

        self.rewrite_eboot([])
        malformed = bytearray(self.eboot.read_bytes())
        elf_offset = registry.SELF_HEADER.size + registry.SELF_SEGMENT.size
        struct.pack_into("<Q", malformed, elf_offset + 32, 0)
        self.eboot.write_bytes(malformed)
        self.eboot_hash = hashlib.sha256(malformed).hexdigest()
        with self.assertRaisesRegex(probe.ProbeError, "program-header table overlaps"):
            self.correlate()

        self.rewrite_eboot([])
        malformed = bytearray(self.eboot.read_bytes())
        program_header_offset = elf_offset + registry.ELF_HEADER.size
        struct.pack_into("<Q", malformed, program_header_offset + 40, 1)
        self.eboot.write_bytes(malformed)
        self.eboot_hash = hashlib.sha256(malformed).hexdigest()
        with self.assertRaisesRegex(
            probe.ProbeError, "file range exceeds its memory range"
        ):
            self.correlate()

        self.rewrite_eboot([], overlapping_mappings=True)
        with self.assertRaisesRegex(probe.ProbeError, "overlap"):
            self.correlate()

    def test_hash_mismatch_and_symlinks_are_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "eboot SHA-256 mismatch"):
            registry.correlate_registry(
                self.xpps,
                expected_xpps_sha256=self.xpps_hash,
                row_index=2,
                eboot=self.eboot,
                expected_eboot_sha256="0" * 64,
            )
        xpps_link = self.root / "link.xpps"
        xpps_link.symlink_to(self.xpps)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            registry.correlate_registry(
                xpps_link,
                expected_xpps_sha256=self.xpps_hash,
                row_index=2,
                eboot=self.eboot,
                expected_eboot_sha256=self.eboot_hash,
            )
        eboot_link = self.root / "link-eboot.bin"
        eboot_link.symlink_to(self.eboot)
        with self.assertRaisesRegex(probe.ProbeError, "nonsymlink"):
            registry.correlate_registry(
                self.xpps,
                expected_xpps_sha256=self.xpps_hash,
                row_index=2,
                eboot=eboot_link,
                expected_eboot_sha256=self.eboot_hash,
            )

    def test_population_and_search_budgets_are_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "distinct DIC hash population"):
            self.correlate(max_distinct_hashes=1)
        with self.assertRaisesRegex(probe.ProbeError, "search product"):
            self.correlate(max_search_product_bytes=1)
        with self.assertRaisesRegex(probe.ProbeError, "candidate budget"):
            self.correlate(max_total_candidates=1)
        self.rewrite_eboot([(32, self.hash_one, 7), (80, self.hash_one, 8)])
        with self.assertRaisesRegex(probe.ProbeError, "occurrence budget"):
            self.correlate(max_total_occurrences=1)

    def test_invalid_hash_and_inherited_row_failures_are_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "lowercase hexadecimal"):
            registry.correlate_registry(
                self.xpps,
                expected_xpps_sha256="A" * 64,
                row_index=2,
                eboot=self.eboot,
                expected_eboot_sha256=self.eboot_hash,
            )
        with self.assertRaisesRegex(probe.ProbeError, "expected 2"):
            registry.correlate_registry(
                self.xpps,
                expected_xpps_sha256=self.xpps_hash,
                row_index=1,
                eboot=self.eboot,
                expected_eboot_sha256=self.eboot_hash,
            )

    def test_malformed_target_report_and_mutation_are_refused(self) -> None:
        with mock.patch.object(
            registry.targets,
            "classify_targets",
            return_value={"observations": {}, "source": {}, "selected_dic_row": {}},
        ):
            with self.assertRaisesRegex(probe.ProbeError, "unexpected shape"):
                self.correlate()

        with mock.patch.object(
            registry,
            "_hash_stream",
            side_effect=[self.xpps_hash, self.eboot_hash, "0" * 64],
        ):
            with self.assertRaisesRegex(probe.ProbeError, "XPPS source changed during"):
                self.correlate()


if __name__ == "__main__":
    unittest.main()
