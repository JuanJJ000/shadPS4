# Steam Deck / inFAMOUS Second Son engineering log

This is the living record for the local Steam Deck-focused shadPS4 fork. An item is not called an
improvement until it has been measured on the user's own legally dumped game in a visible Gamescope
session.

## Baseline

- Started: 2026-08-20
- Upstream: `shadps4-emu/shadPS4`
- Initial upstream revision: `e4ff203093dd1ab452fd401e6a0ccc2e5cbe6c00`
- Target game: inFAMOUS Second Son, package labelled `CUSA00223`
- Target hardware: Steam Deck, 16 GB unified memory
- Runtime policy: foreground Gamescope only, with MangoHud metrics and milestone screenshots
- Publication policy: issues, branches, commits, pushes, and PRs are authorized only on
  `deucebucket/shadPS4`; upstream remains fetch-only unless the user explicitly changes that rule

## Preserved inFAMOUS 1 setup

- The known-good retail PSARC backup and corrected HD source textures remain protected.
- The retail and HD launchers are separate, and live PSARC switching is refused while RPCS3 is
  running.
- The next RPCS3 launch uses the safer audio settings already prepared in the per-title config.

## Findings before the first Second Son build

1. Current shadPS4 probes block-texel-view support once using a sampled BC1 2D image, then applies
   that answer to stricter BC5 image configurations. The Second Son report uses mutable, extended,
   block-compatible BC5 with transfer, sampled, and storage usage; these are not equivalent probes.
2. Compressed-image storage usage was removed in upstream PR 3572, then restored in PR 4553 to
   support uncompressed views. A fallback therefore has to preserve the storage path on capable
   devices while gracefully reducing usage on devices that reject the exact image configuration.
3. The fatal line in issue 4790 is a buffer allocation failure, not the preceding BC5 warning. A
   contributor reports current Second Son use at roughly 18-20 GB of unified memory.
4. The Steam Deck currently has roughly 15 GB physical RAM plus zram and a disk swapfile. Vulkan
   device allocations cannot be assumed to spill safely into swap.
5. `BufferCache::RunGarbageCollector()` constructs an LRU cleanup callback but never iterates the
   LRU cache. The buffer GC therefore performs no deletions even under critical memory pressure.
6. The open upstream PR 4794 replaces a fixed 2 MiB command-copy buffer, which asserts when full,
   with stable chunked arenas to avoid both overflow and span invalidation.

## Planned changes

- Validate the restored buffer-cache LRU traversal and memory behavior.
- Port only the command-copy arena portion of upstream PR 4794 onto current `main`.
- Negotiate compressed-image flags/usage against the exact Vulkan image configuration and log the
  selected fallback.
- Add repeatable Gamescope/MangoHud launch and capture tooling for Second Son.

## Diagnostic tooling

- Added `deck_tools/vulkan_image_probe.cpp` to query the exact BC1/BC5 image flag and usage
  combinations on the Deck and report Vulkan heap size, budget, and current use.
- Compiled the probe with LLVM 20.1.8 in the isolated Freedesktop 25.08 SDK and ran it on the Deck's
  `AMD Custom GPU 0932 (RADV VANGOGH)`.
- All tested BC1 and BC5 1D/2D combinations, including block-compatible views plus storage usage,
  are supported. The Vulkan memory-budget extension reported about 2.86 GiB on heap 0 and 5.73 GiB
  on heap 1. This rules out the reported BC5 configuration as the Deck's immediate blocker and
  keeps memory residency as the primary investigation.

## Game package validation

- Transfer completed as `/home/deck/Documents/Infamous The Second Son CUSA00223.zip` (21,499,478,857
  bytes).
- The archive contains `CUSA00223_00-SECONDSON.pkg` plus two small text files; it is a PS4 package,
  not an ISO.
- A full `unzip -t` CRC pass completed with no errors.
- The PKG was staged intact at
  `/home/deck/Games/shadPS4-import/CUSA00223_00-SECONDSON.pkg` (21,499,478,016 bytes).
- Final installation into `/home/deck/Games/shadPS4-games/CUSA00223` is intentionally waiting for
  the active retail inFAMOUS RPCS3 session to end, because the package installer must remain visible
  in Gamescope and must not steal focus from the known-good game.

## Fork tracker

- Issue 1: buffer garbage collection does not traverse its LRU.
- Issue 2: replace fixed copied GFX command buffers with stable growable storage.
- Issue 3: query exact compressed-image Vulkan support and negotiate fallbacks.
- Issue 4: Steam Deck Second Son playability and measurement milestone.
- Issue 5: log failed Vulkan allocation context and live VMA heap budgets.
- Draft PR 6: restore conservative buffer-cache LRU collection (branch
  `fix/buffer-cache-gc`, fork only), updated through bounded dirty write-back commit `5a4acfe2`.
- Branch `fix/gfx-command-copy-arena` pushed to the fork at commit `9724acc4`; draft PR creation is
  intentionally deferred to a later GitHub workflow turn.
- Branch `fix/compressed-image-negotiation` pushed to the fork at commit `0c66aa0f`; draft PR
  creation is intentionally deferred to a later GitHub workflow turn.
- Branch `diag/vma-allocation-context` pushed to the fork at commit `05afa066`; draft PR creation is
  intentionally deferred to a later GitHub workflow turn.
- No duplicate or superseded fork issues currently need pruning.

## Local code changes

### Conservative buffer-cache garbage collection

- Restored the missing `lru_cache.ForEachItemBelow(...)` traversal in
  `BufferCache::RunGarbageCollector()`.
- Normal-pressure collection evicts only old CPU-authored buffers without introducing GPU stalls.
- Under critical pressure, at most one old GPU-written buffer smaller than 31 MiB is synchronously
  copied through the 32 MiB download ring and written back before eviction. The 1 MiB reserve covers
  alignment overhead from fragmented 4 KiB tracker ranges; larger dirty buffers remain cached.
- Removed the unused asynchronous callback whose stack-owned copy metadata could outlive its scope,
  and invalidate non-coherent download memory after GPU completion before CPU reads.
- Periodic/dirty-eviction logs record heap use, deletion count/bytes, and skipped dirty buffers.
- Status: bounded dirty write-back compiled and linked after a clean incremental rebuild; runtime
  validation pending.

### Exact compressed-image capability fallback

- The Deck probe shows RADV accepts BC1 and BC5 in 1D and 2D with the full mutable, extended,
  block-compatible, transfer, sampled, and storage configuration.
- If another Vulkan driver rejects that exact configuration, image creation now retries without
  optional storage usage, then without block-compatible views. Successful fallbacks are logged.
- Capable drivers such as the Deck's RADV retain the original full-feature path.
- Status: Deck capability verified and patched shadPS4 compiled; cross-driver runtime validation
  remains pending.

### Growable stable GPU command-copy arenas

- Ported only the command-copy storage portion of upstream PR 4794 onto current `main`.
- Replaced each fixed 2 MiB `std::vector` reserve with reusable 4 MiB chunks stored in a deque.
- A large frame can allocate additional chunks without invalidating spans already queued for the GPU,
  and retained chunks are rewound for reuse only by the GPU thread after copied submissions drain.
- Added a copied-submission boundary lock so the next frame cannot allocate and enqueue a command
  span while the previous frame's arenas are being rewound. This closes the content-overwrite race
  that the source PR explicitly leaves unresolved.
- Deliberately excluded the PR's unrelated filesystem, libc, logging, and system-service changes.
- Status: compiled and linked in the full RelWithDebInfo build; runtime validation pending.

### Failure-only Vulkan allocation telemetry

- Failed VMA buffer allocations now report byte size, usage class and flags, buffer-device-address
  use, and the Vulkan error before the existing assertion.
- Failed VMA image allocations now report format, extent, mip/layer/sample counts, usage and create
  flags, and the Vulkan error.
- Both failure paths record live usage, budget, VMA block/allocation bytes, and object counts for
  every populated memory heap. Successful hot paths remain silent.
- Status: tracked by issue 5; compiled and linked, with forced-failure/runtime validation pending.

### Deck-safe build harness

- `deck_tools/build_deck.sh` reproduces the isolated LLVM 20/Freedesktop 25.08 configure and build.
- When the known-good RPCS3 AppImage is active, the harness automatically starts the entire Flatpak
  build tree at nice level 15 and idle I/O priority so compiler children cannot compete at normal
  priority with gameplay.
- `deck_tools/install_second_son.sh` refuses to steal Gamescope focus while RPCS3 is running, then
  launches the isolated official v0.7 package installer visibly on display `:1` once the Deck is
  clear.
- Each completed run parses MangoHud's benchmark CSV with only the Python standard library and
  writes `performance-summary.txt` with sample count, mean/median/1%/0.1% FPS, frame-time tails,
  utilization, thermals, clocks, memory, and power columns when MangoHud provides them.

## Runtime results

No Second Son runtime test has been performed yet. The package is validated and staged, the first
fork RelWithDebInfo build and clean incremental rebuild both passed, and `shadps4 --help` exits
successfully. The built binary SHA-256 is
`b5c509c24d0943e65da73d001bf1466cc2a35323e33b353d25f78523e7769aa4`. The first launch will remain
foreground-visible with MangoHud/Gamescope capture after the user's active retail inFAMOUS RPCS3
session ends.
