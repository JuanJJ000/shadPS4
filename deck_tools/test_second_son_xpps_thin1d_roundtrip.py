# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import copy
import hashlib
import struct
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_xpps_probe as probe
import second_son_xpps_thin1d_roundtrip as roundtrip
import test_second_son_xpps_bitmap_descriptors as bitmap_fixture
import test_second_son_xpps_eboot_type_names as type_fixture

LUT_WORDS = (
    0x11011000,
    0x31213020,
    0x13031202,
    0x33233222,
    0x51415040,
    0x71617060,
    0x53435242,
    0x73637262,
    0x15051404,
    0x35253424,
    0x17071606,
    0x37273626,
    0x55455444,
    0x75657464,
    0x57475646,
    0x77677666,
)


def reference_morton(x: int, y: int) -> int:
    result = 0
    for bit in range(3):
        result |= ((x >> bit) & 1) << (bit * 2)
        result |= ((y >> bit) & 1) << (bit * 2 + 1)
    return result


def reference_coordinates() -> list[tuple[int, int]]:
    coordinates: list[tuple[int, int] | None] = [None] * 64
    for y in range(8):
        for x in range(8):
            coordinates[reference_morton(x, y)] = (x, y)
    if any(coordinate is None for coordinate in coordinates):
        raise RuntimeError("test reference permutation is incomplete")
    return [coordinate for coordinate in coordinates if coordinate is not None]


def reference_retile(
    linear: bytes, *, pitch: int, height: int, element_bytes: int
) -> bytes:
    tiled = bytearray(len(linear))
    tiles_per_row = pitch // 8
    for y in range(height):
        for x in range(pitch):
            tile_index = (y // 8) * tiles_per_row + x // 8
            destination_element = tile_index * 64 + reference_morton(x % 8, y % 8)
            source = (y * pitch + x) * element_bytes
            destination = destination_element * element_bytes
            tiled[destination : destination + element_bytes] = linear[
                source : source + element_bytes
            ]
    return bytes(tiled)


def reference_deswizzle(
    tiled: bytes, *, pitch: int, height: int, element_bytes: int
) -> bytes:
    linear = bytearray(len(tiled))
    tiles_per_row = pitch // 8
    for tile_y in range(height // 8):
        for tile_x in range(tiles_per_row):
            tile_index = tile_y * tiles_per_row + tile_x
            for tiled_index, (local_x, local_y) in enumerate(reference_coordinates()):
                source = (tile_index * 64 + tiled_index) * element_bytes
                x = tile_x * 8 + local_x
                y = tile_y * 8 + local_y
                destination = (y * pitch + x) * element_bytes
                linear[destination : destination + element_bytes] = tiled[
                    source : source + element_bytes
                ]
    return bytes(linear)


def write_shader_tree(
    root: Path, *, tables: dict[int, tuple[int, ...]] | None = None
) -> None:
    selected = tables or {}
    for element_bits, relative_path in roundtrip.HOST_SHADER_PATHS:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        words = selected.get(element_bits, LUT_WORDS)
        body = ", ".join(f"0x{word:08x}" for word in words)
        path.write_text(f"const uint rmort[16] = {{{body}}};\n", encoding="ascii")


class SecondSonXppsThin1DRoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.xpps = self.root / "source.xpps"
        self.eboot = self.root / "eboot.bin"
        self.hash_word = 0x1111222233334444
        bitmap_fixture.make_xpps(
            self.xpps, self.hash_word, bitmap_fixture.default_descriptors()
        )
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
        self.bitmap_report = roundtrip.bitmaps.classify_bitmap_descriptors(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            resolver_overrides=self.resolver_overrides,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prove(self, **kwargs: object) -> dict[str, object]:
        options: dict[str, object] = {
            "resolver_overrides": self.resolver_overrides,
        }
        options.update(kwargs)
        return roundtrip.prove_thin1d_roundtrip(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            **options,
        )

    def test_repository_luts_match_the_independent_reference(self) -> None:
        coordinates, contract = roundtrip._load_host_permutation(
            Path(roundtrip.__file__).resolve().parents[1]
        )
        self.assertEqual(coordinates, reference_coordinates())
        self.assertTrue(contract["all_sources_agree"])
        self.assertEqual(contract["coordinate_count"], 64)
        self.assertEqual(
            contract["inverse_morton_lut_sha256"],
            hashlib.sha256(struct.pack("<16I", *LUT_WORDS)).hexdigest(),
        )
        self.assertEqual(
            {source["element_bits"] for source in contract["sources"]},
            {32, 64, 128},
        )

    def test_known_32_64_and_128_bit_multitile_permutations(self) -> None:
        coordinates = reference_coordinates()
        pitch = 16
        height = 16
        for element_bytes in (4, 8, 16):
            with self.subTest(element_bytes=element_bytes):
                linear = b"".join(
                    index.to_bytes(element_bytes, "little")
                    for index in range(pitch * height)
                )
                expected_tiled = reference_retile(
                    linear,
                    pitch=pitch,
                    height=height,
                    element_bytes=element_bytes,
                )
                observed_linear = roundtrip._deswizzle_mip(
                    expected_tiled,
                    pitch=pitch,
                    height=height,
                    element_bytes=element_bytes,
                    host_coordinates=coordinates,
                )
                self.assertEqual(observed_linear, linear)
                self.assertEqual(
                    roundtrip._retile_mip(
                        observed_linear,
                        pitch=pitch,
                        height=height,
                        element_bytes=element_bytes,
                    ),
                    expected_tiled,
                )

    def test_exact_fixture_is_deterministic_and_byte_exact(self) -> None:
        xpps_before = self.xpps.read_bytes()
        eboot_before = self.eboot.read_bytes()
        first = self.prove()
        second = self.prove()
        self.assertEqual(
            roundtrip.encode_report(first), roundtrip.encode_report(second)
        )
        self.assertEqual(self.xpps.read_bytes(), xpps_before)
        self.assertEqual(self.eboot.read_bytes(), eboot_before)
        self.assertEqual(first["facts"]["descriptor_count"], 2)
        self.assertEqual(first["facts"]["mip_count"], 7)
        self.assertEqual(first["facts"]["total_payload_bytes"], 0xA00)
        self.assertEqual(
            first["facts"]["element_width_descriptor_counts"], {"32": 1, "64": 1}
        )
        self.assertTrue(first["facts"]["all_mips_byte_exact_roundtrip"])
        self.assertTrue(
            all(
                mip["exact_roundtrip"]
                for descriptor in first["descriptors"]
                for mip in descriptor["mips"]
            )
        )
        first_bitmap = self.bitmap_report["descriptors"][0]
        first_mip = first_bitmap["payload"]["mips"][0]
        tiled = xpps_before[first_mip["absolute_start"] : first_mip["absolute_end"]]
        expected_linear = reference_deswizzle(
            tiled,
            pitch=first_mip["aligned_storage_pitch"],
            height=first_mip["aligned_storage_height"],
            element_bytes=4,
        )
        self.assertEqual(
            first["descriptors"][0]["mips"][0]["linear_padded_sha256"],
            hashlib.sha256(expected_linear).hexdigest(),
        )
        encoded = roundtrip.encode_report(first).decode()
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("raw", encoded)
        self.assertNotIn("payload_absolute_start", encoded)

    def test_host_lut_disagreement_malformed_source_and_symlink_are_refused(
        self,
    ) -> None:
        shader_root = self.root / "shaders"
        changed = list(LUT_WORDS)
        changed[0] ^= 1
        write_shader_tree(shader_root, tables={64: tuple(changed)})
        with self.assertRaisesRegex(probe.ProbeError, "tables disagree"):
            roundtrip._load_host_permutation(shader_root)

        write_shader_tree(shader_root)
        malformed_path = shader_root / roundtrip.HOST_SHADER_PATHS[0][1]
        malformed_path.write_text("const uint rmort[16] = {0x0};\n", encoding="ascii")
        with self.assertRaisesRegex(probe.ProbeError, "exactly 16"):
            roundtrip._load_host_permutation(shader_root)

        write_shader_tree(shader_root)
        target = shader_root / "target.comp"
        target.write_text(
            (shader_root / roundtrip.HOST_SHADER_PATHS[0][1]).read_text(
                encoding="ascii"
            ),
            encoding="ascii",
        )
        linked_path = shader_root / roundtrip.HOST_SHADER_PATHS[0][1]
        linked_path.unlink()
        linked_path.symlink_to(target)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            roundtrip._load_host_permutation(shader_root)

    def test_incomplete_duplicate_or_wrong_morton_coordinates_are_refused(self) -> None:
        shader_root = self.root / "bad-coordinates"
        duplicated = list(LUT_WORDS)
        duplicated[0] = (duplicated[0] & ~0xFF) | 0x11
        write_shader_tree(
            shader_root,
            tables={
                32: tuple(duplicated),
                64: tuple(duplicated),
                128: tuple(duplicated),
            },
        )
        with self.assertRaisesRegex(probe.ProbeError, "incomplete or duplicated"):
            roundtrip._load_host_permutation(shader_root)

        swapped = list(LUT_WORDS)
        first_word = bytearray(struct.pack("<I", swapped[0]))
        first_word[0], first_word[1] = first_word[1], first_word[0]
        swapped[0] = struct.unpack("<I", first_word)[0]
        write_shader_tree(
            shader_root,
            tables={32: tuple(swapped), 64: tuple(swapped), 128: tuple(swapped)},
        )
        with self.assertRaisesRegex(probe.ProbeError, "bit order"):
            roundtrip._load_host_permutation(shader_root)

    def test_malformed_inherited_ranges_geometry_and_hash_are_refused(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        malformed = copy.deepcopy(self.bitmap_report)
        malformed["schema"] = "wrong"
        cases.append(("unexpected schema", malformed))

        malformed = copy.deepcopy(self.bitmap_report)
        malformed["selected_dic_row"]["index"] = 1
        cases.append(("wrong DIC row", malformed))

        malformed = copy.deepcopy(self.bitmap_report)
        malformed["descriptors"][0]["payload"]["mips"][0]["bytes"] += 1
        cases.append(("inconsistent bytes", malformed))

        malformed = copy.deepcopy(self.bitmap_report)
        malformed["descriptors"][0]["payload"]["mips"][0]["aligned_storage_pitch"] += 8
        cases.append(("inconsistent aligned_storage_pitch", malformed))

        malformed = copy.deepcopy(self.bitmap_report)
        malformed["descriptors"][0]["payload"]["sha256"] = "0" * 64
        cases.append(("payload bytes disagree", malformed))

        for message, report in cases:
            with self.subTest(message=message):
                with mock.patch.object(
                    roundtrip.bitmaps,
                    "classify_bitmap_descriptors",
                    return_value=report,
                ):
                    with self.assertRaisesRegex(probe.ProbeError, message):
                        self.prove()

    def test_roundtrip_corruption_and_budgets_are_refused(self) -> None:
        original_retile = roundtrip._retile_mip

        def corrupt_retile(*args: object, **kwargs: object) -> bytearray:
            output = original_retile(*args, **kwargs)
            output[0] ^= 1
            return output

        with mock.patch.object(roundtrip, "_retile_mip", side_effect=corrupt_retile):
            with self.assertRaisesRegex(probe.ProbeError, "failed byte-exact"):
                self.prove()

        with self.assertRaisesRegex(probe.ProbeError, "BITMAP population"):
            self.prove(max_descriptors=1)
        with self.assertRaisesRegex(probe.ProbeError, "mip count exceeds"):
            self.prove(max_mips=6)
        with self.assertRaisesRegex(probe.ProbeError, "per-mip byte budget"):
            self.prove(max_mip_bytes=0)
        with self.assertRaisesRegex(probe.ProbeError, "transform budget"):
            self.prove(max_mip_bytes=0xFF)
        with self.assertRaisesRegex(probe.ProbeError, "payload population"):
            self.prove(max_total_bytes=0x9FF)

    def test_source_symlink_and_mutation_are_refused(self) -> None:
        xpps_link = self.root / "source-link.xpps"
        xpps_link.symlink_to(self.xpps)
        with self.assertRaisesRegex(probe.ProbeError, "symlink"):
            roundtrip.prove_thin1d_roundtrip(
                xpps_link,
                expected_xpps_sha256=self.xpps_hash,
                row_index=2,
                eboot=self.eboot,
                expected_eboot_sha256=self.eboot_hash,
                resolver_overrides=self.resolver_overrides,
            )

        original_retile = roundtrip._retile_mip
        changed = False

        def mutate_source(*args: object, **kwargs: object) -> bytearray:
            nonlocal changed
            output = original_retile(*args, **kwargs)
            if not changed:
                changed = True
                with self.xpps.open("r+b") as stream:
                    first = stream.read(1)
                    stream.seek(0)
                    stream.write(bytes([first[0] ^ 1]))
            return output

        with mock.patch.object(
            roundtrip.bitmaps,
            "classify_bitmap_descriptors",
            return_value=self.bitmap_report,
        ):
            with mock.patch.object(roundtrip, "_retile_mip", side_effect=mutate_source):
                with self.assertRaisesRegex(probe.ProbeError, "XPPS changed during"):
                    self.prove()

    def test_shader_source_mutation_is_refused(self) -> None:
        shader_root = self.root / "mutating-shaders"
        write_shader_tree(shader_root)
        source_path = shader_root / roundtrip.HOST_SHADER_PATHS[0][1]
        original_read = roundtrip.registry._read_bounded
        changed = False

        def mutate_after_read(*args: object, **kwargs: object) -> bytes:
            nonlocal changed
            data = original_read(*args, **kwargs)
            if not changed:
                changed = True
                source_path.write_bytes(data + b" ")
            return data

        with ExitStack() as stack:
            with mock.patch.object(
                roundtrip.registry,
                "_read_bounded",
                side_effect=mutate_after_read,
            ):
                with self.assertRaisesRegex(probe.ProbeError, "changed while reading"):
                    roundtrip._read_shader_lut(
                        stack,
                        shader_root,
                        roundtrip.HOST_SHADER_PATHS[0][0],
                        roundtrip.HOST_SHADER_PATHS[0][1],
                    )


if __name__ == "__main__":
    unittest.main()
