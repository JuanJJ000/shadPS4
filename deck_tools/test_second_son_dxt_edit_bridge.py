# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_dxt_edit_bridge as bridge
import second_son_xpps_dds_export as dds
import second_son_xpps_dds_overlay as overlay
import second_son_xpps_probe as probe
import test_second_son_xpps_bitmap_descriptors as bitmap_fixture
import test_second_son_xpps_eboot_type_names as type_fixture


def legacy_from_dx10(data: bytes, *, mutate: bool = True) -> bytes:
    parsed = dds._parse_dds(data)
    format_id = int(parsed["format_id"])
    fourcc = bridge.LEGACY_FORMATS[format_id][0]
    mip_count = int(parsed["mip_count"])
    flags = (
        dds.DDSD_CAPS
        | dds.DDSD_HEIGHT
        | dds.DDSD_WIDTH
        | dds.DDSD_PIXELFORMAT
        | dds.DDSD_LINEARSIZE
    )
    caps = dds.DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= dds.DDSD_MIPMAPCOUNT
        caps |= dds.DDSCAPS_COMPLEX | dds.DDSCAPS_MIPMAP
    reserved = list(struct.unpack("<11I", bridge.IMAGEMAGICK_RESERVED))
    values = [
        dds.DDS_HEADER_SIZE,
        flags,
        int(parsed["height"]),
        int(parsed["width"]),
        int(parsed["mips"][0]["bytes"]),
        0,
        mip_count,
        *reserved,
        dds.DDS_PIXEL_FORMAT_SIZE,
        dds.DDPF_FOURCC,
        fourcc,
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
    payload = bytearray(data[dds.DDS_HEADER_BYTES :])
    if mutate:
        payload[0] ^= 0x5A
    return dds.DDS_MAGIC + dds.DDS_HEADER.pack(*values) + payload


class SecondSonDxtEditBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.xpps = self.root / "source.xpps"
        self.eboot = self.root / "eboot.bin"
        self.hash_word = 0x1111222233334444
        descriptors = [
            bitmap_fixture.image_descriptor(
                base=bitmap_fixture.PAYLOAD_BASES[0],
                data_format=35,
                width=9,
                height=7,
                pitch=16,
                levels=2,
            ),
            bitmap_fixture.image_descriptor(
                base=bitmap_fixture.PAYLOAD_BASES[1],
                data_format=37,
                width=9,
                height=7,
                pitch=16,
                levels=1,
            ),
        ]
        bitmap_fixture.make_xpps(self.xpps, self.hash_word, descriptors)
        type_fixture.make_self(self.eboot, [(self.hash_word, 7)])
        self.xpps_hash = hashlib.sha256(self.xpps.read_bytes()).hexdigest()
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
        self.baseline = self.root / "baseline"
        dds.export_dds(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            output_dir=self.baseline,
            resolver_overrides=self.resolver_overrides,
        )
        self.manifest_hash = hashlib.sha256(
            (self.baseline / dds.MANIFEST_NAME).read_bytes()
        ).hexdigest()
        self.encoded = self.root / "encoded"
        self.encoded.mkdir()
        for path in self.baseline.glob("*.dds"):
            (self.encoded / path.name).write_bytes(legacy_from_dx10(path.read_bytes()))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_bridge(self, output: Path, **kwargs: object) -> dict[str, object]:
        options: dict[str, object] = {}
        options.update(kwargs)
        return bridge.bridge_edits(
            self.baseline / dds.MANIFEST_NAME,
            expected_manifest_sha256=self.manifest_hash,
            encoded_dir=self.encoded,
            output_dir=output,
            **options,
        )

    def test_dxt1_dxt5_normalize_deterministically_and_feed_overlay(self) -> None:
        first_output = self.root / "normalized-a"
        second_output = self.root / "normalized-b"
        first = self.run_bridge(first_output)
        second = self.run_bridge(second_output)
        self.assertEqual(bridge.encode_receipt(first), bridge.encode_receipt(second))
        self.assertEqual(first["facts"]["edit_file_count"], 2)
        self.assertEqual(
            [item["legacy"]["fourcc"] for item in first["files"]],
            ["DXT1", "DXT5"],
        )
        for item in first["files"]:
            normalized = (first_output / item["basename"]).read_bytes()
            baseline = (self.baseline / item["basename"]).read_bytes()
            self.assertEqual(len(normalized), len(baseline))
            self.assertEqual(
                normalized[: dds.DDS_HEADER_BYTES],
                baseline[: dds.DDS_HEADER_BYTES],
            )
            self.assertEqual(
                dds._parse_dds(normalized), dds._parse_dds(baseline)
            )
            self.assertEqual(
                normalized, (second_output / item["basename"]).read_bytes()
            )

        overlay_output = self.root / "xpps-overlay"
        receipt = overlay.build_overlay(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            export_manifest=self.baseline / dds.MANIFEST_NAME,
            edits_dir=first_output,
            output_dir=overlay_output,
            resolver_overrides=self.resolver_overrides,
        )
        self.assertEqual(receipt["facts"]["edit_file_count"], 2)
        self.assertEqual(receipt["facts"]["overlay_changed_bytes"], 2)
        self.assertEqual(self.xpps_hash, hashlib.sha256(self.xpps.read_bytes()).hexdigest())

    def test_odd_dimensions_and_terminal_block_mips_parse_exactly(self) -> None:
        for format_id in (35, 37):
            with self.subTest(format_id=format_id):
                format_info = dds.DDS_FORMATS[format_id]
                payload = bytearray()
                top_bytes = 0
                for mip_index in range(5):
                    width = max(9 >> mip_index, 1)
                    height = max(7 >> mip_index, 1)
                    _, _, _, size = dds._mip_logical_layout(
                        width, height, format_info
                    )
                    if mip_index == 0:
                        top_bytes = size
                    payload.extend(bytes([mip_index + 1]) * size)
                baseline_data = dds._build_dds_header(
                    width=9,
                    height=7,
                    mip_count=5,
                    top_mip_bytes=top_bytes,
                    format_id=format_id,
                    image_type_id=9,
                ) + payload
                parsed = dds._parse_dds(baseline_data)
                legacy = legacy_from_dx10(baseline_data)
                observed = bridge._parse_legacy_dxt(legacy, parsed)
                self.assertEqual(observed["width"], 9)
                self.assertEqual(observed["height"], 7)
                self.assertEqual(observed["mip_count"], 5)
                self.assertEqual(observed["payload_bytes"], len(payload))
                self.assertEqual(observed["mips"][-1]["bytes"], int(format_info["element_bytes"]))

    def test_legacy_header_structure_truncation_and_trailing_are_refused(self) -> None:
        name = sorted(path.name for path in self.encoded.iterdir())[0]
        path = self.encoded / name
        original = path.read_bytes()
        cases: list[tuple[str, bytes]] = []
        changed = bytearray(original)
        changed[32] ^= 1
        cases.append(("signature", bytes(changed)))
        changed = bytearray(original)
        struct.pack_into("<I", changed, 4 + 3 * 4, 10)
        cases.append(("dimensions", bytes(changed)))
        changed = bytearray(original)
        struct.pack_into("<I", changed, 4 + 20 * 4, struct.unpack("<I", b"DXT5")[0])
        cases.append(("format", bytes(changed)))
        changed = bytearray(original)
        struct.pack_into("<I", changed, 4 + 27 * 4, 0x200)
        cases.append(("face|volume", bytes(changed)))
        cases.append(("truncated|limit", original[:-1]))
        cases.append(("trailing|limit", original + b"x"))
        for index, (message, data) in enumerate(cases):
            path.write_bytes(data)
            with self.subTest(message=message):
                with self.assertRaisesRegex(probe.ProbeError, message):
                    self.run_bridge(self.root / f"malformed-{index}")
            path.write_bytes(original)

    def test_manifest_baseline_unknown_symlink_and_identical_are_refused(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "wrong expected hash"):
            bridge.bridge_edits(
                self.baseline / dds.MANIFEST_NAME,
                expected_manifest_sha256="0" * 64,
                encoded_dir=self.encoded,
                output_dir=self.root / "wrong-manifest",
            )

        extra = self.baseline / "extra"
        extra.write_bytes(b"owner")
        with self.assertRaisesRegex(probe.ProbeError, "population"):
            self.run_bridge(self.root / "extra-baseline")
        extra.unlink()

        encoded_name = sorted(path.name for path in self.encoded.iterdir())[0]
        unknown = self.encoded / "unknown.dds"
        unknown.write_bytes((self.encoded / encoded_name).read_bytes())
        with self.assertRaisesRegex(probe.ProbeError, "population"):
            self.run_bridge(self.root / "unknown-output")
        unknown.unlink()

        original = self.encoded / encoded_name
        original.unlink()
        original.symlink_to(self.baseline / encoded_name)
        with self.assertRaisesRegex(probe.ProbeError, "nonsymlink"):
            self.run_bridge(self.root / "symlink-output")
        original.unlink()
        original.write_bytes(
            legacy_from_dx10((self.baseline / encoded_name).read_bytes(), mutate=False)
        )
        with self.assertRaisesRegex(probe.ProbeError, "byte-identical"):
            self.run_bridge(self.root / "identical-output")

    def test_budget_short_write_and_replaced_output_fail_closed(self) -> None:
        with self.assertRaisesRegex(probe.ProbeError, "budget"):
            self.run_bridge(self.root / "budget-output", max_total_bytes=1)

        short_output = self.root / "short-output"

        def short_write(file_descriptor: int, data: bytes) -> None:
            os.write(file_descriptor, data[:7])
            raise probe.ProbeError("synthetic short write")

        with mock.patch.object(dds, "_write_all", side_effect=short_write):
            with self.assertRaisesRegex(probe.ProbeError, "short write"):
                self.run_bridge(short_output)
        self.assertFalse(short_output.exists())

        replaced_output = self.root / "replaced-output"
        original_write = dds._write_exclusive
        replaced_name: str | None = None

        def replace_first(
            output_fd: int,
            name: str,
            data: bytes,
            names: list[str],
            identities: dict[str, tuple[int, int, int, int, int]],
            guards: dict[str, int],
        ) -> bytes:
            nonlocal replaced_name
            observed = original_write(output_fd, name, data, names, identities, guards)
            if replaced_name is None:
                replaced_name = name
                os.unlink(name, dir_fd=output_fd)
                replacement = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=output_fd,
                )
                try:
                    os.write(replacement, b"external replacement")
                finally:
                    os.close(replacement)
            return observed

        with mock.patch.object(dds, "_write_exclusive", side_effect=replace_first):
            with self.assertRaisesRegex(probe.ProbeError, "path binding changed"):
                self.run_bridge(replaced_output)
        self.assertIsNotNone(replaced_name)
        self.assertEqual(
            (replaced_output / str(replaced_name)).read_bytes(), b"external replacement"
        )


if __name__ == "__main__":
    unittest.main()
