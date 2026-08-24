# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import second_son_bazzite_profile as profile


class SecondSonBazziteProfileTests(unittest.TestCase):
    def test_profile_defaults_do_not_change_config(self) -> None:
        config = {"GPU": {"window_width": 2560, "window_height": 1440}}

        receipt = profile.apply_overrides(config)

        self.assertEqual(receipt["changed"], {})
        self.assertEqual(receipt["resolved"]["internal_resolution"], [1280, 720])
        self.assertEqual(receipt["resolved"]["output_resolution"], [2560, 1440])

    def test_complete_8k_high_refresh_override(self) -> None:
        config = {"GPU": {"window_width": 2560, "window_height": 1440}}

        receipt = profile.apply_overrides(
            config,
            internal_resolution="1080p",
            output_resolution="8k",
            vblank_frequency="120",
            fsr="off",
            rcas="on",
            rcas_attenuation="0",
            present_mode="Immediate",
        )

        self.assertEqual(config["GPU"]["internal_screen_width"], 1920)
        self.assertEqual(config["GPU"]["internal_screen_height"], 1080)
        self.assertEqual(config["GPU"]["window_width"], 7680)
        self.assertEqual(config["GPU"]["window_height"], 4320)
        self.assertEqual(config["GPU"]["vblank_frequency"], 120)
        self.assertFalse(config["GPU"]["fsr_enabled"])
        self.assertTrue(config["GPU"]["rcas_enabled"])
        self.assertEqual(config["GPU"]["rcas_attenuation"], 0)
        self.assertEqual(config["GPU"]["present_mode"], "Immediate")
        self.assertIn("window_width", receipt["changed"])

    def test_invalid_values_fail_closed(self) -> None:
        with self.assertRaisesRegex(profile.ProfileError, "resolution must be"):
            profile.apply_overrides({}, output_resolution="16k")
        with self.assertRaisesRegex(profile.ProfileError, "vblank frequency"):
            profile.apply_overrides({}, vblank_frequency="75")
        with self.assertRaisesRegex(profile.ProfileError, "RCAS attenuation"):
            profile.apply_overrides({}, rcas_attenuation="3001")
        with self.assertRaisesRegex(profile.ProfileError, "present mode"):
            profile.apply_overrides({}, present_mode="Fastest")


if __name__ == "__main__":
    unittest.main()
