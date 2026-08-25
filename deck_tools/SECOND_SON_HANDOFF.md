<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# inFAMOUS Second Son Bazzite handoff

Checkpoint: **2026-08-24, 8:51 PM CDT**

This is the public resume authority for the Second Son Bazzite/RTX 3090 lane. It records what is
actually proven, what is merely promising, and the shortest safe continuation path. It contains no
owned game bytes, save data, pipeline/shader contents, virtual addresses, or private host paths.

## RR — Really Readable rundown

### Proven

- The active implementation line is `perf/second-son-fidelity-refresh`, based on
  `second-son/perfect-bazzite` at `8086ffb4`. Draft [PR
  #139](https://github.com/deucebucket/shadPS4/pull/139) owns the cumulative fidelity/performance
  work. The latest implementation checkpoint is
  [`e7c03042`](https://github.com/deucebucket/shadPS4/commit/e7c03042).
- The branch contains current upstream `main` at `7fb1a530`; the final fetch showed zero upstream
  commits absent from the branch. Upstream is configured for fetch-only use in this workflow.
- The exact `e7c03042` Linux executable has SHA-256
  `68d12a41294bd56caaf0117c4583f4cae821a58a7303221d1adf3d6508c53f7c`, embeds
  `v.0.18.0-237-ge7c03042`, passes the focused and complete local Linux gates, preloads 633 warm
  pipelines with zero compilation/regeneration, exits 0, and leaves the live title profile
  unchanged.
- The normal Bazzite profile is 2560×1440 output, fixed 1920×1080 guest buffers, FSR EASU with
  RCAS attenuation 0, Mailbox present, 60 Hz guest vblank, precise readbacks, and a title-specific
  128-operation non-blocking readback-submit budget. None of the code optimizations lower visual
  quality.
- Standard SDL/Xbox input, Back-as-touchpad-click, Back+D-pad synthetic directional swipes, and
  L3+R3 sensorless spray assist are present on the integration line.
- Guarded extraction, fingerprinting, DDS export/edit/rebuild, reversible XPPS overlay staging,
  and v1.00 motion-blur-patch validation tools exist. No replacement texture or motion-blur patch
  has been promoted to normal play.

### Inference

- The strongest settled Bazzite improvements come from reducing serialized CPU work and startup
  compilation, not from lowering output quality. The RTX 3090 had substantial headroom in the
  bounded intro stress runs, but that does not establish city headroom.
- The latest stream-bound change removes a measured 16.08% GpuComm leaf. A small +1.72% warm-intro
  FPS direction supports retaining it, but neutral median/p95 and instrumentation outliers reject a
  dramatic throughput claim.

### Unknown / blocker

- Every current desktop performance capture is from the intro/crash-site workload before the city.
  City traversal, crowds, combat, smoke/particles, rapid camera motion, cutscenes, and long-session
  behavior are unmeasured and may move the limit toward different CPU paths, driver work, or the
  GPU itself.
- The exact `e7c03042` binary is isolated-run proven but has not been promoted into the installed
  Steam shortcut runtime. Steam launchability and exact-candidate deployment are separate gates.
- Prior interactive play exposed incomplete spray/QTE ergonomics. The branch has synthetic helpers,
  but a full exact-candidate pass must verify swipe-up, repeated QTEs, spray activation, controller
  reconnect, audio, game speed, and normal exit.
- No PR review has been submitted yet. PR #139 intentionally remains draft pending the interactive
  and city gates.
- Texture replacement is tool-complete only through reversible overlay construction. A meaningful
  edited texture still needs an in-game activation screenshot and rollback proof.

## Current source topology

| Role | Ref | State |
|---|---|---|
| Upstream snapshot | `upstream/main` at `7fb1a530` | Fully contained by the active line |
| Bazzite integration base | `second-son/perfect-bazzite` at `8086ffb4` | Stable pre-PR #139 base |
| Active cumulative branch | `perf/second-son-fidelity-refresh` | PR #139, draft |
| Latest implementation | `e7c03042` | Submission-side stream-bound recorder |
| Umbrella investigation | [issue #108](https://github.com/deucebucket/shadPS4/issues/108) | Open |
| Latest focused issue | [issue #153](https://github.com/deucebucket/shadPS4/issues/153) | Closes with PR #139 |

Do not add the measured percentages below together: each comes from a different branch point,
scene window, or profiler question.

## What the cumulative branch changes

| Lane | Result | Claim boundary |
|---|---|---|
| Fidelity/cadence capture | Proves fixed 1920×1080 guest buffer, exact 1440p/4K/8K swapchains, and separate host/guest cadence | Output extent is not native internal resolution |
| Bounded launcher stop | Clean IPC stop before nested Gamescope teardown | Not a substitute for user-requested normal exit |
| Filtered logging | Removes 99.48% of measured thread-name queries; paired intro gain 5.38% | Intro-only |
| Warm pipeline cache | Reuses 633 compatible pipelines with zero guest compilation | Startup/stutter improvement, not sustained FPS by itself |
| Partial-image transition cache | Six-run intro mean +15.33%; targeted barrier leaf reduced | Exact-generation read-only states only |
| Clean persistent-buffer reuse | Four-run intro mean +11.19% | Guarded clean-read path only |
| Host tiling shader cache | Ten cold writes followed by ten warm loads and zero rebuilds | Startup only; no sustained-FPS claim |
| Atomic VMA readers | Mutex-unlock profile residency 17.00% to 1.57%; +1.24% post-load bracket | Tail neutral |
| Stream-watch cursor | Corrects last-valid-entry coalescing | No performance claim |
| Submit-boundary watches | Removes per-commit scheduler tick/vector work; +0.82% intro direction | Small CPU-efficiency claim |
| Pending flag | Removes duplicate bound store; +1.68% intro direction | Hardware residency did not improve |
| Shared-memory reservation | Restores required `Commit()` before descriptor bind | Correctness path not hit in bounded title scene |
| Submission-side bound recorder | Removes old 16.08% GpuComm leaf; +1.72% intro direction | Median/p95 neutral; city untested |

The detailed receipts and exact hashes are in
[`SECOND_SON_FIDELITY_REFRESH.md`](SECOND_SON_FIDELITY_REFRESH.md) and the [Second Son wiki dev
log](https://github.com/deucebucket/shadPS4/wiki/Second-Son-Steam-Deck-Dev-Log).

## Current input contract

The checked-in Bazzite bindings use:

- normal SDL/Xbox face buttons, sticks, triggers, shoulders, and D-pad;
- Back/View as touchpad-center click;
- Back/View + D-pad direction as a complete synthetic directional touchpad swipe;
- L3 + R3 as the sensorless spray sequence.

These mappings are implementation evidence, not whole-game acceptance. Resume on the exact branch
binary and record each required gesture, whether the prompt cleared, and whether ordinary controls
were restored afterward. Do not auto-complete all touch events globally: gameplay touch, menu touch,
QTE timing, and spray state do not share one safe bypass invariant.

## Current fidelity contract

- Keep the title's real 1920×1080 source and 2560×1440 desktop output distinct.
- Keep FSR enabled and RCAS attenuation at 0 unless a matched moving-image A/B proves a better
  setting without halos, shimmer, or texture loss.
- Keep 4K/8K and 120 Hz as bounded stress profiles. They prove swapchain/output behavior, not
  native detail or 120 FPS gameplay.
- The guarded v1.00 motion-blur patch remains experimental until matched moving-camera screenshots,
  cutscenes, particles, HUD, and long-session stability pass.
- Do not judge GPU headroom from the intro. Capture city traversal before changing resolution,
  present mode, guest cadence, or quality settings.

## Current modding contract

The XPPS lane can identify guarded archive rows, classify bitmap payloads, export supported DDS,
normalize edits, rebuild reversible overlays, verify round trips, and stage receipt-bound activation.
It cannot yet claim that a visible replacement texture renders correctly in Second Son.

The next safe mod proof is deliberately small:

1. Choose one visually obvious, non-critical texture with a complete source fingerprint.
2. Export and round-trip it unchanged; require byte/descriptor invariants.
3. Produce one edited DDS with the same dimensions/format/mips.
4. Build a reversible overlay and record its manifest/hash.
5. Launch through an isolated profile, capture the exact scene, and prove removal restores baseline.

Do not commit owned archive rows, decrypted executable data, save data, caches, or replacement assets
derived from the owned dump.

## Local Linux acceptance already passed

- complete Deck suite: 143/143;
- focused stream-watch suite: 5/5;
- focused atomic-reader-lock suite: 5/5;
- optimized Linux link: passed;
- exact committed-binary warm smoke: 633 preloaded, zero graphics/compute/shader compiles, zero
  regeneration, exit 0, live profile unchanged;
- `git diff --check`: passed.

Repository CI is not the acceptance authority for this lane. PR-scoped Deck-tool jobs passed; the
known REUSE backlog remains, and duplicate macOS/Windows/full-matrix jobs are intentionally canceled
in favor of the requested local Linux/Bazzite evidence.

## Exact resume order

1. Fetch `origin` and `upstream`, then confirm `upstream/main...HEAD` has zero commits on the
   upstream-only side. Do not merge by assumption.
2. Check out `perf/second-son-fidelity-refresh`, verify its remote tip, and build the optimized Linux
   target with the existing project-local compatibility setup. Do not install another toolchain.
3. Promote the exact candidate into the Steam runtime only through the host installation workflow;
   hash the installed executable and confirm its embedded description before playing.
4. Reach the city using the exact candidate. Preserve the live save/profile and capture an isolated
   2–5 minute traversal/combat/particles run with cadence and MangoHud evidence.
5. Compare city CPU/GPU/VRAM/frame-time behavior with the intro. Profile only the newly dominant
   path; do not optimize the old intro leaf again.
6. Exercise Back+D-pad swipes, L3+R3 spray assist, touch click, ordinary controls, audio, game speed,
   controller reconnect, cutscenes, and normal exit. Record failures as focused issues.
7. If exact-candidate city and input gates pass, update the receipt/wiki, request review on PR #139,
   and only then merge it into `second-son/perfect-bazzite`.
8. Run the one-texture reversible overlay proof as a separate branch/issue; do not mix owned mod
   artifacts into the emulator PR.

## Stop conditions

- Do not claim city performance from the intro evidence.
- Do not claim native 4K/8K from an upscaled 1920×1080 guest surface.
- Do not claim 60/120 FPS from host vblank cadence.
- Do not promote a patch, input bypass, or texture overlay without exact-scene proof and rollback.
- Do not force-rewrite or force-push published history without explicit owner approval.
- Do not make the installed Steam runtime point at an unverified dirty worktree binary.
