<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# inFAMOUS Second Son on Bazzite

`run_second_son_bazzite.sh` creates a disposable shadPS4 user profile for a direct-binary
Linux capture. It copies the live save and warmed CUSA00223 pipeline cache, then writes only to
the copy. The version-controlled title and input profiles are installed after seeding.

The important distinction is that `XDG_DATA_HOME` selects shadPS4's user/configuration root.
`--override-root` selects the guest game mount and is not a configuration-isolation option.
The launcher also omits `--config-global`, allowing `custom_configs/CUSA00223.json` to load.

Validate paths and isolation without launching:

```bash
SECOND_SON_VALIDATE_ONLY=1 deck_tools/run_second_son_bazzite.sh
```

Capture two minutes with the title profile's readback policy. The checked-in Bazzite profile selects
a 128-operation non-blocking work-submit budget for CUSA00223; the global default remains disabled:

```bash
SECOND_SON_CAPTURE_SECONDS=120 deck_tools/run_second_son_bazzite.sh
```

Use an explicit control or calibration value without changing the profile:

```bash
SECOND_SON_READBACK_WORK_BUDGET=0 SECOND_SON_CAPTURE_SECONDS=120 \
  deck_tools/run_second_son_bazzite.sh
SECOND_SON_READBACK_WORK_BUDGET=256 SECOND_SON_CAPTURE_SECONDS=120 \
  deck_tools/run_second_son_bazzite.sh
```

The launcher records `profile` when it defers to the game-specific setting and only exports
`SHADPS4_PRECISE_READBACK_WORK_BUDGET` for an explicit override. This keeps normal Steam-profile
behavior title-specific while preserving an exact A/B control.

## Fidelity and high-refresh matrix

Every fidelity override is applied only to the disposable per-run title profile. `profile` leaves
the checked-in value unchanged. Supported internal/output presets are `720p`, `1080p`, `1440p`,
`4k`, and `8k`; supported vblank frequencies are 30, 50, 60, 90, 120, and 144 Hz. Each run records
the request, changed JSON keys, and fully resolved values in `evidence/fidelity-profile.json`.

Test a sharper 1080p guest surface at 1440p output without changing the live Steam profile:

```bash
SECOND_SON_INTERNAL_RESOLUTION=1080p SECOND_SON_OUTPUT_RESOLUTION=1440p \
  SECOND_SON_SCREENSHOT_AFTER_SECONDS=25 \
  SECOND_SON_VIDEOOUT_STATS_INTERVAL=5 SECOND_SON_PIPELINE_TRACE=1 \
  SECOND_SON_CAPTURE_SECONDS=120 deck_tools/run_second_son_bazzite.sh
```

`SECOND_SON_SCREENSHOT_AFTER_SECONDS=1..600` requests a PNG from the emulator's present thread,
independent of desktop focus or screenshot portals. `SECOND_SON_SCREENSHOT_MODE=game|overlay|both`
selects the pre-scaling guest buffer, the displayed post-scaling swapchain, or both. The request and
saved paths are recorded in the title log; a zero delay disables timed capture.

Request a real nested 4K/120 surface through Gamescope, with cadence counters that distinguish host
vblanks from guest-produced frames:

```bash
SECOND_SON_INTERNAL_RESOLUTION=1080p SECOND_SON_OUTPUT_RESOLUTION=4k \
  SECOND_SON_VBLANK_FREQUENCY=120 SECOND_SON_GAMESCOPE=1 \
  SECOND_SON_VIDEOOUT_STATS_INTERVAL=5 SECOND_SON_PIPELINE_TRACE=1 \
  SECOND_SON_CAPTURE_SECONDS=120 deck_tools/run_second_son_bazzite.sh
```

The 8K path is deliberately bounded and remains a stress experiment. Start with 1080p internal
rendering and 8K output scaling; test 4K/8K internal rendering only after memory and stability gates:

```bash
SECOND_SON_INTERNAL_RESOLUTION=1080p SECOND_SON_OUTPUT_RESOLUTION=8k \
  SECOND_SON_VBLANK_FREQUENCY=60 SECOND_SON_GAMESCOPE=1 \
  SECOND_SON_VIDEOOUT_STATS_INTERVAL=5 SECOND_SON_CAPTURE_SECONDS=30 \
  deck_tools/run_second_son_bazzite.sh
```

Additional controlled selectors are `SECOND_SON_FSR=profile|on|off`,
`SECOND_SON_RCAS=profile|on|off`, `SECOND_SON_RCAS_ATTENUATION=profile|0..3000`, and
`SECOND_SON_PRESENT_MODE=profile|Mailbox|Fifo|Immediate`. Set
`SECOND_SON_GAMESCOPE_ADAPTIVE_SYNC=1` only for a separate VRR candidate. The stable profile is not
promoted until screenshots, actual swapchain extent, guest flip cadence, game speed, audio, and QTE
behavior all agree.

`evidence/title-config-proof.txt` records the reported guest display size, the game's actual
registered output-buffer dimensions, FSR/RCAS state, requested and actual Vulkan swapchain extents,
guest refresh-rate code and flip-rate request, plus opt-in `VideoOut cadence` intervals. A 120-Hz
host presentation count is not treated as 120 FPS unless the guest-flip rate also approaches 120
without game-speed or audio distortion.

On a Wayland desktop, capture the focused run through a temporary project-local ydotool keyboard:

```bash
deck_tools/capture_second_son.sh game
deck_tools/capture_second_son.sh overlay
```

`game` sends shadPS4's F12 game-only capture; `overlay` sends Alt+F12 and includes emulator overlays.
The helper shuts the temporary input daemon down immediately after the key chord. X11 sessions
continue to use `xdotool`.

Create a controlled cold/warm pipeline pair without touching the live Steam cache. The cold run
starts with no title cache; the warm run copies only the cold run's generated cache while still
copying the authoritative save and configuration from the live profile:

```bash
SECOND_SON_SKIP_CACHE_SEED=1 SECOND_SON_PIPELINE_TRACE=1 \
  SECOND_SON_CAPTURE_SECONDS=120 deck_tools/run_second_son_bazzite.sh
cold_run="$(readlink -f scratch/second-son-bazzite/current)"
SECOND_SON_CACHE_SEED_ROOT="${cold_run}/xdg-data/shadPS4" SECOND_SON_PIPELINE_TRACE=1 \
  SECOND_SON_CAPTURE_SECONDS=120 deck_tools/run_second_son_bazzite.sh
```

Each traced run writes `evidence/pipeline-cache-summary.txt` and a hash-only event log. The trace
changes only the disposable profile's log filter; it does not change rendering, readback, or image
quality settings. `SECOND_SON_CACHE_SEED_ROOT` never replaces the live save source.

Run the guarded, opt-in 01.00 motion-blur exposure experiment:

```bash
SECOND_SON_PATCH=deck_tools/second_son_v100_motion_blur.xml \
  SECOND_SON_CAPTURE_SECONDS=120 deck_tools/run_second_son_bazzite.sh
```

The launcher refuses that patch unless the complete owned eboot hash, original setter bytes, patch
identity, title, and app version pass `second_son_v100_patch_guard.py`. Unset `SECOND_SON_PATCH` for
the unchanged baseline. See `SECOND_SON_V100_MOTION_BLUR.md` for evidence and non-claims.

Run interactively until the emulator exits:

```bash
SECOND_SON_CAPTURE_SECONDS=0 deck_tools/run_second_son_bazzite.sh
```

Override `SECOND_SON_BINARY`, `SECOND_SON_EBOOT`, `SECOND_SON_LIVE_USER_ROOT`, or
`SECOND_SON_BAZZITE_DATA_ROOT` for a different local layout. Use
`SECOND_SON_CACHE_SEED_ROOT` only to seed a known prior capture's cache. A captured run is accepted
as title-configured only when `evidence/title-config-proof.txt` includes
`Game-specific config used: true`.
