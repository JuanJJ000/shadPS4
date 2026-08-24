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

Run interactively until the emulator exits:

```bash
SECOND_SON_CAPTURE_SECONDS=0 deck_tools/run_second_son_bazzite.sh
```

Override `SECOND_SON_BINARY`, `SECOND_SON_EBOOT`, `SECOND_SON_LIVE_USER_ROOT`, or
`SECOND_SON_BAZZITE_DATA_ROOT` for a different local layout. A captured run is accepted as
title-configured only when `evidence/title-config-proof.txt` includes
`Game-specific config used: true`.
