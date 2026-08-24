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
readback_work_budget="${SECOND_SON_READBACK_WORK_BUDGET:-0}"

if [[ ! "${capture_seconds}" =~ ^(0|[1-9][0-9]*)$ ]]; then
  echo "SECOND_SON_CAPTURE_SECONDS must be zero or a positive integer" >&2
  exit 2
fi

case "${validate_only}" in
  0|1) ;;
  *)
    echo "SECOND_SON_VALIDATE_ONLY must be 0 or 1" >&2
    exit 2
    ;;
esac

case "${readback_work_budget}" in
  0|32|64|128|256|512|1024|2048|4096) ;;
  *)
    echo "SECOND_SON_READBACK_WORK_BUDGET must be zero or a power of two from 32 to 4096" >&2
    exit 2
    ;;
esac

for required in "${binary}" "${eboot}" "${repo_dir}/deck_tools/second_son_bazzite_config.json" "${repo_dir}/deck_tools/second_son_bazzite_input.ini"; do
  if [[ ! -f "${required}" ]]; then
    echo "Missing required file: ${required}" >&2
    exit 1
  fi
done

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

# The capture profile is disposable. Copies ensure the runtime cannot mutate Steam's saves,
# title configuration, or warmed pipeline cache.
seed_file config.json
seed_file keys.json
seed_file users.json
seed_directory home
seed_directory "cache/${title_id}"

install -m 0644 "${repo_dir}/deck_tools/second_son_bazzite_config.json" "${shad_user}/custom_configs/${title_id}.json"
install -m 0644 "${repo_dir}/deck_tools/second_son_bazzite_input.ini" "${shad_user}/input_config/${title_id}.ini"

# A missing global profile would be generated before the title profile loads. Seed a minimal
# controlled base when the live profile was intentionally unavailable.
if [[ ! -f "${shad_user}/config.json" ]]; then
  install -m 0644 "${repo_dir}/deck_tools/second_son_bazzite_config.json" "${shad_user}/config.json"
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
  echo "precise_readback_work_budget=${readback_work_budget}"
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
if command -v mangohud >/dev/null 2>&1; then
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
  "SHADPS4_PRECISE_READBACK_WORK_BUDGET=${readback_work_budget}"
)

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
if cmp "${run_dir}/evidence/live-before.sha256" "${run_dir}/evidence/live-after.sha256" >/dev/null; then
  echo "true" >"${run_dir}/evidence/live-profile-unchanged.txt"
else
  echo "false" >"${run_dir}/evidence/live-profile-unchanged.txt"
  echo "Live Steam profile changed during capture; inspect the recorded hashes." >&2
fi

title_log="${shad_user}/log/${title_id}.log"
if [[ -f "${title_log}" ]]; then
  rg -n "Game-specific config used|GPU readbacksMode|GPU vblankFrequency|GPU shouldCopyGPUBuffers|PipelineCache" "${title_log}" >"${run_dir}/evidence/title-config-proof.txt" || true
fi

if [[ -f "${repo_dir}/deck_tools/summarize_mangohud.py" ]]; then
  python3 "${repo_dir}/deck_tools/summarize_mangohud.py" "${run_dir}" >"${run_dir}/evidence/performance-summary.txt" 2>&1 || true
fi

if [[ "${capture_seconds}" != "0" && ("${exit_status}" == "124" || "${exit_status}" == "143") ]]; then
  echo "Bounded capture completed: ${run_dir}"
  exit 0
fi

echo "Capture completed with emulator status ${exit_status}: ${run_dir}"
exit "${exit_status}"
