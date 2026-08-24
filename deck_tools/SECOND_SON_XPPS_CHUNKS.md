<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS chunk-list classifier

## Blocked question

Does the observed high-kind-2 XPPS row contain a fully bounded chunk stream and fixed-width DIC
offset/hash registry, or is its resemblance to a later Sucker Punch layout only superficial?

The classifier answers only that structural question. It consumes the owned container directly and
emits metadata; it neither extracts nor decodes payload objects.

## Reference boundary

The public [Ghost of Tsushima Blender
toolkit](https://github.com/coolab342/Ghost-of-Tsushima-Toolkit-for-Blender) parses a later Sucker
Punch PACK kind-2 row as FourCC/u32-size chunks and a DIC list of u64 offset/hash pairs. That is
useful independent evidence for a shape worth testing. It is not authority for Second Son object
types, offsets beyond the DIC registry, or safe replacement.

## Contract

### Inputs and bounds

- One regular, nonsymlink `.xpps` file accepted by `second_son_xpps_probe.py`.
- One exact lowercase expected whole-file SHA-256.
- One zero-based candidate row whose observed `kind_word >> 16` equals 2.
- Maximum 65,536 chunks and 65,536 total DIC entries per invocation.
- The probe's 512 MiB file and 4,096-row limits remain authoritative.
- The source is opened read-only and re-hashed after classification.

### Chunk grammar under test

- Each chunk begins with four observed tag bytes and a little-endian u32 content size.
- Content begins immediately after the eight-byte prefix and must end inside the selected row.
- The next chunk begins at that exact end; padding and implicit alignment are not assumed.
- A zero-size chunk is accepted only as the final eight bytes of the row.
- Printable tag bytes are emitted as ASCII; every tag is also emitted as lowercase hex.

### DIC grammar under test

Only a chunk with exact observed tag ` DIC` receives additional structural parsing:

- little-endian u32 entry count and u32 reserved word;
- exact content size `8 + count * 16`;
- each entry is an opaque little-endian u64 relative offset and u64 hash word;
- every relative offset must be strictly inside the complete XPPS data region;
- absolute offsets are computed from the already validated data start.

No hash-to-name, offset-to-object, object-header, or resource-type meaning is admitted.

### Deterministic output

Sorted UTF-8 JSON with a trailing newline:

- schema/version and proof class `chunk_structure_classifier`;
- source basename, size, and verified SHA-256;
- selected row index/kind/range;
- ordered chunk tags, prefix/content ranges, and sizes;
- bounded DIC count/reserved word and offset/hash observations;
- explicit facts, warnings, and non-claims.

No timestamps, inodes, host paths, payload bytes, decoded names, or inferred resource labels are
emitted.

### Failure behavior

Bad source/hash/row kind, truncation, chunk overflow, nonterminal zero-size content, excess
population, malformed DIC size/count, out-of-range DIC offsets, source mutation, or I/O failure
returns nonzero and emits no JSON report.

### Acceptance tests

- valid synthetic multi-chunk row with an exact DIC registry;
- deterministic report and source hash retention;
- hash mismatch and wrong row kind;
- truncated prefix and oversized chunk;
- nonterminal zero-size chunk;
- DIC count/size mismatch and out-of-range offset;
- chunk and DIC population budgets;
- inherited bad-magic and symlink refusal;
- one real owned graffiti sample with exact retained hash and observed chunk registry.

## Route back to the modding goal

If the real DIC registry is exact and its offsets remain bounded across a named asset family, the
next increment may classify bytes at those offsets by header fingerprints and sizes. Texture
decoding remains blocked until a descriptor can be related to a separate texel range without using
the XPPS filename as proof. Injection remains blocked on byte-exact rebuild and overlay rollback.

## Usage

Run the focused tests:

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_chunks.py
```

Classify one high-kind-2 row only when the whole source hash matches:

```sh
deck_tools/second_son_xpps_chunks.py /path/to/owned/file.xpps \
  --expected-sha256 64-lowercase-hex-characters \
  --row 2
```

The first owned result retained source SHA-256
`254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7` and exactly covered its
3,104-byte row with five chunks: `KNLI`, ` DIC`, `PYTO`, `PLPS`, and terminal ` DNE`. Its DIC
content is exactly `8 + 77 * 16` bytes; all 77 offsets are inside the XPPS data region.

The same guarded classifier accepted all 59 owned `graffiti_*.xpps` files. Every file has the same
five-tag sequence and exact row coverage. Across the family it observed 5,708 bounded DIC entries
(74 to 125 per file) using only 16 distinct opaque hash words. This proves a consistent structural
family; it does not identify any of those hashes or offsets as textures.
