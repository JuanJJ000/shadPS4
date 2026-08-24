<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son reversible DDS-to-XPPS overlay

This opt-in tool applies strictly compatible edited DDS logical mip bytes to a fresh, same-size
copy of one proven Second Son XPPS. It never modifies, renames, deletes, or relinks the owned game
tree. The copy is an offline mod artifact, not automatic runtime activation.

## Blocked question

Can an edited logical DDS be returned to the exact Thin1D guest layout while retaining every byte
of pitch, power-of-two, and microtile padding—and can the resulting container change be proven to
touch nothing outside the selected mip ranges?

## Inherited authority

The tool reruns and correlates the complete exact chain:

- the XPPS package/DIC scanner and bounded extractor;
- the eboot registry and exact type-name resolver;
- the `BITMAP` descriptor/payload classifier;
- the host-shader-derived Thin1D deswizzle plus independent Morton retile proof;
- the guarded DDS export manifest and every baseline DDS byte.

The supplied `manifest.json` must be canonical sorted JSON and byte-identical to a manifest rebuilt
from the current source/proofs. Its directory must contain exactly that manifest and the complete
proven DDS population. A stale, edited, partial, extended, symlinked, or path-swapped baseline is
refused.

## Contract

### Inputs

- One regular, nonsymlink XPPS and CUSA00223 01.00 eboot with explicit lowercase whole-file
  SHA-256 values and one zero-based high-kind-2 DIC row.
- The exact guarded DDS export `manifest.json` and its unmodified sibling DDS files.
- One regular, nonsymlink edit directory containing a nonempty subset of those exact DDS basenames
  and no other entries.
- One output path whose parent is an existing nonsymlink directory and whose final component does
  not exist.
- At most 512 MiB source, 128 MiB eboot, 512 MiB edited DDS, 512 MiB changed bytes, one million
  changed ranges, and a 16 MiB receipt.

Every input directory remains open and its descriptor/path identity is revalidated. Every file is
opened relative to that descriptor with `O_NOFOLLOW`, bounded by its expected size, read completely,
and revalidated by device, inode, size, mtime, and ctime. Duplicate JSON keys, nonfinite JSON,
noncanonical encoding, unexpected directory entries, and source mutation are refused.

The command-line interface refuses any DDS that is byte-identical to its baseline. The Python test
boundary has an explicit `allow_identical_edits=True` proof mode so exact no-op reconstruction can
be verified without making no-op CLI mod packs ambiguous.

### DDS compatibility

An edited DDS must preserve the complete 148-byte canonical DX10 header and strict parsed structure:

- format and DXGI mapping;
- `TEXTURE1D` or `TEXTURE2D` resource dimension;
- width, height, mip count, array size, flags, caps, pitch/linear size;
- every logical mip byte range and exact end-of-file.

Only bytes inside those logical mip ranges may differ. Header changes, a different encoder layout,
missing/trailing bytes, unsupported format, renamed files, and unrecognized entries fail closed.

### Logical overlay and Thin1D retile

For each mip, the tool reads the original tiled bytes from the proven absolute XPPS range and
requires the inherited tiled SHA-256. It deswizzles with the exact permutation parsed from all three
current 32/64/128-bit host detiler shaders and requires the inherited padded-linear SHA-256.

Each edited DDS logical row replaces only the corresponding prefix of its padded-linear row:

- RGBA8 uses logical texel rows;
- BC1/BC3/BC4/BC5 use complete logical block rows;
- the row stride remains the inherited aligned-storage pitch;
- bytes after logical row width and below logical row height remain original.

The concatenated padding byte count/hash is identical before and after. The patched buffer is
retiled with the independently derived Morton formula, deswizzled again, and required to reproduce
the patched padded-linear buffer byte-for-byte. A permutation must preserve the logical/tiled
changed-byte count.

Only mips with at least one changed logical byte are written into the in-memory XPPS copy. All gaps
and the tail outside those full mip ranges must remain byte-identical. A final whole-container diff
must equal the sum of proven tiled changes and remain within the byte/range budgets.

### Output transaction and receipt

The output directory is created mode 0700 relative to an opened parent descriptor. `overlay.xpps`
and `receipt.json` are created once with `O_EXCL|O_NOFOLLOW`, mode 0600, fully written, synced,
reopened, and compared. Their creation descriptors remain open through commit or cleanup, so an
unlinked inode cannot be recycled beneath a tracked basename. Full identities are revalidated.

On failure, cleanup unlinks only a name still bound to the exact open file created by this
invocation. An external replacement is preserved and the nonempty directory is retained. No
preexisting path is deleted or overwritten.

The deterministic receipt contains no host path or owned bytes. It records:

- exact source, eboot, inherited report, DDS-manifest, and host-shader identities;
- baseline and edited DDS hashes;
- logical/tiled changed byte and contiguous-range counts for every mip;
- source/overlay padded-linear and tiled hashes;
- padding byte count/hash and absolute XPPS target range geometry;
- aggregate non-target hash, output size/hash, warnings, and explicit non-claims.

## Acceptance tests

- RGBA8, BC1, BC3, BC4, and BC5 with both Color1D and Color2D resources;
- single- and multi-mip chains plus logical rows narrower/shorter than padded storage;
- deterministic changed and explicitly allowed no-op overlays;
- exact logical/tiled changed-byte equality, padding identity, reverse deswizzle, non-target hash,
  and same-size output;
- canonical-manifest and complete-baseline reconstruction;
- unknown, identical, symlinked, trailing, and structurally incompatible edits;
- source/edit/change budgets, source mutation, short write, collision, and output path replacement;
- normal and Python `-O` execution.

## Exact owned one-byte proof

The tool accepted the exact owned `graffiti_a8_family.xpps`/eboot pair and guarded export manifest
SHA-256 `fd98abd613ecdb5746c9b17f2c4c9f35f9e3ca971788326f3ff06bbd420db826`.
The local edit changed one byte in mip zero of `bitmap_000_0000a0f0_bc3.dds`; no owned source was
modified.

Two fresh output directories reproduced byte-identical results:

- source: 7,974,032 bytes, SHA-256
  `254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7`;
- overlay: 7,974,032 bytes, SHA-256
  `f0f8667148b50521ec28ba25ec3191a7530da8c6293663a5e3eeb773b1d20731`;
- receipt: SHA-256
  `4f484c038b24b623a1561918e7c078e0e74ee2ce3ccec92672e52ecaac40faca`;
- changed logical/tiled/container bytes: one / one / one, each one contiguous range;
- changed mips: one;
- bytes outside the changed mip range: 5,876,880, all exact, aggregate SHA-256
  `c6b8fcd7219aca682c1d30345bbd815670a945464ff933ff28bbfc3a88f92dba`;
- all padding exact and the retiled mip deswizzled back exactly.

The explicit Python-only no-op proof also rebuilt the complete owned source byte-for-byte: zero
changed mips/bytes/ranges, all 7,974,032 bytes in the non-target proof, overlay SHA-256 equal to the
source SHA-256 `254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7`, and receipt SHA-256
`856bb19609d1ff3823eb3a1ef007b59dff0cf8abb4381fd9481648cdece53549`.

The DDS edit, resulting XPPS copies, and receipts remain only in ignored local scratch. They are not
committed or distributed.

## Route back to the game

This proves a reversible texture-container artifact. The next lane should activate a selected
overlay through an isolated game-tree shadow or a title-scoped shadPS4 redirection, with an exact
source/overlay receipt gate and one-command rollback. PNG/BC editing and runtime visual validation
remain separate proofs.

## Usage

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_dds_overlay.py
```

```sh
deck_tools/second_son_xpps_dds_overlay.py /path/to/owned/file.xpps \
  --expected-xpps-sha256 64-lowercase-hex-characters \
  --row 2 \
  --eboot /path/to/owned/eboot.bin \
  --expected-eboot-sha256 64-lowercase-hex-characters \
  --export-manifest /path/to/proven-dds/manifest.json \
  --edits-dir /path/to/edited-dds-subset \
  --output-dir /path/to/new-overlay-output
```

## Non-claims

An exact reversible overlay does not prove artwork identity, channel swizzle, alpha/color-space
semantics, decoded visual correctness, PNG or block-compression encoder equivalence, arbitrary XPPS
support, runtime activation, or game behavior.
