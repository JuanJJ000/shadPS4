# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Apply bounded, disposable Second Son fidelity overrides to an isolated JSON profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


RESOLUTIONS: dict[str, tuple[int, int]] = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
    "8k": (7680, 4320),
}
VBLANK_FREQUENCIES = {30, 50, 60, 90, 120, 144}
PRESENT_MODES = {"Mailbox", "Fifo", "Immediate"}


class ProfileError(ValueError):
    """Raised when a requested override is outside the controlled matrix."""


def _resolution(value: str) -> tuple[int, int] | None:
    if value == "profile":
        return None
    try:
        return RESOLUTIONS[value]
    except KeyError as exc:
        choices = ", ".join(("profile", *RESOLUTIONS))
        raise ProfileError(f"resolution must be one of: {choices}") from exc


def _switch(value: str, name: str) -> bool | None:
    if value == "profile":
        return None
    if value == "on":
        return True
    if value == "off":
        return False
    raise ProfileError(f"{name} must be profile, on, or off")


def apply_overrides(
    config: dict[str, Any],
    *,
    internal_resolution: str = "profile",
    output_resolution: str = "profile",
    vblank_frequency: str = "profile",
    fsr: str = "profile",
    rcas: str = "profile",
    rcas_attenuation: str = "profile",
    present_mode: str = "profile",
) -> dict[str, Any]:
    gpu = config.setdefault("GPU", {})
    if not isinstance(gpu, dict):
        raise ProfileError("GPU must be a JSON object")

    requested = {
        "internal_resolution": internal_resolution,
        "output_resolution": output_resolution,
        "vblank_frequency": vblank_frequency,
        "fsr": fsr,
        "rcas": rcas,
        "rcas_attenuation": rcas_attenuation,
        "present_mode": present_mode,
    }
    changed: dict[str, dict[str, Any]] = {}

    def set_gpu(key: str, value: Any) -> None:
        before = gpu.get(key)
        gpu[key] = value
        if before != value:
            changed[key] = {"before": before, "after": value}

    internal = _resolution(internal_resolution)
    if internal is not None:
        set_gpu("internal_screen_width", internal[0])
        set_gpu("internal_screen_height", internal[1])

    output = _resolution(output_resolution)
    if output is not None:
        set_gpu("window_width", output[0])
        set_gpu("window_height", output[1])

    if vblank_frequency != "profile":
        try:
            frequency = int(vblank_frequency)
        except ValueError as exc:
            raise ProfileError("vblank frequency must be numeric or profile") from exc
        if frequency not in VBLANK_FREQUENCIES:
            choices = ", ".join(str(item) for item in sorted(VBLANK_FREQUENCIES))
            raise ProfileError(f"vblank frequency must be profile or one of: {choices}")
        set_gpu("vblank_frequency", frequency)

    fsr_value = _switch(fsr, "FSR")
    if fsr_value is not None:
        set_gpu("fsr_enabled", fsr_value)

    rcas_value = _switch(rcas, "RCAS")
    if rcas_value is not None:
        set_gpu("rcas_enabled", rcas_value)

    if rcas_attenuation != "profile":
        try:
            attenuation = int(rcas_attenuation)
        except ValueError as exc:
            raise ProfileError("RCAS attenuation must be an integer or profile") from exc
        if not 0 <= attenuation <= 3000:
            raise ProfileError("RCAS attenuation must be from 0 through 3000")
        set_gpu("rcas_attenuation", attenuation)

    if present_mode != "profile":
        if present_mode not in PRESENT_MODES:
            choices = ", ".join(sorted(PRESENT_MODES))
            raise ProfileError(f"present mode must be profile or one of: {choices}")
        set_gpu("present_mode", present_mode)

    resolved = {
        "internal_resolution": [
            gpu.get("internal_screen_width", 1280),
            gpu.get("internal_screen_height", 720),
        ],
        "output_resolution": [gpu.get("window_width", 1280), gpu.get("window_height", 720)],
        "vblank_frequency": gpu.get("vblank_frequency", 60),
        "fsr": gpu.get("fsr_enabled", False),
        "rcas": gpu.get("rcas_enabled", True),
        "rcas_attenuation": gpu.get("rcas_attenuation", 250),
        "present_mode": gpu.get("present_mode", "Mailbox"),
    }
    return {"requested": requested, "changed": changed, "resolved": resolved}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--internal-resolution", default="profile")
    parser.add_argument("--output-resolution", default="profile")
    parser.add_argument("--vblank-frequency", default="profile")
    parser.add_argument("--fsr", default="profile")
    parser.add_argument("--rcas", default="profile")
    parser.add_argument("--rcas-attenuation", default="profile")
    parser.add_argument("--present-mode", default="profile")
    args = parser.parse_args()

    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        receipt = apply_overrides(
            config,
            internal_resolution=args.internal_resolution,
            output_resolution=args.output_resolution,
            vblank_frequency=args.vblank_frequency,
            fsr=args.fsr,
            rcas=args.rcas,
            rcas_attenuation=args.rcas_attenuation,
            present_mode=args.present_mode,
        )
    except (OSError, json.JSONDecodeError, ProfileError) as exc:
        parser.error(str(exc))

    args.config.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
