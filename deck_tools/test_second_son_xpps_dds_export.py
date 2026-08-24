# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import copy
import hashlib
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_xpps_dds_export as dds
import second_son_xpps_probe as probe
import test_second_son_xpps_bitmap_descriptors as bitmap_fixture
import test_second_son_xpps_eboot_type_names as type_fixture


def logical_mip_bytes(
    width: int, height: int, mip_count: int, format_id: int
) -> tuple[list[bytes], int]:
    format_info = dds.DDS_FORMATS[format_id]
    mips: list[bytes] = []
    for mip_index in range(mip_count):
        mip_width = max(width >> mip_index, 1)
        mip_height = max(height >> mip_index, 1)
        _, _, _, mip_bytes = dds._mip_logical_layout(mip_width, mip_height, format_info)
        mips.append(bytes((mip_index * 31 + index) % 251 for index in range(mip_bytes)))
    return mips, len(mips[0])


class SecondSonXppsDdsExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.xpps = self.root / "source.xpps"
        self.eboot = self.root / "eboot.bin"
        self.hash_word = 0x1111222233334444
        self.rewrite_xpps(bitmap_fixture.default_descriptors())
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
        self.roundtrip_report = dds.roundtrip.prove_thin1d_roundtrip(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            resolver_overrides=self.resolver_overrides,
        )
        self.bitmap_report = dds.bitmaps.classify_bitmap_descriptors(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            resolver_overrides=self.resolver_overrides,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite_xpps(self, descriptors: list[bytes]) -> None:
        bitmap_fixture.make_xpps(self.xpps, self.hash_word, descriptors)
        self.xpps_hash = hashlib.sha256(self.xpps.read_bytes()).hexdigest()

    def export(self, output: Path, **kwargs: object) -> dict[str, object]:
        options: dict[str, object] = {
            "resolver_overrides": self.resolver_overrides,
        }
        options.update(kwargs)
        return dds.export_dds(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            output_dir=output,
            **options,
        )

    def test_exact_dx10_headers_parse_all_formats_and_dimensions(self) -> None:
        for format_id, format_info in dds.DDS_FORMATS.items():
            for image_type_id, height in ((8, 1), (9, 7)):
                with self.subTest(format_id=format_id, image_type=image_type_id):
                    width = 9
                    mip_count = 4
                    mips, top_bytes = logical_mip_bytes(
                        width, height, mip_count, format_id
                    )
                    header = dds._build_dds_header(
                        width=width,
                        height=height,
                        mip_count=mip_count,
                        top_mip_bytes=top_bytes,
                        format_id=format_id,
                        image_type_id=image_type_id,
                    )
                    data = header + b"".join(mips)
                    parsed = dds._parse_dds(data)
                    self.assertEqual(parsed["dxgi_format"], format_info["dxgi_format"])
                    self.assertEqual(parsed["width"], width)
                    self.assertEqual(parsed["height"], height)
                    self.assertEqual(parsed["mip_count"], mip_count)
                    self.assertEqual(parsed["header_bytes"], 148)
                    self.assertEqual(
                        parsed["resource_dimension"],
                        dds.IMAGE_TYPE_DIMENSIONS[image_type_id][1],
                    )
                    self.assertEqual(
                        [mip["bytes"] for mip in parsed["mips"]],
                        [len(mip) for mip in mips],
                    )
                    header_words = dds.DDS_HEADER.unpack_from(data, 4)
                    dx10_words = dds.DDS_DX10_HEADER.unpack_from(
                        data, 4 + dds.DDS_HEADER.size
                    )
                    self.assertEqual(data[:4], b"DDS ")
                    self.assertEqual(header_words[0], 124)
                    self.assertEqual(header_words[18], 32)
                    self.assertEqual(header_words[20], struct.unpack("<I", b"DX10")[0])
                    self.assertEqual(dx10_words[0], format_info["dxgi_format"])
                    self.assertEqual(dx10_words[3], 1)

    def test_strict_parser_refuses_corrupt_truncated_and_trailing_files(self) -> None:
        mips, top_bytes = logical_mip_bytes(8, 8, 2, 37)
        valid = dds._build_dds_header(
            width=8,
            height=8,
            mip_count=2,
            top_mip_bytes=top_bytes,
            format_id=37,
            image_type_id=9,
        ) + b"".join(mips)
        cases: list[tuple[str, bytes]] = []
        malformed = bytearray(valid)
        malformed[0] ^= 1
        cases.append(("wrong magic", bytes(malformed)))
        malformed = bytearray(valid)
        struct.pack_into("<I", malformed, 4 + 7 * 4, 1)
        cases.append(("reserved", bytes(malformed)))
        malformed = bytearray(valid)
        struct.pack_into("<I", malformed, 4 + 1 * 4, 0)
        cases.append(("flags", bytes(malformed)))
        cases.append(("truncated", valid[:-1]))
        cases.append(("trailing", valid + b"x"))
        for message, data in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(probe.ProbeError, message):
                    dds._parse_dds(data)

    def test_logical_crop_is_independent_for_32_64_and_128_bit_rows(self) -> None:
        padded_pitch = 16
        padded_height = 8
        for format_id in (10, 35, 37):
            format_info = dds.DDS_FORMATS[format_id]
            element_bytes = int(format_info["element_bytes"])
            linear = bytearray()
            for y in range(padded_height):
                for x in range(padded_pitch):
                    token = (y * padded_pitch + x).to_bytes(element_bytes, "little")
                    linear.extend(token)
            if format_info["block_coded"] is True:
                logical_width, logical_height = 9, 5
                expected_width, expected_height = 3, 2
            else:
                logical_width, logical_height = 5, 3
                expected_width, expected_height = 5, 3
            cropped, geometry = dds._crop_logical_mip(
                linear,
                padded_pitch=padded_pitch,
                padded_height=padded_height,
                logical_width=logical_width,
                logical_height=logical_height,
                format_info=format_info,
            )
            expected = b"".join(
                (y * padded_pitch + x).to_bytes(element_bytes, "little")
                for y in range(expected_height)
                for x in range(expected_width)
            )
            self.assertEqual(cropped, expected)
            self.assertEqual(geometry["element_width"], expected_width)
            self.assertEqual(geometry["element_height"], expected_height)

    def test_exact_fixture_export_is_deterministic_and_sources_are_retained(
        self,
    ) -> None:
        xpps_before = self.xpps.read_bytes()
        eboot_before = self.eboot.read_bytes()
        first_output = self.root / "export-a"
        second_output = self.root / "export-b"
        first = self.export(first_output)
        second = self.export(second_output)
        self.assertEqual(dds.encode_manifest(first), dds.encode_manifest(second))
        self.assertEqual(self.xpps.read_bytes(), xpps_before)
        self.assertEqual(self.eboot.read_bytes(), eboot_before)
        self.assertEqual(first["facts"]["dds_file_count"], 2)
        self.assertEqual(first["facts"]["mip_count"], 7)
        self.assertTrue(first["facts"]["all_dds_files_strictly_parsed"])
        self.assertEqual(
            (first_output / dds.MANIFEST_NAME).read_bytes(),
            dds.encode_manifest(first),
        )
        self.assertEqual(
            sorted(path.name for path in first_output.iterdir()),
            sorted([dds.MANIFEST_NAME, *[item["basename"] for item in first["files"]]]),
        )
        for item in first["files"]:
            file_data = (first_output / item["basename"]).read_bytes()
            self.assertEqual(hashlib.sha256(file_data).hexdigest(), item["sha256"])
            self.assertEqual(dds._parse_dds(file_data)["mip_count"], len(item["mips"]))
            self.assertEqual(
                stat.S_IMODE((first_output / item["basename"]).stat().st_mode), 0o600
            )
        encoded = dds.encode_manifest(first).decode()
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("artwork_name", encoded)

    def test_width_not_guest_pitch_controls_one_pixel_exports(self) -> None:
        descriptors = [
            bitmap_fixture.image_descriptor(
                base=0x200,
                data_format=10,
                width=1,
                height=1,
                pitch=8,
                levels=1,
                image_type=8,
            ),
            bitmap_fixture.image_descriptor(
                base=0x600,
                data_format=35,
                width=1,
                height=1,
                pitch=32,
                levels=1,
                image_type=8,
            ),
        ]
        self.rewrite_xpps(descriptors)
        output = self.root / "one-pixel"
        manifest = self.export(output)
        self.assertEqual(
            [
                (item["dds"]["width"], item["dds"]["height"])
                for item in manifest["files"]
            ],
            [(1, 1), (1, 1)],
        )
        self.assertEqual(
            [item["dds"]["payload_bytes"] for item in manifest["files"]], [4, 8]
        )
        self.assertEqual(
            [item["mips"][0]["padded_storage_pitch"] for item in manifest["files"]],
            [8, 8],
        )
        self.assertEqual(
            [item["mips"][0]["width"] for item in manifest["files"]], [1, 1]
        )

    def test_inherited_proof_and_geometry_disagreement_are_refused_before_output(
        self,
    ) -> None:
        cases: list[tuple[str, dict[str, object], dict[str, object]]] = []
        bad_roundtrip = copy.deepcopy(self.roundtrip_report)
        bad_roundtrip["inherited_bitmap_proof"]["report_sha256"] = "0" * 64
        cases.append(("different BITMAP report", self.bitmap_report, bad_roundtrip))

        bad_roundtrip = copy.deepcopy(self.roundtrip_report)
        bad_roundtrip["descriptors"][0]["mips"][0]["linear_padded_sha256"] = "0" * 64
        cases.append(("linear hash changed", self.bitmap_report, bad_roundtrip))

        bad_roundtrip = copy.deepcopy(self.roundtrip_report)
        bad_roundtrip["descriptors"][0]["linear_padded_chain_sha256"] = "0" * 64
        cases.append(
            ("padded-linear chain hash changed", self.bitmap_report, bad_roundtrip)
        )

        bad_bitmap = copy.deepcopy(self.bitmap_report)
        bad_bitmap["descriptors"][0]["payload"]["mips"][0]["bytes"] += 1
        cases.append(("inconsistent bytes", bad_bitmap, self.roundtrip_report))

        for index, (message, bitmap_report, roundtrip_report) in enumerate(cases):
            output = self.root / f"malformed-{index}"
            with self.subTest(message=message):
                with mock.patch.object(
                    dds.roundtrip,
                    "prove_thin1d_roundtrip",
                    return_value=roundtrip_report,
                ):
                    with mock.patch.object(
                        dds.bitmaps,
                        "classify_bitmap_descriptors",
                        return_value=bitmap_report,
                    ):
                        with self.assertRaisesRegex(probe.ProbeError, message):
                            self.export(output)
            self.assertFalse(output.exists())

    def test_existing_symlink_and_symlink_parent_outputs_are_refused(self) -> None:
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep"
        marker.write_bytes(b"owner")
        with self.assertRaisesRegex(probe.ProbeError, "fresh output"):
            self.export(existing)
        self.assertEqual(marker.read_bytes(), b"owner")

        target = self.root / "target"
        target.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(probe.ProbeError, "fresh output"):
            self.export(linked)
        self.assertEqual(list(target.iterdir()), [])

        parent_target = self.root / "parent-target"
        parent_target.mkdir()
        parent_link = self.root / "parent-link"
        parent_link.symlink_to(parent_target, target_is_directory=True)
        with self.assertRaisesRegex(probe.ProbeError, "nonsymlink directory"):
            self.export(parent_link / "output")
        self.assertEqual(list(parent_target.iterdir()), [])

    def test_short_write_and_source_mutation_cleanup_only_fresh_output(self) -> None:
        short_output = self.root / "short-write"

        def short_write(file_descriptor: int, data: bytes) -> None:
            os.write(file_descriptor, data[:7])
            raise probe.ProbeError("synthetic short write")

        with mock.patch.object(dds, "_write_all", side_effect=short_write):
            with self.assertRaisesRegex(probe.ProbeError, "short write"):
                self.export(short_output)
        self.assertFalse(short_output.exists())

        mutation_output = self.root / "mutation"
        original_deswizzle = dds.roundtrip._deswizzle_mip
        changed = False

        def mutate_source(*args: object, **kwargs: object) -> bytearray:
            nonlocal changed
            linear = original_deswizzle(*args, **kwargs)
            if not changed:
                changed = True
                with self.xpps.open("r+b") as stream:
                    first = stream.read(1)
                    stream.seek(0)
                    stream.write(bytes([first[0] ^ 1]))
            return linear

        with mock.patch.object(
            dds.roundtrip,
            "prove_thin1d_roundtrip",
            return_value=self.roundtrip_report,
        ):
            with mock.patch.object(
                dds.roundtrip, "_deswizzle_mip", side_effect=mutate_source
            ):
                with self.assertRaisesRegex(probe.ProbeError, "XPPS changed during"):
                    self.export(mutation_output)
        self.assertFalse(mutation_output.exists())

    def test_replaced_output_binding_is_refused_and_not_deleted(self) -> None:
        output = self.root / "replaced-output"
        original_write = dds._write_exclusive
        replaced_name: str | None = None

        def replace_first_output(
            output_fd: int,
            name: str,
            data: bytes,
            created_names: list[str],
            created_file_identities: dict[str, tuple[int, int]],
        ) -> bytes:
            nonlocal replaced_name
            observed = original_write(
                output_fd,
                name,
                data,
                created_names,
                created_file_identities,
            )
            if replaced_name is None and name.endswith(".dds"):
                replaced_name = name
                os.unlink(name, dir_fd=output_fd)
                replacement_fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=output_fd,
                )
                try:
                    os.write(replacement_fd, b"external replacement")
                finally:
                    os.close(replacement_fd)
            return observed

        with mock.patch.object(
            dds, "_write_exclusive", side_effect=replace_first_output
        ):
            with self.assertRaisesRegex(probe.ProbeError, "path binding changed"):
                self.export(output)
        self.assertIsNotNone(replaced_name)
        self.assertTrue(output.is_dir())
        self.assertEqual(
            (output / str(replaced_name)).read_bytes(), b"external replacement"
        )
        self.assertEqual(
            sorted(path.name for path in output.iterdir()), [replaced_name]
        )

    def test_population_source_and_dds_budgets_are_refused(self) -> None:
        cases = [
            ("descriptor budget", {"max_descriptors": 0}),
            ("mip budget", {"max_mips": 0}),
            ("per-mip byte budget", {"max_mip_bytes": 0}),
            ("source byte budget", {"max_total_source_bytes": 0}),
            ("DDS byte budget", {"max_total_dds_bytes": 0}),
            ("BITMAP population", {"max_descriptors": 1}),
            ("mip count exceeds", {"max_mips": 6}),
            ("DDS files exceed", {"max_total_dds_bytes": 487}),
        ]
        for index, (message, options) in enumerate(cases):
            output = self.root / f"budget-{index}"
            with self.subTest(message=message):
                with self.assertRaisesRegex(probe.ProbeError, message):
                    self.export(output, **options)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
