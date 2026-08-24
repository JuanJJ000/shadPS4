<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son ImageMagick DXT edit bridge

This opt-in tool normalizes strict legacy `DXT1`/`DXT5` DDS output from ImageMagick into the exact
canonical DX10 DDS structure required by the reversible XPPS overlay tool. It does not encode,
decode, alter, stage, or activate textures itself.

## Blocked question

Can an ordinary PNG editor/BC encoder feed the proven modding lane without teaching the XPPS tool
to accept ambiguous DDS structures or weakening its exact header/mip checks?

## Exact local encoder observation

ImageMagick 7.1.2-13 reads the proven BC3 file and, with
`-define dds:compression=dxt5 -define dds:mipmaps=12`, writes:

- the same 2048x1024 dimensions and 12 mip levels;
- the exact 2,796,240-byte BC3 logical mip payload required by the proven export;
- a 128-byte legacy header containing FourCC `DXT5`;
- total size 2,796,368 bytes.

The proven DDS is 2,796,388 bytes because its identical-size payload follows a 148-byte canonical
DX10 header. The bridge therefore validates the legacy structure and moves only its mip payload
under the original proven header.

## Contract

### Baseline and input

- One exact guarded-export `manifest.json` plus its explicit expected SHA-256.
- A nonsymlink baseline directory containing exactly that canonical manifest and its complete DDS
  population. Every file must match its manifest size/hash and strict DX10 parse facts.
- One nonsymlink encoded directory containing a nonempty subset of known basenames and no other
  entries.
- At most 256 edits and 512 MiB total encoded input.
- One output path whose existing nonsymlink parent does not yet contain the final component.

All directories remain open and are path/descriptor revalidated. Files are opened relative to
those descriptors with `O_NOFOLLOW`, bounded, completely read, and revalidated by full identity.
Unknown names, collisions, symlinks, partial baselines, stale manifests, and unexpected entries are
refused.

### Strict ImageMagick legacy header

The bridge accepts only a 4-byte `DDS ` magic plus one 124-byte legacy header with:

- exact baseline width, height, mip count, and top-level linear byte size;
- canonical compressed texture flags and texture/complex/mipmap caps;
- pixel-format size 32 and FourCC `DXT1` only for baseline BC1 or `DXT5` only for baseline BC3;
- exact 44-byte ImageMagick reserved block: `IMAGEMAGICK\0` followed by 32 zero bytes;
- zero depth, RGB bit masks, caps2/caps3/caps4, and reserved2;
- no cubemap faces, volume, array, alternate format, or DX10 extension;
- complete calculated mip ranges beginning at byte 128 and ending exactly at EOF.

Odd dimensions use complete BC blocks, and every terminal mip consumes one full 8-byte BC1 or
16-byte BC3 block. Truncation and trailing bytes are distinct failures.

### Normalization

For every file, the output is exactly:

```text
original proven bytes [0,148) + encoded legacy bytes [128,EOF)
```

The result must have the same total size as the baseline and parse to the identical strict DX10
structure: format, dimension, width, height, mip count, logical ranges, payload size, and EOF.
At least one payload byte must differ. Whole-file and per-mip source/normalized hashes plus changed
byte/contiguous-range counts are emitted.

### Output and receipt

The fresh output directory contains only normalized DDS basenames, so it can be passed directly as
`--edits-dir` to `second_son_xpps_dds_overlay.py`. Files are written mode 0600 with
`O_EXCL|O_NOFOLLOW`, fsynced, reopened, strict-parsed, byte-compared, and held open through
commit/cleanup. The directory is mode 0700 and full bindings are revalidated.

The deterministic receipt is written to stdout only. It contains no encoded payload, pixel,
private path, inode, or timestamp. On failure, cleanup removes only names still bound to the exact
open files created by this invocation; replacements and preexisting paths are preserved.

## Acceptance tests

- DXT1↔BC1 and DXT5↔BC3;
- one and multiple mips, odd dimensions, and terminal 1x1 block mips;
- deterministic normalization and direct acceptance by the reversible XPPS builder;
- wrong signature, format, flags, caps, dimensions, mip count, linear size, cubemap/volume bits,
  truncation, and trailing bytes;
- wrong manifest, partial/extended baseline, unknown/symlink input, identical edit, byte budget,
  collision, short write, and output path replacement;
- normal and Python `-O` execution.

## Exact owned proof

The exact ImageMagick DXT5 output for `bitmap_000_0000a0f0_bc3.dds` was normalized twice with
byte-identical results:

- inherited guarded-export manifest SHA-256
  `fd98abd613ecdb5746c9b17f2c4c9f35f9e3ca971788326f3ff06bbd420db826`;
- legacy file: 2,796,368 bytes, SHA-256
  `c754a25131398b29331410b2c8fbcd3b66c9d61b22be54c848b852bbb2044159`;
- normalized strict DX10 DDS: 2,796,388 bytes, SHA-256
  `fd2cb6fde55a3a3f08ce2bdb9c8aa48c82c185d8ce0ac6a3b3535695a41d2e92`;
- bridge stdout receipt SHA-256
  `d57d34ffb8523e6794188a3ad0818c632ab97bf375b54679f3b166d9276e9db8`;
- changed bytes/ranges versus the original BC3 chain: 195,528 / 51,353.

The reversible XPPS tool accepted that normalized DDS without an exception: all 12 mips retiled and
reverse-deswizzled exactly, padding and non-target bytes remained exact, and the same-size local
overlay hash is `34671d0cdd2049c3e012f99c6a45f5325cd6aba5017edec2c4611bad4a1dced2`
with receipt SHA-256 `0b08a525d902e6596cb8bcf5bd6bd76308e2f2f6b12caed1785984e69a1667a9`.

Decoding the original and normalized top mip produces visually coherent, apparently identical
graffiti/stencil sheets at normal inspection size; a difference visualization localizes the
expected recompression changes around textured/gradient edges. This is evidence that the byte
route works, not a quality or semantic claim. All DDS, PNG, overlay, and difference images remain
in ignored local scratch and are not committed or distributed.

## Usage

Decode/edit and encode outside this tool:

```sh
magick 'baseline.dds[0]' edited.png
magick edited.png \
  -define dds:compression=dxt5 \
  -define dds:mipmaps=12 \
  encoded/bitmap_000_0000a0f0_bc3.dds
```

Normalize:

```sh
deck_tools/second_son_dxt_edit_bridge.py \
  --export-manifest /path/to/proven-dds/manifest.json \
  --expected-manifest-sha256 64-lowercase-hex-characters \
  --encoded-dir /path/to/legacy-dxt-edits \
  --output-dir /path/to/new-strict-dx10-edits
```

Test:

```sh
python3 -B -m unittest -v deck_tools/test_second_son_dxt_edit_bridge.py
```

## Non-claims

Normalization does not establish encoder quality, alpha/color-space semantics, decoded visual
correctness, BC4/BC5 encoding, artwork identity, runtime activation, or game behavior.
