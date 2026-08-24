# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import copy
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_xpps_dds_export as dds
import second_son_xpps_dds_overlay as overlay
import second_son_xpps_probe as probe
import test_second_son_xpps_bitmap_descriptors as bitmap_fixture
import test_second_son_xpps_eboot_type_names as type_fixture


class SecondSonXppsDdsOverlayTests(unittest.TestCase):
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
        self.baseline = self.export_baseline("baseline")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite_xpps(self, descriptors: list[bytes]) -> None:
        bitmap_fixture.make_xpps(self.xpps, self.hash_word, descriptors)
        self.xpps_hash = hashlib.sha256(self.xpps.read_bytes()).hexdigest()

    def export_baseline(self, name: str) -> Path:
        destination = self.root / name
        dds.export_dds(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            output_dir=destination,
            resolver_overrides=self.resolver_overrides,
        )
        return destination

    def edits_from_baseline(
        self, name: str, basenames: list[str] | None = None, *, mutate: bool = True
    ) -> Path:
        edits = self.root / name
        edits.mkdir()
        available = sorted(path.name for path in self.baseline.glob("*.dds"))
        for basename in basenames or available:
            data = bytearray((self.baseline / basename).read_bytes())
            if mutate:
                data[dds.DDS_HEADER_BYTES] ^= 0x5A
            (edits / basename).write_bytes(data)
        return edits

    def build(self, edits: Path, output: Path, **kwargs: object) -> dict[str, object]:
        options: dict[str, object] = {"resolver_overrides": self.resolver_overrides}
        options.update(kwargs)
        return overlay.build_overlay(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            export_manifest=self.baseline / dds.MANIFEST_NAME,
            edits_dir=edits,
            output_dir=output,
            **options,
        )

    def test_changed_overlay_is_deterministic_and_sources_are_retained(self) -> None:
        source_before = self.xpps.read_bytes()
        eboot_before = self.eboot.read_bytes()
        edits = self.edits_from_baseline("edits")
        first = self.build(edits, self.root / "overlay-a")
        second = self.build(edits, self.root / "overlay-b")
        self.assertEqual(overlay.encode_receipt(first), overlay.encode_receipt(second))
        self.assertEqual(self.xpps.read_bytes(), source_before)
        self.assertEqual(self.eboot.read_bytes(), eboot_before)
        self.assertEqual(first["facts"]["edit_file_count"], 2)
        self.assertEqual(first["facts"]["logical_changed_bytes"], 2)
        self.assertEqual(first["facts"]["overlay_changed_bytes"], 2)
        self.assertTrue(first["facts"]["all_padding_bytes_exact"])
        self.assertTrue(first["facts"]["all_non_target_bytes_exact"])
        output = (self.root / "overlay-a" / overlay.OVERLAY_NAME).read_bytes()
        self.assertEqual(hashlib.sha256(output).hexdigest(), first["output"]["sha256"])
        self.assertEqual(len(output), len(source_before))
        self.assertEqual(
            (self.root / "overlay-a" / overlay.RECEIPT_NAME).read_bytes(),
            overlay.encode_receipt(first),
        )

    def test_all_formats_and_image_dimensions_preserve_padding(self) -> None:
        for format_id in dds.DDS_FORMATS:
            with self.subTest(format_id=format_id):
                descriptors = [
                    bitmap_fixture.image_descriptor(
                        base=bitmap_fixture.PAYLOAD_BASES[0],
                        data_format=format_id,
                        width=9,
                        height=7,
                        pitch=16,
                        levels=1,
                        image_type=9,
                    ),
                    bitmap_fixture.image_descriptor(
                        base=bitmap_fixture.PAYLOAD_BASES[1],
                        data_format=10,
                        width=1,
                        height=1,
                        pitch=8,
                        levels=1,
                        image_type=8,
                    ),
                ]
                self.rewrite_xpps(descriptors)
                self.baseline = self.export_baseline(f"formats-{format_id}")
                edits = self.edits_from_baseline(f"format-edits-{format_id}")
                receipt = self.build(edits, self.root / f"format-overlay-{format_id}")
                self.assertEqual(receipt["facts"]["logical_changed_bytes"], 2)
                self.assertEqual(receipt["facts"]["overlay_changed_bytes"], 2)
                for edit in receipt["edits"]:
                    self.assertGreater(edit["mips"][0]["padding_bytes"], 0)
                self.assertNotEqual(
                    receipt["edits"][0]["mips"][0]["source_padded_linear_sha256"],
                    receipt["edits"][0]["mips"][0]["overlay_padded_linear_sha256"],
                )

    def test_identical_edit_is_refused_by_default_and_explicitly_provable(self) -> None:
        edits = self.edits_from_baseline("identical", mutate=False)
        refused = self.root / "identical-refused"
        with self.assertRaisesRegex(probe.ProbeError, "byte-identical"):
            self.build(edits, refused)
        self.assertFalse(refused.exists())
        receipt = self.build(
            edits,
            self.root / "identical-allowed",
            allow_identical_edits=True,
        )
        self.assertEqual(receipt["facts"]["overlay_changed_bytes"], 0)
        self.assertEqual(receipt["output"]["sha256"], self.xpps_hash)

    def test_incompatible_unknown_trailing_and_symlink_edits_are_refused(self) -> None:
        available = sorted(path.name for path in self.baseline.glob("*.dds"))

        unknown = self.root / "unknown"
        unknown.mkdir()
        (unknown / "unknown.dds").write_bytes(b"DDS " + bytes(200))
        with self.assertRaisesRegex(probe.ProbeError, "unknown basename"):
            self.build(unknown, self.root / "unknown-output")

        trailing = self.edits_from_baseline("trailing", [available[0]], mutate=False)
        (trailing / available[0]).write_bytes(
            (trailing / available[0]).read_bytes() + b"x"
        )
        with self.assertRaisesRegex(probe.ProbeError, "trailing|limit"):
            self.build(trailing, self.root / "trailing-output")

        incompatible = self.edits_from_baseline(
            "incompatible", [available[0]], mutate=False
        )
        data = bytearray((incompatible / available[0]).read_bytes())
        data[dds.DDS_HEADER_BYTES - 20] ^= 1
        (incompatible / available[0]).write_bytes(data)
        with self.assertRaisesRegex(probe.ProbeError, "unsupported|structure"):
            self.build(incompatible, self.root / "incompatible-output")

        linked = self.root / "linked-edits"
        linked.mkdir()
        (linked / available[0]).symlink_to(self.baseline / available[0])
        with self.assertRaisesRegex(probe.ProbeError, "nonsymlink"):
            self.build(linked, self.root / "linked-output")

    def test_manifest_and_baseline_population_must_be_exact(self) -> None:
        edits = self.edits_from_baseline("manifest-edits")
        manifest = self.baseline / dds.MANIFEST_NAME
        original_manifest = manifest.read_bytes()
        bad_manifest = copy.deepcopy(json_load(original_manifest))
        bad_manifest["facts"]["mip_count"] += 1
        manifest.write_bytes(dds.encode_manifest(bad_manifest))
        with self.assertRaisesRegex(probe.ProbeError, "manifest disagrees"):
            self.build(edits, self.root / "bad-manifest-output")
        manifest.write_bytes(original_manifest)

        extra = self.baseline / "extra"
        extra.write_bytes(b"owner")
        with self.assertRaisesRegex(probe.ProbeError, "population"):
            self.build(edits, self.root / "extra-output")
        self.assertEqual(extra.read_bytes(), b"owner")

    def test_budgets_source_mutation_and_output_cleanup_fail_closed(self) -> None:
        edits = self.edits_from_baseline("guard-edits")
        cases = [
            ("exceeds 1 bytes", {"max_source_bytes": 1}),
            ("1-byte limit", {"max_edit_bytes": 1}),
            ("changed byte budget", {"max_changed_bytes": 1}),
            ("changed range budget", {"max_changed_ranges": 1}),
        ]
        for index, (message, options) in enumerate(cases):
            with self.subTest(message=message):
                with self.assertRaisesRegex(probe.ProbeError, message):
                    self.build(edits, self.root / f"budget-output-{index}", **options)

        output = self.root / "short-write-output"

        def short_write(file_descriptor: int, data: bytes) -> None:
            os.write(file_descriptor, data[:7])
            raise probe.ProbeError("synthetic short write")

        with mock.patch.object(dds, "_write_all", side_effect=short_write):
            with self.assertRaisesRegex(probe.ProbeError, "short write"):
                self.build(edits, output)
        self.assertFalse(output.exists())

        original_source_check = overlay._source_is_unchanged
        mutated = False

        def mutate_before_final_check(*args: object, **kwargs: object) -> None:
            nonlocal mutated
            if not mutated:
                mutated = True
                data = bytearray(self.xpps.read_bytes())
                data[0] ^= 1
                self.xpps.write_bytes(data)
            original_source_check(*args, **kwargs)

        mutation_output = self.root / "mutation-output"
        with mock.patch.object(
            overlay, "_source_is_unchanged", side_effect=mutate_before_final_check
        ):
            with self.assertRaisesRegex(probe.ProbeError, "changed during"):
                self.build(edits, mutation_output)
        self.assertFalse(mutation_output.exists())

    def test_replaced_output_binding_is_refused_and_not_deleted(self) -> None:
        edits = self.edits_from_baseline("replacement-edits")
        output = self.root / "replacement-output"
        original_write = dds._write_exclusive
        replaced = False

        def replace_overlay(
            output_fd: int,
            name: str,
            data: bytes,
            created_names: list[str],
            created_identities: dict[str, tuple[int, int, int, int, int]],
            created_guards: dict[str, int],
        ) -> bytes:
            nonlocal replaced
            observed = original_write(
                output_fd,
                name,
                data,
                created_names,
                created_identities,
                created_guards,
            )
            if name == overlay.OVERLAY_NAME and not replaced:
                replaced = True
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

        with mock.patch.object(dds, "_write_exclusive", side_effect=replace_overlay):
            with self.assertRaisesRegex(probe.ProbeError, "path binding changed"):
                self.build(edits, output)
        self.assertTrue(replaced)
        self.assertTrue(output.is_dir())
        self.assertEqual(
            (output / overlay.OVERLAY_NAME).read_bytes(), b"external replacement"
        )
        self.assertEqual(
            sorted(path.name for path in output.iterdir()), [overlay.OVERLAY_NAME]
        )


def json_load(data: bytes) -> dict[str, object]:
    import json

    value = json.loads(data)
    if not isinstance(value, dict):
        raise TypeError("expected object")
    return value


if __name__ == "__main__":
    unittest.main()
