<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son 01.00 motion-blur exposure patch

## Blocked question

Can the owned CUSA00223 01.00 executable disable its motion-blur exposure setter through a
reversible shadPS4 runtime patch without modifying the game tree or silently matching another
executable?

This increment proves patch identity and application mechanics. It does not prove visual quality;
that requires a same-save, same-camera gameplay A/B.

## Evidence boundary

The owned `eboot.bin` has SHA-256
`99c7fe77f8348062cb3e0e7218c1991cda3515188f0d308b64f7dca058997d87`. Its SELF relocation
metadata uniquely associates the `MOTION_BLUR_EXPOSURE` name with a setter at guest virtual
address `0x00c5bc70`. The complete 16-byte setter is:

```text
c5 fa 11 87 c8 01 00 00 80 8f f4 00 00 00 04 c3
```

It stores one float at render-state offset `0x1c8`, sets dirty bit `0x04` at offset `0xf4`, and
returns. The proposed first eight replacement bytes zero that 32-bit field and leave the dirty-bit
instruction and return intact:

```text
83 a7 c8 01 00 00 00 90
```

## Guard contract

`second_son_v100_patch_guard.py` accepts:

- one regular, nonsymlink eboot path;
- one regular, nonsymlink patch XML path;
- an optional `--json` report mode.

It refuses input unless all of these facts are exact:

- whole-eboot SHA-256 equals the owned 01.00 hash;
- file size and SELF offset `0x8627e0` contain the complete expected setter;
- the complete setter occurs exactly once in the eboot;
- XML parses as one `Patch` document with CUSA00223 title identity;
- exactly one enabled 01.00 metadata entry names the clarity patch;
- exactly one raw-byte line targets guest address `0x00c5bc70` with the eight replacement bytes;
- no additional metadata or patch lines exist.

The source and XML are opened read-only, hashed again after validation, and required to retain
their file identity. Success emits no payload bytes in normal mode. JSON mode emits only schema,
basenames, sizes, hashes, exact accepted coordinates, facts, and explicit non-claims. It never
emits host paths or game payload data.

The guard never edits, copies, extracts, launches, or patches a file. It is an authorization gate
for a separate launcher.

## Launcher contract

`run_second_son_bazzite.sh` accepts `SECOND_SON_PATCH` only after the guard succeeds. The selected
patch hash is recorded in the disposable run manifest, and the launcher passes the same file to
shadPS4 with `--patch`. With the variable unset, the baseline path is byte-for-byte unchanged.

## Failure behavior

Bad hash, unexpected source type, symlink, wrong/missing opcode, duplicate opcode, XML parse error,
extra metadata/line, wrong title/version/address/value, source mutation, or I/O failure returns
nonzero. A failed guard never launches shadPS4.

## Acceptance tests

- exact synthetic eboot/XML pair;
- deterministic JSON and source retention;
- bad whole-file hash and wrong opcode;
- duplicate opcode and truncated source;
- symlink refusal for both inputs;
- wrong title, app version, address, value, enable state, and extra patch line;
- post-validation mutation signal;
- repository XML checked against the owned eboot;
- isolated baseline/patched boot pair with explicit patch-application log evidence;
- later interactive same-save visual A/B before default enablement.

## Rollback

Unset `SECOND_SON_PATCH` (or remove `--patch` from the Steam wrapper). The XML never touches the
owned dump, save, pipeline cache, or title configuration. A new game update must fail the old hash
guard and receive its own analysis.

## Non-claims

This work does not claim that zero exposure is visually correct, that all motion blur is removed,
that the 01.05 PS4 Pro path is present, or that resolution, textures, temporal reconstruction, and
frame pacing are changed. Those remain separate evidence lanes.

