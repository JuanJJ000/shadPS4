<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Second Son fidelity and high-refresh evidence

This is the public, hash-bound receipt for issue #137 and draft PR #139. The captures used the
same owned CUSA00223 v1.00 save, an isolated copy of the shadPS4 user profile, a warm 636-pipeline
cache, and optimized Linux binary SHA-256
`0d0741b6d2bf337db576e762f8008494cbff4bf0f0b5099bf565e4b87aa9190c` at commit
`fd44306c2f9fd19dd167a363966f002ac2dde4ff`.

## RR — Really Readable rundown

### Proven

- The game calls `sceVideoOutSetBufferAttribute` and `RegisterBuffers` with 1920×1080 buffers.
  Requests for 1280×720 and 1920×1080 guest-display metadata both produced 1920×1080 pre-scale
  game screenshots. The new shadPS4 internal-screen setting does not change this title's render
  buffer allocation.
- Gamescope and Vulkan created the exact requested 2560×1440, 3840×2160, and 7680×4320
  swapchains. Post-scale screenshots had those exact dimensions.
- The 120 Hz profiles measured approximately 120 host vblanks/s. During the loaded unattended
  crash-site scene, unique guest flips settled near 18–20/s at both 60 and 120 Hz. A 120 Hz
  presentation is therefore not 120 FPS gameplay.
- Warm runs preloaded 636 pipelines. The FSR-on 1440p, 4K, and 8K captures compiled zero graphics,
  compute, or guest shaders.
- The bounded 8K/60 FSR+RCAS run peaked at 10.38 GB VRAM, 46% GPU load, and 163 W. The bounded
  8K/120 run peaked at 10.51 GB VRAM, 50% GPU load, and 248 W. Both left the live Steam profile
  byte-for-byte unchanged.
- The issue #140 IPC-stop launcher candidate completed a 35-second post-load Gamescope run with
  both screenshot modes, status 0, a normal play-time/cache close, no Vulkan assertion, no retained
  processes, and an unchanged live profile.
- A matched-camera 8K/60 control with FSR and RCAS disabled had consistently lower grayscale edge
  energy after resizing to the host's 2560×1440 display. Across four static-heavy crops, the
  FSR+RCAS image measured about 1.3–2.1% higher. This is a directional sharpness measure, not a
  perceptual-quality score.
- Issue #141 traced a filtered-logging hot path to 255,269 and 256,727 Linux
  `pthread_getname_np` calls in two 27-second control runs. Gating `LOG_GENERIC` before argument
  evaluation reduced both patched runs to 1,320 calls, a 99.48% reduction against the control
  mean. All four runs exited status 0 and left the live Steam profile unchanged.
- In the same A/B/A/B sequence, MangoHud's final-ten-second mean rose from 21.67 and 21.12 FPS in
  the controls to 22.72 and 22.37 FPS in the candidates. The two-run averages are 21.395 versus
  22.545 FPS, a replicated 5.38% improvement. Direct guest-flip cadence in the first A/B/A legs
  changed from 20.774 / 22.289 / 20.996 FPS, or 6.72% above the mean of the two controls.
- The exact-commit replication used commit `1f169375a180be331337fdee31d0e9048dd040b5`
  and binary SHA-256 `b2daafb47298d930c75cb72100e883d4af0ad4853cdbfba65cda36a13c5f9407`.
  The focused logging regression test, all 78 settings tests, and all 134 Deck-tool tests passed
  locally on Linux.

| Profile | Actual game buffer | Actual swapchain | Host refresh | Loaded guest flips | Peak VRAM | Peak GPU |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1440p/60, FSR+RCAS | 1920×1080 | 2560×1440 | 60 Hz | about 20 FPS | 6.88 GB | 64% |
| 4K/60, FSR+RCAS | 1920×1080 | 3840×2160 | 60 Hz | about 19–20 FPS | 7.55 GB | 62% |
| 4K/120, FSR+RCAS | 1920×1080 | 3840×2160 | 120 Hz | about 19–20 FPS | 7.58 GB | 40% |
| 8K/60, FSR+RCAS | 1920×1080 | 7680×4320 | 60 Hz | about 18–19 FPS | 10.38 GB | 46% |
| 8K/120, FSR+RCAS | 1920×1080 | 7680×4320 | 120 Hz | about 18–19 FPS | 10.51 GB | 50% |

Screenshot SHA-256 receipts:

- 4K/60 HUD: `7e91984b125ccbd3fae774043b74fd4fb6621b83c63d440260c4ea7a74857795`
- 4K/120 HUD: `fde97d02ec8a43f9e701d456cad6d2469f191e420a2458078b49bbcea6611d91`
- 8K/60 FSR+RCAS HUD: `f20693c4aa13cec2b5f48b2561d406b54f1bbf2eaed1f14714c4ea7b7f7a1e26`
- 8K/120 FSR+RCAS HUD: `5ad982b50660de4802e14c9ec6f8e006bdbbf9f27cf0adc730eb130775350b94`
- 8K/60 plain-scaler HUD: `68fc85c144062d3c8dd141f6f4ed2cce814ab542eefbca1330bb3b78046d4526`

### Inference

- Output scaling is not the cause of the loaded scene's low guest rate: 8K/120 leaves substantial
  GPU and VRAM headroom while unique game flips remain near 20/s.
- Filtered log argument evaluation was a real CPU-side contributor in the serialized GPU command
  lane. Removing it produced a repeatable gain, but the remaining roughly 21–23 FPS phase proves
  that it was not the dominant bottleneck.
- FSR EASU with RCAS attenuation 0 is the strongest current clarity candidate for this machine.
  It improves edge retention, but it cannot restore texture or geometry detail that is absent from
  the fixed 1920×1080 source.
- Real native 1440p/4K rendering requires a guarded game patch or hook at the title's allocation
  site. Relabeling shadPS4's guest-display metadata is insufficient.

### Unknown / next gate

- Interactive traversal, combat, camera motion, cutscenes, controller/touch/QTE timing, audio,
  game speed, and long-session stability still need to be tested on the exact candidate.
- The remaining loaded-scene limit still needs finer attribution inside GPU command processing,
  especially resource binding, buffer/texture-cache lookup and synchronization, page protection,
  and pipeline-key lookup. The high-output runs rule out simple RTX 3090 saturation, and the
  post-budget readback timing no longer supports precise-readback finish as the dominant cost.
- Whether the guarded v1.00 motion-blur exposure patch improves moving-image clarity without
  artifacts remains unproven.
- A normal user-requested in-game exit remains to be included in the interactive acceptance run.
  The separate issue #140 timeout-order defect is fixed and locally proven in this branch, but is
  not integrated until PR #139 lands.

No 4K, 8K, or 120 Hz profile is promoted to the normal Steam launch by this receipt.
