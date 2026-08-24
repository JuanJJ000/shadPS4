<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS guarded DDS exporter

This opt-in tool turns the exact proven `BITMAP` mip chains into standard local DDS inspection
files. It never modifies an owned XPPS/eboot and never treats a cropped DDS as a replacement
payload.

## Blocked question

Can the complete logical image rectangles be separated from guest pitch, power-of-two padding, and
8x8 microtile padding and wrapped in deterministic DDS containers without losing track of the
original padded bytes required for a later reversible injection?

## Inherited authority

The exporter proceeds only after reproducing both exact reports:

- `second_son_xpps_bitmap_descriptors.py` supplies descriptor width, height, pitch, image type,
  accepted Unorm format, absolute mip ranges, and complete tiled hashes;
- `second_son_xpps_thin1d_roundtrip.py` proves the current host-shader Morton permutation and the
  padded-linear SHA-256 of every mip.

The two reports must agree on source identities, selected DIC row, descriptor order and hashes,
format, mip geometry, payload starts/sizes/hashes, and the complete inherited-report hash. The
exporter then re-reads and deswizzles each source mip and requires its padded-linear hash to match
the roundtrip receipt before cropping one byte.

## Contract

### Inputs and output boundary

- One regular, nonsymlink XPPS and eboot accepted by both exact inherited proofs.
- Exact lowercase whole-file XPPS and CUSA00223 01.00 eboot SHA-256 values.
- One zero-based high-kind-2 DIC row.
- One explicit output path whose parent is an existing nonsymlink directory and whose final
  component does not exist.
- At most 256 descriptors, 4,096 mips, 64 MiB in one padded mip, 512 MiB of inherited payload, and
  512 MiB of total DDS output.
- Synthetic-only resolver/shader-root overrides through the Python call boundary; the CLI keeps
  exact executable and repository-shader gates.

The output directory is created mode 0700 relative to an opened parent-directory descriptor. Every
file is created exactly once with `O_EXCL|O_NOFOLLOW`, mode 0600, completely written, synced,
reopened read-only, parsed, and hashed. Its creation descriptor remains open until transaction
commit or cleanup, preventing an unlinked inode from being recycled beneath the same basename;
the complete device/inode/size/mtime/ctime identity is revalidated. Existing output, symlinks,
nonregular files, path swaps, short writes, or unexpected directory entries are refused.

On failure, the tool removes only names it created inside that fresh directory and removes the
directory only if its descriptor identity still matches. It never deletes or overwrites a
preexisting path.

### Logical crop

The padded-linear source remains intact in memory while the logical view is selected. For mip `m`:

- logical texel width is `max(descriptor_width >> m, 1)`;
- logical texel height is `max(descriptor_height >> m, 1)`;
- RGBA8 copies `logical_width * 4` bytes from each logical texel row;
- BC1/BC3/BC4/BC5 copy `ceil(logical_width / 4)` complete blocks from each of
  `ceil(logical_height / 4)` block rows;
- every row begins at the inherited aligned-storage pitch, not descriptor pitch and not the
  previous row's cropped end.

The logical rectangle must fit the inherited storage and padded dimensions. Cropped mip ranges are
contiguous in DDS order. Their SHA-256 values and source padded-linear SHA-256 values are recorded
separately.

This distinction is mandatory: the exact owned set contains 1x1 resources stored with guest pitch
8 or 32. Guest pitch and padding must not appear as visible DDS width.

### DDS representation

Every file uses the 4-byte `DDS ` magic, the 124-byte DDS header, a 32-byte pixel format containing
FourCC `DX10`, and the 20-byte DX10 extension (148 header bytes total).

| XPPS format | DXGI format | Element |
| --- | --- | ---: |
| `Format8_8_8_8` Unorm | `DXGI_FORMAT_R8G8B8A8_UNORM` (28) | 4-byte texel |
| `FormatBc1` Unorm | `DXGI_FORMAT_BC1_UNORM` (71) | 8-byte 4x4 block |
| `FormatBc3` Unorm | `DXGI_FORMAT_BC3_UNORM` (77) | 16-byte 4x4 block |
| `FormatBc4` Unorm | `DXGI_FORMAT_BC4_UNORM` (80) | 8-byte 4x4 block |
| `FormatBc5` Unorm | `DXGI_FORMAT_BC5_UNORM` (83) | 16-byte 4x4 block |

`Color1D` descriptors use DX10 resource dimension `TEXTURE1D` and require height one; `Color2D`
uses `TEXTURE2D`. Array size is one. Compressed files set `DDSD_LINEARSIZE`; RGBA8 sets
`DDSD_PITCH`. Mipmap flags/caps are present only when the chain has more than one level.

After writing, an independent strict parser revalidates magic, header sizes/flags, reserved fields,
DXGI format, resource dimension, array size, top-level pitch/linear size, mip count, exact logical
mip ranges, and end-of-file. Trailing, truncated, or structurally ambiguous files are refused.

### Deterministic names and manifest

Files are named only by canonical descriptor index, data-relative payload start, and proven format;
for example `bitmap_000_0000a0f0_bc3.dds`. No artwork identity is inferred.

`manifest.json` and identical sorted UTF-8 JSON on stdout contain:

- schema/version and proof class `xpps_dds_export`;
- exact source, inherited-report, and host-shader identities;
- ordered output basenames, whole-file hashes, DDS facts, and source descriptor/payload hashes;
- each logical mip's width, height, row geometry, byte range/hash, and padded-linear source hash;
- aggregate descriptor, mip, DDS payload, and whole-file byte counts;
- warnings and explicit non-claims.

No timestamp, inode, host/output path, texel/block byte, decoded pixel, inferred artwork name, or
private location is serialized.

### Failure behavior

Inherited proof failure/disagreement, malformed report, unsupported format/type, logical crop
outside storage, overflow, population/byte budget, source hash/mutation, output collision/symlink,
short write, invalid reread/header/hash, unexpected directory entry, or I/O failure returns nonzero,
emits no JSON manifest on stdout, and invokes the bounded fresh-output cleanup.

### Acceptance tests

- exact independent DX10 headers and strict parse-back for RGBA8, BC1, BC3, BC4, and BC5;
- `Color1D` and `Color2D`, one and multiple mips, odd BC dimensions, and pitch wider than width;
- independently patterned logical-crop hashes from padded 32/64/128-bit buffers;
- deterministic synthetic XPPS/eboot integration with exact file and manifest hashes;
- inherited-report disagreement, bad crop/range/hash, output and total-byte budgets;
- existing/symlink output, short write and bounded cleanup, and source mutation;
- normal and Python `-O` execution;
- one exact-hash owned export accepted by the strict parser and local DDS decoder.

## Exact owned acceptance result

The guarded exporter accepted the exact owned XPPS/eboot pair from the BITMAP and Thin1D proofs.
Two distinct fresh output directories produced byte-identical `manifest.json` files with SHA-256
`fd98abd613ecdb5746c9b17f2c4c9f35f9e3ca971788326f3ff06bbd420db826`.

The manifests bind BITMAP report SHA-256
`aceea979ad065cd6592260d689114153907a42ce42b5061355e2ac2747da9da5` and Thin1D report SHA-256
`bad198da31569bcf5798d07feddf81e2b7e3cc8ccce1174535739cd24e8c7bfe`. All 12 files and all 82
mips pass strict parse-back. They contain `0x780108` (`7,864,584`) logical payload bytes and
`7,866,360` total DDS bytes:

| Basename | Dimensions | Format | Mips | Whole-file SHA-256 |
| --- | ---: | --- | ---: | --- |
| `bitmap_000_0000a0f0_bc3.dds` | 2048x1024 | BC3 | 12 | `80ec2afc68c4b1a4bbeb48970e8ce78eaa53f13eca992742767e1f702a753c42` |
| `bitmap_001_002b60f0_bc3.dds` | 2048x1024 | BC3 | 12 | `5100b5bacbf694505888c36f90041ba292281e025f9a2844223b3d55bcde65bb` |
| `bitmap_002_005620f0_bc3.dds` | 1024x512 | BC3 | 11 | `65f3ebaf5707e9af08db415f812836ec41455d266d033a7e04ecf37fa91102a5` |
| `bitmap_003_0060e0f0_bc3.dds` | 1024x512 | BC3 | 11 | `9838645a5bcba3b14a5c0a95652286264a81440b0efd7f96fff2e83d52fc636f` |
| `bitmap_004_006ba0f0_bc5.dds` | 1024x512 | BC5 | 11 | `26fcdd07d0a5a15eebf1830b3e0ff49219d9ece8964e0541c2d3ecf226b9e1fa` |
| `bitmap_005_007660f0_bc4.dds` | 512x256 | BC4 | 10 | `4a308f17062e50fa71c8ef830546d9b70d021367f1924a4c7c0b979b3ef7783d` |
| `bitmap_006_0077c0f0_bc4.dds` | 512x256 | BC4 | 10 | `68fe603e079d22655ada2614d0fff826a2876efa2315700d51f46806a003215e` |
| `bitmap_007_007947f0_bc5.dds` | 1x1 | BC5 | 1 | `58ad20468916c17b27d5356748c8c4dce400552f2d4112b33bfd3c02bf905ec6` |
| `bitmap_008_007954f0_bc1.dds` | 1x1 | BC1 | 1 | `4bd9a63cdafd404fff90eab04afbace9164dd89f3a244e5f87446cda14b5b4f8` |
| `bitmap_009_007956f0_bc1.dds` | 1x1 | BC1 | 1 | `15204e9cd32d8d332b5d9239ef27b2077acdb7161abbac545981b81bdf5e59be` |
| `bitmap_010_007970f0_rgba8.dds` | 1x1 | RGBA8 | 1 | `1515dc7501a7c7a3de2a65ee2939d014115a530b240c9c93a84c693fd7feeaca` |
| `bitmap_011_007971f0_rgba8.dds` | 1x1 | RGBA8 | 1 | `b5c7e532774689825d2ad142e1a060ecd70fed03de6988878c9347f77ff75c05` |

FFmpeg's local DDS decoder recognizes all 12 exact files with the manifest dimensions. ImageMagick
recognizes the four BC3 color candidates and the large BC5 file; its coder rejects the BC4 and
small DX10 `TEXTURE1D` entries, which FFmpeg accepts.

Visual inspection is coherent: the four BC3 images contain two cardboard/graffiti color sheets and
two stencil/alpha-like components; the large BC5 image is consistent with a tangent-space normal
map; and the two BC4 images are consistent with grayscale material/mask layers. Those semantic
roles remain visual inferences, not serialized artwork names or an injection claim. A wrong
expected eboot hash returns status two, emits zero stdout bytes, and creates no output directory.

Owned DDS and decoded PNG bytes remain only in ignored local scratch and are not committed.

## Route back to modding

After export proof, the local DDS files may be decoded to PNG for artwork identification and owner
inspection. A later injection lane must re-read the original padded-linear mip, overlay only edited
logical texels/blocks, preserve every untouched padding byte, retile, verify unchanged regions, and
use a rollback-safe runtime overlay before any container rebuild is considered.

## Usage

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_dds_export.py
```

```sh
deck_tools/second_son_xpps_dds_export.py /path/to/owned/file.xpps \
  --expected-xpps-sha256 64-lowercase-hex-characters \
  --row 2 \
  --eboot /path/to/owned/eboot.bin \
  --expected-eboot-sha256 64-lowercase-hex-characters \
  --output-dir /path/to/new/local/output-directory
```

## Non-claims

A structurally valid DDS and exact logical crop do not prove channel swizzle, alpha semantics,
color space, decoded visual correctness, artwork identity, editability, compression-encoder
equivalence, padding reconstruction from DDS, safe replacement, XPPS rebuild, runtime overlay,
injection support, or game runtime behavior.
