#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Fail-closed identity guard for the owned Second Son 01.00 clarity patch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

SCHEMA = "shadps4.second_son_v100_patch_guard"
SCHEMA_VERSION = 1
EXPECTED_EBOOT_SHA256 = "99c7fe77f8348062cb3e0e7218c1991cda3515188f0d308b64f7dca058997d87"
EXPECTED_EBOOT_BYTES = 16_714_837
SETTER_SELF_OFFSET = 0x8627E0
SETTER_GUEST_ADDRESS = 0x00C5BC70
EXPECTED_SETTER = bytes.fromhex("c5fa1187c8010000808ff400000004c3")
REPLACEMENT_BYTES = bytes.fromhex("83a7c80100000090")
PATCH_NAME = "Disable Motion Blur Exposure (CUSA00223 01.00)"
TITLE_ID = "CUSA00223"
APP_VERSION = "01.00"
MAX_PATCH_XML_BYTES = 64 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
NON_CLAIMS = (
    "complete_motion_blur_removal",
    "frame_pacing_change",
    "ps4_pro_rendering_path",
    "resolution_change",
    "texture_change",
    "visual_quality_acceptance",
)


class GuardError(ValueError):
    """Raised when an input fails the patch identity contract."""


def _open_regular(stack: ExitStack, path: Path, label: str) -> BinaryIO:
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise GuardError(f"cannot open {label} as a nonsymlink regular file: {error.strerror}") from error
    stream = stack.enter_context(os.fdopen(descriptor, "rb"))
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        raise GuardError(f"{label} is not a regular file")
    return stream


def _read_bounded(stream: BinaryIO, maximum: int, label: str) -> bytes:
    stream.seek(0)
    data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise GuardError(f"{label} exceeds the {maximum}-byte limit")
    return data


def _hash_open_file(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _validate_xml(document: bytes) -> None:
    try:
        root = ET.fromstring(document)
    except ET.ParseError as error:
        raise GuardError(f"patch XML is malformed: {error}") from error
    if root.tag != "Patch":
        raise GuardError("patch XML root must be Patch")
    children = list(root)
    if [child.tag for child in children] != ["TitleID", "Metadata"]:
        raise GuardError("patch XML must contain exactly one TitleID and one Metadata entry")

    title_ids = list(children[0])
    if len(title_ids) != 1 or title_ids[0].tag != "ID" or title_ids[0].text != TITLE_ID:
        raise GuardError(f"patch XML must target only {TITLE_ID}")

    metadata = children[1]
    expected_metadata = {
        "AppElf": "eboot.bin",
        "AppVer": APP_VERSION,
        "Name": PATCH_NAME,
        "isEnabled": "true",
    }
    for name, expected in expected_metadata.items():
        if metadata.get(name) != expected:
            raise GuardError(f"patch Metadata {name} must be {expected!r}")
    patch_lists = list(metadata)
    if len(patch_lists) != 1 or patch_lists[0].tag != "PatchList":
        raise GuardError("patch Metadata must contain exactly one PatchList")
    lines = list(patch_lists[0])
    if len(lines) != 1 or lines[0].tag != "Line":
        raise GuardError("patch XML must contain exactly one patch line")
    line = lines[0]
    expected_line = {
        "Address": f"0x{SETTER_GUEST_ADDRESS:08x}",
        "Type": "bytes",
        "Value": REPLACEMENT_BYTES.hex(),
    }
    if set(line.attrib) != set(expected_line):
        raise GuardError("patch line has an unexpected attribute set")
    for name, expected in expected_line.items():
        if line.get(name) != expected:
            raise GuardError(f"patch line {name} must be {expected!r}")


def validate_patch(
    eboot: Path,
    patch_xml: Path,
    *,
    expected_eboot_sha256: str = EXPECTED_EBOOT_SHA256,
    expected_eboot_bytes: int = EXPECTED_EBOOT_BYTES,
    setter_self_offset: int = SETTER_SELF_OFFSET,
    expected_setter: bytes = EXPECTED_SETTER,
) -> dict[str, object]:
    with ExitStack() as stack:
        eboot_stream = _open_regular(stack, eboot, "eboot")
        patch_stream = _open_regular(stack, patch_xml, "patch XML")
        eboot_before = os.fstat(eboot_stream.fileno())
        patch_before = os.fstat(patch_stream.fileno())
        if eboot_before.st_size != expected_eboot_bytes:
            raise GuardError(
                f"eboot size mismatch: expected {expected_eboot_bytes}, observed {eboot_before.st_size}"
            )
        if setter_self_offset < 0 or setter_self_offset + len(expected_setter) > eboot_before.st_size:
            raise GuardError("expected setter range is outside the eboot")

        eboot_data = _read_bounded(eboot_stream, expected_eboot_bytes, "eboot")
        observed_eboot_sha256 = hashlib.sha256(eboot_data).hexdigest()
        if observed_eboot_sha256 != expected_eboot_sha256:
            raise GuardError(
                "eboot SHA-256 mismatch: "
                f"expected {expected_eboot_sha256}, observed {observed_eboot_sha256}"
            )
        if eboot_data[setter_self_offset : setter_self_offset + len(expected_setter)] != expected_setter:
            raise GuardError("expected motion-blur setter bytes are absent at the guarded SELF offset")
        if eboot_data.count(expected_setter) != 1:
            raise GuardError("expected motion-blur setter is not unique in the eboot")

        patch_data = _read_bounded(patch_stream, MAX_PATCH_XML_BYTES, "patch XML")
        patch_sha256 = hashlib.sha256(patch_data).hexdigest()
        _validate_xml(patch_data)

        if _hash_open_file(eboot_stream) != expected_eboot_sha256:
            raise GuardError("eboot changed during validation")
        if _hash_open_file(patch_stream) != patch_sha256:
            raise GuardError("patch XML changed during validation")
        if _identity(os.fstat(eboot_stream.fileno())) != _identity(eboot_before):
            raise GuardError("eboot identity changed during validation")
        if _identity(os.fstat(patch_stream.fileno())) != _identity(patch_before):
            raise GuardError("patch XML identity changed during validation")

    return {
        "facts": {
            "eboot_hash_exact": True,
            "owned_dump_modified": False,
            "patch_line_exact": True,
            "setter_bytes_exact": True,
            "setter_unique": True,
            "title_and_version_exact": True,
        },
        "non_claims": list(NON_CLAIMS),
        "patch": {
            "basename": Path(patch_xml).name,
            "bytes": patch_before.st_size,
            "sha256": patch_sha256,
        },
        "schema": SCHEMA,
        "source": {
            "basename": Path(eboot).name,
            "bytes": eboot_before.st_size,
            "sha256": observed_eboot_sha256,
        },
        "target": {
            "app_version": APP_VERSION,
            "guest_address": f"0x{SETTER_GUEST_ADDRESS:08x}",
            "self_offset": f"0x{setter_self_offset:x}",
            "title_id": TITLE_ID,
        },
        "version": SCHEMA_VERSION,
        "warnings": [],
    }


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the exact owned Second Son 01.00 eboot and clarity patch identities."
    )
    parser.add_argument("eboot", type=Path)
    parser.add_argument("patch_xml", type=Path)
    parser.add_argument("--json", action="store_true", help="emit a deterministic evidence report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = validate_patch(args.eboot, args.patch_xml)
        if args.json:
            sys.stdout.buffer.write(encode_report(report))
        else:
            print("Second Son 01.00 motion-blur patch guard: accepted")
    except (OSError, GuardError) as error:
        print(f"second_son_v100_patch_guard: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
