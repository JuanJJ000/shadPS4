# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import second_son_xpps_probe as probe


def make_xpps(
    path: Path,
    rows: list[tuple[int, bytes, int | None]],
    *,
    magic: bytes = b"KCAP",
    package_header_offset: int = 152,
    extent_adjustment: int = 0,
) -> None:
    row_count = len(rows)
    package_extent = probe.PACKAGE_FIXED_BYTES + row_count * probe.TABLE_ROW_BYTES
    data_start = package_header_offset + package_extent + extent_adjustment
    if not rows:
        data_start = 0
    payload_size = 0
    normalized: list[tuple[int, bytes, int]] = []
    for kind, payload, explicit_offset in rows:
        relative_offset = payload_size if explicit_offset is None else explicit_offset
        normalized.append((kind, payload, relative_offset))
        payload_size = max(payload_size, relative_offset + len(payload))

    file_size = package_header_offset + package_extent if not rows else data_start + payload_size
    data = bytearray(file_size)
    data[:4] = magic
    struct.pack_into("<I", data, 24, package_header_offset)
    struct.pack_into("<I", data, 28, package_extent)
    struct.pack_into("<I", data, 40, data_start)
    struct.pack_into("<I", data, 44, payload_size)
    struct.pack_into("<I", data, package_header_offset + 8, row_count)

    table_start = package_header_offset + probe.PACKAGE_FIXED_BYTES
    for index, (kind, payload, relative_offset) in enumerate(normalized):
        struct.pack_into(
            "<10I",
            data,
            table_start + index * probe.TABLE_ROW_BYTES,
            kind,
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
    path.write_bytes(data)


class SecondSonXppsProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_valid_table_is_deterministic_and_exact(self) -> None:
        target = self.root / "sample.xpps"
        make_xpps(target, [(0xFF00, b"alpha", None), (0x1FF12, b" DIComega", None)])
        before = hashlib.sha256(target.read_bytes()).hexdigest()

        first = probe.build_report(target)
        second = probe.build_report(target)

        self.assertEqual(probe.encode_report(first), probe.encode_report(second))
        self.assertEqual(first["files"][0]["input"]["sha256"], before)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), before)
        self.assertTrue(first["files"][0]["facts"]["payload_ranges_exactly_cover_data"])
        self.assertEqual(first["files"][0]["structural_tag_offsets"][" DIC"], [285])

    def test_valid_empty_stub(self) -> None:
        target = self.root / "empty.xpps"
        make_xpps(target, [])
        result = probe.probe_file(target)
        self.assertEqual(result["candidate_layout"]["row_count"], 0)
        self.assertTrue(result["facts"]["payload_ranges_exactly_cover_data"])

    def test_bad_magic_is_refused(self) -> None:
        target = self.root / "wrong.xpps"
        make_xpps(target, [], magic=b"PACK")
        with self.assertRaisesRegex(probe.ProbeError, "wrong magic"):
            probe.probe_file(target)

    def test_truncated_header_is_refused(self) -> None:
        target = self.root / "short.xpps"
        target.write_bytes(b"KCAP" + bytes(59))
        with self.assertRaisesRegex(probe.ProbeError, "too small|truncated"):
            probe.probe_file(target)

    def test_out_of_range_row_is_refused(self) -> None:
        target = self.root / "range.xpps"
        make_xpps(target, [(1, b"one", None)])
        table_start = 152 + probe.PACKAGE_FIXED_BYTES
        with target.open("r+b") as stream:
            stream.seek(table_start + 4)
            stream.write(struct.pack("<I", 9999))
        with self.assertRaisesRegex(probe.ProbeError, "exceeds the declared data"):
            probe.probe_file(target)

    def test_overlapping_rows_are_refused(self) -> None:
        target = self.root / "overlap.xpps"
        make_xpps(target, [(1, b"abcd", 0), (2, b"xy", 2)])
        with self.assertRaisesRegex(probe.ProbeError, "overlap"):
            probe.probe_file(target)

    def test_near_match_extent_is_refused(self) -> None:
        target = self.root / "extent.xpps"
        make_xpps(target, [(1, b"data", None)], extent_adjustment=4)
        with self.assertRaisesRegex(probe.ProbeError, "extent"):
            probe.probe_file(target)

    def test_directory_order_is_deterministic(self) -> None:
        make_xpps(self.root / "z.xpps", [])
        make_xpps(self.root / "A.xpps", [])
        result = probe.build_report(self.root)
        self.assertEqual(
            [item["input"]["basename"] for item in result["files"]], ["A.xpps", "z.xpps"]
        )

    def test_directory_population_limit_is_refused(self) -> None:
        make_xpps(self.root / "a.xpps", [])
        make_xpps(self.root / "b.xpps", [])
        with self.assertRaisesRegex(probe.ProbeError, "exceeds 1"):
            probe.collect_inputs(self.root, max_files=1)

    def test_per_file_row_limit_is_refused(self) -> None:
        target = self.root / "rows.xpps"
        make_xpps(target, [(1, b"a", None), (2, b"b", None)])
        original = probe.MAX_ROWS_PER_FILE
        try:
            probe.MAX_ROWS_PER_FILE = 1
            with self.assertRaisesRegex(probe.ProbeError, "rows per file"):
                probe.probe_file(target)
        finally:
            probe.MAX_ROWS_PER_FILE = original

    def test_total_row_limit_is_refused(self) -> None:
        target = self.root / "rows.xpps"
        make_xpps(target, [(1, b"a", None), (2, b"b", None)])
        with self.assertRaisesRegex(probe.ProbeError, "total table rows"):
            probe.build_report(target, max_total_rows=1)

    def test_total_tag_offset_limit_is_refused(self) -> None:
        target = self.root / "tags.xpps"
        make_xpps(target, [(1, b" DIC", None)])
        with self.assertRaisesRegex(probe.ProbeError, "total tag offsets"):
            probe.build_report(target, max_total_tag_offsets=1)

    def test_symlink_is_refused(self) -> None:
        target = self.root / "target.xpps"
        make_xpps(target, [])
        link = self.root / "link.xpps"
        link.symlink_to(target)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            probe.probe_file(link)

    def test_size_limit_is_refused(self) -> None:
        target = self.root / "large.xpps"
        make_xpps(target, [])
        original = probe.MAX_FILE_BYTES
        try:
            probe.MAX_FILE_BYTES = target.stat().st_size - 1
            with self.assertRaisesRegex(probe.ProbeError, "exceeds"):
                probe.probe_file(target)
        finally:
            probe.MAX_FILE_BYTES = original

    def test_output_is_created_once(self) -> None:
        output = self.root / "report.json"
        probe.write_new(output, b"first\n")
        self.assertEqual(output.read_bytes(), b"first\n")
        with self.assertRaisesRegex(probe.ProbeError, "replace"):
            probe.write_new(output, b"second\n")
        self.assertEqual(output.read_bytes(), b"first\n")


if __name__ == "__main__":
    unittest.main()
