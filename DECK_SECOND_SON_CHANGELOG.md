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
- Issue 57 tracks a behavior-neutral write-discard eligibility probe for the dominant Second Son
  `rep movsq` readback site. It must prove exact dirty-byte coverage before any copy can be skipped.
- Issue 59 tracks the one-commit upstream sync that corrects an out-of-bounds controller-combo
  array write while preserving all fork-only Steam Deck work.
- Issue 61 tracks a standalone synthetic probe of direct Vulkan host-memory import before any
  attempt to replace the buffer cache's separate device-local shadow allocations.
- Issue 67 tracks behavior-neutral attribution of precise-readback bytes and completion waits to
  cached-buffer identities before another memory-placement candidate is selected.
- Issue 77 tracks bounded GPU timestamps around the entire command buffer that contains a precise
  readback, so earlier recorded GPU work can be separated from the barrier-and-copy span measured
  by issue 75.

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

### Upstream controller-input safety sync

- The fork incorporated upstream commit `500f4137`, which corrects the alternate two-key combo path
  from `keys[3]` to the valid final element `keys[2]` of a three-element array.
- The ancestry merge was conflict-free and changed only that one index. All fork-only controller
  pose helpers and Steam Deck mappings remain intact; the full Deck build passes.

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

### Precise-readback fault-site identity

- Issue 42 extends the existing opt-in readback counters with the faulting instruction address
  paired to the touched 4 KiB data page. The bounded table records only on GpuComm after the fault
  callback is queued; the signal handler still performs no logging or allocation.
- The three hottest instruction/page pairs and their write counts are emitted with each existing
  readback interval. The local summarizer aggregates those pairs while remaining compatible with
  older logs that contain only hot data pages.
- This is measurement only: the 512 KiB coherence window, synchronous GPU completion, page
  protection, tracker transitions, and all stats-off synchronization behavior remain unchanged.
- A 156-second foreground Steam/Gamescope run reached correctly lit cannery gameplay, kept the
  stereo 48 kHz output path, and exited with status 0. Its final eight intervals measured 1,024
  readback requests, 545 writes, 479 reads, and 3,327.092 ms spent finishing readbacks.
- Two stable write sites dominated the bounded sample: `libc.prx+0x45dfe` wrote page
  `0x2edaf8000` 272 times, while `eboot.bin+0x349827` wrote page `0x24bf86000` 136 times.
  `eboot.bin+0x350ca4` was the repeated read-side caller, with its data page changing over time.
  These module identities come from the exact loader ranges recorded in the same run.
- The diagnostic therefore passed its behavior-neutral foreground gate. It identifies a small,
  repeatable caller set for the next bounded experiment without claiming that the call sites are
  themselves safe to bypass.

### Precise-readback operand context

- Issue 44 follows the dominant `libc.prx+0x45dfe` write site, whose exact instruction is
  `rep movsq` inside a memmove-like routine. A faulting instruction alone does not reveal the
  original copy length, remaining qwords, source, destination, or game call path.
- The existing opt-in counter now snapshots the x86-64 integer registers from the already supplied
  fault context and copies those values through the synchronous GpuComm callback. The signal
  handler still performs no memory dereference, allocation, formatting, or logging.
- The bounded hot-site table records interval ranges for `RCX` and `RDX` plus the last source,
  destination, frame, and stack values. The summarizer remains compatible with logs that have no
  operand context.
- An 86-second foreground Steam/Gamescope run reached correctly lit cannery gameplay with Delsin
  rendered, stereo audio, and exit status 0. The final eight intervals contained 1,024 readback
  requests and spent 3,651.618 ms finishing them.
- At the dominant `rep movsq` site, `RDX` stayed between `0xb0` and `0x630` bytes (176 to 1,584)
  and `RCX` stayed between `0x16` and `0xc6` remaining qwords. Destinations stayed within the hot
  `0x2edaf8000` page while source ranges changed.
- The `eboot.bin+0x349827` site is a 16-byte `vmovaps` through `RCX`; its unrelated 30 MiB `RDX`
  value demonstrates why register meaning must be interpreted from the exact instruction.
- This is still measurement only. The result supports a separate, opt-in A/B of a smaller
  readback window for the bounded memmove write site; it does not make that optimization here.

### Site-specific precise-readback window

- Issue 46 adds an explicit, opt-in selector for a smaller precise-readback window at one write
  fault instruction. The emulator accepts only a nonzero guest PC paired with a power-of-two
  window from 4 through 512 KiB; an absent, `off`, or invalid selector leaves every request on the
  existing 512 KiB policy.
- The first controlled candidate is `0x809b1dfe:64`, targeting only the dominant `rep movsq`
  write site measured in issue 44. Reads, every other write site, synchronization, page
  protection, tracker transitions, and the default configuration remain unchanged.
- Interval diagnostics report both the configured site window and its exact hit count. The local
  summarizer consumes those fields while treating older logs as selector-off, and the Deck
  launcher records the selector and its source in each run.
- This selector is test infrastructure until matched foreground control/candidate evidence proves
  both correct rendering and better frame pacing. Downloaded-byte reduction alone is not an
  acceptance result because a narrower window can create extra future faults and GPU waits.
- Matched foreground runs used the exact `0c6ff39d` binary and the same cannery save. Both showed
  correct lighting and Delsin, opened the main output as 48 kHz stereo, and exited with status 0.
  Control measured 9.97 median FPS and 100.35 ms median frame time; the 64 KiB candidate measured
  9.96 FPS and 100.40 ms. The settled final 600 samples also had the same 8.586 FPS median.
- In the final eight counter intervals, the candidate applied the narrow window 506 times and cut
  finish time from 3,575.905 to 2,490.485 ms (-30.4%). It nevertheless increased downloaded bytes
  from 173,711,168 to 188,956,288 (+8.8%) and request rate from 62.133 to 89.851 per second
  (+44.6%). The narrower site window moved work into more frequent faults instead of improving the
  frame path.
- The candidate fails the acceptance gate because median FPS/frame time did not improve and total
  downloaded bytes increased. The external selector is restored to `off`; the 512 KiB default is
  preserved, and this experimental branch is not promoted.

### 256 KiB site-window midpoint

- Issue 48 reuses the same disabled-by-default selector to test `0x809b1dfe:256`, halfway between
  the rejected 64 KiB site window and the accepted 512 KiB control. The selector remains external;
  no game identity or address is enabled in the emulator by default.
- This is a separate foreground A/B, not a reinterpretation of issue 46. It asks whether retaining
  more neighboring data can preserve part of the per-request saving without producing the 64 KiB
  candidate's higher fault rate and downloaded-byte total.
- The same acceptance gate applies: correct rendering/audio and a clean exit are necessary, while
  a repeatable median FPS/frame-time improvement is required for promotion. The selector stays
  `off` until that evidence exists.
- Two matched foreground pairs used the exact `2178ab75` binary and the stationary cannery
  right-stick tutorial scene. All four runs rendered the correctly lit scene, opened the 48 kHz
  stereo main output, and exited with status 0. Candidate screenshots also preserved the visible
  tutorial prompt and foreground geometry.
- In the final 600 samples of pair one, median FPS improved 8.585 to 9.510 (+10.8%) and median
  frame time fell 116.488 to 105.148 ms (-9.7%). The replicate improved 8.680 to 9.271 FPS
  (+6.8%) and 115.204 to 107.863 ms (-6.4%). Across the two pairs, settled median FPS averaged
  +8.8%, mean FPS +1.2%, 1% low +1.9%, median frame time -8.1%, and p95 frame time -3.1%.
- The candidate reduced final-eight-interval downloaded bytes by 12.3% and 7.5%, and readback
  finish time by 13.1% and 4.5%. Request rate rose 12.7% and 8.1%, but unlike the 64 KiB case it
  did not erase the repeatable median gain.
- The 256 KiB site window passes this bounded scene gate and can be promoted as an opt-in Second
  Son profile after review and an exact merged-binary foreground run. This is a modest frame-pacing
  improvement in one stationary scene, not a claim of real-time playability or broad game coverage.

### Write-discard coverage probe

- Issue 57 asks a narrower question than the rejected readback-window and barrier experiments: at
  the exact `0x809b1dfe` `rep movsq` write fault, does the remaining CPU copy overwrite every byte
  that the GPU marked dirty on the faulting 4 KiB page?
- The probe is disabled by default and accepts only an explicit nonzero guest PC. Invalid launcher
  and emulator values fail closed to `off`; no game or site identity is built into the emulator.
- Context validation requires the selected write PC, forward copy direction, a nonzero bounded
  `RCX`, arithmetic without overflow, and a remaining `RDI..RDI+RCX*8` span containing the actual
  fault. The fault context now carries `RFLAGS` so a backward string copy cannot be misclassified.
- Measurement runs on GpuComm before the existing download mutates the exact GPU-dirty range set.
  It reports selector/valid hits, whole remaining span and page-local write bytes, dirty and covered
  bytes, fully covered requests, and requests with no dirty bytes.
- This branch does not skip downloads, alter barriers, change page protection or tracking, or avoid
  GPU completion. A future write-discard experiment is allowed only if foreground evidence shows
  that full dirty-byte coverage is common and repeatable.
- The exact `c72df549` diagnostic build ran through the Steam/Gamescope shortcut into the correctly
  lit cannery scene with Delsin and the objective rendered, an active float32 stereo 48 kHz stream,
  and exit status 0. The 1,381-sample run measured 7.50 median FPS and 133.37 ms median frame time;
  this measurement-only run is not an FPS comparison.
- Across 43 counter intervals, all 987 selected-PC hits passed the context checks. Of those, 601
  encountered no GPU-dirty bytes. Only 25 of the remaining 386 dirty requests were fully covered
  by the pending write (6.477%), and only 50,756 of 833,020 exact dirty bytes overlapped the pending
  write span (6.093%).
- The evidence rejects a general write-discard bypass at this site: most dirty requests still need
  old GPU data. The disabled-by-default probe remains useful diagnostic infrastructure, but no
  readback skip is implemented or planned from this result.

### External host-memory buffer probe

- RADV on the Steam Deck exposes `VK_EXT_external_memory_host` with 4 KiB pointer alignment and
  reports the full buffer-cache usage combination as importable.
- A standalone synthetic probe imports a 64 KiB aligned host allocation, binds it to a Vulkan
  buffer, copies CPU-written patterns through the GPU into a separate readback buffer, then fills
  the imported buffer on the GPU and verifies the original host pointer after explicit barriers and
  a fence.
- Five validation-enabled runs completed 500 of 500 two-way iterations without a mismatch or
  validation message. Individual 100-iteration runs took 9.556 to 14.380 ms; those microbenchmarks
  prove coherence, not game performance.
- `vkGetMemoryHostPointerPropertiesEXT` returned only memory type bit `0x20`. The selected type 5
  has flags `0xE`: host-visible, host-coherent, and host-cached, but not device-local. Direct guest
  buffers are therefore technically possible but may trade readback-copy cost for slower GPU
  access. No emulator behavior changes in this issue.

### External host-memory selective benchmark

- Issue 63 extends only the standalone probe. It compares a device-local GPU fill followed by a
  copy into host-cached readback memory against a GPU fill of imported coherent host memory with
  direct CPU visibility. It does not hook BufferCache, identify the game, or alter emulator
  behavior.
- Each 64, 256, and 512 KiB size receives ten warmups and 200 measured samples per mode. Alternating
  `ABBA`/`BAAB` order balances both time position and same-mode adjacency; every fill is checked
  across its entire requested range before the sample is accepted. Vulkan timestamps isolate GPU
  fill time, while wall time measures queue submission through fence completion.
- Five validation-layer replicates all passed without a validation message. Across those replicates,
  the median direct-import wall-time change was -9.697% at 64 KiB, -14.396% at 256 KiB, and
  -17.906% at 512 KiB. Five normal-layer replicates reported -10.600%, -16.191%, and -20.570%
  respectively.
- Imported-memory GPU-fill median time was never higher in the 30 size/replicate comparisons; its
  change ranged from unchanged to 2.564% lower at the timer's 40 ns resolution. This synthetic
  result clears a narrow, opt-in runtime-prototype gate, but it is not a gameplay, FPS, or full
  BufferCache result. Foreground correctness and performance A/B remain mandatory before any
  runtime path can be accepted.

### Per-buffer precise-readback contributions

- Issue 67 adds a fixed 64-entry table under the existing opt-in readback statistics. It attributes
  request participation, writes, download calls, copy ranges, downloaded bytes, and the observed
  completion timing to each cached buffer's guest base and allocation size. Stats-off memory
  placement, barriers, tracking, readback windows, and synchronization are unchanged.
- Each 128-request interval emits the top three buffers by downloaded bytes and by observed finish
  time. The table reports explicit overflow drops instead of silently pretending a partial census
  is complete. The local summarizer parses both lists, deduplicates a buffer listed in both, and
  remains compatible with all older logs.
- The exact `66526899` diagnostic binary ran for 92 seconds in foreground Steam/Gamescope and
  reached correctly lit cannery gameplay with Delsin and the objective rendered. Controller input,
  48 kHz stereo output, and exit status 0 were preserved. The 920-sample run measured 8.58 median
  FPS and 116.56 ms median frame time; this measurement-only run is not an FPS comparison.
- The final eight intervals tracked six or seven unique buffers apiece with zero table drops. Two
  18,956,288-byte allocations accounted for 119,792,704 of 125,087,744 downloaded bytes (95.767%)
  and 1,889.706 of 4,651.313 observed finish milliseconds (40.627%). This is the first measured
  high-byte candidate set; the prior hot fault-page count did not expose it.
- A separate 268,451,840-byte allocation was associated with 2,013.495 finish milliseconds
  (43.289%) while downloading only 106,560 bytes (0.085%). This timing is an association, not a
  causal per-buffer cost: sequential `scheduler.Finish()` calls can charge earlier queued GPU work
  to whichever buffer finishes first in a request.
- The diagnostic passes its behavior-neutral gate. Any follow-up must use the byte census to select
  one bounded allocation at a time and must not treat the observed finish ranking as proof that
  moving that allocation alone removes the wait.

### Precise-readback wait-phase diagnostic

- Issue 73 adds a disabled-by-default diagnostic that separates older queued GPU work from the
  command buffer containing the current precise-readback copy. The accepted path remains one
  submission and one timeline wait unless the explicit phase selector is enabled together with
  readback statistics.
- In split mode, the scheduler first waits for the last previously submitted tick, then submits and
  waits for the current readback command buffer. Counters report prior-backlog wait, submission,
  and current-command-buffer wait separately. This intentionally changes scheduling and is
  diagnostic evidence only, never an FPS comparison.
- The Deck launcher accepts exactly `0` or `1` from an environment override or profile file,
  records the resolved value and source in every run, and fails closed to `0`. The emulator also
  refuses to enable phase timing when readback statistics are disabled.
- The foreground gate requires correct lighting, geometry, controller, 48 kHz stereo audio, and a
  clean exit before the timing split can guide the next optimization. The selector must be restored
  to `0` after the evidence run.
- The first discovery binary exposed a request-aggregation omission: per-buffer phase values were
  measured but not added to the request sample. That run is excluded. The corrected exact binary
  is 374,359,136 bytes with SHA-256 `c4892933137727a262151138e1baa31a393082ec51bd978b5feb300b04d3f8de`.
- Corrected foreground runs reached the lit cannery scene with Delsin, full geometry, Steam Deck
  controller slot 0, and the main 48 kHz stereo output. One run was stopped externally after its
  screenshot; a direct X11 window-destroy attempt is separately retained as an exit-133 teardown
  failure and is not treated as a game or phase-timing crash.
- The final exact run used shadPS4's supported IPC stop path and exited 0 after 1:59 with the cache
  dumped. Across its final eight intervals, 1,024 requests spent 800.815 ms waiting for prior
  submissions (23.926% of measured finish time) and 2,474.714 ms waiting for the command buffer
  containing the current readback (73.938%). The earlier corrected run measured 38.091% and
  59.963%, so both phases are material while the current command-buffer side is larger.
- The current-command-buffer phase is not a pure copy timer: it can contain GPU commands recorded
  before the readback copy. The result therefore supports finer command-buffer workload counters,
  not a claim that readback copies alone own 59.963-73.938% of the stall. Split timing remains
  disabled by default and is accepted as diagnostic infrastructure only; it makes no FPS claim.

### Precise-readback GPU timestamp diagnostic

- Issue 75 extends only the existing opt-in phase-timing path with a two-slot Vulkan timestamp
  query. One timestamp is recorded after all earlier commands in the current command buffer and
  immediately before the readback pre-barrier; the second follows the buffer copy.
- The query result is consumed only after the existing synchronous finish has completed. Queue
  timestamp valid bits are applied before converting the wrapped tick delta with the physical
  device's timestamp period.
- Each interval reports timestamp availability, valid bits, period, successful/failed samples,
  total GPU time from the pre-barrier through the copy, and that span as a percentage of the
  current-command-buffer CPU wait. Older logs remain readable with zero-valued defaults.
- Selector-off creates no query pool and records no timestamp commands. Unsupported queues fail
  closed with an explicit warning. This is a bounded attribution diagnostic, not an FPS change or
  proof that CPU handling outside the timestamp span is free.

### Precise-readback command-buffer envelope diagnostic

- Issue 77 extends only the existing opt-in phase-timing path with a fixed pool of 64 reusable
  three-timestamp slots. Each measured command buffer records its start, the point immediately
  before the readback barrier, and the point immediately after the copy.
- Every slot is associated with the exact scheduler timeline tick submitted for that command
  buffer. A readback-marked slot cannot be reset until its result is consumed after the existing
  synchronous finish; completed command buffers without a readback may be reused. Exhaustion is
  explicit and fails closed instead of overwriting an unconsumed measurement.
- The diagnostic adds no queue submission and no new wait. It reports GPU time before the readback,
  the full command-buffer envelope through the copy, successful/failed samples, slot exhaustion,
  and the envelope as a percentage of the already measured current-command-buffer CPU wait.
- Selector-off creates neither the command-buffer query pool nor timestamp commands. This is a
  workload-attribution probe, not a performance change: foreground evidence must still prove
  correct rendering, controller input, stereo audio, zero query failures/exhaustion, and a clean
  exit before the result can be accepted.
- The exact feature binary (`5afbdf81`, 374,479,656 bytes, SHA-256
  `9287b1f3d2b2bfc7a4666bc28107dad9e93b5875a1798664638996ab78dae48a`) completed the foreground
  run `20260821-231302-fork`. It reached correctly lit cannery gameplay, retained the Steam Deck
  controller in slot 0 with motion sensors, opened the main 48 kHz stereo output, saved both clean
  and HUD screenshots, dumped the cache, and exited 0 through the supported IPC stop path.
- Across the final eight intervals, all 677 measured downloads produced valid envelope triplets
  with zero query failures or slot exhaustion. GPU time before the readback was 2,118.567 ms; the
  barrier-and-copy span was 23.927 ms; and the full GPU envelope was 2,145.069 ms versus 2,569.242
  ms of current-command-buffer CPU wait. The envelope therefore explains 83.490% of that wait,
  while the measured copy span explains only 0.931%.
- This accepts the diagnostic, not an optimization. About 98.8% of the measured GPU envelope was
  already recorded before the readback barrier; the remaining CPU-wait gap can include driver,
  timeline-signal, scheduling, and wake-up costs. The next gate should partition or reduce the
  earlier command stream without weakening precise-readback correctness.

### Bounded early precise-readback submits

- Issue 79 / PR 80 turns the command-envelope result into a bounded, opt-in scheduling change.
  Successfully emitted guest draws and dispatches are counted, and a validated nonzero budget
  submits the current Vulkan command buffer without waiting. The existing precise-readback Finish,
  copy, CPU writeback, and dirty tracking remain intact. Zero is the code and launcher default.
- The Deck launcher accepts only zero or powers of two from 32 through 4096, records the resolved
  value and source, and fails closed to zero. Runtime counters attribute residual commands before
  each readback plus early-submit count, draw count, and dispatch count. The exact feature binary
  is 374,492,144 bytes with SHA-256
  `8ac1d9b3e4def5627d86f5df953c69a7774f1815552e767d2b5005f56c651a1c`.
- Six exact-binary foreground Gamescope runs reached the same correctly lit cannery tutorial scene
  with Delsin, complete geometry and text, controller slot 0 with motion sensors, the main 48 kHz
  stereo output, zero query failures or slot exhaustion, cache closure, and exit status 0.
- The primary two-pair final-600 comparison used budget 256. Candidate averages were 9.290 median
  FPS, 9.117 mean FPS, 108.236 ms median frame time, and 141.632 ms p95 frame time. Control averages
  were 8.562 median FPS, 8.426 mean FPS, 116.797 ms median frame time, and 159.152 ms p95 frame time.
  That is +8.500% median FPS, +8.198% mean FPS, -7.330% median frame time, and -11.008% p95 frame
  time, with +10.470% median CPU load.
- A later cooled-control/hot-candidate pair also favored the candidate, but its 6.062-FPS control
  and 64% median GPU load were outliers. It is preserved as directionally supportive evidence and
  excluded from the headline estimate rather than used to inflate the result.
- Budget 256 is accepted for the Second Son Deck profile. This is a modest stationary-scene and
  frame-pacing improvement, not a 30-FPS result or proof of whole-game playability; the measured
  scene remains roughly 9-10 FPS.

### Independent early-submit activation correction

- Issue 81 / PR 82 corrects an integration mistake found after PR 80: command counting and the
  work budget had been initialized only by the optional phase-timing diagnostic. The launcher
  could resolve budget 256 while the normal phase-timing-off profile silently left early submits
  inactive. The corrected scheduler owns independent work counters; phase timing, GPU-envelope
  timestamps, and budgeted submits can now each remain off or run independently.
- The exact corrected feature binary (`b7bf3f5c`, 374,495,832 bytes, SHA-256
  `b900559a0a5ae8ccd5a5beede45fccf2f9a2cd0ff3a05e8f63e122f26fa18631`) completed two matched
  budget-0/budget-256 foreground pairs with phase timing and the GPU envelope both off. All four
  runs reached the same correctly lit cannery tutorial scene, retained controller slot 0 and the
  main 48 kHz stereo output, dumped the cache, and exited 0.
- The two-run final-600 average improved from 8.569 to 10.012 median FPS (+16.842%) and from 8.876
  to 10.426 mean FPS (+17.466%). Median frame time fell from 116.696 to 99.875 ms (-14.414%), mean
  frame time fell 18.106%, and p95 frame time fell 35.563%. Both individual pairs agreed:
  +16.677% and +17.007% median FPS.
- The final-eight readback intervals also repeated the mechanism. Candidate averages recorded
  549.810 ms of Finish time versus 2,249.331 ms for controls (-75.557%) and 499.580 ms of current
  wait versus 2,174.646 ms (-77.027%). Every candidate early submit represented exactly 256
  counted draws plus dispatches; controls recorded zero work tracking and zero early submits.
- The correction supersedes PR 80's profile-selection claim, not its exact diagnostic-enabled
  evidence. Budget 256 is selected only after this independent phase-off proof. The result remains
  a stationary-scene improvement around 10 FPS, not whole-game or 30-FPS playability proof.

### Post-readback SpinLock class attribution

- Issue 84 re-profiled the accepted phase-off budget-256 build after synchronous readback Finish
  fell to roughly 7% of sampled wall time. A bounded 15-second user-cycle profile attributed
  30.80% to the GPU communication thread, 30.57% to the translated game main thread, and 35.51%
  to the five job workers. `Common::SpinLock::lock` was the hottest named host symbol at 27.19%,
  but the captured caller stacks did not identify the owning lock class.
- Issue 85 / PR 86 adds fixed, off-by-default class counters instead of guessing. The four concrete
  users are tagged as page tracking, region tracking, slab allocation, or sleep queue, with a
  generic fallback. Enabled counters record acquisitions, contended acquisitions, local spin
  iterations, maximum spins, try-lock attempts, and failures; they emit one compact record on the
  existing readback-stat cadence and use no dynamic storage or per-acquisition logging.
- The exact feature binary (`808a91f6`, 374,542,888 bytes, SHA-256
  `052744cdf64bd5e1e04a26e93360d1ed417e0bbc8dc0dfab6dfefc19f47bf8c4`) completed clean
  selector-off and selector-on foreground runs. Both retained correct cannery rendering,
  controller slot 0, the main 48 kHz stereo output, cache closure, and exit 0. Selector-off emitted
  no class records; selector-on emitted 98 reconciled intervals.
- Across the enabled run, the sleep queue made 304,531 acquisitions, 70,455 of them contended
  (23.136%), and accumulated 3,074,155,468 pause-loop iterations with a 950,536-iteration maximum.
  The slab allocator made 100,030 acquisitions with zero measured contention or spins. Generic,
  page-manager, and region-manager SpinLock buckets were zero; the latter two use the platform
  adaptive-mutex path in this build.
- Diagnostic overhead was bounded: enabled versus disabled changed final-600 median FPS by
  -0.019% and median frame time by +0.019%; mean FPS changed -0.675%. This accepts the attribution
  tool, not a performance change. The next gate is a sleep-queue-only bounded wait strategy, not a
  global SpinLock replacement.

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
- The exact issue 75 binary (`cb16d254`, SHA-256 `05feccfeae0cb515f37b0be4de535dceb54d60b23377e0a62d2d018d9f18dc78`)
  completed two foreground runs with 64-bit, 10 ns GPU timestamps and zero query failures. The
  primary run `20260821-224357-fork` measured 664 timestamped downloads in its final eight intervals:
  33.495 ms from the pre-barrier through the copy versus 3,744.076 ms of current-command-buffer CPU
  wait, or 0.895%. The screenshot replicate `20260821-224527-fork` measured 1.059% over its final
  eight intervals, saved clean game/HUD images, retained controller and 48 kHz stereo output, dumped
  the cache, and exited 0. This proves the measured barrier/copy span is a small part of the current
  wait in these runs; it does not prove where the remaining time goes or provide an FPS gain.
- Remaining limitation: the correctness path is substantially below real-time on the Steam Deck.
  Precise GPU-to-CPU readbacks dominate frame time, so 30 FPS is not yet achieved even though the
  title is now visually correct, audible, controller-connected, saveable, and in gameplay.
