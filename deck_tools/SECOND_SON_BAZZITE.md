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

Capture two minutes with the default no-early-submit readback policy:

```bash
SECOND_SON_CAPTURE_SECONDS=120 deck_tools/run_second_son_bazzite.sh
```

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
