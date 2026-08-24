# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import hashlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_xpps_extract as extractor
import second_son_xpps_probe as probe


def make_xpps(path: Path, payloads: list[bytes], *, magic: bytes = b"KCAP") -> None:
    package_header_offset = 152
    package_extent = probe.PACKAGE_FIXED_BYTES + len(payloads) * probe.TABLE_ROW_BYTES
    data_start = package_header_offset + package_extent if payloads else 0
    payload_size = sum(map(len, payloads))
    file_size = package_header_offset + package_extent if not payloads else data_start + payload_size
    data = bytearray(file_size)
    data[:4] = magic
    struct.pack_into("<I", data, 24, package_header_offset)
    struct.pack_into("<I", data, 28, package_extent)
    struct.pack_into("<I", data, 40, data_start)
    struct.pack_into("<I", data, 44, payload_size)
    struct.pack_into("<I", data, package_header_offset + 8, len(payloads))
    table_start = package_header_offset + probe.PACKAGE_FIXED_BYTES
    relative_offset = 0
    for index, payload in enumerate(payloads):
        struct.pack_into(
            "<10I",
            data,
            table_start + index * probe.TABLE_ROW_BYTES,
            0xFF00 + index,
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


class SecondSonXppsExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.xpps"
        make_xpps(self.source, [b"first", b"second-payload"])
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_row_and_manifest_are_deterministic(self) -> None:
        before = self.source.read_bytes()
        first_output = self.root / "first.bin"
        second_output = self.root / "second.bin"

        first = extractor.extract_row(
            self.source,
            expected_sha256=self.source_hash,
            row_index=1,
            output=first_output,
        )
        second = extractor.extract_row(
            self.source,
            expected_sha256=self.source_hash,
            row_index=1,
            output=second_output,
        )

        self.assertEqual(first_output.read_bytes(), b"second-payload")
        self.assertEqual(second_output.read_bytes(), b"second-payload")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(first["output"]["sha256"], hashlib.sha256(b"second-payload").hexdigest())
        first["output"]["basename"] = second["output"]["basename"]
        self.assertEqual(extractor.encode_manifest(first), extractor.encode_manifest(second))
        self.assertNotIn(str(self.root), extractor.encode_manifest(second).decode())

    def test_hash_mismatch_is_refused(self) -> None:
        output = self.root / "payload.bin"
        with self.assertRaisesRegex(probe.ProbeError, "mismatch"):
            extractor.extract_row(
                self.source,
                expected_sha256="0" * 64,
                row_index=0,
                output=output,
            )
        self.assertFalse(output.exists())

    def test_malformed_hash_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "lowercase"):
            extractor.extract_row(
                self.source,
                expected_sha256="A" * 64,
                row_index=0,
                output=self.root / "payload.bin",
            )

    def test_invalid_row_is_refused(self) -> None:
        output = self.root / "payload.bin"
        with self.assertRaisesRegex(probe.ProbeError, "outside"):
            extractor.extract_row(
                self.source,
                expected_sha256=self.source_hash,
                row_index=2,
                output=output,
            )
        self.assertFalse(output.exists())

    def test_negative_row_is_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "nonnegative"):
            extractor.extract_row(
                self.source,
                expected_sha256=self.source_hash,
                row_index=-1,
                output=self.root / "payload.bin",
            )

    def test_existing_output_is_refused(self) -> None:
        output = self.root / "payload.bin"
        output.write_bytes(b"keep")
        with self.assertRaisesRegex(probe.ProbeError, "replace"):
            extractor.extract_row(
                self.source,
                expected_sha256=self.source_hash,
                row_index=0,
                output=output,
            )
        self.assertEqual(output.read_bytes(), b"keep")

    def test_bad_magic_is_inherited(self) -> None:
        source = self.root / "bad.xpps"
        make_xpps(source, [], magic=b"PACK")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        with self.assertRaisesRegex(probe.ProbeError, "wrong magic"):
            extractor.extract_row(
                source,
                expected_sha256=source_hash,
                row_index=0,
                output=self.root / "payload.bin",
            )

    def test_symlink_is_inherited(self) -> None:
        link = self.root / "link.xpps"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            extractor.extract_row(
                link,
                expected_sha256=self.source_hash,
                row_index=0,
                output=self.root / "payload.bin",
            )

    def test_source_mutation_signal_leaves_no_output(self) -> None:
        output = self.root / "payload.bin"
        with mock.patch.object(extractor, "_hash_stream", return_value="0" * 64):
            with self.assertRaisesRegex(probe.ProbeError, "source changed"):
                extractor.extract_row(
                    self.source,
                    expected_sha256=self.source_hash,
                    row_index=0,
                    output=output,
                )
        self.assertFalse(output.exists())

    def test_short_read_is_refused_and_cleanup_runs(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "short read"):
            extractor._copy_range(io.BytesIO(b"x"), io.BytesIO(), absolute_start=0, size=2)

        output = self.root / "payload.bin"
        with mock.patch.object(
            extractor, "_copy_range", side_effect=probe.ProbeError("short read")
        ):
            with self.assertRaisesRegex(probe.ProbeError, "short read"):
                extractor.extract_row(
                    self.source,
                    expected_sha256=self.source_hash,
                    row_index=0,
                    output=output,
                )
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".payload.bin.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
