#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 shadPS4 Project
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
title_id="CUSA00223"
binary="${SECOND_SON_BINARY:-${repo_dir}/build-linux-local/shadps4}"
game_root="${SECOND_SON_ROOT:-/var/home/deucebucket/ai-drive/projects/bucketcomps/infamous_secondson/CUSA00223}"
eboot="${SECOND_SON_EBOOT:-${game_root}/eboot.bin}"
live_user_root="${SECOND_SON_LIVE_USER_ROOT:-${XDG_DATA_HOME:-${HOME}/.local/share}/shadPS4}"
data_root="${SECOND_SON_BAZZITE_DATA_ROOT:-${repo_dir}/scratch/second-son-bazzite}"
capture_seconds="${SECOND_SON_CAPTURE_SECONDS:-120}"
validate_only="${SECOND_SON_VALIDATE_ONLY:-0}"
readback_work_budget="${SECOND_SON_READBACK_WORK_BUDGET:-profile}"
patch_xml="${SECOND_SON_PATCH:-}"
cache_seed_root="${SECOND_SON_CACHE_SEED_ROOT:-${live_user_root}}"
skip_cache_seed="${SECOND_SON_SKIP_CACHE_SEED:-0}"
pipeline_trace="${SECOND_SON_PIPELINE_TRACE:-0}"
internal_resolution="${SECOND_SON_INTERNAL_RESOLUTION:-profile}"
output_resolution="${SECOND_SON_OUTPUT_RESOLUTION:-profile}"
vblank_frequency="${SECOND_SON_VBLANK_FREQUENCY:-profile}"
fsr_mode="${SECOND_SON_FSR:-profile}"
rcas_mode="${SECOND_SON_RCAS:-profile}"
rcas_attenuation="${SECOND_SON_RCAS_ATTENUATION:-profile}"
present_mode="${SECOND_SON_PRESENT_MODE:-profile}"
gamescope_enabled="${SECOND_SON_GAMESCOPE:-0}"
gamescope_adaptive_sync="${SECOND_SON_GAMESCOPE_ADAPTIVE_SYNC:-0}"
videoout_stats_interval="${SECOND_SON_VIDEOOUT_STATS_INTERVAL:-0}"
screenshot_after_seconds="${SECOND_SON_SCREENSHOT_AFTER_SECONDS:-0}"

if [[ ! "${capture_seconds}" =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "SECOND_SON_CAPTURE_SECONDS must be zero or a positive integer" >&2
  exit 2
fi

case "${validate_only}:${skip_cache_seed}:${pipeline_trace}" in
  0:0:0|0:0:1|0:1:0|0:1:1|1:0:0|1:0:1|1:1:0|1:1:1) ;;
  *)
    echo "SECOND_SON_VALIDATE_ONLY, SECOND_SON_SKIP_CACHE_SEED, and" \
      "SECOND_SON_PIPELINE_TRACE must each be 0 or 1" >&2
    exit 2
    ;;
esac

case "${gamescope_enabled}:${gamescope_adaptive_sync}" in
  0:0|0:1|1:0|1:1) ;;
  *)
    echo "SECOND_SON_GAMESCOPE and SECOND_SON_GAMESCOPE_ADAPTIVE_SYNC must each be 0 or 1" >&2
    exit 2
    ;;
esac

if [[ ! "${videoout_stats_interval}" =~ ^(0|[1-9]|[1-5][0-9]|60)$ ]]; then
  echo "SECOND_SON_VIDEOOUT_STATS_INTERVAL must be zero or 1 through 60 seconds" >&2
  exit 2
fi

if [[ ! "${screenshot_after_seconds}" =~ ^(0|[1-9]|[1-9][0-9]|[1-5][0-9][0-9]|600)$ ]]; then
  echo "SECOND_SON_SCREENSHOT_AFTER_SECONDS must be zero or 1 through 600 seconds" >&2
  exit 2
fi

case "${readback_work_budget}" in
  profile|0|32|64|128|256|512|1024|2048|4096) ;;
  *)
    echo "SECOND_SON_READBACK_WORK_BUDGET must be profile, zero, or a power of two from 32 to 4096" >&2
    exit 2
    ;;
esac

for required in "${binary}" "${eboot}" \
  "${repo_dir}/deck_tools/second_son_bazzite_config.json" \
  "${repo_dir}/deck_tools/second_son_bazzite_input.ini" \
  "${repo_dir}/deck_tools/second_son_bazzite_profile.py"; do
  if [[ ! -f "${required}" ]]; then
    echo "Missing required file: ${required}" >&2
    exit 1
  fi
done

if [[ -n "${patch_xml}" ]]; then
  if [[ ! -f "${patch_xml}" ]]; then
    echo "Missing requested Second Son patch: ${patch_xml}" >&2
    exit 1
  fi
  python3 "${repo_dir}/deck_tools/second_son_v100_patch_guard.py" "${eboot}" "${patch_xml}"
fi

if [[ ! -x "${binary}" ]]; then
  echo "Second Son binary is not executable: ${binary}" >&2
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  jq empty "${repo_dir}/deck_tools/second_son_bazzite_config.json"
fi

run_stamp="$(date +%Y%m%d-%H%M%S)-$$"
run_dir="${data_root}/runs/${run_stamp}"
xdg_data="${run_dir}/xdg-data"
shad_user="${xdg_data}/shadPS4"
mkdir -p "${shad_user}/custom_configs" "${shad_user}/input_config" "${shad_user}/cache"
mkdir -p "${shad_user}/home/1000/savedata" "${shad_user}/home/1000/trophy"
mkdir -p "${shad_user}/home/1000/inputs" "${run_dir}/evidence"

seed_file() {
  local relative="$1"
  if [[ -f "${live_user_root}/${relative}" ]]; then
    install -D -m 0600 "${live_user_root}/${relative}" "${shad_user}/${relative}"
  fi
}

seed_directory() {
  local relative="$1"
  if [[ -d "${live_user_root}/${relative}" ]]; then
    mkdir -p "${shad_user}/${relative}"
    cp -a --reflink=auto "${live_user_root}/${relative}/." "${shad_user}/${relative}/"
  fi
}

seed_cache_directory() {
  local relative="cache/${title_id}"
  if [[ -d "${cache_seed_root}/${relative}" ]]; then
    mkdir -p "${shad_user}/${relative}"
    cp -a --reflink=auto "${cache_seed_root}/${relative}/." "${shad_user}/${relative}/"
  fi
}

# The capture profile is disposable. Copies ensure the runtime cannot mutate Steam's saves,
# title configuration, or warmed pipeline cache.
seed_file config.json
seed_file keys.json
seed_file users.json
seed_directory home
if [[ "${skip_cache_seed}" == "0" ]]; then
  seed_cache_directory
fi

install -m 0644 "${repo_dir}/deck_tools/second_son_bazzite_config.json" "${shad_user}/custom_configs/${title_id}.json"
install -m 0644 "${repo_dir}/deck_tools/second_son_bazzite_input.ini" "${shad_user}/input_config/${title_id}.ini"

profile_receipt="${run_dir}/evidence/fidelity-profile.json"
python3 "${repo_dir}/deck_tools/second_son_bazzite_profile.py" \
  "${shad_user}/custom_configs/${title_id}.json" \
  --receipt "${profile_receipt}" \
  --internal-resolution "${internal_resolution}" \
  --output-resolution "${output_resolution}" \
  --vblank-frequency "${vblank_frequency}" \
  --fsr "${fsr_mode}" \
  --rcas "${rcas_mode}" \
  --rcas-attenuation "${rcas_attenuation}" \
  --present-mode "${present_mode}"

# A missing global profile would be generated before the title profile loads. Seed a minimal
# controlled base when the live profile was intentionally unavailable.
if [[ ! -f "${shad_user}/config.json" ]]; then
  install -m 0644 "${repo_dir}/deck_tools/second_son_bazzite_config.json" "${shad_user}/config.json"
fi

if [[ "${pipeline_trace}" == "1" || "${videoout_stats_interval}" != "0" ]]; then
  python3 - "${shad_user}/config.json" "${shad_user}/custom_configs/${title_id}.json" <<'PY'
import json
import sys
from pathlib import Path

for value in sys.argv[1:]:
    path = Path(value)
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("Log", {})["filter"] = (
        "*:Critical Input:Info Loader:Info Config:Info Lib.VideoOut:Info Render:Info "
        "Render.Vulkan:Info"
    )
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY
fi

snapshot_live() {
  local output="$1"
  {
    for relative in config.json "custom_configs/${title_id}.json" "input_config/${title_id}.ini"; do
      if [[ -f "${live_user_root}/${relative}" ]]; then
        sha256sum "${live_user_root}/${relative}"
      fi
    done
    for relative in "home/1000/savedata" "cache/${title_id}"; do
      if [[ -d "${live_user_root}/${relative}" ]]; then
        find "${live_user_root}/${relative}" -type f -print0 | sort -z | xargs -0 -r sha256sum
      fi
    done
  } >"${output}"
}

snapshot_live "${run_dir}/evidence/live-before.sha256"

mangohud_config="${run_dir}/MangoHud.conf"
cat >"${mangohud_config}" <<EOF
fps
frametime
frame_timing=1
cpu_stats
cpu_temp
gpu_stats
gpu_temp
gpu_power
ram
vram
position=top-left
font_size=20
background_alpha=0.5
autostart_log=1
log_interval=100
output_folder=${run_dir}
fps_metrics=avg,0.01,0.001
log_versioning
permit_upload=0
EOF

{
  echo "timestamp=${run_stamp}"
  echo "branch=$(git -C "${repo_dir}" branch --show-current)"
  echo "commit=$(git -C "${repo_dir}" rev-parse HEAD)"
  echo "binary=${binary}"
  sha256sum "${binary}"
  echo "eboot=${eboot}"
  sha256sum "${eboot}"
  echo "live_user_root=${live_user_root}"
  echo "isolated_user_root=${shad_user}"
  echo "capture_seconds=${capture_seconds}"
  echo "cache_seed_root=${cache_seed_root}"
  echo "cache_seed_skipped=$([[ "${skip_cache_seed}" == "1" ]] && echo true || echo false)"
  echo "pipeline_trace=$([[ "${pipeline_trace}" == "1" ]] && echo true || echo false)"
  echo "precise_readback_work_budget=${readback_work_budget}"
  echo "internal_resolution=${internal_resolution}"
  echo "output_resolution=${output_resolution}"
  echo "vblank_frequency=${vblank_frequency}"
  echo "fsr=${fsr_mode}"
  echo "rcas=${rcas_mode}"
  echo "rcas_attenuation=${rcas_attenuation}"
  echo "present_mode=${present_mode}"
  echo "gamescope=$([[ "${gamescope_enabled}" == "1" ]] && echo true || echo false)"
  echo "gamescope_adaptive_sync=$([[ "${gamescope_adaptive_sync}" == "1" ]] && echo true || echo false)"
  echo "videoout_stats_interval=${videoout_stats_interval}"
  echo "screenshot_after_seconds=${screenshot_after_seconds}"
  sha256sum "${profile_receipt}"
  if [[ -n "${patch_xml}" ]]; then
    echo "patch=${patch_xml}"
    sha256sum "${patch_xml}"
  else
    echo "patch=none"
  fi
  sha256sum "${shad_user}/config.json" "${shad_user}/custom_configs/${title_id}.json" "${shad_user}/input_config/${title_id}.ini"
  uname -a
  lscpu
  free -h
  nvidia-smi --query-gpu=name,driver_version,memory.total,pstate --format=csv,noheader 2>/dev/null || true
} >"${run_dir}/evidence/manifest.txt"

ln -sfn "${run_dir}" "${data_root}/current"
echo "Prepared isolated Second Son capture: ${run_dir}"
echo "The launcher deliberately uses XDG_DATA_HOME and does not pass --config-global or --override-root."

if [[ "${validate_only}" == "1" ]]; then
  snapshot_live "${run_dir}/evidence/live-after.sha256"
  cmp "${run_dir}/evidence/live-before.sha256" "${run_dir}/evidence/live-after.sha256"
  echo "Validation passed; live Steam profile is unchanged."
  exit 0
fi

launch=("${binary}" --game "${eboot}" --same-process --fullscreen true --show-fps)
if [[ -n "${patch_xml}" ]]; then
  launch+=(--patch "${patch_xml}")
fi
readarray -t resolved_fidelity < <(python3 - "${profile_receipt}" <<'PY'
import json
import sys

resolved = json.load(open(sys.argv[1], encoding="utf-8"))["resolved"]
print(resolved["output_resolution"][0])
print(resolved["output_resolution"][1])
print(resolved["vblank_frequency"])
PY
)
resolved_output_width="${resolved_fidelity[0]}"
resolved_output_height="${resolved_fidelity[1]}"
resolved_vblank_frequency="${resolved_fidelity[2]}"

if [[ "${gamescope_enabled}" == "1" ]]; then
  if ! command -v gamescope >/dev/null 2>&1; then
    echo "SECOND_SON_GAMESCOPE=1 requested, but gamescope is unavailable" >&2
    exit 1
  fi
  gamescope_launch=(
    gamescope
    -W "${resolved_output_width}"
    -H "${resolved_output_height}"
    -w "${resolved_output_width}"
    -h "${resolved_output_height}"
    -r "${resolved_vblank_frequency}"
    -f
    --force-windows-fullscreen
    --mangoapp
  )
  if [[ "${gamescope_adaptive_sync}" == "1" ]]; then
    gamescope_launch+=(--adaptive-sync)
  fi
  launch=("${gamescope_launch[@]}" -- "${launch[@]}")
elif command -v mangohud >/dev/null 2>&1; then
  launch=(mangohud "${launch[@]}")
fi

launch_env=(
  "XDG_DATA_HOME=${xdg_data}"
  "MANGOHUD_CONFIGFILE=${mangohud_config}"
  "SDL_GAMECONTROLLER_IGNORE_DEVICES=${SDL_GAMECONTROLLER_IGNORE_DEVICES:-0x04e8/0x7021}"
  "SHADPS4_FORCE_STEREO_DOWNMIX=${SHADPS4_FORCE_STEREO_DOWNMIX:-1}"
  "SHADPS4_READONLY_FORMATTED_BUFFER_LIMIT_MB=${SHADPS4_READONLY_FORMATTED_BUFFER_LIMIT_MB:-256}"
  "SHADPS4_PRECISE_READBACK_STATS=${SHADPS4_PRECISE_READBACK_STATS:-0}"
  "SHADPS4_PRECISE_READBACK_PHASE_TIMING=${SHADPS4_PRECISE_READBACK_PHASE_TIMING:-0}"
)
if [[ "${videoout_stats_interval}" != "0" ]]; then
  launch_env+=("SHADPS4_VIDEOOUT_CADENCE_STATS_INTERVAL=${videoout_stats_interval}")
fi
if [[ "${screenshot_after_seconds}" != "0" ]]; then
  launch_env+=("SHADPS4_VIDEOOUT_SCREENSHOT_AFTER_SECONDS=${screenshot_after_seconds}")
fi
if [[ "${readback_work_budget}" != "profile" ]]; then
  launch_env+=("SHADPS4_PRECISE_READBACK_WORK_BUDGET=${readback_work_budget}")
fi

ulimit -c 0
set +e
if [[ "${capture_seconds}" == "0" ]]; then
  env "${launch_env[@]}" "${launch[@]}" 2>&1 | tee "${run_dir}/console.log"
  exit_status="${PIPESTATUS[0]}"
else
  env "${launch_env[@]}" timeout --foreground --signal=TERM --kill-after=15s "${capture_seconds}s" "${launch[@]}" 2>&1 | tee "${run_dir}/console.log"
  exit_status="${PIPESTATUS[0]}"
fi
set -e

echo "${exit_status}" >"${run_dir}/evidence/emulator-exit-status.txt"
snapshot_live "${run_dir}/evidence/live-after.sha256"
# `cmp` is the condition of this `if`, so Bash deliberately exempts its nonzero result from
# `errexit`; a mismatch reaches the evidence-producing else branch below.
if cmp "${run_dir}/evidence/live-before.sha256" "${run_dir}/evidence/live-after.sha256" >/dev/null; then
  echo "true" >"${run_dir}/evidence/live-profile-unchanged.txt"
else
  echo "false" >"${run_dir}/evidence/live-profile-unchanged.txt"
  echo "Live Steam profile changed during capture; inspect the recorded hashes." >&2
fi

title_log="${shad_user}/log/${title_id}.log"
if [[ -f "${title_log}" ]]; then
  rg -n "Game-specific config used|GPU windowSize|GPU internalScreen|GPU fullScreen|GPU FSR|GPU readbacksMode|GPU readbackWorkSubmitBudget|GPU vblankFrequency|GPU shouldCopyGPUBuffers|PipelineCache|Guest display initialized|Guest flip rate set|VideoOut cadence|Requested game-only screenshot|Saved screenshot|Swapchain surface" "${title_log}" >"${run_dir}/evidence/title-config-proof.txt" || true
fi

if [[ -n "${patch_xml}" ]]; then
  # A requested patch is not an accepted capture unless the runtime proves it applied the exact
  # named entry. `rg` is the condition, so a miss reaches the evidence-producing failure branch.
  if ! rg -n "Applied patch: Disable Motion Blur Exposure \(CUSA00223 01\.00\)" \
    "${shad_user}/log" >"${run_dir}/evidence/patch-application-proof.txt"; then
    echo "Requested Second Son patch has no runtime application proof." >&2
    exit 1
  fi
fi

if [[ -f "${repo_dir}/deck_tools/summarize_mangohud.py" ]]; then
  python3 "${repo_dir}/deck_tools/summarize_mangohud.py" "${run_dir}" \
    --phase startup=0:15 --phase post-load=15: --tail-seconds 10 --bin-seconds 5 \
    >"${run_dir}/evidence/performance-summary.txt" 2>&1 || true
fi

if [[ "${pipeline_trace}" == "1" ]]; then
  {
    echo "Pipeline cache capture summary"
    printf 'preloaded_pipelines='
    grep -Eo 'Preloaded [0-9]+ pipelines' "${run_dir}/console.log" |
      tail -n 1 |
      grep -Eo '[0-9]+' || echo 0
    printf 'graphics_pipeline_compiles='
    grep -c 'Compiling graphics pipeline' "${run_dir}/console.log" || true
    printf 'compute_pipeline_compiles='
    grep -c 'Compiling compute pipeline' "${run_dir}/console.log" || true
    printf 'shader_compiles='
    grep -c 'Compiling .* shader' "${run_dir}/console.log" || true
    printf 'cache_regenerations='
    grep -c 'Regenerating the cache' "${run_dir}/console.log" || true
  } >"${run_dir}/evidence/pipeline-cache-summary.txt"
  grep -E 'Preloaded [0-9]+ pipelines|Compiling (graphics|compute) pipeline|Regenerating the cache' \
    "${run_dir}/console.log" >"${run_dir}/evidence/pipeline-cache-events.log" || true
fi

if [[ "${capture_seconds}" != "0" && ("${exit_status}" == "124" || "${exit_status}" == "143") ]]; then
  echo "Bounded capture completed: ${run_dir}"
  exit 0
fi

echo "Capture completed with emulator status ${exit_status}: ${run_dir}"
exit "${exit_status}"
