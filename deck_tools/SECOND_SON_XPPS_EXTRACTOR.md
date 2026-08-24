<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS bounded row extractor

## Blocked question

Can one proven candidate row be isolated from an owned inFAMOUS Second Son XPPS file without
trusting stale offsets, mutating the container, or claiming that the row is a texture?

This extractor is the evidence bridge between the corpus-wide structure proof in
`SECOND_SON_XPPS_PROBE.md` and a later payload-family classifier. It does not decode or inject.

## Contract

### Inputs and bounds

- One regular, nonsymlink `.xpps` file accepted by `second_son_xpps_probe.py`.
- One exact lowercase 64-character expected source SHA-256.
- One zero-based candidate row index.
- One output file whose parent already exists and whose destination does not exist.
- The structure probe's 512 MiB file and 4,096-row limits remain authoritative.
- Extraction uses 1 MiB streaming reads and does not retain the payload in memory.

### Validation order

1. Re-run the current structure probe against the source; never trust a saved report.
2. Compare its full-file SHA-256 with the expected hash.
3. Resolve the selected row and re-check its absolute start/end against the source size.
4. Stream exactly that range into a private sibling temporary file while hashing it.
5. Re-hash the complete source after extraction and refuse publication if it changed.
6. Flush and sync the temporary output, set mode 0644, then publish with a no-replace hard link.

An invalid hash/index/range, source mutation, symlink, existing output, short read, or I/O failure
returns nonzero and leaves no published output.

### Deterministic manifest

On success, stdout receives sorted UTF-8 JSON with a trailing newline containing only:

- schema/version and proof class `bounded_extraction`;
- source basename, byte size, and verified SHA-256;
- selected row index, opaque kind word, relative/absolute range, and byte size;
- output basename, exact byte size, and SHA-256;
- explicit warnings and non-claims.

No timestamps, inode values, host paths, decoded names, or payload bytes enter the manifest.

### Proof and non-claims

A successful result proves that the new output is byte-identical to one range named by the
currently validated candidate table in the expected source file. It does **not** prove resource
identity, compression, texture format, object boundaries inside the row, safe replacement, or
runtime acceptance.

### Acceptance tests

- exact synthetic row extraction and deterministic manifest;
- hash mismatch and malformed expected hash;
- invalid row index and existing-output refusal;
- inherited bad-magic, symlink, and bounds refusal;
- simulated source mutation between validation and publication;
- short-read failure with no published output;
- one owned real row with retained source/payload hashes and unchanged source.

## Route back to the modding goal

The first owned output is inspected only for bounded headers/tags and entropy. If evidence supports
one payload family, the next tool increment will classify that family without using filenames as
semantic proof. An injector remains blocked until a byte-exact rebuild invariant and emulator
overlay rollback path are proven independently.

## Usage

Run the focused tests:

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_extract.py
```

Extract one selected row only when the whole-file hash matches:

```sh
deck_tools/second_son_xpps_extract.py /path/to/owned/file.xpps \
  --expected-sha256 64-lowercase-hex-characters \
  --row 2 \
  --output /existing/output/directory/new-row.bin
```

The first owned extraction selected row 2 of `graffiti_a8_family.xpps`. The source retained
SHA-256 `254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7` before and after.
The new 3,104-byte output has SHA-256
`b29e9776b3068bdf5030ca3eeae6c2024ccbfe9ff0fdab09d1da7ccf212eab1b`. Its bounded bytes begin
with the observed tag `KNLI` and contain ` DIC` at relative offset `0x510`; those observations do
not establish resource semantics.

A separate row-0 observation contains Havok 2013 class/type labels, which suggests that filenames
such as `graffiti_*` may package multiple resource families. It is evidence against treating a
whole XPPS or every row as a texture, not a decoded type claim.
