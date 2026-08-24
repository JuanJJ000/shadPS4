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
import second_son_xpps_chunks as classifier
import second_son_xpps_probe as probe


def chunk(tag: bytes, content: bytes) -> bytes:
    return tag + struct.pack("<I", len(content)) + content


def dic_chunk(entries: list[tuple[int, int]], *, count: int | None = None) -> bytes:
    observed_count = len(entries) if count is None else count
    content = struct.pack("<II", observed_count, 0)
    content += b"".join(struct.pack("<QQ", offset, hash_word) for offset, hash_word in entries)
    return chunk(b" DIC", content)


def make_xpps(
    path: Path, rows: list[tuple[int, bytes]], *, magic: bytes = b"KCAP"
) -> None:
    package_header_offset = 152
    package_extent = probe.PACKAGE_FIXED_BYTES + len(rows) * probe.TABLE_ROW_BYTES
    data_start = package_header_offset + package_extent if rows else 0
    payload_size = sum(len(payload) for _, payload in rows)
    file_size = package_header_offset + package_extent if not rows else data_start + payload_size
    data = bytearray(file_size)
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


class SecondSonXppsChunksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.xpps"
        self.chunk_row = (
            chunk(b"KNLI", b"abcd")
            + dic_chunk([(0, 0x1234), (16, 0x5678)])
            + chunk(b" DNE", b"")
        )
        make_xpps(
            self.source,
            [(0x0FF00, b"A" * 64), (0x1FF12, b"B" * 32), (0x2FF03, self.chunk_row)],
        )
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def classify(self, **kwargs: object) -> dict[str, object]:
        return classifier.classify_chunks(
            self.source,
            expected_sha256=self.source_hash,
            row_index=2,
            **kwargs,
        )

    def rewrite_chunk_row(self, payload: bytes) -> None:
        make_xpps(
            self.source,
            [(0x0FF00, b"A" * 64), (0x1FF12, b"B" * 32), (0x2FF03, payload)],
        )
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_valid_chunk_and_dic_registry_is_deterministic(self) -> None:
        before = self.source.read_bytes()
        first = self.classify()
        second = self.classify()

        self.assertEqual(classifier.encode_report(first), classifier.encode_report(second))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual([item["tag_ascii"] for item in first["chunks"]], ["KNLI", " DIC", " DNE"])
        self.assertEqual(first["chunks"][1]["dic"]["count"], 2)
        self.assertEqual(first["chunks"][1]["dic"]["entries"][1]["relative_offset"], 16)
        self.assertTrue(first["facts"]["chunk_stream_exactly_covers_row"])
        self.assertNotIn(str(self.root), classifier.encode_report(first).decode())

    def test_hash_mismatch_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "mismatch"):
            classifier.classify_chunks(
                self.source,
                expected_sha256="0" * 64,
                row_index=2,
            )

    def test_wrong_row_kind_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "expected 2"):
            classifier.classify_chunks(
                self.source,
                expected_sha256=self.source_hash,
                row_index=1,
            )

    def test_truncated_prefix_is_refused(self) -> None:
        self.rewrite_chunk_row(b"short")
        with self.assertRaisesRegex(probe.ProbeError, "truncated chunk prefix"):
            self.classify()

    def test_oversized_chunk_is_refused(self) -> None:
        self.rewrite_chunk_row(b"TEST" + struct.pack("<I", 99) + b"x")
        with self.assertRaisesRegex(probe.ProbeError, "exceeds"):
            self.classify()

    def test_nonterminal_zero_size_chunk_is_refused(self) -> None:
        self.rewrite_chunk_row(chunk(b"ZERO", b"") + chunk(b" DNE", b""))
        with self.assertRaisesRegex(probe.ProbeError, "not terminal"):
            self.classify()

    def test_dic_count_size_mismatch_is_refused(self) -> None:
        self.rewrite_chunk_row(dic_chunk([(0, 1)], count=2))
        with self.assertRaisesRegex(probe.ProbeError, "does not exactly match"):
            self.classify()

    def test_dic_out_of_range_offset_is_refused(self) -> None:
        invalid_offset = len(b"A" * 64 + b"B" * 32 + dic_chunk([]))
        self.rewrite_chunk_row(dic_chunk([(invalid_offset + 1024, 1)]))
        with self.assertRaisesRegex(probe.ProbeError, "offset exceeds"):
            self.classify()

    def test_chunk_population_budget_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "exceeds 1 chunks"):
            self.classify(max_chunks=1)

    def test_dic_population_budget_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "exceeds 1 total entries"):
            self.classify(max_dic_entries=1)

    def test_bad_magic_is_inherited(self) -> None:
        make_xpps(self.source, [], magic=b"PACK")
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(probe.ProbeError, "wrong magic"):
            classifier.classify_chunks(
                self.source,
                expected_sha256=source_hash,
                row_index=0,
            )

    def test_symlink_is_inherited(self) -> None:
        link = self.root / "link.xpps"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            classifier.classify_chunks(
                link,
                expected_sha256=self.source_hash,
                row_index=2,
            )

    def test_source_mutation_signal_is_refused(self) -> None:
        with mock.patch.object(classifier, "_hash_stream", return_value="0" * 64):
            with self.assertRaisesRegex(probe.ProbeError, "source changed"):
                self.classify()


if __name__ == "__main__":
    unittest.main()
