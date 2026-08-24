# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


class WarmCacheError(RuntimeError):
    pass


@dataclass(frozen=True)
class CacheInventory:
    profile_present: bool
    pipeline_keys: int
    files: int
    safe_layout: bool
    sha256: str

    @property
    def usable(self) -> bool:
        return self.profile_present and self.pipeline_keys > 0 and self.safe_layout


def title_cache(root: Path, title_id: str) -> Path:
    return root / "cache" / title_id


def inventory(cache: Path) -> CacheInventory:
    if not cache.is_dir():
        return CacheInventory(False, 0, 0, False, "")
    entries = sorted(cache.iterdir(), key=lambda path: path.name)
    safe_layout = all(path.is_file() and not path.is_symlink() for path in entries)
    files = [path for path in entries if path.is_file() and not path.is_symlink()]
    digest = hashlib.sha256()
    for path in files:
        encoded_name = path.name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "little"))
        digest.update(encoded_name)
        digest.update(path.stat().st_size.to_bytes(8, "little"))
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return CacheInventory(
        profile_present=(cache / "profile.bin").is_file()
        and not (cache / "profile.bin").is_symlink(),
        pipeline_keys=sum(path.suffix == ".key" for path in files),
        files=len(files),
        safe_layout=safe_layout,
        sha256=digest.hexdigest(),
    )


def resolve_seed_root(
    *, explicit_root: Path | None, warm_cache_root: Path, live_user_root: Path, title_id: str
) -> tuple[Path, str]:
    if explicit_root is not None:
        return explicit_root, "explicit"
    if inventory(title_cache(warm_cache_root, title_id)).usable:
        return warm_cache_root, "project-warm-cache"
    return live_user_root, "live-read-only-fallback"


def promote(
    *,
    isolated_user_root: Path,
    warm_cache_root: Path,
    title_id: str,
    regenerated: bool,
) -> dict[str, object]:
    source = title_cache(isolated_user_root, title_id)
    target = title_cache(warm_cache_root, title_id)
    source_inventory = inventory(source)
    if not source_inventory.usable:
        raise WarmCacheError(f"isolated cache is incomplete: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / ".promotion.lock"
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        previous_inventory = inventory(target)
        if (
            previous_inventory.usable
            and not regenerated
            and source_inventory.pipeline_keys <= previous_inventory.pipeline_keys
        ):
            return {
                "status": "unchanged",
                "reason": "candidate-does-not-add-pipelines",
                "regenerated": regenerated,
                "source": str(source),
                "target": str(target),
                "source_inventory": asdict(source_inventory),
                "previous_inventory": asdict(previous_inventory),
            }

        staging = Path(tempfile.mkdtemp(prefix=f".{title_id}.next-", dir=target.parent))
        previous = target.parent / f".{title_id}.previous"
        moved_previous = False
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True, copy_function=shutil.copy2)
            if inventory(source) != source_inventory or inventory(staging) != source_inventory:
                raise WarmCacheError("staged cache inventory does not match the isolated cache")
            if previous.exists():
                shutil.rmtree(previous)
            if target.exists():
                os.replace(target, previous)
                moved_previous = True
            os.replace(staging, target)
        except Exception:
            if moved_previous and not target.exists() and previous.exists():
                os.replace(previous, target)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)

        if previous.exists():
            shutil.rmtree(previous)
        return {
            "status": "promoted",
            "reason": "cache-regenerated" if regenerated else "new-or-expanded-cache",
            "regenerated": regenerated,
            "source": str(source),
            "target": str(target),
            "source_inventory": asdict(source_inventory),
            "previous_inventory": asdict(previous_inventory),
        }


def write_receipt(path: Path, receipt: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Second Son's isolated warm pipeline cache")
    subparsers = parser.add_subparsers(dest="command", required=True)

    select = subparsers.add_parser("select")
    select.add_argument("--explicit-root")
    select.add_argument("--warm-cache-root", type=Path, required=True)
    select.add_argument("--live-user-root", type=Path, required=True)
    select.add_argument("--title-id", required=True)

    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--isolated-user-root", type=Path, required=True)
    promote_parser.add_argument("--warm-cache-root", type=Path, required=True)
    promote_parser.add_argument("--title-id", required=True)
    promote_parser.add_argument("--regenerated", action="store_true")
    promote_parser.add_argument("--receipt", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "select":
        root, source = resolve_seed_root(
            explicit_root=Path(args.explicit_root) if args.explicit_root else None,
            warm_cache_root=args.warm_cache_root,
            live_user_root=args.live_user_root,
            title_id=args.title_id,
        )
        print(root)
        print(source)
        return 0

    try:
        receipt = promote(
            isolated_user_root=args.isolated_user_root,
            warm_cache_root=args.warm_cache_root,
            title_id=args.title_id,
            regenerated=args.regenerated,
        )
    except WarmCacheError as error:
        receipt = {"status": "rejected", "reason": str(error)}
        write_receipt(args.receipt, receipt)
        print(error)
        return 1
    write_receipt(args.receipt, receipt)
    print(receipt["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
