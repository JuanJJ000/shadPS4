<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son receipt-gated mod overlay

This title-specific tool stages a proven DDS-to-XPPS result outside the owned game tree and selects
it through shadPS4's existing `-mods` filesystem overlay. It does not swap, edit, rename, delete, or
relink retail files.

## Existing shadPS4 path

`src/core/file_sys/fs.cpp` already supplies the runtime mechanism:

1. `/app0` and `/hostapp` mounts probe a sibling `<game-root>-mods` backend;
2. the backend stack order is mods, update/patch, then base;
3. file open returns the first backend containing the requested relative path;
4. directories merge entries in the same order;
5. launching an overlay-named root rebases to the base game.

For the unpacked title root `CUSA00223`, the active entry is therefore `CUSA00223-mods`. A mod pack
only needs the relative asset path it replaces; it does not need a copied or symlinked game tree.

## Layout

Staged packs are stored in the non-runtime sibling directory:

```text
CUSA00223-modpacks/
  <pack-id>/
    .shadps4-second-son-pack.json
    .shadps4-xpps-overlay-receipt.json
    art/cache/graffiti_a8_family.xpps
```

The active entry, when deliberately enabled, is one relative symlink:

```text
CUSA00223-mods -> CUSA00223-modpacks/<pack-id>
```

The pack store is not a recognized runtime overlay. Staging alone cannot affect a game launch.

## Contract

### Stage

- Require the game-root basename `CUSA00223` as a real nonsymlink directory.
- Accept a safe lowercase pack ID and one canonical POSIX relative target: no absolute path,
  backslash, NUL, empty/dot/dot-dot component, oversized component, or more than 32 components.
- Open the prior overlay output as a nonsymlink directory containing exactly `overlay.xpps` and
  `receipt.json`.
- Require the exact DDS-overlay schema/version/proof class, canonical JSON, output size/hash, base
  XPPS basename/size/hash, and 01.00 eboot size/hash.
- Open every base component beneath the game descriptor with `O_NOFOLLOW`; require the target
  basename and complete base/eboot bytes to match the receipt.
- Create the pack and all target directories mode 0700. Create its target and two manifests once,
  mode 0600, with the same guarded write/reopen/full-identity transaction as the DDS overlay tools.
- Copy the complete overlay receipt into the pack. The pack manifest binds its hash, output
  identity, base identities, target, selector, title, and precedence.
- Reopen and verify the staged target, copied receipt, manifest, exact directory population, base
  source, and eboot before reporting success.

On failure, cleanup removes only files and directories still bound to the open objects created by
this invocation. A preexisting pack/store/active entry or externally replaced path is preserved.
If the store existed before the command, it is never cleanup-owned.

### Status

Status is read-only. It revalidates the canonical pack manifest, copied overlay receipt and its
hash, overlay target size/hash, receipt/output/base agreement, exact nested population, current
base XPPS, and current eboot. It reports only:

- `disabled`: the active entry is absent;
- `enabled`: the active entry is the exact expected relative symlink;
- `conflict`: another symlink, regular file, or directory owns the active name.

No host path or symlink target is serialized.

### Enable and disable

Enable repeats complete status verification. It creates `CUSA00223-mods` only when absent using
exclusive symlink creation and then verifies the exact relative selector. An already selected pack
is idempotent. Any other owner is refused.

Disable repeats complete verification, requires the active entry to remain the selected symlink,
checks its device/inode/ctime binding immediately before unlink, removes only that symlink, and
confirms absence. It never removes a pack or retail path. An absent selector is idempotent; a
replacement/conflict is preserved.

## Acceptance tests

- stage/status/enable/disable/re-enable with a nested XPPS target;
- exact mod/update/base precedence recorded from the current core contract;
- source XPPS and eboot retained byte-for-byte through the full cycle;
- pack and active-name collisions preserved;
- wrong source, eboot, overlay, receipt, staged target, copied receipt, and directory population;
- path traversal, bad pack IDs, symlinked input, and unexpected overlay entries;
- short write and mid-stage source mutation with cleanup restricted to the owned fresh stage;
- normal and Python `-O` execution.

## Exact owned disabled stage

The exact one-byte graffiti proof from the reversible XPPS lane was staged locally as pack
`graffiti-one-byte-proof`. The pack binds:

- base XPPS SHA-256 `254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7`;
- overlay SHA-256 `f0f8667148b50521ec28ba25ec3191a7530da8c6293663a5e3eeb773b1d20731`;
- overlay receipt SHA-256 `4f484c038b24b623a1561918e7c078e0e74ee2ce3ccec92672e52ecaac40faca`;
- pack manifest SHA-256 `c9c291e404006523d6261684926d6e375dfce035b2fcdd416facbc62179c8991`;
- target `art/cache/graffiti_a8_family.xpps`, 7,974,032 bytes.

Status is `disabled`, and `CUSA00223-mods` remains absent. The random one-byte proof is deliberately
not active and will not affect Steam launches. The next visual lane must create an intentional,
decoded artwork edit before enabling a pack for interactive validation.

## Usage

```sh
python3 -B -m unittest -v deck_tools/test_second_son_mod_overlay.py
```

```sh
deck_tools/second_son_mod_overlay.py stage /path/to/CUSA00223 \
  --pack-id my-pack \
  --relative-target art/cache/graffiti_a8_family.xpps \
  --overlay-dir /path/to/proven-overlay-output
```

```sh
deck_tools/second_son_mod_overlay.py status /path/to/CUSA00223 --pack-id my-pack
deck_tools/second_son_mod_overlay.py enable /path/to/CUSA00223 --pack-id my-pack
deck_tools/second_son_mod_overlay.py disable /path/to/CUSA00223 --pack-id my-pack
```

## Non-claims

Staging and selector verification do not establish artwork identity, texture semantics, a runtime
visual change, game behavior, arbitrary-title support, or permission to redistribute game assets.
