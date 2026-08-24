# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import second_son_warm_cache as warm_cache


TITLE_ID = "CUSA00223"


def make_cache(root: Path, key_count: int, *, profile: bool = True) -> Path:
    cache = warm_cache.title_cache(root, TITLE_ID)
    cache.mkdir(parents=True)
    if profile:
        (cache / "profile.bin").write_bytes(b"profile")
    for index in range(key_count):
        (cache / f"g_{index:016x}.key").write_bytes(f"key-{index}".encode())
    return cache


class SecondSonWarmCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.live = self.root / "live"
        self.warm = self.root / "warm"
        self.isolated = self.root / "isolated"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_explicit_seed_root_always_wins(self) -> None:
        make_cache(self.warm, 2)
        explicit = self.root / "explicit"

        root, source = warm_cache.resolve_seed_root(
            explicit_root=explicit,
            warm_cache_root=self.warm,
            live_user_root=self.live,
            title_id=TITLE_ID,
        )

        self.assertEqual(root, explicit)
        self.assertEqual(source, "explicit")

    def test_project_cache_precedes_live_fallback_only_when_usable(self) -> None:
        make_cache(self.warm, 0)
        root, source = warm_cache.resolve_seed_root(
            explicit_root=None,
            warm_cache_root=self.warm,
            live_user_root=self.live,
            title_id=TITLE_ID,
        )
        self.assertEqual((root, source), (self.live, "live-read-only-fallback"))

        (warm_cache.title_cache(self.warm, TITLE_ID) / "g_0000000000000000.key").write_bytes(b"key")
        root, source = warm_cache.resolve_seed_root(
            explicit_root=None,
            warm_cache_root=self.warm,
            live_user_root=self.live,
            title_id=TITLE_ID,
        )
        self.assertEqual((root, source), (self.warm, "project-warm-cache"))

    def test_promote_installs_complete_cache(self) -> None:
        source = make_cache(self.isolated, 3)

        receipt = warm_cache.promote(
            isolated_user_root=self.isolated,
            warm_cache_root=self.warm,
            title_id=TITLE_ID,
            regenerated=False,
        )

        target = warm_cache.title_cache(self.warm, TITLE_ID)
        self.assertEqual(receipt["status"], "promoted")
        self.assertEqual(warm_cache.inventory(target), warm_cache.inventory(source))
        self.assertEqual((target / "g_0000000000000002.key").read_bytes(), b"key-2")

    def test_incomplete_candidate_is_rejected_without_touching_target(self) -> None:
        target = make_cache(self.warm, 2)
        make_cache(self.isolated, 2, profile=False)
        before = sorted(path.name for path in target.iterdir())

        with self.assertRaisesRegex(warm_cache.WarmCacheError, "incomplete"):
            warm_cache.promote(
                isolated_user_root=self.isolated,
                warm_cache_root=self.warm,
                title_id=TITLE_ID,
                regenerated=True,
            )

        self.assertEqual(sorted(path.name for path in target.iterdir()), before)

    def test_symlink_candidate_is_rejected_without_touching_target(self) -> None:
        target = make_cache(self.warm, 2)
        source = make_cache(self.isolated, 1)
        (source / "linked.key").symlink_to(source / "g_0000000000000000.key")
        before = warm_cache.inventory(target)

        with self.assertRaisesRegex(warm_cache.WarmCacheError, "incomplete"):
            warm_cache.promote(
                isolated_user_root=self.isolated,
                warm_cache_root=self.warm,
                title_id=TITLE_ID,
                regenerated=True,
            )

        self.assertEqual(warm_cache.inventory(target), before)

    def test_source_mutation_during_copy_is_rejected(self) -> None:
        target = make_cache(self.warm, 2)
        source = make_cache(self.isolated, 3)
        before = warm_cache.inventory(target)
        original_copy = warm_cache.shutil.copy2

        def mutating_copy(source_path: str, target_path: str, **kwargs: object) -> str:
            result = original_copy(source_path, target_path, **kwargs)
            if Path(source_path).name == "profile.bin":
                (source / "g_0000000000000002.key").write_bytes(b"changed")
            return result

        with mock.patch.object(warm_cache.shutil, "copy2", side_effect=mutating_copy):
            with self.assertRaisesRegex(warm_cache.WarmCacheError, "does not match"):
                warm_cache.promote(
                    isolated_user_root=self.isolated,
                    warm_cache_root=self.warm,
                    title_id=TITLE_ID,
                    regenerated=True,
                )

        self.assertEqual(warm_cache.inventory(target), before)

    def test_unchanged_candidate_does_not_replace_larger_cache(self) -> None:
        target = make_cache(self.warm, 3)
        make_cache(self.isolated, 2)
        (target / "sentinel").write_bytes(b"keep")

        receipt = warm_cache.promote(
            isolated_user_root=self.isolated,
            warm_cache_root=self.warm,
            title_id=TITLE_ID,
            regenerated=False,
        )

        self.assertEqual(receipt["status"], "unchanged")
        self.assertTrue((target / "sentinel").is_file())

    def test_regenerated_candidate_replaces_incompatible_larger_cache(self) -> None:
        make_cache(self.warm, 3)
        make_cache(self.isolated, 1)

        receipt = warm_cache.promote(
            isolated_user_root=self.isolated,
            warm_cache_root=self.warm,
            title_id=TITLE_ID,
            regenerated=True,
        )

        self.assertEqual(receipt["status"], "promoted")
        self.assertEqual(warm_cache.inventory(warm_cache.title_cache(self.warm, TITLE_ID)).pipeline_keys, 1)


if __name__ == "__main__":
    unittest.main()
