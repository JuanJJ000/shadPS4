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
import second_son_xpps_chunks as chunks
import second_son_xpps_probe as probe
import second_son_xpps_targets as targets


def chunk(tag: bytes, content: bytes) -> bytes:
    return tag + struct.pack("<I", len(content)) + content


def dic_chunk(entries: list[tuple[int, int]]) -> bytes:
    content = struct.pack("<II", len(entries), 0)
    content += b"".join(struct.pack("<QQ", offset, hash_word) for offset, hash_word in entries)
    return chunk(b" DIC", content)


def make_xpps(path: Path, rows: list[tuple[int, bytes]], *, magic: bytes = b"KCAP") -> None:
    package_header_offset = 152
    package_extent = probe.PACKAGE_FIXED_BYTES + len(rows) * probe.TABLE_ROW_BYTES
    data_start = package_header_offset + package_extent
    payload_size = sum(len(payload) for _, payload in rows)
    data = bytearray(data_start + payload_size)
    data[:4] = magic
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


class SecondSonXppsTargetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.xpps"
        self.row_zero = bytes(range(256))
        self.entries = [(32, 0x1111), (128, 0x2222)]
        self.rewrite(self.entries)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite(self, entries: list[tuple[int, int]], *, magic: bytes = b"KCAP") -> None:
        row_two = dic_chunk(entries) + chunk(b" DNE", b"")
        make_xpps(
            self.source,
            [(0x0FF00, self.row_zero), (0x1FF12, b"B" * 96), (0x2FF03, row_two)],
            magic=magic,
        )
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def classify(self, **kwargs: object) -> dict[str, object]:
        return targets.classify_targets(
            self.source,
            expected_sha256=self.source_hash,
            row_index=2,
            **kwargs,
        )

    def test_cross_row_targets_are_deterministic_and_retained(self) -> None:
        before = self.source.read_bytes()
        first = self.classify()
        second = self.classify()
        self.assertEqual(targets.encode_report(first), targets.encode_report(second))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(first["facts"]["total_dic_entries"], 2)
        self.assertEqual(first["observations"][0]["containing_row"]["index"], 0)
        self.assertEqual(first["observations"][0]["target"]["offset_in_row"], 32)
        self.assertEqual(first["observations"][0]["neighbors"]["next_unique_delta"], 96)
        self.assertNotIn(str(self.root), targets.encode_report(first).decode())

    def test_same_offset_aliases_retain_unique_neighbors(self) -> None:
        self.rewrite([(32, 1), (32, 2), (128, 3)])
        report = self.classify()
        self.assertEqual(report["facts"]["distinct_target_offsets"], 2)
        self.assertEqual(report["facts"]["max_alias_count"], 2)
        self.assertEqual(report["observations"][0]["alias_count"], 2)
        self.assertEqual(report["observations"][1]["neighbors"]["next_unique_delta"], 96)

    def test_incomplete_predecessor_window_is_refused(self) -> None:
        self.rewrite([(8, 1)])
        with self.assertRaisesRegex(probe.ProbeError, "predecessor window"):
            self.classify()

    def test_incomplete_target_window_is_refused(self) -> None:
        self.rewrite([(224, 1)])
        with self.assertRaisesRegex(probe.ProbeError, "target window"):
            self.classify()

    def test_entry_budget_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "exceeds 1 total entries"):
            self.classify(max_entries=1)

    def test_empty_dic_registry_is_refused(self) -> None:
        self.rewrite([])
        with self.assertRaisesRegex(probe.ProbeError, "no DIC entries"):
            self.classify()

    def test_hash_and_row_kind_refusals_are_inherited(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "mismatch"):
            targets.classify_targets(
                self.source,
                expected_sha256="0" * 64,
                row_index=2,
            )
        with self.assertRaisesRegex(probe.ProbeError, "expected 2"):
            targets.classify_targets(
                self.source,
                expected_sha256=self.source_hash,
                row_index=1,
            )

    def test_bad_magic_and_symlink_refusals_are_inherited(self) -> None:
        self.rewrite(self.entries, magic=b"PACK")
        with self.assertRaisesRegex(probe.ProbeError, "wrong magic"):
            self.classify()
        self.rewrite(self.entries)
        link = self.root / "link.xpps"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            targets.classify_targets(
                link,
                expected_sha256=self.source_hash,
                row_index=2,
            )

    def test_malformed_classifier_report_is_refused(self) -> None:
        with mock.patch.object(chunks, "classify_chunks", return_value={"chunks": {}}):
            with self.assertRaisesRegex(probe.ProbeError, "unexpected shape"):
                self.classify()

    def test_post_classification_mutation_signal_is_refused(self) -> None:
        with mock.patch.object(
            targets,
            "_hash_stream",
            side_effect=[self.source_hash, "0" * 64],
        ):
            with self.assertRaisesRegex(probe.ProbeError, "source changed during"):
                self.classify()


if __name__ == "__main__":
    unittest.main()
