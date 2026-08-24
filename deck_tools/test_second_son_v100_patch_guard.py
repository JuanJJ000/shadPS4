# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_v100_patch_guard as guard


def patch_xml(**overrides: str) -> bytes:
    values = {
        "address": "0x00c5bc70",
        "app_version": "01.00",
        "enabled": "true",
        "name": guard.PATCH_NAME,
        "title_id": "CUSA00223",
        "value": "83a7c80100000090",
    }
    values.update(overrides)
    return f'''<?xml version="1.0" encoding="utf-8"?>
<Patch>
  <TitleID><ID>{values["title_id"]}</ID></TitleID>
  <Metadata Name="{values["name"]}" AppVer="{values["app_version"]}" AppElf="eboot.bin" isEnabled="{values["enabled"]}">
    <PatchList><Line Type="bytes" Address="{values["address"]}" Value="{values["value"]}"/></PatchList>
  </Metadata>
</Patch>
'''.encode()


class SecondSonV100PatchGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.eboot = self.root / "eboot.bin"
        self.patch = self.root / "patch.xml"
        self.setter = b"unique-setter-16"
        self.offset = 64
        data = bytearray(256)
        data[self.offset : self.offset + len(self.setter)] = self.setter
        self.eboot.write_bytes(data)
        self.eboot_hash = hashlib.sha256(data).hexdigest()
        self.patch.write_bytes(patch_xml())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self, **kwargs: object) -> dict[str, object]:
        options: dict[str, object] = {
            "expected_eboot_sha256": self.eboot_hash,
            "expected_eboot_bytes": 256,
            "setter_self_offset": self.offset,
            "expected_setter": self.setter,
        }
        options.update(kwargs)
        return guard.validate_patch(
            self.eboot,
            self.patch,
            **options,
        )

    def test_exact_pair_is_deterministic_and_retained(self) -> None:
        source_before = self.eboot.read_bytes()
        patch_before = self.patch.read_bytes()
        first = self.validate()
        second = self.validate()
        self.assertEqual(guard.encode_report(first), guard.encode_report(second))
        self.assertEqual(self.eboot.read_bytes(), source_before)
        self.assertEqual(self.patch.read_bytes(), patch_before)
        self.assertTrue(first["facts"]["setter_unique"])
        self.assertNotIn(str(self.root), guard.encode_report(first).decode())

    def test_bad_whole_file_hash_is_refused(self) -> None:
        with self.assertRaisesRegex(guard.GuardError, "SHA-256 mismatch"):
            guard.validate_patch(
                self.eboot,
                self.patch,
                expected_eboot_sha256="0" * 64,
                expected_eboot_bytes=256,
                setter_self_offset=self.offset,
                expected_setter=self.setter,
            )

    def test_wrong_setter_at_offset_is_refused(self) -> None:
        with self.assertRaisesRegex(guard.GuardError, "setter bytes are absent"):
            self.validate(setter_self_offset=self.offset + 1)

    def test_duplicate_setter_is_refused(self) -> None:
        data = bytearray(self.eboot.read_bytes())
        data[128 : 128 + len(self.setter)] = self.setter
        self.eboot.write_bytes(data)
        self.eboot_hash = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(guard.GuardError, "not unique"):
            self.validate()

    def test_truncated_source_is_refused(self) -> None:
        self.eboot.write_bytes(b"short")
        with self.assertRaisesRegex(guard.GuardError, "size mismatch"):
            self.validate()

    def test_input_symlinks_are_refused(self) -> None:
        eboot_link = self.root / "eboot-link.bin"
        patch_link = self.root / "patch-link.xml"
        eboot_link.symlink_to(self.eboot)
        patch_link.symlink_to(self.patch)
        with self.assertRaisesRegex(guard.GuardError, "nonsymlink"):
            guard.validate_patch(
                eboot_link,
                self.patch,
                expected_eboot_sha256=self.eboot_hash,
                expected_eboot_bytes=256,
                setter_self_offset=self.offset,
                expected_setter=self.setter,
            )
        with self.assertRaisesRegex(guard.GuardError, "nonsymlink"):
            guard.validate_patch(
                self.eboot,
                patch_link,
                expected_eboot_sha256=self.eboot_hash,
                expected_eboot_bytes=256,
                setter_self_offset=self.offset,
                expected_setter=self.setter,
            )

    def test_wrong_xml_identity_fields_are_refused(self) -> None:
        cases = {
            "address": ("0x00c5bc71", "Address"),
            "app_version": ("01.05", "AppVer"),
            "enabled": ("false", "isEnabled"),
            "name": ("Different patch", "Name"),
            "title_id": ("CUSA00000", "target only"),
            "value": ("90", "Value"),
        }
        for field, (value, message) in cases.items():
            with self.subTest(field=field):
                self.patch.write_bytes(patch_xml(**{field: value}))
                with self.assertRaisesRegex(guard.GuardError, message):
                    self.validate()

    def test_extra_patch_line_is_refused(self) -> None:
        data = patch_xml().replace(b"</PatchList>", b'<Line Type="bytes" Address="0x00c5bc70" Value="90"/></PatchList>')
        self.patch.write_bytes(data)
        with self.assertRaisesRegex(guard.GuardError, "exactly one patch line"):
            self.validate()

    def test_post_validation_hash_change_signal_is_refused(self) -> None:
        original = guard._hash_open_file
        calls = 0

        def changed_on_second_source_hash(stream: object) -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return "0" * 64
            return original(stream)  # pragma: no cover - defensive fallback

        with mock.patch.object(guard, "_hash_open_file", side_effect=changed_on_second_source_hash):
            with self.assertRaisesRegex(guard.GuardError, "eboot changed"):
                self.validate()


if __name__ == "__main__":
    unittest.main()
