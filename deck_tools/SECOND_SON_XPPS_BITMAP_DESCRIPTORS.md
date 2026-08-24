<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS BITMAP descriptor and payload classifier

This read-only tool proves where named `BITMAP` descriptors and their complete tiled mip payloads
live in one exact owned XPPS; it does not yet turn those bytes into viewable images.

## Blocked question

Do the DIC targets proven as `BITMAP` by the exact eboot resolver contain repository-defined
`AmdGpu::Image` descriptors, and do their serialized base fields plus shadPS4 sizing rules select
complete bounded payload ranges?

## Local code authority

The decoder mirrors only structures and calculations already used by shadPS4:

- `src/video_core/amdgpu/resource.h`: the four-u64/32-byte `AmdGpu::Image` bit layout, image types,
  address field, pitch, level, layer, format, and tile accessors;
- `src/video_core/amdgpu/pixel_format.h` and `.cpp`: observed data-format IDs, block coding, and bits
  per block;
- `src/video_core/amdgpu/tiling.h` and `.cpp`: tile mode 13 `Thin1DThin` maps to
  `Array1DTiledThin1`;
- `src/video_core/texture_cache/image_info.cpp` and `tile.h`: block-dimension conversion,
  per-mip pitch/height shifts, 8x8 microtile alignment, and 256-byte slice alignment.

The tool implements no general PS4 texture API and does not silently fall back to a different tile
path.

## Contract

### Inputs and inherited proof

- One regular, nonsymlink XPPS accepted by the structure, chunk, target, registry, and exact
  type-name tools.
- Exact lowercase whole-file XPPS and CUSA00223 01.00 eboot SHA-256 values.
- One zero-based high-kind-2 DIC row.
- At most 256 selected `BITMAP` entries and 512 MiB total classified payload bytes.
- Synthetic-only resolver-address overrides are accepted through the Python call boundary; the CLI
  keeps every exact executable gate from `second_son_xpps_eboot_type_names.py`.

Both sources are opened read-only without following symlinks, hashed before use, then re-hashed and
re-statted after classification.

### Name and descriptor selection

The exact type-name report must contain at least one resolution named exactly `BITMAP`. Only target
observations whose DIC hash is in that proven set are selected. Target aliases are retained but one
physical descriptor is decoded once.

Each selected target must be in a validated high-kind-0 metadata row. Exactly 32 bytes beginning at
target plus eight are interpreted as four little-endian u64 words using the repository bit layout.
The descriptor itself must remain fully inside the same row. Its SHA-256 is emitted; its raw words
are not.

### Accepted descriptor subset

This first classifier accepts the exact subset observed in the owned graffiti sample:

- image type 8 `Color1D` or 9 `Color2D`;
- number format 0 `Unorm`;
- data format 10 `Format8_8_8_8`, 35 `FormatBc1`, 37 `FormatBc3`, 38 `FormatBc4`, or 39
  `FormatBc5`;
- tile mode 13 `Thin1DThin`, one sample, one layer, one depth slice, base level zero, optional
  repository-defined power-of-two padding, and no Neo compression/alternate-tile flags;
- positive width, height, pitch, and level count, with pitch at least width; `Color1D` also requires
  height one.

Reserved bits in the third and fourth u64 words must be zero. Unknown formats, image types, tile
modes, array/depth layouts, or flags are refused instead of approximated.

### Serialized payload relationship

The descriptor's 38-bit base field is treated only as an observed XPPS data-relative offset. After
adding the validated XPPS data start, the complete computed range must fit in exactly one
high-kind-1 payload row.

For every mip the classifier mirrors the applicable `ImageInfo::UpdateSize` path:

1. shift descriptor pitch and height by the mip index, clamping each to one;
2. for BC formats, convert each dimension to 4x4 block units with ceiling division;
3. when the descriptor requests it, round each storage dimension to its next power of two;
4. align both storage dimensions to an 8x8 microtile;
5. grow aligned pitch in eight-element steps until the slice is 256-byte aligned;
6. append that bounded slice to the one-layer payload.

All descriptor payloads must be nonempty, stay under the configured total budget, and be pairwise
nonoverlapping. The report hashes each complete payload, records each mip range, and marks exact
adjacency or the nonnegative gap to the next descriptor base.

### Deterministic output

Sorted UTF-8 JSON with a trailing newline contains:

- schema/version and proof class `xpps_bitmap_descriptor_classifier`;
- exact source identities, selected DIC row, and proven `BITMAP` hash set;
- validated layout and decoder-contract facts;
- ordered target/alias identity, descriptor SHA-256 and decoded fields;
- exact per-mip and complete payload ranges, owning row, payload SHA-256, and adjacency;
- aggregate type/format counts, warnings, and explicit non-claims.

No timestamps, inodes, host paths, raw descriptor words, raw texels, inferred artwork names, or
output files are emitted.

### Failure behavior

Inherited proof failure, missing `BITMAP` name, malformed report, excess population, incomplete
descriptor, invalid/reserved fields, unsupported format/type/tile path, size/range overflow,
wrong-row ownership, payload overlap, exceeded budget, source mutation, or I/O failure returns
nonzero and emits no JSON report.

### Acceptance tests

- deterministic synthetic RGBA8 and BC1 descriptors with exact mip/payload hashes;
- descriptor field, reserved-bit, format/type/tile/pitch/level/layer refusal;
- wrong-row, range overflow, overlap, population, and total-byte budgets;
- missing/malformed inherited `BITMAP` proof;
- symlink and mutation refusal;
- normal and Python `-O` execution;
- one exact-hash owned result covering all 12 proven `BITMAP` entries.

## Exact owned acceptance result

The guarded classifier accepted the exact owned XPPS and eboot pair from the type-name proof. Two
independent runs produced report SHA-256
`aceea979ad065cd6592260d689114153907a42ce42b5061355e2ac2747da9da5`.

All 12 `BITMAP` entries are distinct targets. They decode to five formats and sizes from 1x1
through 2048x1024, occupy `0x788a00` total payload bytes, are pairwise nonoverlapping, and have
eight exactly adjacent boundaries. The complete result is:

| Data-relative start | Format | Dimensions | Levels | Payload bytes | Gap to next |
| ---: | --- | ---: | ---: | ---: | ---: |
| `0x0000a0f0` | BC3 | 2048x1024 | 12 | `0x2ac000` | `0x0` |
| `0x002b60f0` | BC3 | 2048x1024 | 12 | `0x2ac000` | `0x0` |
| `0x005620f0` | BC3 | 1024x512 | 11 | `0x0ac000` | `0x0` |
| `0x0060e0f0` | BC3 | 1024x512 | 11 | `0x0ac000` | `0x0` |
| `0x006ba0f0` | BC5 | 1024x512 | 11 | `0x0ac000` | `0x0` |
| `0x007660f0` | BC4 | 512x256 | 10 | `0x016000` | `0x0` |
| `0x0077c0f0` | BC4 | 512x256 | 10 | `0x016000` | `0x2700` |
| `0x007947f0` | BC5 | 1x1 | 1 | `0x400` | `0x900` |
| `0x007954f0` | BC1 | 1x1 | 1 | `0x200` | `0x0` |
| `0x007956f0` | BC1 | 1x1 | 1 | `0x200` | `0x1800` |
| `0x007970f0` | RGBA8 | 1x1 | 1 | `0x100` | `0x0` |
| `0x007971f0` | RGBA8 | 1x1 | 1 | `0x100` | n/a |

The first payload begins exactly at the validated high-kind-1 row start. The seven large
descriptors use power-of-two padding, and their computed chain sizes are:

- 2048x1024 BC3, 12 levels: `0x2ac000` bytes;
- 1024x512 BC3/BC5, 11 levels: `0x0ac000` bytes;
- 512x256 BC4, 10 levels: `0x016000` bytes.

The observed bases begin `0x0000a0f0`, `0x002b60f0`, `0x005620f0`, `0x0060e0f0`,
`0x006ba0f0`, `0x007660f0`, and `0x0077c0f0`; every delta through that sequence exactly equals the
preceding computed size. Each complete payload also has its own deterministic SHA-256 in the JSON
report. A wrong expected eboot hash returns status two and emits zero report bytes.

## Route back to modding

After these ranges are proven, a separate lane may implement `Thin1DThin` deswizzle and retile for
one extracted owned payload, require byte-exact round trip across every mip, and produce a viewable
image. Replacement, XPPS rebuilding, runtime overlays, and injection remain blocked until their own
rollback-safe gates.

## Usage

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_bitmap_descriptors.py
```

```sh
deck_tools/second_son_xpps_bitmap_descriptors.py /path/to/owned/file.xpps \
  --expected-xpps-sha256 64-lowercase-hex-characters \
  --row 2 \
  --eboot /path/to/owned/eboot.bin \
  --expected-eboot-sha256 64-lowercase-hex-characters
```

## Non-claims

A valid descriptor and bounded payload do not prove deswizzle order, pixel-channel interpretation,
alpha semantics, color space, artwork identity, a decoded image, safe replacement, byte-exact
retile, container rebuild, runtime overlay activation, or injection support.
