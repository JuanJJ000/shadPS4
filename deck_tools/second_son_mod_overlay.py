#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Stage and select receipt-gated Second Son packs through shadPS4's -mods overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath

import second_son_xpps_dds_overlay as xpps_overlay
import second_son_xpps_eboot_registry as registry
import second_son_xpps_probe as probe

SCHEMA = "shadps4.second_son_mod_pack"
SCHEMA_VERSION = 1
STATUS_SCHEMA = "shadps4.second_son_mod_pack_status"
TITLE_ID = "CUSA00223"
PACK_MANIFEST = ".shadps4-second-son-pack.json"
PACK_OVERLAY_RECEIPT = ".shadps4-xpps-overlay-receipt.json"
ACTIVE_SUFFIX = "-mods"
STORE_SUFFIX = "-modpacks"
MAX_COMPONENTS = 32
MAX_COMPONENT_BYTES = 255
MAX_OVERLAY_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
PACK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
NON_CLAIMS = (
    "artwork_identity",
    "texture_semantics",
    "runtime_visual_change",
    "game_runtime_behavior",
    "arbitrary_title_support",
    "pack_removal",
)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_pack_id(pack_id: str) -> str:
    if not PACK_ID.fullmatch(pack_id) or pack_id in (".", ".."):
        raise probe.ProbeError("pack ID is not a safe lowercase component")
    return pack_id


def _target_parts(relative_target: str) -> tuple[str, ...]:
    if not relative_target or "\\" in relative_target or "\x00" in relative_target:
        raise probe.ProbeError("mod target is not a normalized POSIX relative path")
    path = PurePosixPath(relative_target)
    parts = path.parts
    if (
        path.is_absolute()
        or not parts
        or len(parts) > MAX_COMPONENTS
        or any(
            part in ("", ".", "..")
            or len(part.encode("utf-8")) > MAX_COMPONENT_BYTES
            for part in parts
        )
        or str(path) != relative_target
    ):
        raise probe.ProbeError("mod target is not a normalized POSIX relative path")
    return parts


def _open_directory_at(parent_fd: int, name: str, label: str) -> int:
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise probe.ProbeError(f"{label} has an invalid component")
    try:
        descriptor = os.open(name, xpps_overlay._directory_flags(), dir_fd=parent_fd)
    except OSError as error:
        raise probe.ProbeError(
            f"cannot open {label} as a nonsymlink directory: {error.strerror}"
        ) from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise probe.ProbeError(f"{label} is not a directory")
    return descriptor


def _read_beneath(
    root_fd: int, parts: tuple[str, ...], maximum: int, label: str
) -> bytes:
    current = os.dup(root_fd)
    try:
        for index, component in enumerate(parts[:-1]):
            child = _open_directory_at(current, component, f"{label} parent {index}")
            os.close(current)
            current = child
        return xpps_overlay._read_regular_at(current, parts[-1], maximum, label)
    finally:
        os.close(current)


def _canonical_json(data: bytes, label: str) -> dict[str, object]:
    return xpps_overlay._parse_canonical_json(data, label)


def _list_names(descriptor: int, label: str) -> list[str]:
    try:
        return sorted(xpps_overlay.dds._list_output_names(descriptor))
    except (OSError, probe.ProbeError) as error:
        raise probe.ProbeError(f"cannot list {label}: {error}") from error


def encode_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()


def _require_dict(value: object, label: str) -> dict[str, object]:
    return xpps_overlay._require_dict(value, label)


def _require_int(value: object, label: str) -> int:
    return xpps_overlay._require_int(value, label)


def _require_string(value: object, label: str) -> str:
    return xpps_overlay._require_string(value, label)


def _validate_sha(value: object, label: str) -> str:
    text = _require_string(value, label)
    registry._validate_sha256(text, label)
    return text


def _read_overlay_input(overlay_dir: Path) -> tuple[dict[str, object], bytes, bytes]:
    descriptor, identity = xpps_overlay._open_input_directory(
        Path(overlay_dir), "XPPS overlay output"
    )
    try:
        expected_names = sorted(
            [xpps_overlay.OVERLAY_NAME, xpps_overlay.RECEIPT_NAME]
        )
        if _list_names(descriptor, "XPPS overlay output") != expected_names:
            raise probe.ProbeError("XPPS overlay output population is not exact")
        receipt_data = xpps_overlay._read_regular_at(
            descriptor,
            xpps_overlay.RECEIPT_NAME,
            MAX_MANIFEST_BYTES,
            "XPPS overlay receipt",
        )
        receipt = _canonical_json(receipt_data, "XPPS overlay receipt")
        if (
            receipt.get("schema") != xpps_overlay.SCHEMA
            or receipt.get("version") != xpps_overlay.SCHEMA_VERSION
            or receipt.get("proof_class") != xpps_overlay.PROOF_CLASS
        ):
            raise probe.ProbeError("XPPS overlay receipt has the wrong exact contract")
        output = _require_dict(receipt.get("output"), "XPPS overlay output identity")
        if output.get("basename") != xpps_overlay.OVERLAY_NAME:
            raise probe.ProbeError("XPPS overlay receipt has the wrong output basename")
        output_bytes = _require_int(output.get("bytes"), "XPPS overlay byte size")
        if output_bytes < 1 or output_bytes > MAX_OVERLAY_BYTES:
            raise probe.ProbeError("XPPS overlay receipt has an invalid byte size")
        output_sha = _validate_sha(output.get("sha256"), "XPPS overlay SHA-256")
        overlay_data = xpps_overlay._read_regular_at(
            descriptor,
            xpps_overlay.OVERLAY_NAME,
            output_bytes,
            "XPPS overlay",
        )
        if len(overlay_data) != output_bytes or _hash(overlay_data) != output_sha:
            raise probe.ProbeError("XPPS overlay bytes disagree with their receipt")
        if not xpps_overlay._directory_binding_matches(
            Path(overlay_dir), descriptor, identity
        ):
            raise probe.ProbeError("XPPS overlay output path binding changed")
        return receipt, receipt_data, overlay_data
    finally:
        os.close(descriptor)


def _open_game(game_root: Path) -> tuple[int, int, str, tuple[int, int]]:
    root = Path(game_root)
    if root.name != TITLE_ID:
        raise probe.ProbeError(f"game root basename must be {TITLE_ID}")
    parent_fd, parent_identity = xpps_overlay._open_input_directory(
        root.parent, "game parent"
    )
    try:
        game_fd = _open_directory_at(parent_fd, TITLE_ID, "base game root")
    except BaseException:
        os.close(parent_fd)
        raise
    game_info = os.fstat(game_fd)
    path_info = os.stat(TITLE_ID, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(path_info.st_mode)
        or (path_info.st_dev, path_info.st_ino) != (game_info.st_dev, game_info.st_ino)
    ):
        os.close(game_fd)
        os.close(parent_fd)
        raise probe.ProbeError("base game root path binding changed")
    return parent_fd, game_fd, TITLE_ID, parent_identity


def _verify_base(
    game_fd: int, target_parts: tuple[str, ...], receipt: dict[str, object]
) -> tuple[dict[str, object], dict[str, object]]:
    xpps = _require_dict(receipt.get("xpps"), "overlay source XPPS identity")
    eboot = _require_dict(receipt.get("eboot"), "overlay source eboot identity")
    xpps_bytes = _require_int(xpps.get("bytes"), "base XPPS bytes")
    xpps_sha = _validate_sha(xpps.get("sha256"), "base XPPS SHA-256")
    if target_parts[-1] != xpps.get("basename"):
        raise probe.ProbeError("mod target basename disagrees with the receipt XPPS")
    base_data = _read_beneath(game_fd, target_parts, xpps_bytes, "base XPPS")
    if len(base_data) != xpps_bytes or _hash(base_data) != xpps_sha:
        raise probe.ProbeError("base XPPS disagrees with the overlay receipt")
    eboot_bytes = _require_int(eboot.get("bytes"), "base eboot bytes")
    eboot_sha = _validate_sha(eboot.get("sha256"), "base eboot SHA-256")
    base_eboot = _read_beneath(game_fd, ("eboot.bin",), eboot_bytes, "base eboot")
    if len(base_eboot) != eboot_bytes or _hash(base_eboot) != eboot_sha:
        raise probe.ProbeError("base eboot disagrees with the overlay receipt")
    return xpps, eboot


def _write_guarded(
    directory_fd: int,
    name: str,
    data: bytes,
    records: list[
        tuple[
            int,
            str,
            int,
            tuple[int, int, int, int, int],
        ]
    ],
) -> None:
    names: list[str] = []
    identities: dict[str, tuple[int, int, int, int, int]] = {}
    guards: dict[str, int] = {}
    try:
        observed = xpps_overlay.dds._write_exclusive(
            directory_fd, name, data, names, identities, guards
        )
    finally:
        for created_name in names:
            if created_name in guards and created_name in identities:
                records.append(
                    (
                        directory_fd,
                        created_name,
                        guards[created_name],
                        identities[created_name],
                    )
                )
    if observed != data or names != [name]:
        raise probe.ProbeError("staged mod file changed during its guarded write")


def _cleanup_stage(
    file_records: list[tuple[int, str, int, tuple[int, int, int, int, int]]],
    directory_records: list[tuple[int, str, int, tuple[int, int]]],
) -> str | None:
    failures: list[str] = []
    for parent_fd, name, guard_fd, _ in reversed(file_records):
        try:
            guarded = registry._identity(os.fstat(guard_fd))
            observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            failures.append(f"cannot inspect staged file {name}: {error.strerror}")
            continue
        if not stat.S_ISREG(observed.st_mode) or registry._identity(observed) != guarded:
            failures.append(f"staged file binding changed for {name}")
            continue
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError as error:
            failures.append(f"cannot remove staged file {name}: {error.strerror}")
    for parent_fd, name, directory_fd, identity in reversed(directory_records):
        try:
            observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            names = _list_names(directory_fd, f"staged directory {name}")
        except OSError as error:
            failures.append(f"cannot inspect staged directory {name}: {error.strerror}")
            continue
        if (
            not stat.S_ISDIR(observed.st_mode)
            or (observed.st_dev, observed.st_ino) != identity
            or names
        ):
            failures.append(f"staged directory binding or population changed for {name}")
            continue
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError as error:
            failures.append(f"cannot remove staged directory {name}: {error.strerror}")
    return "; ".join(failures) if failures else None


def _close_stage_records(
    file_records: list[tuple[int, str, int, tuple[int, int, int, int, int]]],
    directory_records: list[tuple[int, str, int, tuple[int, int]]],
) -> None:
    for _, _, descriptor, _ in file_records:
        try:
            os.close(descriptor)
        except OSError:
            pass
    for _, _, descriptor, _ in reversed(directory_records):
        try:
            os.close(descriptor)
        except OSError:
            pass


def stage_pack(
    game_root: Path,
    *,
    pack_id: str,
    relative_target: str,
    overlay_dir: Path,
) -> dict[str, object]:
    pack_id = _validate_pack_id(pack_id)
    parts = _target_parts(relative_target)
    receipt, receipt_data, overlay_data = _read_overlay_input(Path(overlay_dir))
    parent_fd, game_fd, game_name, parent_identity = _open_game(Path(game_root))
    file_records: list[tuple[int, str, int, tuple[int, int, int, int, int]]] = []
    directory_records: list[tuple[int, str, int, tuple[int, int]]] = []
    unowned_store_fd: int | None = None
    try:
        xpps, eboot = _verify_base(game_fd, parts, receipt)
        output = _require_dict(receipt.get("output"), "overlay output identity")
        store_name = game_name + STORE_SUFFIX
        created_store = False
        try:
            store_fd = _open_directory_at(parent_fd, store_name, "mod-pack store")
        except probe.ProbeError:
            try:
                os.mkdir(store_name, 0o700, dir_fd=parent_fd)
            except OSError as error:
                raise probe.ProbeError(
                    f"cannot create mod-pack store: {error.strerror}"
                ) from error
            store_fd = _open_directory_at(parent_fd, store_name, "mod-pack store")
            created_store = True
            store_info = os.fstat(store_fd)
            directory_records.append(
                (parent_fd, store_name, store_fd, (store_info.st_dev, store_info.st_ino))
            )
        if not created_store:
            # Retain the open store for the complete transaction, but it is not cleanup-owned.
            unowned_store_fd = store_fd
        try:
            os.mkdir(pack_id, 0o700, dir_fd=store_fd)
        except OSError as error:
            raise probe.ProbeError(
                f"cannot create one fresh mod pack: {error.strerror}"
            ) from error
        pack_fd = _open_directory_at(store_fd, pack_id, "fresh mod pack")
        pack_info = os.fstat(pack_fd)
        directory_records.append(
            (store_fd, pack_id, pack_fd, (pack_info.st_dev, pack_info.st_ino))
        )
        current_fd = pack_fd
        for index, component in enumerate(parts[:-1]):
            try:
                os.mkdir(component, 0o700, dir_fd=current_fd)
            except OSError as error:
                raise probe.ProbeError(
                    f"cannot create mod target directory {index}: {error.strerror}"
                ) from error
            child_fd = _open_directory_at(
                current_fd, component, f"mod target directory {index}"
            )
            child_info = os.fstat(child_fd)
            directory_records.append(
                (
                    current_fd,
                    component,
                    child_fd,
                    (child_info.st_dev, child_info.st_ino),
                )
            )
            current_fd = child_fd
        _write_guarded(current_fd, parts[-1], overlay_data, file_records)
        _write_guarded(pack_fd, PACK_OVERLAY_RECEIPT, receipt_data, file_records)
        manifest = {
            "base": {"eboot": eboot, "xpps": xpps},
            "inherited_overlay": {
                "output": output,
                "receipt_sha256": _hash(receipt_data),
                "schema": xpps_overlay.SCHEMA,
                "version": xpps_overlay.SCHEMA_VERSION,
            },
            "non_claims": list(NON_CLAIMS),
            "pack_id": pack_id,
            "runtime": {
                "active_entry": game_name + ACTIVE_SUFFIX,
                "relative_selector": f"{store_name}/{pack_id}",
                "shadps4_precedence": ["mods", "update_or_patch", "base"],
            },
            "schema": SCHEMA,
            "target": {
                "bytes": len(overlay_data),
                "relative_path": relative_target,
                "sha256": _hash(overlay_data),
            },
            "title_id": TITLE_ID,
            "version": SCHEMA_VERSION,
            "warnings": [],
        }
        manifest_data = encode_report(manifest)
        if len(manifest_data) > MAX_MANIFEST_BYTES:
            raise probe.ProbeError("mod-pack manifest exceeds its byte limit")
        _write_guarded(pack_fd, PACK_MANIFEST, manifest_data, file_records)

        expected_root = sorted([PACK_MANIFEST, PACK_OVERLAY_RECEIPT, parts[0]])
        if _list_names(pack_fd, "fresh mod pack") != expected_root:
            raise probe.ProbeError("fresh mod-pack root population is not exact")
        if not xpps_overlay._directory_binding_matches(
            Path(game_root).parent, parent_fd, parent_identity
        ):
            raise probe.ProbeError("game parent path binding changed during staging")
        verified, _ = _verify_pack_open(
            parent_fd, game_fd, game_name, pack_id, expected_manifest=manifest
        )
        if verified != manifest:
            raise probe.ProbeError("staged mod-pack verification changed its manifest")
        return manifest
    except BaseException as error:
        cleanup_error = _cleanup_stage(file_records, directory_records)
        if cleanup_error:
            raise probe.ProbeError(
                f"{error}; staged mod-pack cleanup incomplete: {cleanup_error}"
            ) from error
        raise
    finally:
        _close_stage_records(file_records, directory_records)
        if unowned_store_fd is not None:
            os.close(unowned_store_fd)
        os.close(game_fd)
        os.close(parent_fd)


def _active_state(parent_fd: int, game_name: str, pack_id: str) -> str:
    active_name = game_name + ACTIVE_SUFFIX
    expected = f"{game_name + STORE_SUFFIX}/{pack_id}"
    try:
        info = os.stat(active_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "disabled"
    except OSError as error:
        raise probe.ProbeError(f"cannot inspect active mod selector: {error.strerror}") from error
    if not stat.S_ISLNK(info.st_mode):
        return "conflict"
    try:
        target = os.readlink(active_name, dir_fd=parent_fd)
    except OSError as error:
        raise probe.ProbeError(f"cannot read active mod selector: {error.strerror}") from error
    return "enabled" if target == expected else "conflict"


def _verify_pack_open(
    parent_fd: int,
    game_fd: int,
    game_name: str,
    pack_id: str,
    *,
    expected_manifest: dict[str, object] | None = None,
) -> tuple[dict[str, object], bytes]:
    store_fd = _open_directory_at(parent_fd, game_name + STORE_SUFFIX, "mod-pack store")
    try:
        pack_fd = _open_directory_at(store_fd, pack_id, "selected mod pack")
        try:
            manifest_data = xpps_overlay._read_regular_at(
                pack_fd, PACK_MANIFEST, MAX_MANIFEST_BYTES, "mod-pack manifest"
            )
            manifest = _canonical_json(manifest_data, "mod-pack manifest")
            if (
                manifest.get("schema") != SCHEMA
                or manifest.get("version") != SCHEMA_VERSION
                or manifest.get("title_id") != TITLE_ID
                or manifest.get("pack_id") != pack_id
            ):
                raise probe.ProbeError("mod-pack manifest has the wrong exact contract")
            if expected_manifest is not None and manifest != expected_manifest:
                raise probe.ProbeError("mod-pack manifest differs from staged facts")
            overlay_receipt_data = xpps_overlay._read_regular_at(
                pack_fd,
                PACK_OVERLAY_RECEIPT,
                MAX_MANIFEST_BYTES,
                "staged XPPS overlay receipt",
            )
            overlay_receipt = _canonical_json(
                overlay_receipt_data, "staged XPPS overlay receipt"
            )
            if (
                overlay_receipt.get("schema") != xpps_overlay.SCHEMA
                or overlay_receipt.get("version") != xpps_overlay.SCHEMA_VERSION
                or overlay_receipt.get("proof_class") != xpps_overlay.PROOF_CLASS
            ):
                raise probe.ProbeError("staged XPPS overlay receipt has the wrong contract")
            target = _require_dict(manifest.get("target"), "mod-pack target")
            relative_target = _require_string(
                target.get("relative_path"), "mod-pack target path"
            )
            parts = _target_parts(relative_target)
            target_bytes = _require_int(target.get("bytes"), "mod-pack target bytes")
            target_sha = _validate_sha(target.get("sha256"), "mod-pack target SHA-256")
            packed = _read_beneath(pack_fd, parts, target_bytes, "staged XPPS overlay")
            if len(packed) != target_bytes or _hash(packed) != target_sha:
                raise probe.ProbeError("staged XPPS overlay disagrees with its manifest")
            if _list_names(pack_fd, "selected mod pack") != sorted(
                [PACK_MANIFEST, PACK_OVERLAY_RECEIPT, parts[0]]
            ):
                raise probe.ProbeError("selected mod-pack root population is not exact")
            current = os.dup(pack_fd)
            try:
                for index, component in enumerate(parts[:-1]):
                    child = _open_directory_at(
                        current, component, f"selected mod target directory {index}"
                    )
                    expected_name = parts[index + 1]
                    if _list_names(
                        child, f"selected mod target directory {index}"
                    ) != [expected_name]:
                        os.close(child)
                        raise probe.ProbeError("selected mod target population is not exact")
                    os.close(current)
                    current = child
            finally:
                os.close(current)
            base = _require_dict(manifest.get("base"), "mod-pack base identities")
            receipt = {
                "xpps": _require_dict(base.get("xpps"), "mod-pack base XPPS"),
                "eboot": _require_dict(base.get("eboot"), "mod-pack base eboot"),
            }
            _verify_base(game_fd, parts, receipt)
            inherited = _require_dict(
                manifest.get("inherited_overlay"), "inherited overlay identity"
            )
            inherited_receipt_sha = _validate_sha(
                inherited.get("receipt_sha256"), "overlay receipt SHA-256"
            )
            if _hash(overlay_receipt_data) != inherited_receipt_sha:
                raise probe.ProbeError("staged overlay receipt hash changed")
            receipt_output = _require_dict(
                overlay_receipt.get("output"), "staged overlay output identity"
            )
            if receipt_output != inherited.get("output") or (
                _require_int(receipt_output.get("bytes"), "staged overlay bytes")
                != target_bytes
            ) or _validate_sha(
                receipt_output.get("sha256"), "staged overlay SHA-256"
            ) != target_sha:
                raise probe.ProbeError("staged target disagrees with its overlay receipt")
            receipt_xpps = _require_dict(
                overlay_receipt.get("xpps"), "staged overlay source XPPS"
            )
            receipt_eboot = _require_dict(
                overlay_receipt.get("eboot"), "staged overlay source eboot"
            )
            if receipt_xpps != base.get("xpps") or receipt_eboot != base.get("eboot"):
                raise probe.ProbeError("staged overlay receipt and base identities disagree")
            return manifest, manifest_data
        finally:
            os.close(pack_fd)
    finally:
        os.close(store_fd)


def verify_pack(game_root: Path, *, pack_id: str) -> dict[str, object]:
    pack_id = _validate_pack_id(pack_id)
    parent_fd, game_fd, game_name, parent_identity = _open_game(Path(game_root))
    try:
        manifest, manifest_data = _verify_pack_open(
            parent_fd, game_fd, game_name, pack_id
        )
        state = _active_state(parent_fd, game_name, pack_id)
        if not xpps_overlay._directory_binding_matches(
            Path(game_root).parent, parent_fd, parent_identity
        ):
            raise probe.ProbeError("game parent path binding changed during verification")
        return {
            "active_state": state,
            "pack_id": pack_id,
            "pack_manifest_sha256": _hash(manifest_data),
            "schema": STATUS_SCHEMA,
            "target": manifest.get("target"),
            "title_id": TITLE_ID,
            "version": SCHEMA_VERSION,
        }
    finally:
        os.close(game_fd)
        os.close(parent_fd)


def enable_pack(game_root: Path, *, pack_id: str) -> dict[str, object]:
    status = verify_pack(Path(game_root), pack_id=pack_id)
    if status["active_state"] == "enabled":
        return {**status, "action": "already_enabled"}
    if status["active_state"] != "disabled":
        raise probe.ProbeError("active mod selector conflicts with the selected pack")
    parent_fd, game_fd, game_name, _ = _open_game(Path(game_root))
    try:
        _verify_pack_open(parent_fd, game_fd, game_name, pack_id)
        active_name = game_name + ACTIVE_SUFFIX
        target = f"{game_name + STORE_SUFFIX}/{pack_id}"
        try:
            os.symlink(target, active_name, dir_fd=parent_fd)
        except OSError as error:
            raise probe.ProbeError(
                f"cannot create active mod selector exclusively: {error.strerror}"
            ) from error
        if _active_state(parent_fd, game_name, pack_id) != "enabled":
            raise probe.ProbeError("active mod selector failed post-create verification")
        return {**status, "action": "enabled", "active_state": "enabled"}
    finally:
        os.close(game_fd)
        os.close(parent_fd)


def disable_pack(game_root: Path, *, pack_id: str) -> dict[str, object]:
    status = verify_pack(Path(game_root), pack_id=pack_id)
    if status["active_state"] == "disabled":
        return {**status, "action": "already_disabled"}
    if status["active_state"] != "enabled":
        raise probe.ProbeError("active mod selector conflicts with the selected pack")
    parent_fd, game_fd, game_name, _ = _open_game(Path(game_root))
    try:
        _verify_pack_open(parent_fd, game_fd, game_name, pack_id)
        active_name = game_name + ACTIVE_SUFFIX
        expected_target = f"{game_name + STORE_SUFFIX}/{pack_id}"
        before = os.stat(active_name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISLNK(before.st_mode) or os.readlink(
            active_name, dir_fd=parent_fd
        ) != expected_target:
            raise probe.ProbeError("active mod selector changed before disable")
        observed = os.stat(active_name, dir_fd=parent_fd, follow_symlinks=False)
        if (before.st_dev, before.st_ino, before.st_ctime_ns) != (
            observed.st_dev,
            observed.st_ino,
            observed.st_ctime_ns,
        ):
            raise probe.ProbeError("active mod selector binding changed before disable")
        os.unlink(active_name, dir_fd=parent_fd)
        if _active_state(parent_fd, game_name, pack_id) != "disabled":
            raise probe.ProbeError("active mod selector remained after disable")
        return {**status, "action": "disabled", "active_state": "disabled"}
    finally:
        os.close(game_fd)
        os.close(parent_fd)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage and select verified Second Son packs through shadPS4's -mods overlay."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    stage = subparsers.add_parser("stage")
    stage.add_argument("game_root", type=Path)
    stage.add_argument("--pack-id", required=True)
    stage.add_argument("--relative-target", required=True)
    stage.add_argument("--overlay-dir", required=True, type=Path)
    for command in ("status", "enable", "disable"):
        action = subparsers.add_parser(command)
        action.add_argument("game_root", type=Path)
        action.add_argument("--pack-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "stage":
            report = stage_pack(
                args.game_root,
                pack_id=args.pack_id,
                relative_target=args.relative_target,
                overlay_dir=args.overlay_dir,
            )
        elif args.command == "status":
            report = verify_pack(args.game_root, pack_id=args.pack_id)
        elif args.command == "enable":
            report = enable_pack(args.game_root, pack_id=args.pack_id)
        else:
            report = disable_pack(args.game_root, pack_id=args.pack_id)
        sys.stdout.buffer.write(encode_report(report))
    except (OSError, probe.ProbeError) as error:
        print(f"second_son_mod_overlay: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
