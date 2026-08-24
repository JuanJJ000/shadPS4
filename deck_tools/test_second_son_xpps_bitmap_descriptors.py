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
import second_son_xpps_bitmap_descriptors as bitmaps
import second_son_xpps_probe as probe
import test_second_son_xpps_eboot_type_names as type_fixture

ROW0_BYTES = 0x200
ROW1_BYTES = 0xA00
TARGETS = (0x20, 0x80)
PAYLOAD_BASES = (0x200, 0x600)


def chunk(tag: bytes, content: bytes) -> bytes:
    return tag + struct.pack("<I", len(content)) + content


def dic_chunk(entries: list[tuple[int, int]]) -> bytes:
    content = struct.pack("<II", len(entries), 0)
    content += b"".join(
        struct.pack("<QQ", offset, hash_word) for offset, hash_word in entries
    )
    return chunk(b" DIC", content)


def image_descriptor(
    *,
    base: int,
    data_format: int,
    width: int,
    height: int,
    pitch: int,
    levels: int,
    image_type: int = 9,
    number_format: int = 0,
    tile_mode: int = 13,
    min_lod: int = 0,
    base_level: int = 0,
    depth: int = 1,
    base_array: int = 0,
    last_array: int = 0,
    pow2pad: int = 0,
    compression: int = 0,
    alternate_tile: int = 0,
    word2_reserved: int = 0,
    word3_reserved: int = 0,
) -> bytes:
    word0 = base | (min_lod << 40) | (data_format << 52) | (number_format << 58)
    word1 = (
        (width - 1)
        | ((height - 1) << 14)
        | (base_level << 44)
        | ((levels - 1) << 48)
        | (tile_mode << 52)
        | (pow2pad << 57)
        | (image_type << 60)
    )
    word2 = (
        (depth - 1)
        | ((pitch - 1) << 13)
        | (base_array << 32)
        | (last_array << 45)
        | word2_reserved
    )
    word3 = compression << 21 | alternate_tile << 24 | word3_reserved
    return bitmaps.DESCRIPTOR.pack(word0, word1, word2, word3)


def default_descriptors() -> list[bytes]:
    return [
        image_descriptor(
            base=PAYLOAD_BASES[0],
            data_format=10,
            width=8,
            height=8,
            pitch=8,
            levels=4,
        ),
        image_descriptor(
            base=PAYLOAD_BASES[1],
            data_format=35,
            width=4,
            height=4,
            pitch=4,
            levels=3,
        ),
    ]


def make_xpps(path: Path, hash_word: int, descriptors: list[bytes]) -> None:
    row0 = bytearray(ROW0_BYTES)
    for target, descriptor in zip(TARGETS, descriptors, strict=True):
        start = target + bitmaps.DESCRIPTOR_OFFSET_FROM_TARGET
        row0[start : start + len(descriptor)] = descriptor
    row1 = bytes(index % 251 for index in range(ROW1_BYTES))
    row2 = dic_chunk([(target, hash_word) for target in TARGETS]) + chunk(b" DNE", b"")
    rows = [
        (0x0FF00, bytes(row0)),
        (0x1FF12, row1),
        (0x2FF03, row2),
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


class SecondSonXppsBitmapDescriptorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.xpps = self.root / "source.xpps"
        self.eboot = self.root / "eboot.bin"
        self.hash_word = 0x1111222233334444
        self.rewrite_xpps(default_descriptors())
        type_fixture.make_self(self.eboot, [(self.hash_word, 7)])
        self.eboot_hash = hashlib.sha256(self.eboot.read_bytes()).hexdigest()
        self.resolver_overrides: dict[str, object] = {
            "hash_lookup_guest": type_fixture.HASH_LOOKUP_GUEST,
            "hash_lookup_bytes": len(type_fixture.HASH_LOOKUP_BLOB),
            "expected_hash_lookup_sha256": hashlib.sha256(
                type_fixture.HASH_LOOKUP_BLOB
            ).hexdigest(),
            "name_lookup_guest": type_fixture.NAME_LOOKUP_GUEST,
            "name_lookup_bytes": len(type_fixture.NAME_LOOKUP_BLOB),
            "expected_name_lookup_sha256": hashlib.sha256(
                type_fixture.NAME_LOOKUP_BLOB
            ).hexdigest(),
            "name_count_guest": type_fixture.NAME_COUNT_GUEST,
            "name_count_bytes": len(type_fixture.NAME_COUNT_BLOB),
            "expected_name_count_sha256": hashlib.sha256(
                type_fixture.NAME_COUNT_BLOB
            ).hexdigest(),
            "hash_table_guest": type_fixture.HASH_TABLE_GUEST,
            "hash_record_count": 1,
            "name_table_guest": type_fixture.NAME_TABLE_GUEST,
            "name_table_count": 16,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite_xpps(self, descriptors: list[bytes]) -> None:
        make_xpps(self.xpps, self.hash_word, descriptors)
        self.xpps_hash = hashlib.sha256(self.xpps.read_bytes()).hexdigest()

    def classify(self, **kwargs: object) -> dict[str, object]:
        options: dict[str, object] = {
            "resolver_overrides": self.resolver_overrides,
        }
        options.update(kwargs)
        return bitmaps.classify_bitmap_descriptors(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            **options,
        )

    def test_exact_descriptors_are_deterministic_and_sources_are_retained(self) -> None:
        xpps_before = self.xpps.read_bytes()
        eboot_before = self.eboot.read_bytes()
        first = self.classify()
        second = self.classify()
        self.assertEqual(bitmaps.encode_report(first), bitmaps.encode_report(second))
        self.assertEqual(self.xpps.read_bytes(), xpps_before)
        self.assertEqual(self.eboot.read_bytes(), eboot_before)
        self.assertEqual(first["facts"]["bitmap_dic_entries"], 2)
        self.assertEqual(first["facts"]["distinct_bitmap_targets"], 2)
        self.assertEqual(first["facts"]["total_payload_bytes"], 0xA00)
        self.assertEqual(first["facts"]["contiguous_payload_adjacencies"], 1)
        descriptors = first["descriptors"]
        self.assertEqual(
            [
                item["descriptor"]["fields"]["data_format"]["name"]
                for item in descriptors
            ],
            ["Format8_8_8_8", "FormatBc1"],
        )
        self.assertEqual(
            [mip["bytes"] for mip in descriptors[0]["payload"]["mips"]],
            [0x100, 0x100, 0x100, 0x100],
        )
        self.assertEqual(
            [mip["bytes"] for mip in descriptors[1]["payload"]["mips"]],
            [0x200, 0x200, 0x200],
        )
        data_start = first["layout"]["data_start"]
        expected_first = hashlib.sha256(
            xpps_before[data_start + 0x200 : data_start + 0x600]
        ).hexdigest()
        self.assertEqual(descriptors[0]["payload"]["sha256"], expected_first)
        self.assertNotIn(str(self.root), bitmaps.encode_report(first).decode())
        self.assertNotIn("raw_words", bitmaps.encode_report(first).decode())

    def test_unsupported_descriptor_fields_are_refused(self) -> None:
        cases = [
            (
                "image type",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=8,
                    height=8,
                    pitch=8,
                    levels=4,
                    image_type=0,
                ),
            ),
            (
                "data format",
                image_descriptor(
                    base=0x200,
                    data_format=36,
                    width=8,
                    height=8,
                    pitch=8,
                    levels=4,
                ),
            ),
            (
                "number format",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=8,
                    height=8,
                    pitch=8,
                    levels=4,
                    number_format=1,
                ),
            ),
            (
                "tile mode",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=8,
                    height=8,
                    pitch=8,
                    levels=4,
                    tile_mode=14,
                ),
            ),
            (
                "base mip level zero",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=8,
                    height=8,
                    pitch=8,
                    levels=4,
                    base_level=1,
                ),
            ),
            (
                "pitch is smaller",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=8,
                    height=8,
                    pitch=4,
                    levels=4,
                ),
            ),
            (
                "Color1D",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=1,
                    height=2,
                    pitch=1,
                    levels=1,
                    image_type=8,
                ),
            ),
            (
                "one-layer",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=8,
                    height=8,
                    pitch=8,
                    levels=4,
                    depth=2,
                ),
            ),
            (
                "Neo compression",
                image_descriptor(
                    base=0x200,
                    data_format=10,
                    width=8,
                    height=8,
                    pitch=8,
                    levels=4,
                    compression=1,
                ),
            ),
        ]
        for message, malformed in cases:
            with self.subTest(message=message):
                self.rewrite_xpps([malformed, default_descriptors()[1]])
                with self.assertRaisesRegex(probe.ProbeError, message):
                    self.classify()

    def test_power_of_two_padding_uses_the_repository_size_path(self) -> None:
        padded = image_descriptor(
            base=0x200,
            data_format=10,
            width=5,
            height=3,
            pitch=5,
            levels=1,
            pow2pad=1,
        )
        second = image_descriptor(
            base=0x300,
            data_format=10,
            width=1,
            height=1,
            pitch=1,
            levels=1,
        )
        self.rewrite_xpps([padded, second])
        report = self.classify()
        mip = report["descriptors"][0]["payload"]["mips"][0]
        self.assertEqual(mip["storage_pitch"], 8)
        self.assertEqual(mip["storage_height"], 4)
        self.assertEqual(mip["aligned_storage_pitch"], 8)
        self.assertEqual(mip["aligned_storage_height"], 8)
        self.assertEqual(mip["bytes"], 0x100)

    def test_reserved_bits_are_refused(self) -> None:
        malformed = image_descriptor(
            base=0x200,
            data_format=10,
            width=8,
            height=8,
            pitch=8,
            levels=4,
            word2_reserved=1 << 27,
        )
        self.rewrite_xpps([malformed, default_descriptors()[1]])
        with self.assertRaisesRegex(probe.ProbeError, "third word"):
            self.classify()

        malformed = image_descriptor(
            base=0x200,
            data_format=10,
            width=8,
            height=8,
            pitch=8,
            levels=4,
            word3_reserved=1 << 25,
        )
        self.rewrite_xpps([malformed, default_descriptors()[1]])
        with self.assertRaisesRegex(probe.ProbeError, "fourth word"):
            self.classify()

    def test_wrong_row_overflow_and_overlap_are_refused(self) -> None:
        metadata_payload = image_descriptor(
            base=0x100,
            data_format=10,
            width=1,
            height=1,
            pitch=1,
            levels=1,
        )
        self.rewrite_xpps([metadata_payload, default_descriptors()[1]])
        with self.assertRaisesRegex(probe.ProbeError, "not in a high-kind-1"):
            self.classify()

        overflow = image_descriptor(
            base=0x4000,
            data_format=10,
            width=8,
            height=8,
            pitch=8,
            levels=4,
        )
        self.rewrite_xpps([overflow, default_descriptors()[1]])
        with self.assertRaisesRegex(probe.ProbeError, "exceeds the XPPS data"):
            self.classify()

        overlapping = image_descriptor(
            base=0x300,
            data_format=35,
            width=4,
            height=4,
            pitch=4,
            levels=3,
        )
        self.rewrite_xpps([default_descriptors()[0], overlapping])
        with self.assertRaisesRegex(probe.ProbeError, "ranges overlap"):
            self.classify()

    def test_population_and_payload_budgets_are_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "BITMAP population"):
            self.classify(max_bitmap_entries=1)
        with self.assertRaisesRegex(probe.ProbeError, "payload population"):
            self.classify(max_total_payload_bytes=0x9FF)
        with self.assertRaisesRegex(probe.ProbeError, "unknown synthetic"):
            self.classify(resolver_overrides={"not_a_gate": 1})

    def test_missing_malformed_and_disagreeing_name_proofs_are_refused(self) -> None:
        with mock.patch.object(
            bitmaps.type_names,
            "resolve_type_names",
            return_value={"resolutions": []},
        ):
            with self.assertRaisesRegex(probe.ProbeError, "no BITMAP"):
                self.classify()

        with mock.patch.object(
            bitmaps.type_names,
            "resolve_type_names",
            return_value={
                "resolutions": [
                    {
                        "name": "BITMAP",
                        "dic_hash_word_hex": "bad",
                        "xpps_dic_entry_count": 2,
                    }
                ]
            },
        ):
            with self.assertRaisesRegex(probe.ProbeError, "malformed DIC hash"):
                self.classify()

        exact = bitmaps.type_names.resolve_type_names(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            **self.resolver_overrides,
        )
        malformed = copy.deepcopy(exact)
        bitmap_resolution = next(
            item for item in malformed["resolutions"] if item["name"] == "BITMAP"
        )
        bitmap_resolution["xpps_dic_entry_count"] = 1
        with mock.patch.object(
            bitmaps.type_names,
            "resolve_type_names",
            return_value=malformed,
        ):
            with self.assertRaisesRegex(probe.ProbeError, "population disagrees"):
                self.classify()

    def test_symlinks_and_mutation_are_refused(self) -> None:
        xpps_link = self.root / "source-link.xpps"
        xpps_link.symlink_to(self.xpps)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            bitmaps.classify_bitmap_descriptors(
                xpps_link,
                expected_xpps_sha256=self.xpps_hash,
                row_index=2,
                eboot=self.eboot,
                expected_eboot_sha256=self.eboot_hash,
                resolver_overrides=self.resolver_overrides,
            )

        original = bitmaps.registry._hash_stream
        calls = 0

        def changed_at_final_hash(stream: object) -> str:
            nonlocal calls
            calls += 1
            value = original(stream)
            if calls == 11:
                return "0" * 64
            return value

        with mock.patch.object(
            bitmaps.registry,
            "_hash_stream",
            side_effect=changed_at_final_hash,
        ):
            with self.assertRaisesRegex(probe.ProbeError, "changed during BITMAP"):
                self.classify()


if __name__ == "__main__":
    unittest.main()
