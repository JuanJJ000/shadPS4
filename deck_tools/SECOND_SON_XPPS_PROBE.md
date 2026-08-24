# Second Son XPPS structure probe

## Blocked question

Do inFAMOUS Second Son's PS4 `.xpps` files expose a consistent, range-safe PACK table that can
support a later bounded resource/texture extractor, or would treating the older PS3 XPP tooling as
compatible corrupt evidence or game data?

The probe answers only that structural question. A valid result enables corpus-level table
classification and a later extractor for one proven payload class.

## Gap and prior evidence

- The local `if1-tex` and Infamous Mod Manager implementations explicitly target the PS3
  inFAMOUS 1/2/Festival of Blood layouts. They are not Second Son parsers.
- The public Ghost of Tsushima Blender toolkit is reference evidence for a later Sucker Punch PACK
  table shape, not authority that Second Son shares its semantic chunk types.
- The owned CUSA00223 dump contains 1,551 `.xpps` files. A bounded sample begins with `KCAP`
  (little-endian `PACK`) and has range-safe table/data offsets, but no resource semantics have
  been admitted.

## Contract

### Inputs and bounds

- One regular `.xpps` file or one directory scanned non-recursively.
- Maximum input size: 512 MiB per file.
- Maximum directory population: 2,048 files.
- Input files are opened read-only and hashed with streaming reads.
- Symlinks, non-regular files, and ambiguous suffixes are refused.

### Deterministic output

UTF-8 JSON with sorted keys and a trailing newline:

- schema name and version;
- proof class `structure_probe`;
- input basename, byte size, and SHA-256;
- observed magic and fixed header words;
- candidate table/data offsets;
- each candidate table row and its absolute bounded payload range;
- table coverage/contiguity facts;
- bounded offsets for recognized printable structural tags;
- explicit warnings and non-claims.

No timestamps, inode values, host paths, retail bytes, names recovered from payloads, or inferred
resource labels are emitted.

### Safety and failure behavior

- Default mode writes JSON to stdout.
- `--output` creates one new file atomically and refuses an existing destination.
- Invalid magic, truncated headers/tables, arithmetic overflow, out-of-file ranges, overlaps, excess
  population, or size-limit violations fail nonzero.
- A directory scan fails as a unit; it does not publish a partial registry.
- The tool never extracts, patches, copies, renames, or rewrites an XPPS input.
- Peak working memory remains below 16 MiB independent of input size.

### Proof and non-claims

A successful result proves only that the observed candidate table is internally range-safe under
this schema. It does **not** prove chunk semantics, texture formats, compression, object identity,
Ghost/PS3 compatibility, safe replacement, or runtime acceptance.

### Acceptance tests

- valid synthetic table;
- valid empty/stub table;
- bad magic and truncation;
- out-of-range and overlapping rows;
- near-match header with wrong offsets;
- deterministic file and directory output;
- symlink, population, and size refusal;
- no-overwrite output behavior;
- one real owned target with retained input/output hashes and no input mutation.

## Route back to the game/modding goal

The first real corpus result feeds issue #99. If one or more table families are consistent, the next
tool increment may extract only a hash-addressed payload range with the same no-overwrite contract.
Texture decoding or injection remains blocked until a descriptor/texel relationship and exact
round-trip invariants are separately proven.

## Usage

Implementation is intentionally queued behind this contract. The usage card will be completed with
the CLI, focused test command, and first real result in the implementation commit.
