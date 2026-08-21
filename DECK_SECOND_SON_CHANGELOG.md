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
7. Current Second Son reports confirm that `readbacks_mode = 2` (Precise) is required for correct
   gameplay lighting, particles, and the early graffiti interaction. Disabled readbacks produce a
   dark scene and can softlock progression; Precise readbacks are slower but mandatory for the
   correctness baseline.

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
- Issue 7: preserve the stencil comparison reference when stencil writes are masked off.
- Issue 8: opt-in PS4 multichannel-to-stereo downmix for the Deck speaker path.
- Issue 9: configurable controller-pose helpers for motion-only tutorials.
- Issue 11: reduce the remaining Precise-readback synchronization cost.
- PR 6: conservative buffer-cache LRU collection merged into the fork's `main` at `293c1ee6`.
- Branch `fix/gfx-command-copy-arena` pushed to the fork at commit `9724acc4`; draft PR creation is
  intentionally deferred to a later GitHub workflow turn.
- Branch `fix/compressed-image-negotiation` pushed to the fork at commit `0c66aa0f`; draft PR
  creation is intentionally deferred to a later GitHub workflow turn.
- Branch `diag/vma-allocation-context` pushed to the fork at commit `05afa066`; draft PR creation is
  intentionally deferred to a later GitHub workflow turn.
- Issue 1 is complete: PR 6 merged and foreground Second Son runs now report bounded GC activity
  without a Vulkan allocation failure.
- PR 10 targets the fork's `deck-second-son` branch with the validated stencil, readback, audio,
  motion-helper, and foreground launcher changes.
- Issue 11 now includes precise-readback request, wait-time, hot-page, and CPU call-stack evidence.
- Issue 12 tracks the independently measured Deck CPU-affinity improvement while the deeper
  readback work remains open in issue 11.
- Issue 25 tracks opt-in sleep-queue contention and owner-preemption diagnostics. Simple mutex and
  hybrid replacements are rejected because they reduced gameplay speed despite lowering CPU use.

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
- The controlled baseline and fork profiles now force Precise GPU readbacks on every launch, so a
  stale disabled-readback profile cannot invalidate lighting, particle, or progression tests.

### Second Son gameplay progression

- Corrected Vulkan stencil-reference selection when a PS4 stencil test has a `ReplaceOp` but a zero
  write mask. The operation reference is irrelevant when writes are disabled; using it for the
  comparison changed the required test reference from 4 to 0 and prevented the graffiti stencil
  from completing.
- Added deduplicated state logging for genuine stencil test/op-reference conflicts. This exposed
  the exact zero-write-mask state without flooding the runtime log.
- Readbacks that span more than one cached buffer now download the intersecting range from every
  buffer instead of selecting only one entry. Downloads retain a bounded 512 KiB window per
  intersection; a 2 MiB A/B test produced no measurable frame-rate gain and was reverted.
- A title-scoped environment limit clamps obviously oversized read-only formatted shader-buffer
  descriptors to 256 MiB. Second Son otherwise requests two multi-gigabyte ranges and exhausts the
  Deck's unified-memory Vulkan heaps before gameplay. The two warnings are deduplicated by address.
- Fixed ASC compute-ring parsing so type-2 padding is never interpreted as a type-3 packet length,
  and a type-3 packet can be accumulated safely across more than one ring-boundary submission.
- Added controller-pose helpers for sideways and shake gestures. The Deck back buttons expose the
  helpers, while the right stick and right trigger complete the motion-only graffiti step without
  disconnecting the real Steam Deck controller or its gyro.

### Steam Deck stereo audio

- PipeWire advertises an eight-channel virtual input to shadPS4 even when the selected Deck speaker
  sink is stereo. That bypassed the existing PS4 7.1-to-stereo fold-down and left center/surround
  content missing or badly balanced.
- Added `SHADPS4_FORCE_STEREO_DOWNMIX=1` as an opt-in and enabled it only in the Second Son Deck
  wrapper. The game still submits `Float_8CH_Std`, while shadPS4 now sends a two-channel float stream
  to PipeWire.
- PipeWire reported only active FL/FR ports for the repaired stream. A 5.94-second speaker-monitor
  capture was stereo, measured about -18.46 dBFS peak and -30.49 dBFS RMS, and showed no clipping.

### Steam Gaming Mode integration

- Installed a non-Steam shortcut targeting `deck_tools/run_second_son.sh` and preserved its Steam
  Input application identity while renaming it to `inFAMOUS Second Son (shadPS4 Deck)`.
- Stopped and restarted `steam-launcher.service` before and after editing `shortcuts.vdf`, then
  launched the selected Play button from the visible Gaming Mode library.
- The shortcut uses the isolated fork profile and autosave. It does not replace, modify, or launch
  the preserved RPCS3/inFAMOUS 1 installation.

### Steam Deck CPU topology

- Live thread sampling showed `Game:Main` and `shadPS4:GpuComm` were frequently scheduled on CPUs 1
  and 0, which are SMT siblings on the same physical Deck core. The five lighter `JobWorker*`
  threads also migrated onto the sibling CPUs of manually isolated hot threads and erased the gain.
- The Second Son wrapper now follows only its foreground emulator child, pins `shadPS4:GpuComm` to
  CPU 2, pins `Game:Main` to CPU 4, and confines `JobWorker*` to CPUs 0, 1, 6, and 7. Audio,
  presentation, operating-system, and Gamescope threads remain under the normal scheduler.
- The helper is title-scoped, opt-out with `SECOND_SON_CPU_AFFINITY=0`, requires no root access,
  applies each thread mask once, and exits with the game. It does not disable SMT or change global
  CPU governors, clocks, or power limits.
- The watcher also exits when the emulator reaches a zombie or dead process state, so Bash and
  Steam cannot retain an already-closed title while waiting for its `/proc` entry to disappear.
- Steam overlay diagnostics from helper subprocesses are excluded from `affinity.log`, and the
  post-run MangoHud summarizer clears the overlay preload so its report begins with metrics.
- In the same visible cannery scene, a 30-second unpinned sample measured 5.627 FPS and 178.3 ms
  mean frame time. Isolating the two hot threads plus workers measured 6.257 FPS and 160.6 ms, an
  11.2% mean-FPS gain. Allowing workers to roam again fell to 5.662 FPS, confirming sibling
  contention rather than run-to-run noise.

### Sleep-queue contention diagnostics

- Added opt-in per-bucket sleep-queue counters for acquisitions, contention, wait and hold time,
  owner off-CPU time, stable wait-channel identity, and guest thread classes. The normal launch
  keeps the counters disabled.
- Added a standard-library-only summarizer and launcher collection step so every enabled foreground
  run produces a weighted eight-interval `sleepq-summary.txt` instead of requiring manual log math.
- In the final visible cannery sample, the last eight intervals recorded 131,067 acquisitions at
  2,123.236 per second. 29,101 were contended (22.203%), with aggregate waiter time equal to
  62.486% of one wall-clock interval across the participating threads.
- Bucket 298 accounted for 129,859 acquisitions and all 29,101 contentions without changing its
  wait-channel address. This is one hot condition variable, not unrelated objects colliding in the
  hash table.
- Job workers owned 28,809 of 29,101 contentions (99.0%) and 3,976.041 of 3,980.058 measured
  off-CPU milliseconds (99.9%). The next bounded test is therefore worker placement, while the
  original spin lock remains selected.

### Five-worker placement experiment

- Issue 39 tests one scheduler variable: whether five `JobWorker*` threads benefit from five
  logical CPU slots instead of the known-good four-CPU mask.
- The launcher now accepts an opt-in `SECOND_SON_JOB_WORKER_CPUS` value or
  `job-worker-cpus.txt`, records the selected value and source in every run, and falls back to
  `0,1,6,7` unless the value is a unique comma-separated list of Deck CPU IDs 0 through 7.
- The safe default remains `0,1,6,7`. The candidate is `0,1,3,6,7`; `shadPS4:GpuComm` remains on
  CPU 2 and `Game:Main` remains on CPU 4. No global CPU, SMT, governor, clock, or power setting is
  changed.
- This selector is test infrastructure, not an accepted performance change. A matched foreground
  control/candidate result must pass the acceptance gate in issue 39 before the candidate can be
  selected.
- Matched foreground evidence rejected the CPU-3 candidate. The `0,1,6,7` control measured 9.91
  median FPS, 100.92 ms median frame time, and 3,926.670 ms of worker-owned off-CPU hold time in
  the final eight intervals. The `0,1,3,6,7` candidate cut worker-owned off-CPU hold time to
  1,459.799 ms but fell to 8.57 median FPS and 116.63 ms median frame time.
- The candidate therefore improved the lock diagnostic while making gameplay 13.5% slower by
  median FPS. CPU 3 is the SMT sibling of the CPU-2 GPU communication thread, so GPU-side sibling
  competition is the likely tradeoff; that explanation is an inference, not a directly measured
  cause.
- The external selector is restored to `0,1,6,7`, sleep-queue statistics are disabled, and the
  launcher retains the safe four-CPU default. The selector remains useful for future controlled
  tests without changing the accepted runtime policy.

## Runtime results

- The legally dumped CUSA00223 package installs and launches from Steam Gaming Mode into foreground
  Gamescope. The opening, first rendered cutscene, motion-only graffiti tutorial, autosave, and fish
  cannery transition all complete.
- A fresh post-restart shortcut launch resumed directly in controllable cannery gameplay. Camera
  and forward movement advanced the tutorial to `Find the back door out of the fish cannery`.
- Precise readbacks produce correct bright cannery lighting and progression at roughly 6-11 FPS;
  the final movement capture showed 7-8 FPS. Disabled readbacks reached roughly 12 FPS but produced
  black character/lighting output and is rejected as visually incorrect. Relaxed readbacks and a
  larger readback window did not produce a useful gain.
- The successful mixed intro/tutorial/gameplay run in `20260820-204333-fork` measured 9.92 FPS mean,
  8.57 FPS median, and 5.00 FPS 1% low. The final clean Steam/audio run is
  `20260820-213701-fork`; screenshots, console log, system data, MangoHud CSV, and the local stereo
  monitor diagnostic are stored with that run.
- Runtime memory is stable in the tested cannery segment at about 3.1 GiB process RSS, 3.9 GiB
  MangoHud VRAM, and roughly 9 GiB total system RAM. No Vulkan device loss or allocation failure was
  recorded. Buffer GC ran periodically and the runtime log remained below 700 lines instead of
  repeating tens of thousands of identical oversized-buffer warnings.
- The clean merged-source binary with automatic affinity launched through Steam into correct
  foreground gameplay in run `20260820-230413-fork`. Its later 30-second sample measured 7.202 FPS
  and 149.6 ms mean frame time; all seven target thread masks were verified and no fatal marker was
  logged.
- Readback profiling showed that the two hottest 4 KiB pages are predominantly CPU write faults
  into GPU-owned device-local buffers. A recent-page batch prototype increased the fault storm, and
  a narrow-hot-write prototype increased synchronization frequency without improving FPS. Both
  experiments were fully reverted and are retained only as run evidence.
- Remaining limitation: the correctness path is substantially below real-time on the Steam Deck.
  Precise GPU-to-CPU readbacks dominate frame time, so 30 FPS is not yet achieved even though the
  title is now visually correct, audible, controller-connected, saveable, and in gameplay.
