<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS Thin1DThin deswizzle/retile proof

This read-only tool proves the storage permutation of the complete `BITMAP` payloads classified by
the exact XPPS/eboot toolchain. It does not export images or modify an owned dump.

## Blocked question

Can every accepted mip in the exact owned Second Son v1.00 `BITMAP` set be transformed from PS4
`Thin1DThin` microtiled storage to padded linear storage and back without changing one byte, using a
Morton permutation independently reconciled with shadPS4's host detiler shaders?

## Local code authority

The transform follows only repository-defined behavior:

- `src/video_core/host_shaders/tiling.comp`: `Array1DTiledThin1` addresses 8x8 microtiles in
  row-major order and interleaves `x0,y0,x1,y1,x2,y2` inside each thin microtile;
- `src/video_core/host_shaders/detilers/micro_32bpp.comp`, `micro_64bpp.comp`, and
  `micro_128bpp.comp`: all three contain the same 64-entry inverse-Morton lookup table;
- `second_son_xpps_bitmap_descriptors.py`: exact source identity, accepted formats, block geometry,
  aligned storage pitch/height, mip ranges, and complete payload hashes.

The tool parses and reconciles the inverse-Morton words from all three host shaders at runtime. It
does not treat its own address function as independent evidence for itself.

## Contract

### Inputs and inherited proof

- One regular, nonsymlink XPPS and eboot accepted by the exact `BITMAP` descriptor classifier.
- Exact lowercase whole-file XPPS and CUSA00223 01.00 eboot SHA-256 values.
- One zero-based high-kind-2 DIC row.
- Repository host-detiler sources containing exactly one common 16-word inverse-Morton table.
- At most 256 descriptors, 4,096 mips, 64 MiB in any one mip, and 512 MiB total transformed bytes.
- Synthetic-only resolver and shader-root overrides through the Python call boundary; the CLI uses
  the exact executable gates and its own repository shader tree.

The owned sources are opened read-only without following symlinks, hashed before use, then
re-hashed and re-statted after the complete transform. The host shader sources are bounded to
64 KiB each and are also opened without following symlinks.

### Host-shader permutation proof

Each detiler source must contain exactly 16 hexadecimal u32 words in `rmort[16]`. The three word
arrays must be identical. Unpacking each word little-endian by byte yields 64 `(column,row)`
coordinates indexed by tiled element order.

The coordinates must:

1. stay inside one 8x8 microtile;
2. form a complete, duplicate-free 64-coordinate permutation; and
3. exactly invert the independently calculated thin Morton index
   `x0 | y0<<1 | x1<<2 | y1<<3 | x2<<4 | y2<<5`.

The report hashes both the common LUT words and the unpacked coordinate permutation. Raw shader
source is not copied into the report.

### Per-mip transform

Only the accepted 32-, 64-, and 128-bit element paths are supported. For BC1 and BC4, one element
is one 8-byte 4x4 block; for BC3 and BC5 it is one 16-byte 4x4 block; for RGBA8 it is one 4-byte
texel. The inherited aligned storage pitch and height must both be positive multiples of eight,
and their product times the element size must exactly equal the inherited mip byte range.

For each padded storage coordinate `(x,y)`:

- the linear byte offset is `(y * aligned_pitch + x) * element_bytes`;
- the tiled byte offset is
  `((tile_y * tiles_per_row + tile_x) * 64 + morton(x&7,y&7)) * element_bytes`.

The complete tiled mip is deswizzled into padded row-major storage. That storage is then retiled
through the inverse direction. The retiled bytes must equal the original mip byte-for-byte, not
merely in length or aggregate hash. Every mip records tiled, padded-linear, and retiled SHA-256
values plus geometry; no bytes are emitted.

### Inherited-report validation

Before reading payload bytes, the tool independently revalidates the relevant descriptor report:

- exact schema, proof class, source hashes, tile/array mode, and 8x8 microtile contract;
- canonical descriptor and mip counts under the configured budgets;
- accepted format/element-size pairs and one-sample, one-layer descriptor fields;
- contiguous per-payload mip offsets and exact absolute/data-relative range arithmetic;
- aligned dimensions, per-mip byte arithmetic, payload sums, nonoverlap, and aggregate totals.

Each inherited complete-payload SHA-256 must match the bytes read during this proof.

### Deterministic output

Sorted UTF-8 JSON with a trailing newline contains:

- schema/version and proof class `xpps_thin1d_roundtrip`;
- exact source identities and inherited proof identity;
- host-shader LUT agreement, permutation hashes, and coordinate count;
- ordered descriptors and mips with format, element width, padded geometry, tiled/linear/retiled
  hashes, and an exact-roundtrip boolean;
- aggregate descriptor, mip, byte, and element-width counts;
- warnings and explicit non-claims.

No timestamp, inode, host path, shader source text, descriptor word, texel/block byte, decoded pixel,
artwork name, or output file is emitted.

### Failure behavior

Inherited proof failure, malformed report, shader LUT disagreement, incomplete/duplicate Morton
permutation, unsupported element width, noncanonical ranges, overflow, population or byte budget,
truncated input, payload-hash mismatch, roundtrip mismatch, source mutation, symlink, or I/O failure
returns nonzero and emits no JSON report.

### Acceptance tests

- independent 32-, 64-, and 128-bit synthetic permutations with known coordinate patterns;
- multi-tile and multi-mip padded layouts, including BC block geometry;
- exact deterministic integration through the synthetic XPPS/eboot fixture;
- host-LUT disagreement, malformed inherited report, bad range/size/hash, and roundtrip refusal;
- descriptor, mip, per-mip-byte, and total-byte budgets;
- symlink and source-mutation refusal;
- normal and Python `-O` execution;
- one exact-hash owned result covering all 12 descriptors and every complete mip.

## Exact owned acceptance result

Pending implementation and the exact-hash local run. The result will be recorded here only after
every accepted mip has a byte-exact roundtrip receipt.

## Route back to modding

After this proof passes, a separate lane may crop padded linear storage to logical texel/block
dimensions and wrap one owned texture in a standard container such as DDS. Pixel decoding,
channel/color interpretation, artwork identification, edited-image validation, retile of a
replacement, XPPS rebuilding, runtime overlay activation, rollback, and gameplay testing remain
separate gates.

## Usage

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_thin1d_roundtrip.py
```

```sh
deck_tools/second_son_xpps_thin1d_roundtrip.py /path/to/owned/file.xpps \
  --expected-xpps-sha256 64-lowercase-hex-characters \
  --row 2 \
  --eboot /path/to/owned/eboot.bin \
  --expected-eboot-sha256 64-lowercase-hex-characters
```

## Non-claims

A byte-exact padded-storage roundtrip does not prove pixel-channel interpretation, alpha semantics,
color space, logical-edge cropping, decoded-image correctness, artwork identity, editable-image
roundtrip, safe replacement, XPPS rebuild, runtime overlay activation, injection support, or game
runtime behavior.
