# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import copy
import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_xpps_eboot_registry as registry
import second_son_xpps_eboot_type_names as resolver
import second_son_xpps_probe as probe

LOAD_GUEST = 0x400000
LOAD_LOGICAL = 0x1000
LOAD_PHYSICAL = 0x200
LOAD_BYTES = 0x400
BSS_GUEST = 0x500000
DYNAMIC_LOGICAL = 0x3000
DYNAMIC_PHYSICAL = 0x600
DYNLIB_LOGICAL = 0x4000
DYNLIB_PHYSICAL = 0x640
HASH_LOOKUP_GUEST = LOAD_GUEST + 0x10
NAME_LOOKUP_GUEST = LOAD_GUEST + 0x40
NAME_COUNT_GUEST = LOAD_GUEST + 0x70
HASH_TABLE_GUEST = LOAD_GUEST + 0x100
HASH_TABLE_PHYSICAL = LOAD_PHYSICAL + 0x100
STRING_ONE_GUEST = LOAD_GUEST + 0x200
STRING_ONE_PHYSICAL = LOAD_PHYSICAL + 0x200
STRING_TWO_GUEST = LOAD_GUEST + 0x240
STRING_TWO_PHYSICAL = LOAD_PHYSICAL + 0x240
NAME_TABLE_GUEST = BSS_GUEST + 0x100
RELOCATION_PHYSICAL = DYNLIB_PHYSICAL + 0x20
HASH_LOOKUP_BLOB = b"HASH-LOOKUP-PROOF"
NAME_LOOKUP_BLOB = b"NAME-LOOKUP-PROOF"
NAME_COUNT_BLOB = b"NAME-COUNT-LOOP-PROOF"


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
    data = bytearray(data_start + sum(len(payload) for _, payload in rows))
    data[:4] = b"KCAP"
    struct.pack_into("<I", data, 24, package_header_offset)
    struct.pack_into("<I", data, 28, package_extent)
    struct.pack_into("<I", data, 40, data_start)
    struct.pack_into("<I", data, 44, len(data) - data_start)
    struct.pack_into("<I", data, package_header_offset + 8, len(rows))
    table_start = package_header_offset + probe.PACKAGE_FIXED_BYTES
    relative = 0
    for index, (kind_word, payload) in enumerate(rows):
        struct.pack_into(
            "<10I",
            data,
            table_start + index * probe.TABLE_ROW_BYTES,
            kind_word,
            len(payload),
            relative,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        start = data_start + relative
        data[start : start + len(payload)] = payload
        relative += len(payload)
    path.write_bytes(data)


def make_self(
    path: Path,
    records: list[tuple[int, int]],
    *,
    relocations: list[tuple[int, int, int]] | None = None,
    dynamic_entries: list[tuple[int, int]] | None = None,
) -> None:
    segment_count = 3
    program_count = 4
    elf_offset = registry.SELF_HEADER.size + segment_count * registry.SELF_SEGMENT.size
    program_table = elf_offset + registry.ELF_HEADER.size
    data = bytearray(DYNLIB_PHYSICAL + 0x100)
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
        LOAD_PHYSICAL,
        0,
        len(data),
        0,
        segment_count,
        0x22,
        0,
    )
    segment_specs = (
        (0, LOAD_PHYSICAL, LOAD_BYTES),
        (2, DYNAMIC_PHYSICAL, 0x40),
        (3, DYNLIB_PHYSICAL, 0x100),
    )
    for segment_index, (program_index, physical, size) in enumerate(segment_specs):
        flags = registry.SELF_FLAG_BLOCKED | 0x4 | (program_index << 20)
        registry.SELF_SEGMENT.pack_into(
            data,
            registry.SELF_HEADER.size + segment_index * registry.SELF_SEGMENT.size,
            flags,
            physical,
            size,
            size,
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
    program_specs = (
        (registry.PT_LOAD, LOAD_LOGICAL, LOAD_GUEST, LOAD_BYTES, LOAD_BYTES),
        (registry.PT_LOAD, 0x2000, BSS_GUEST, 0, 0x1000),
        (resolver.PT_DYNAMIC, DYNAMIC_LOGICAL, 0, 0x40, 0x40),
        (resolver.PT_SCE_DYNLIBDATA, DYNLIB_LOGICAL, 0, 0x100, 0x100),
    )
    for index, (kind, logical, guest, file_size, memory_size) in enumerate(
        program_specs
    ):
        registry.ELF_PROGRAM_HEADER.pack_into(
            data,
            program_table + index * registry.ELF_PROGRAM_HEADER.size,
            kind,
            4,
            logical,
            guest,
            0,
            file_size,
            memory_size,
            0x1000,
        )

    hash_code_offset = LOAD_PHYSICAL + HASH_LOOKUP_GUEST - LOAD_GUEST
    name_code_offset = LOAD_PHYSICAL + NAME_LOOKUP_GUEST - LOAD_GUEST
    name_count_offset = LOAD_PHYSICAL + NAME_COUNT_GUEST - LOAD_GUEST
    data[hash_code_offset : hash_code_offset + len(HASH_LOOKUP_BLOB)] = HASH_LOOKUP_BLOB
    data[name_code_offset : name_code_offset + len(NAME_LOOKUP_BLOB)] = NAME_LOOKUP_BLOB
    data[name_count_offset : name_count_offset + len(NAME_COUNT_BLOB)] = NAME_COUNT_BLOB
    for index, (hash_word, registry_id) in enumerate(records):
        resolver.HASH_RECORD.pack_into(
            data,
            HASH_TABLE_PHYSICAL + index * resolver.HASH_RECORD.size,
            hash_word,
            registry_id,
            0,
        )
    data[STRING_ONE_PHYSICAL : STRING_ONE_PHYSICAL + 10] = b"TRANSFORM\0"
    data[STRING_TWO_PHYSICAL : STRING_TWO_PHYSICAL + 7] = b"BITMAP\0"

    defaults = [
        (NAME_TABLE_GUEST + 3 * 8, resolver.R_X86_64_RELATIVE_INFO, STRING_ONE_GUEST),
        (NAME_TABLE_GUEST + 7 * 8, resolver.R_X86_64_RELATIVE_INFO, STRING_TWO_GUEST),
    ]
    selected_relocations = defaults if relocations is None else relocations
    for index, relocation in enumerate(selected_relocations):
        resolver.RELOCATION_ENTRY.pack_into(
            data,
            RELOCATION_PHYSICAL + index * resolver.RELOCATION_ENTRY.size,
            *relocation,
        )
    relocation_bytes = len(selected_relocations) * resolver.RELOCATION_ENTRY.size
    defaults_dynamic = [
        (resolver.DT_SCE_RELA, 0x20),
        (resolver.DT_SCE_RELASZ, relocation_bytes),
        (resolver.DT_SCE_RELAENT, resolver.RELOCATION_ENTRY.size),
        (resolver.DT_NULL, 0),
    ]
    selected_dynamic = defaults_dynamic if dynamic_entries is None else dynamic_entries
    for index, entry in enumerate(selected_dynamic):
        resolver.DYNAMIC_ENTRY.pack_into(
            data,
            DYNAMIC_PHYSICAL + index * resolver.DYNAMIC_ENTRY.size,
            *entry,
        )
    path.write_bytes(data)


class SecondSonXppsEbootTypeNamesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.xpps = self.root / "source.xpps"
        self.eboot = self.root / "eboot.bin"
        self.hash_one = 0x1111222233334444
        self.hash_two = 0x5555666677778888
        make_xpps(
            self.xpps,
            [(32, self.hash_one), (128, self.hash_two), (160, self.hash_two)],
        )
        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        self.xpps_hash = hashlib.sha256(self.xpps.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite_eboot(
        self,
        records: list[tuple[int, int]],
        *,
        relocations: list[tuple[int, int, int]] | None = None,
        dynamic_entries: list[tuple[int, int]] | None = None,
    ) -> None:
        make_self(
            self.eboot,
            records,
            relocations=relocations,
            dynamic_entries=dynamic_entries,
        )
        self.eboot_hash = hashlib.sha256(self.eboot.read_bytes()).hexdigest()

    def resolve(self, **kwargs: object) -> dict[str, object]:
        options: dict[str, object] = {
            "hash_lookup_guest": HASH_LOOKUP_GUEST,
            "hash_lookup_bytes": len(HASH_LOOKUP_BLOB),
            "expected_hash_lookup_sha256": hashlib.sha256(HASH_LOOKUP_BLOB).hexdigest(),
            "name_lookup_guest": NAME_LOOKUP_GUEST,
            "name_lookup_bytes": len(NAME_LOOKUP_BLOB),
            "expected_name_lookup_sha256": hashlib.sha256(NAME_LOOKUP_BLOB).hexdigest(),
            "name_count_guest": NAME_COUNT_GUEST,
            "name_count_bytes": len(NAME_COUNT_BLOB),
            "expected_name_count_sha256": hashlib.sha256(NAME_COUNT_BLOB).hexdigest(),
            "hash_table_guest": HASH_TABLE_GUEST,
            "hash_record_count": 2,
            "name_table_guest": NAME_TABLE_GUEST,
            "name_table_count": 16,
        }
        options.update(kwargs)
        return resolver.resolve_type_names(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            **options,
        )

    def mutate(self, offset: int, payload: bytes) -> None:
        data = bytearray(self.eboot.read_bytes())
        data[offset : offset + len(payload)] = payload
        self.eboot.write_bytes(data)
        self.eboot_hash = hashlib.sha256(data).hexdigest()

    def test_exact_names_are_deterministic_and_sources_are_retained(self) -> None:
        xpps_before = self.xpps.read_bytes()
        eboot_before = self.eboot.read_bytes()
        first = self.resolve()
        second = self.resolve()
        self.assertEqual(resolver.encode_report(first), resolver.encode_report(second))
        self.assertEqual(self.xpps.read_bytes(), xpps_before)
        self.assertEqual(self.eboot.read_bytes(), eboot_before)
        self.assertEqual(
            [item["name"] for item in first["resolutions"]],
            ["TRANSFORM", "BITMAP"],
        )
        self.assertEqual(first["facts"]["total_xpps_dic_entries"], 3)
        self.assertEqual(first["dynamic_relocation_proof"]["relocation_count"], 2)
        self.assertTrue(
            first["lookup_proof"]["hash_registry"]["signed_keys_strictly_increasing"]
        )
        self.assertNotIn(str(self.root), resolver.encode_report(first).decode())

    def test_registry_order_reserved_absence_and_range_are_refused(self) -> None:
        self.mutate(
            HASH_TABLE_PHYSICAL + resolver.HASH_RECORD.size,
            resolver.HASH_RECORD.pack(self.hash_one - 1, 7, 0),
        )
        with self.assertRaisesRegex(probe.ProbeError, "strictly increasing"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        self.mutate(HASH_TABLE_PHYSICAL + 12, struct.pack("<I", 1))
        with self.assertRaisesRegex(probe.ProbeError, "nonzero reserved"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        with self.assertRaisesRegex(probe.ProbeError, "absent from the guarded"):
            self.resolve(hash_record_count=1)

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 20)])
        with self.assertRaisesRegex(probe.ProbeError, "outside the 16 name slots"):
            self.resolve()

    def test_registry_id_disagreement_and_lookup_drift_are_refused(self) -> None:
        inherited = registry.correlate_registry(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
        )
        malformed = copy.deepcopy(inherited)
        malformed["correlations"][0]["candidates"][0][
            "opaque_registry_value_decimal"
        ] = 99
        with mock.patch.object(
            resolver.registry, "correlate_registry", return_value=malformed
        ):
            with self.assertRaisesRegex(probe.ProbeError, "disagrees"):
                self.resolve()

        self.mutate(LOAD_PHYSICAL + 0x10, b"X")
        with self.assertRaisesRegex(probe.ProbeError, "hash lookup function SHA-256"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        self.mutate(LOAD_PHYSICAL + 0x70, b"X")
        with self.assertRaisesRegex(probe.ProbeError, "name-count loop function"):
            self.resolve()

    def test_missing_duplicate_and_wrong_relocations_are_refused(self) -> None:
        missing = [
            (NAME_TABLE_GUEST + 3 * 8, 8, STRING_ONE_GUEST),
            (NAME_TABLE_GUEST + 9 * 8, 8, STRING_TWO_GUEST),
        ]
        self.rewrite_eboot(
            [(self.hash_one, 3), (self.hash_two, 7)], relocations=missing
        )
        with self.assertRaisesRegex(probe.ProbeError, "must have exactly one"):
            self.resolve()

        duplicate = [
            (NAME_TABLE_GUEST + 3 * 8, 8, STRING_ONE_GUEST),
            (NAME_TABLE_GUEST + 3 * 8, 8, STRING_TWO_GUEST),
        ]
        self.rewrite_eboot(
            [(self.hash_one, 3), (self.hash_two, 7)], relocations=duplicate
        )
        with self.assertRaisesRegex(probe.ProbeError, "must have exactly one"):
            self.resolve()

        wrong = [
            (NAME_TABLE_GUEST + 3 * 8, 7, STRING_ONE_GUEST),
            (NAME_TABLE_GUEST + 7 * 8, 8, STRING_TWO_GUEST),
        ]
        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)], relocations=wrong)
        with self.assertRaisesRegex(probe.ProbeError, "exact relative"):
            self.resolve()

    def test_name_slot_target_and_ascii_edges_are_refused(self) -> None:
        bss_header = (
            registry.SELF_HEADER.size
            + 3 * registry.SELF_SEGMENT.size
            + registry.ELF_HEADER.size
            + registry.ELF_PROGRAM_HEADER.size
        )
        self.mutate(bss_header + 32, struct.pack("<Q", 0x2000))
        with self.assertRaisesRegex(probe.ProbeError, "file range exceeds"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        with self.assertRaisesRegex(probe.ProbeError, "load memory range"):
            self.resolve(name_table_guest=0x600000)

        unmapped = [
            (NAME_TABLE_GUEST + 3 * 8, 8, 0x900000),
            (NAME_TABLE_GUEST + 7 * 8, 8, STRING_TWO_GUEST),
        ]
        self.rewrite_eboot(
            [(self.hash_one, 3), (self.hash_two, 7)], relocations=unmapped
        )
        with self.assertRaisesRegex(probe.ProbeError, "file-backed load mapping"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        self.mutate(STRING_ONE_PHYSICAL, b"\0")
        with self.assertRaisesRegex(probe.ProbeError, "is empty"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        self.mutate(STRING_ONE_PHYSICAL, b"\xff")
        with self.assertRaisesRegex(probe.ProbeError, "printable ASCII"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        self.mutate(STRING_ONE_PHYSICAL, b"A" * resolver.MAX_NAME_BYTES)
        with self.assertRaisesRegex(probe.ProbeError, "not terminated"):
            self.resolve()

    def test_dynamic_metadata_ranges_and_budgets_are_refused(self) -> None:
        duplicate_tags = [
            (resolver.DT_SCE_RELA, 0x20),
            (resolver.DT_SCE_RELA, 0x20),
            (resolver.DT_SCE_RELASZ, 48),
            (resolver.DT_NULL, 0),
        ]
        self.rewrite_eboot(
            [(self.hash_one, 3), (self.hash_two, 7)],
            dynamic_entries=duplicate_tags,
        )
        with self.assertRaisesRegex(probe.ProbeError, "exactly one DT_SCE_RELA"):
            self.resolve()

        bad_range = [
            (resolver.DT_SCE_RELA, 0xF0),
            (resolver.DT_SCE_RELASZ, 48),
            (resolver.DT_SCE_RELAENT, 24),
            (resolver.DT_NULL, 0),
        ]
        self.rewrite_eboot(
            [(self.hash_one, 3), (self.hash_two, 7)], dynamic_entries=bad_range
        )
        with self.assertRaisesRegex(probe.ProbeError, "exceeds PT_SCE_DYNLIBDATA"):
            self.resolve()

        self.rewrite_eboot([(self.hash_one, 3), (self.hash_two, 7)])
        with self.assertRaisesRegex(probe.ProbeError, "relocation population"):
            self.resolve(max_relocations=1)

    def test_symlinks_and_mutation_are_refused(self) -> None:
        eboot_link = self.root / "eboot-link.bin"
        eboot_link.symlink_to(self.eboot)
        with self.assertRaisesRegex(probe.ProbeError, "nonsymlink"):
            resolver.resolve_type_names(
                self.xpps,
                expected_xpps_sha256=self.xpps_hash,
                row_index=2,
                eboot=eboot_link,
                expected_eboot_sha256=self.eboot_hash,
            )

        original = resolver.registry._hash_stream
        calls = 0

        def changed_after_correlation(stream: object) -> str:
            nonlocal calls
            calls += 1
            value = original(stream)
            if calls == 7:
                return "0" * 64
            return value

        with mock.patch.object(
            resolver.registry, "_hash_stream", side_effect=changed_after_correlation
        ):
            with self.assertRaisesRegex(probe.ProbeError, "changed during"):
                self.resolve()


if __name__ == "__main__":
    unittest.main()
