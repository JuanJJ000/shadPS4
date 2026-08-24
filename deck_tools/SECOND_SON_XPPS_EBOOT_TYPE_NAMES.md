<!-- SPDX-FileCopyrightText: 2026 shadPS4 Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Second Son XPPS-to-eboot type-name resolver

## Blocked question

Can the opaque executable registry IDs correlated from an owned Second Son XPPS file be resolved to
exact names through executable code and relocation evidence, without guessing from object counts or
scanning arbitrary strings?

This resolver answers only that registry-name question. It does not decode a named object or locate
its payload.

## Exact executable evidence

In the exact owned CUSA00223 01.00 eboot:

- guest function `0x00643e60` performs a signed binary search over `0x1b80` (7,040)
  16-byte records beginning at guest `0x00e00ef0`, returning the u32 at record offset eight;
- that 88-byte function has SHA-256
  `58c0c1f210701d9147373ac8ea1ddad7f5aecaf433bbaaa9abb1f47a90d7d9b0`;
- guest function `0x0066a590` rejects ID `-1` and otherwise indexes eight-byte pointer slots
  beginning at guest `0x010a7510`;
- that 28-byte function has SHA-256
  `3bb5f4ac084f3331d3c83dd92e10ec241f4150c32abd1e8670752b1975a69a92`;
- guest function `0x00643ec0` iterates IDs zero through `0x3fd`, calls the exact name lookup above,
  and returns the matching ID or `-1`;
- that 62-byte bound-and-lookup function has SHA-256
  `a12bd7bd60d4e72ff5948552e8d91427b2b28eb759593e39f53d0441a80a7d34`, proving that the ID
  table admits 1,022 slots;
- used slots are populated by ELF type-8 relative relocations to names in a validated load segment.

The hash table is strictly increasing when keys are interpreted as signed little-endian u64 values.
Its record tail is a u32 value followed by four zero bytes. Some global values are flags or enum
values outside the name-table range, so only IDs actually correlated from the selected XPPS are
resolved as name-table indices.

## Contract

### Inputs and inherited validation

- One regular, nonsymlink `.xpps` accepted by
  `second_son_xpps_eboot_registry.py`.
- One exact lowercase expected whole-file XPPS SHA-256.
- One zero-based high-kind-2 XPPS row accepted by the inherited chunk classifier.
- One regular, nonsymlink SELF eboot no larger than 128 MiB.
- One exact lowercase expected whole-file eboot SHA-256.
- The inherited 4,096-entry and 128-distinct-hash limits.

Both sources remain read-only and are re-hashed and re-statted after resolution.

### Exact lookup-code gates

The three guarded guest ranges must map through exactly one validated, uncompressed SELF-to-ELF load
mapping and match the complete expected function hashes and byte lengths. The resolver does not
accept lookup/table/name addresses from the command line.

Synthetic tests may override those constants only through the Python call boundary; the CLI always
uses the exact CUSA00223 01.00 contract.

### Hash registry

The complete 7,040-record default table must:

- map through exactly one validated load segment;
- fit completely in that segment and the eboot;
- use exact 16-byte records;
- have a zero reserved u32 in every record;
- have strictly increasing, unique signed-u64 keys.

Each inherited DIC hash must have exactly one table record and the table u32 must equal the opaque ID
already observed immediately after that hash by the correlator. Used IDs must fall within the 1,022
name slots.

### Dynamic relocation and name proof

The resolver parses bounded SELF segment and embedded ELF64 program-header tables. It requires one
`PT_DYNAMIC` and one PS4 `PT_SCE_DYNLIBDATA` program header, then maps their logical ELF file ranges
through unencrypted, uncompressed blocked SELF segments using the same program-header ownership
rule as the loader.

The dynamic table must contain exactly one `DT_SCE_RELA`, `DT_SCE_RELASZ`, and
`DT_SCE_RELAENT`. The relocation entry size is exactly 24 bytes, the relocation table is at most
32 MiB/131,072 entries, and every range remains mapped and bounded.

For each used ID, its name slot must:

- fit in exactly one ELF load memory range, including valid zero-fill memory;
- have exactly one relocation;
- use exact relative relocation info value eight;
- point into exactly one file-backed validated load mapping;
- reach a nonempty printable ASCII name terminated within 128 bytes and its load mapping.

The resolver emits the relocation index and target guest address as proof. It never searches for a
matching string by content.

### Deterministic output

Sorted UTF-8 JSON with a trailing newline:

- schema/version and proof class `xpps_eboot_type_name_resolver`;
- exact source identities and inherited selected DIC-row identity;
- guarded function/table/dynamic/relocation facts;
- ordered DIC hash, exact registry ID/name, XPPS DIC entry count, registry record offset, relocation
  slot/index, and name target address;
- warnings and explicit non-claims.

No timestamps, inodes, host paths, raw executable bytes, object bytes, or arbitrary strings are
emitted.

### Failure behavior

Inherited correlation refusal, lookup-code drift, malformed or unsorted registry records,
absent/duplicate keys, ID mismatch/range failure, malformed SELF/ELF/dynamic metadata,
absent/duplicate/wrong-type relocations, unmapped slots or targets, invalid names, exceeded budgets,
source mutation, or I/O failure returns nonzero and emits no JSON report.

### Acceptance tests

- exact synthetic name resolution and deterministic retained inputs;
- signed key ordering, reserved tail, absent/duplicate keys, and ID mismatch/range refusal;
- lookup-code hash drift;
- missing/duplicate/wrong-type relocation refusal;
- name-slot and target mapping edges, empty/non-ASCII/unterminated names;
- malformed dynamic tags/ranges, symlinks, mutation, and budgets;
- one exact-hash real owned XPPS/eboot pair.

## Exact owned acceptance result

The guarded resolver accepted `graffiti_a8_family.xpps` with whole-file SHA-256
`254c56a776b3c0317007e07d22a293404103c79ccc28c0f64a5c7f6b9a5588c7` and the owned
CUSA00223 01.00 `eboot.bin` with SHA-256
`99c7fe77f8348062cb3e0e7218c1991cda3515188f0d308b64f7dca058997d87`. Two independent runs
produced the same report SHA-256
`c0116380b6051aaddd12f3ce66146fbe8464e1c8083abde9d4709633206ce94b`.

The proof validated all 7,040 signed-sorted hash records, the complete 1,022-slot name table, and
95,104 relocation entries. Every used name slot had exactly one relocation with exact info value
eight. It resolved all 15 distinct hashes and all 77 selected DIC entries:

| DIC hash | ID | Exact relocated name | XPPS count |
| --- | ---: | --- | ---: |
| `000000f4d417169a` | 3 | `TRANSFORM` | 2 |
| `0d89711abf5505a2` | 937 | `FX_VELOCITY_BUFFER` | 2 |
| `10dfae538e97245a` | 925 | `FX_DEFERRED_DECAL_META` | 3 |
| `1cff349a460e76e6` | 934 | `FX_GHOST` | 2 |
| `5ce117b42469facb` | 824 | `PROXY_FILE_DATA` | 2 |
| `6fabfe95da448b74` | 522 | `GRAFFITI_MINIGAME_DATA` | 1 |
| `732dadd676cc1e95` | 960 | `FX_OBJECT_REFLECTION_SHADOW` | 2 |
| `763a774d6d8cff64` | 779 | `LO_ARRAY_DATA` | 2 |
| `97dfee94a3b3e3b5` | 919 | `FX_DEFERRED_OBJECT` | 4 |
| `baf0a00fe64f06f4` | 959 | `FX_DEPTH_ONLY` | 2 |
| `bd32c5d2f4512b39` | 958 | `FX_SHADOW_MAP` | 2 |
| `ee8b65de084738c4` | 501 | `BITMAP` | 12 |
| `ee8b65e82e41ae89` | 506 | `FX_FILE` | 5 |
| `ee8b66079a913386` | 508 | `SHADER` | 11 |
| `ee8b6614026bcdac` | 513 | `TRANSFORM_DATA` | 25 |

A wrong expected eboot hash returned status two and emitted zero report bytes. These names select
families for subsequent classifiers; they do not weaken any of the non-claims below.

## Route back to the modding goal

An exact `BITMAP` registry name selects the first descriptor family for a separate bounded field and
range classifier. The next gate must prove dimensions, format, mip/layer rules, and the relationship
between a descriptor and texel bytes. Decode/re-encode, overlay activation, and injection remain
blocked until they each have byte-exact round-trip and rollback evidence.

## Usage

Run the focused tests:

```sh
python3 -B -m unittest -v deck_tools/test_second_son_xpps_eboot_type_names.py
```

Resolve one owned source pair only when both complete hashes match:

```sh
deck_tools/second_son_xpps_eboot_type_names.py /path/to/owned/file.xpps \
  --expected-xpps-sha256 64-lowercase-hex-characters \
  --row 2 \
  --eboot /path/to/owned/eboot.bin \
  --expected-eboot-sha256 64-lowercase-hex-characters
```

## Non-claims

A resolved registry name does not prove an object boundary, descriptor layout, field meaning, texel
range, texture format, dimensions, mip/layer layout, shader bytecode boundary, safe replacement, or
injection support. `BITMAP` is a proven registry name; it is not yet a decoded image.
