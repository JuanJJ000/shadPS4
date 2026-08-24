<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS-to-eboot registry correlator

## Blocked question

Do opaque hash words observed in a bounded Second Son XPPS ` DIC` registry also occur as
repeatable, aligned records in the exact owned executable, and can their executable locations be
mapped through the SELF and embedded ELF headers without guessing a fixed address delta?

This correlator answers only that identity and location question. It does not assign a class name,
decode an object, or identify a texture.

## Evidence boundary

The exact owned `graffiti_a8_family.xpps` sample exposes Havok 2013.2 reflection strings and 15
distinct opaque DIC hash words. Every one of those 15 raw little-endian hash patterns appears once
in the exact owned CUSA00223 01.00 eboot, followed by a small opaque 64-bit value. Nearby executable
bytes repeat the same 16-byte record shape.

That is evidence for a serialization registry relationship. It is not evidence that a hash names a
Havok class, that the following value is a runtime class index, or that the graffiti container holds
texture data.

## Contract

### Inputs and inherited validation

- One regular, nonsymlink `.xpps` accepted by `second_son_xpps_targets.py`.
- One exact lowercase expected whole-file XPPS SHA-256.
- One zero-based high-kind-2 XPPS row accepted by `second_son_xpps_chunks.py`.
- One regular, nonsymlink SELF eboot no larger than 128 MiB.
- One exact lowercase expected whole-file eboot SHA-256.
- At most 128 distinct DIC hash words and a 512 MiB hash-search product.
- At most 4,096 DIC entries, 65,536 raw occurrences, and 4,096 aligned mapped candidates.

Both sources are opened read-only. The complete hash and open-file identity of both inputs are
verified again after correlation.

### SELF and ELF mapping

The tool accepts only the little-endian PS4 SELF identity used by the loader and a bounded ELF64
program-header table. It validates all table ranges before reading them. A usable mapping must:

- be a blocked, uncompressed, unencrypted SELF segment;
- reference an existing ELF program header;
- contain at least the ELF program header file range;
- begin outside the validated SELF/ELF header region;
- have an ELF file size no larger than its memory size;
- remain inside the eboot;
- have non-overlapping SELF file and ELF virtual-address ranges.

For a complete aligned 16-byte candidate record, the ELF virtual address is derived from the
candidate offset within its validated SELF segment. No constant file-to-address delta is accepted as
input or assumed by the implementation.

### Correlations

For every distinct opaque DIC hash, the tool searches its exact little-endian eight-byte pattern in
the bounded eboot and emits:

- the number of XPPS DIC entries carrying that hash;
- the number of raw eboot occurrences;
- the number of complete, eight-byte-aligned occurrences owned by exactly one usable mapping;
- a status: `absent`, `unaligned_or_unmapped`, `unique_aligned_record`, or
  `ambiguous_aligned_records`;
- for each bounded mapped candidate, its SELF offset, ELF virtual address, segment/program-header
  identity, alignment residues, and immediately following opaque u64 value.

The following value is reported as both fixed-width hexadecimal and decimal so later evidence can
correlate it without assigning semantics. Raw executable bytes, neighboring records, symbol names,
and strings are not emitted.

### Deterministic output

Sorted UTF-8 JSON with a trailing newline:

- schema/version and proof class `xpps_eboot_registry_correlator`;
- exact source basenames, sizes, and verified hashes;
- inherited selected XPPS DIC-row identity;
- bounded SELF/ELF mapping metadata;
- ordered correlations and aggregate status counts;
- warnings and explicit non-claims.

No timestamps, inodes, host paths, guessed names, extracted data, or source bytes are emitted.

### Failure behavior

Inherited XPPS refusal, malformed hashes, oversized inputs or populations, malformed/truncated SELF
or ELF headers, invalid or overlapping mappings, exceeded occurrence/candidate/search budgets,
source mutation, or I/O failure returns nonzero and emits no JSON report. A valid eboot with an absent
or ambiguous hash still emits a truthful status; ambiguity is evidence, not a parser failure.

### Acceptance tests

- exact synthetic SELF/ELF mappings and deterministic unique records;
- duplicate, absent, unaligned, unmapped, and segment-edge occurrences;
- malformed and overlapping SELF mappings;
- hash mismatch, symlink, mutation, and population/search budgets;
- inherited XPPS structure and selected-row failures;
- one exact-hash real owned XPPS/eboot pair.

## Route back to the modding goal

A stable DIC-hash-to-opaque-ID relationship can justify a later, independently guarded correlator
between those IDs and runtime descriptors. That can prove which XPPS families contain Havok
collision/physics metadata and keep texture work focused on containers with a demonstrated
descriptor-to-texel relationship. Injection remains blocked until a decoder byte-round-trips owned
data and an isolated overlay has rollback proof.

## Usage

Run the focused tests:

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_eboot_registry.py
```

Correlate one owned source pair only when both complete hashes match:

```sh
deck_tools/second_son_xpps_eboot_registry.py /path/to/owned/file.xpps \
  --expected-xpps-sha256 64-lowercase-hex-characters \
  --row 2 \
  --eboot /path/to/owned/eboot.bin \
  --expected-eboot-sha256 64-lowercase-hex-characters
```

## Exact owned evidence

The first guarded run retained XPPS SHA-256
`254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7` and CUSA00223
01.00 eboot SHA-256
`99c7fe77f8348062cb3e0e7218c1991cda3515188f0d308b64f7dca058997d87`.

The parser independently recovered the embedded ELF header at SELF offset 288, ten ELF program
headers, and two usable non-overlapping load mappings. All 77 DIC entries grouped into 15 distinct
hashes. Each hash had exactly one raw eboot occurrence and exactly one complete aligned record in
ELF load program header 0, so all 15 statuses are `unique_aligned_record`.

The 15 following opaque values are `3`, `937`, `925`, `934`, `824`, `522`, `960`, `779`, `919`,
`959`, `958`, `501`, `506`, `508`, and `513` in sorted-hash order. The four nearby `ee8b65*`/
`ee8b66*` records carry `501`, `506`, `508`, and `513`. Those correlations are exact; the values
remain unnamed.

## Non-claims

DIC hashes are not names or proven type identifiers. The following opaque value is not a proven
class index. An ELF virtual address is not a runtime pointer. No record proves object identity,
resource type, texture format, mesh layout, shader/material identity, replacement safety, or
injection support.
