<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS DIC-target fingerprint classifier

## Blocked question

Do the bounded offsets in an observed Second Son ` DIC` registry point to repeatable fixed-size
header families inside validated XPPS rows, and is the 16-byte predecessor convention seen in a
later Sucker Punch game relevant to this earlier format?

This classifier answers only that structural question. It does not decode, extract, name, replace,
or inject an object.

## Reference boundary

The public [Ghost of Tsushima Blender
toolkit](https://github.com/coolab342/Ghost-of-Tsushima-Toolkit-for-Blender) reads a later Sucker
Punch DIC target from `data_start + offset - 16`. That is a useful location hypothesis, not
authority for Second Son. This tool observes both the exact Second Son offset and its immediate
16-byte predecessor without adopting the later title's boundary or type semantics.

## Contract

### Inputs and inherited validation

- One regular, nonsymlink `.xpps` accepted by `second_son_xpps_probe.py`.
- One exact lowercase expected whole-file SHA-256.
- One zero-based high-kind-2 row accepted by `second_son_xpps_chunks.py`.
- At most 4,096 total DIC entries per invocation.
- The probe's 512 MiB and 4,096-row bounds remain authoritative.
- The source is opened read-only and re-hashed after fingerprinting.

### Target windows

For each DIC entry, the tool requires:

- the exact absolute target to fall in exactly one already validated payload row;
- a complete 16-byte window immediately before the target in that same row;
- a complete 64-byte window beginning at the target in that same row.

It emits the containing row index, observed kind word/class/flags, target offset within that row,
absolute alignment residues modulo 16, 32, and 96, and the deltas to the previous and next unique
sorted DIC targets. Duplicate target offsets are retained as aliases and receive the same neighbor
facts.

For each location it emits SHA-256 fingerprints of the 16-byte predecessor and 64-byte target
window. It also emits the predecessor's two and target's first two opaque little-endian u64 words,
plus zero-byte counts. Those four words are admitted only as bounded header observations. No bytes
after the 64-byte target window, decoded strings, object bodies, or extracted files are emitted.

### Deterministic output

Sorted UTF-8 JSON with a trailing newline:

- schema/version and proof class `dic_target_fingerprint_classifier`;
- source basename, size, and verified SHA-256;
- selected DIC row identity;
- ordered entry index, opaque DIC hash, target/row/alignment/neighbor facts;
- fixed-window hashes, opaque header words, zero counts, alias count;
- aggregate counts, warnings, and explicit non-claims.

No timestamps, inodes, host paths, filenames inferred from hashes, object labels, texture labels, or
bulk payload data are emitted.

### Failure behavior

Inherited probe/classifier refusal, an empty or excess DIC population, target outside all rows,
overlapping row ownership, incomplete predecessor/target window, malformed inherited report,
source mutation, or I/O failure returns nonzero and emits no JSON report.

### Acceptance tests

- exact synthetic cross-row targets with deterministic fingerprints;
- same-offset aliases and unique-neighbor deltas;
- target at each invalid predecessor/target edge;
- entry-population budget;
- inherited hash, row-kind, bad-magic, and symlink refusal;
- malformed inherited report and post-classification mutation signal;
- one exact-hash real owned graffiti sample;
- bounded 59-file family aggregation without semantic resource labels.

## Route back to the modding goal

If an opaque DIC hash consistently selects one repeatable header family, a later tool may test a
single field-layout hypothesis against descriptor ranges. A texture claim still requires a proven
descriptor-to-texel relationship and a decoder that round-trips owned bytes. Injection remains
blocked until byte-exact rebuild, isolated overlay activation, and rollback are separately proven.

## Usage

Run the focused tests:

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_targets.py
```

Fingerprint one owned source only when its complete hash matches:

```sh
deck_tools/second_son_xpps_targets.py /path/to/owned/file.xpps \
  --expected-sha256 64-lowercase-hex-characters \
  --row 2
```

The first guarded owned sample retained SHA-256
`254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7`. All 77 targets belong
to validated row 0, all fixed windows are complete, and the entries use 15 opaque DIC hashes.

The same tool accepted all 59 owned `graffiti_*.xpps` files: 5,708 entries, 5,708 targets in row
0, and 16 opaque hashes across the family. Of the 5,708 immediate 16-byte predecessors, 5,366 are
entirely zero, 341 contain four zero bytes, and one contains five. Several opaque hashes select a
stable first 16-byte target pattern while others select many patterns. These are structural facts,
not resource identities or boundaries.

## Non-claims

The predecessor is not claimed to be an object header. DIC hashes are not names or types. Fixed
windows are not object boundaries. Repeated 96-byte spacing is not a structure size. No fingerprint
is a texture, mesh, shader, material, animation, or gameplay object until independent evidence
establishes that meaning.
