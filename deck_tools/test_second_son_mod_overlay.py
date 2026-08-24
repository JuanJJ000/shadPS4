# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_mod_overlay as mods
import second_son_xpps_dds_export as dds
import second_son_xpps_dds_overlay as overlay
import second_son_xpps_probe as probe
import test_second_son_xpps_bitmap_descriptors as bitmap_fixture
import test_second_son_xpps_eboot_type_names as type_fixture


class SecondSonModOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.games = self.root / "games"
        self.game = self.games / mods.TITLE_ID
        self.target_relative = "art/cache/graffiti_a8_family.xpps"
        self.xpps = self.game / self.target_relative
        self.eboot = self.game / "eboot.bin"
        self.xpps.parent.mkdir(parents=True)
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
        self.edits = self.root / "edits"
        self.edits.mkdir()
        first_dds = sorted(self.baseline.glob("*.dds"))[0]
        edited = bytearray(first_dds.read_bytes())
        edited[dds.DDS_HEADER_BYTES] ^= 0x5A
        (self.edits / first_dds.name).write_bytes(edited)
        self.overlay_dir = self.root / "overlay"
        overlay.build_overlay(
            self.xpps,
            expected_xpps_sha256=self.xpps_hash,
            row_index=2,
            eboot=self.eboot,
            expected_eboot_sha256=self.eboot_hash,
            export_manifest=self.baseline / dds.MANIFEST_NAME,
            edits_dir=self.edits,
            output_dir=self.overlay_dir,
            resolver_overrides=self.resolver_overrides,
        )
        self.source_before = self.xpps.read_bytes()
        self.eboot_before = self.eboot.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def store(self) -> Path:
        return self.games / f"{mods.TITLE_ID}{mods.STORE_SUFFIX}"

    @property
    def active(self) -> Path:
        return self.games / f"{mods.TITLE_ID}{mods.ACTIVE_SUFFIX}"

    def stage(self, pack_id: str = "graffiti-proof") -> dict[str, object]:
        return mods.stage_pack(
            self.game,
            pack_id=pack_id,
            relative_target=self.target_relative,
            overlay_dir=self.overlay_dir,
        )

    def test_stage_status_enable_disable_and_reenable_leave_base_exact(self) -> None:
        manifest = self.stage()
        self.assertEqual(manifest["title_id"], mods.TITLE_ID)
        self.assertEqual(manifest["target"]["relative_path"], self.target_relative)
        pack = self.store / "graffiti-proof"
        self.assertFalse(self.active.exists())
        self.assertEqual(
            sorted(path.name for path in pack.iterdir()),
            sorted([mods.PACK_MANIFEST, mods.PACK_OVERLAY_RECEIPT, "art"]),
        )
        staged = pack / self.target_relative
        self.assertEqual(
            hashlib.sha256(staged.read_bytes()).hexdigest(),
            manifest["target"]["sha256"],
        )
        status = mods.verify_pack(self.game, pack_id="graffiti-proof")
        self.assertEqual(status["active_state"], "disabled")

        enabled = mods.enable_pack(self.game, pack_id="graffiti-proof")
        self.assertEqual(enabled["action"], "enabled")
        self.assertTrue(self.active.is_symlink())
        self.assertEqual(
            os.readlink(self.active), f"{mods.TITLE_ID}{mods.STORE_SUFFIX}/graffiti-proof"
        )
        self.assertEqual(
            mods.verify_pack(self.game, pack_id="graffiti-proof")["active_state"],
            "enabled",
        )

        disabled = mods.disable_pack(self.game, pack_id="graffiti-proof")
        self.assertEqual(disabled["action"], "disabled")
        self.assertFalse(self.active.is_symlink())
        self.assertEqual(
            mods.enable_pack(self.game, pack_id="graffiti-proof")["action"],
            "enabled",
        )
        self.assertEqual(self.xpps.read_bytes(), self.source_before)
        self.assertEqual(self.eboot.read_bytes(), self.eboot_before)

    def test_pack_and_active_collisions_are_refused_and_preserved(self) -> None:
        self.stage()
        with self.assertRaisesRegex(probe.ProbeError, "fresh mod pack"):
            self.stage()
        self.assertTrue((self.store / "graffiti-proof").is_dir())

        self.active.mkdir()
        marker = self.active / "owner"
        marker.write_bytes(b"owner")
        with self.assertRaisesRegex(probe.ProbeError, "conflicts"):
            mods.enable_pack(self.game, pack_id="graffiti-proof")
        self.assertEqual(marker.read_bytes(), b"owner")
        marker.unlink()
        self.active.rmdir()

        mods.enable_pack(self.game, pack_id="graffiti-proof")
        self.active.unlink()
        self.active.symlink_to("someone-elses-mods")
        with self.assertRaisesRegex(probe.ProbeError, "conflicts"):
            mods.disable_pack(self.game, pack_id="graffiti-proof")
        self.assertTrue(self.active.is_symlink())
        self.assertEqual(os.readlink(self.active), "someone-elses-mods")

    def test_source_eboot_overlay_and_receipt_changes_are_refused(self) -> None:
        cases: list[tuple[str, Path]] = [
            ("base XPPS disagrees", self.xpps),
            ("base eboot disagrees", self.eboot),
            ("overlay bytes disagree", self.overlay_dir / overlay.OVERLAY_NAME),
            ("manifest|receipt", self.overlay_dir / overlay.RECEIPT_NAME),
        ]
        for index, (message, path) in enumerate(cases):
            original = path.read_bytes()
            changed = bytearray(original)
            changed[0] ^= 1
            path.write_bytes(changed)
            with self.subTest(message=message):
                with self.assertRaisesRegex(probe.ProbeError, message):
                    self.stage(f"changed-{index}")
            path.write_bytes(original)
            self.assertFalse((self.store / f"changed-{index}").exists())

    def test_path_traversal_bad_id_symlink_and_extra_input_are_refused(self) -> None:
        cases = [
            ("pack ID", {"pack_id": "../bad", "relative_target": self.target_relative}),
            ("normalized", {"pack_id": "good", "relative_target": "../owned"}),
            ("normalized", {"pack_id": "good", "relative_target": "/absolute"}),
            ("normalized", {"pack_id": "good", "relative_target": "art//cache/file"}),
        ]
        for message, options in cases:
            with self.subTest(options=options):
                with self.assertRaisesRegex(probe.ProbeError, message):
                    mods.stage_pack(
                        self.game,
                        overlay_dir=self.overlay_dir,
                        **options,
                    )

        linked = self.root / "linked-overlay"
        linked.symlink_to(self.overlay_dir, target_is_directory=True)
        with self.assertRaisesRegex(probe.ProbeError, "nonsymlink"):
            mods.stage_pack(
                self.game,
                pack_id="linked",
                relative_target=self.target_relative,
                overlay_dir=linked,
            )

        extra = self.overlay_dir / "extra"
        extra.write_bytes(b"owner")
        with self.assertRaisesRegex(probe.ProbeError, "population"):
            self.stage("extra")
        self.assertEqual(extra.read_bytes(), b"owner")

    def test_staged_target_receipt_and_population_changes_block_activation(self) -> None:
        self.stage("target-change")
        pack = self.store / "target-change"
        target = pack / self.target_relative
        original = target.read_bytes()
        changed = bytearray(original)
        changed[0] ^= 1
        target.write_bytes(changed)
        with self.assertRaisesRegex(probe.ProbeError, "staged XPPS overlay"):
            mods.enable_pack(self.game, pack_id="target-change")
        self.assertFalse(self.active.exists())
        target.write_bytes(original)

        receipt = pack / mods.PACK_OVERLAY_RECEIPT
        original_receipt = receipt.read_bytes()
        changed_receipt = bytearray(original_receipt)
        changed_receipt[-2] = ord(" ")
        receipt.write_bytes(changed_receipt)
        with self.assertRaisesRegex(probe.ProbeError, "canonical|receipt"):
            mods.enable_pack(self.game, pack_id="target-change")
        receipt.write_bytes(original_receipt)

        extra = pack / "extra"
        extra.write_bytes(b"owner")
        with self.assertRaisesRegex(probe.ProbeError, "population"):
            mods.enable_pack(self.game, pack_id="target-change")
        self.assertEqual(extra.read_bytes(), b"owner")

    def test_short_write_and_source_mutation_cleanup_only_owned_stage(self) -> None:
        def short_write(file_descriptor: int, data: bytes) -> None:
            os.write(file_descriptor, data[:7])
            raise probe.ProbeError("synthetic short write")

        with mock.patch.object(
            overlay.dds, "_write_all", side_effect=short_write
        ):
            with self.assertRaisesRegex(probe.ProbeError, "short write"):
                self.stage("short")
        self.assertFalse(self.store.exists())

        original_verify = mods._verify_pack_open
        changed = False

        def mutate_before_verify(*args: object, **kwargs: object) -> object:
            nonlocal changed
            if not changed:
                changed = True
                data = bytearray(self.xpps.read_bytes())
                data[0] ^= 1
                self.xpps.write_bytes(data)
            return original_verify(*args, **kwargs)

        with mock.patch.object(
            mods, "_verify_pack_open", side_effect=mutate_before_verify
        ):
            with self.assertRaisesRegex(probe.ProbeError, "base XPPS disagrees"):
                self.stage("mutated")
        self.assertFalse(self.store.exists())
        self.xpps.write_bytes(self.source_before)
        self.assertEqual(self.eboot.read_bytes(), self.eboot_before)


if __name__ == "__main__":
    unittest.main()
