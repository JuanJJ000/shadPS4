#!/usr/bin/env bash
set -euo pipefail

variant="${1:-fork}"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
game_root="${SECOND_SON_ROOT:-/home/deck/Games/shadPS4-games/CUSA00223}"
eboot="${SECOND_SON_EBOOT:-${game_root}/eboot.bin}"
data_root="${SECOND_SON_DATA_ROOT:-/home/deck/Games/shadPS4-second-son}"
readback_stats_file="${SECOND_SON_READBACK_STATS_FILE:-${data_root}/readback-stats.txt}"
readback_stats_source="default"
if [[ -n "${SECOND_SON_READBACK_STATS:-}" ]]; then
  readback_stats="${SECOND_SON_READBACK_STATS}"
  readback_stats_source="environment"
elif [[ -r "${readback_stats_file}" ]]; then
  IFS= read -r readback_stats <"${readback_stats_file}" || true
  readback_stats="${readback_stats:-1}"
  readback_stats_source="${readback_stats_file}"
else
  readback_stats="1"
fi
case "${readback_stats}" in
  0|1) ;;
  *)
    echo "Ignoring invalid precise-readback stats value '${readback_stats}'; expected 0 or 1" >&2
    readback_stats="1"
    readback_stats_source="invalid-fallback"
    ;;
esac
readback_stats_interval="${SECOND_SON_READBACK_STATS_INTERVAL:-128}"
readback_phase_timing_file="${SECOND_SON_READBACK_PHASE_TIMING_FILE:-${data_root}/readback-phase-timing.txt}"
readback_phase_timing_source="default"
if [[ -n "${SECOND_SON_READBACK_PHASE_TIMING:-}" ]]; then
  readback_phase_timing="${SECOND_SON_READBACK_PHASE_TIMING}"
  readback_phase_timing_source="environment"
elif [[ -r "${readback_phase_timing_file}" ]]; then
  IFS= read -r readback_phase_timing <"${readback_phase_timing_file}" || true
  readback_phase_timing="${readback_phase_timing:-0}"
  readback_phase_timing_source="${readback_phase_timing_file}"
else
  readback_phase_timing="0"
fi
case "${readback_phase_timing}" in
  0|1) ;;
  *)
    echo "Ignoring invalid precise-readback phase timing value '${readback_phase_timing}'; expected 0 or 1" >&2
    readback_phase_timing="0"
    readback_phase_timing_source="invalid-fallback"
    ;;
esac
readback_work_budget_file="${SECOND_SON_READBACK_WORK_BUDGET_FILE:-${data_root}/readback-work-budget.txt}"
readback_work_budget_source="default"
if [[ -n "${SECOND_SON_READBACK_WORK_BUDGET:-}" ]]; then
  readback_work_budget="${SECOND_SON_READBACK_WORK_BUDGET}"
  readback_work_budget_source="environment"
elif [[ -r "${readback_work_budget_file}" ]]; then
  IFS= read -r readback_work_budget <"${readback_work_budget_file}" || true
  readback_work_budget="${readback_work_budget:-0}"
  readback_work_budget_source="${readback_work_budget_file}"
else
  readback_work_budget="0"
fi
case "${readback_work_budget}" in
  0|32|64|128|256|512|1024|2048|4096) ;;
  *)
    echo "Ignoring invalid precise-readback work budget '${readback_work_budget}'; expected 0 or a power of two from 32 through 4096" >&2
    readback_work_budget="0"
    readback_work_budget_source="invalid-fallback"
    ;;
esac
spinlock_stats_file="${SECOND_SON_SPINLOCK_STATS_FILE:-${data_root}/spinlock-stats.txt}"
spinlock_stats_source="default"
if [[ -n "${SECOND_SON_SPINLOCK_STATS:-}" ]]; then
  spinlock_stats="${SECOND_SON_SPINLOCK_STATS}"
  spinlock_stats_source="environment"
elif [[ -r "${spinlock_stats_file}" ]]; then
  IFS= read -r spinlock_stats <"${spinlock_stats_file}" || true
  spinlock_stats="${spinlock_stats:-0}"
  spinlock_stats_source="${spinlock_stats_file}"
else
  spinlock_stats="0"
fi
case "${spinlock_stats}" in
  0|1) ;;
  *)
    echo "Ignoring invalid SpinLock statistics value '${spinlock_stats}'; expected 0 or 1" >&2
    spinlock_stats="0"
    spinlock_stats_source="invalid-fallback"
    ;;
esac
sleepq_stats_file="${SECOND_SON_SLEEPQ_STATS_FILE:-${data_root}/sleepq-stats.txt}"
sleepq_stats_source="default"
if [[ -n "${SECOND_SON_SLEEPQ_STATS:-}" ]]; then
  sleepq_stats="${SECOND_SON_SLEEPQ_STATS}"
  sleepq_stats_source="environment"
elif [[ -r "${sleepq_stats_file}" ]]; then
  IFS= read -r sleepq_stats <"${sleepq_stats_file}" || true
  sleepq_stats="${sleepq_stats:-0}"
  sleepq_stats_source="${sleepq_stats_file}"
else
  sleepq_stats="0"
fi
case "${sleepq_stats}" in
  0|1) ;;
  *)
    echo "Ignoring invalid sleep-queue stats value '${sleepq_stats}'; expected 0 or 1" >&2
    sleepq_stats="0"
    sleepq_stats_source="invalid-fallback"
    ;;
esac
sleepq_stats_interval_file="${SECOND_SON_SLEEPQ_STATS_INTERVAL_FILE:-${data_root}/sleepq-stats-interval.txt}"
sleepq_stats_interval_source="default"
if [[ -n "${SECOND_SON_SLEEPQ_STATS_INTERVAL:-}" ]]; then
  sleepq_stats_interval="${SECOND_SON_SLEEPQ_STATS_INTERVAL}"
  sleepq_stats_interval_source="environment"
elif [[ -r "${sleepq_stats_interval_file}" ]]; then
  IFS= read -r sleepq_stats_interval <"${sleepq_stats_interval_file}" || true
  sleepq_stats_interval="${sleepq_stats_interval:-1048576}"
  sleepq_stats_interval_source="${sleepq_stats_interval_file}"
else
  sleepq_stats_interval="1048576"
fi
if [[ ! "${sleepq_stats_interval}" =~ ^[0-9]{1,10}$ ]] ||
   (( 10#${sleepq_stats_interval} < 1024 || 10#${sleepq_stats_interval} > 1000000000 )); then
  echo "Ignoring invalid sleep-queue stats interval '${sleepq_stats_interval}'; expected 1024-1000000000" >&2
  sleepq_stats_interval="1048576"
  sleepq_stats_interval_source="invalid-fallback"
fi
job_worker_cpus_file="${SECOND_SON_JOB_WORKER_CPUS_FILE:-${data_root}/job-worker-cpus.txt}"
job_worker_cpus_source="default"
if [[ -n "${SECOND_SON_JOB_WORKER_CPUS:-}" ]]; then
  job_worker_cpus="${SECOND_SON_JOB_WORKER_CPUS}"
  job_worker_cpus_source="environment"
elif [[ -r "${job_worker_cpus_file}" ]]; then
  IFS= read -r job_worker_cpus <"${job_worker_cpus_file}" || true
  job_worker_cpus="${job_worker_cpus:-0,1,6,7}"
  job_worker_cpus_source="${job_worker_cpus_file}"
else
  job_worker_cpus="0,1,6,7"
fi
job_worker_cpus_valid="1"
if [[ ! "${job_worker_cpus}" =~ ^[0-7](,[0-7])*$ ]]; then
  job_worker_cpus_valid="0"
else
  IFS=',' read -r -a job_worker_cpu_items <<<"${job_worker_cpus}"
  declare -A job_worker_cpu_seen=()
  for job_worker_cpu in "${job_worker_cpu_items[@]}"; do
    if [[ -n "${job_worker_cpu_seen[${job_worker_cpu}]:-}" ]]; then
      job_worker_cpus_valid="0"
      break
    fi
    job_worker_cpu_seen[${job_worker_cpu}]="1"
  done
fi
if [[ "${job_worker_cpus_valid}" != "1" ]]; then
  echo "Ignoring invalid JobWorker CPU mask '${job_worker_cpus}'; expected unique CPU IDs 0-7 separated by commas" >&2
  job_worker_cpus="0,1,6,7"
  job_worker_cpus_source="invalid-fallback"
fi
readback_window_file="${SECOND_SON_READBACK_WINDOW_FILE:-${data_root}/readback-window-kb.txt}"
readback_window_source="default"
if [[ -n "${SECOND_SON_READBACK_WINDOW_KB:-}" ]]; then
  readback_window_kb="${SECOND_SON_READBACK_WINDOW_KB}"
  readback_window_source="environment"
elif [[ -r "${readback_window_file}" ]]; then
  IFS= read -r readback_window_kb <"${readback_window_file}" || true
  readback_window_kb="${readback_window_kb:-512}"
  readback_window_source="${readback_window_file}"
else
  readback_window_kb="512"
fi
readback_write_site_window_file="${SECOND_SON_READBACK_WRITE_SITE_WINDOW_FILE:-${data_root}/readback-write-site-window.txt}"
readback_write_site_window_source="default"
if [[ -n "${SECOND_SON_READBACK_WRITE_SITE_WINDOW:-}" ]]; then
  readback_write_site_window="${SECOND_SON_READBACK_WRITE_SITE_WINDOW}"
  readback_write_site_window_source="environment"
elif [[ -r "${readback_write_site_window_file}" ]]; then
  IFS= read -r readback_write_site_window <"${readback_write_site_window_file}" || true
  readback_write_site_window="${readback_write_site_window:-off}"
  readback_write_site_window_source="${readback_write_site_window_file}"
else
  readback_write_site_window="off"
fi
case "${readback_write_site_window}" in
  off) ;;
  *)
    if [[ ! "${readback_write_site_window}" =~ ^0[xX][0-9a-fA-F]+:(4|8|16|32|64|128|256|512)$ ]] ||
       [[ "${readback_write_site_window}" =~ ^0[xX]0+: ]]; then
      echo "Ignoring invalid precise write-site window '${readback_write_site_window}'; expected nonzero-hex-pc:4|8|16|32|64|128|256|512" >&2
      readback_write_site_window="off"
      readback_write_site_window_source="invalid-fallback"
    fi
    ;;
esac
readback_write_discard_probe_file="${SECOND_SON_READBACK_WRITE_DISCARD_PROBE_FILE:-${data_root}/readback-write-discard-probe.txt}"
readback_write_discard_probe_source="default"
if [[ -n "${SECOND_SON_READBACK_WRITE_DISCARD_PROBE_PC:-}" ]]; then
  readback_write_discard_probe="${SECOND_SON_READBACK_WRITE_DISCARD_PROBE_PC}"
  readback_write_discard_probe_source="environment"
elif [[ -r "${readback_write_discard_probe_file}" ]]; then
  IFS= read -r readback_write_discard_probe <"${readback_write_discard_probe_file}" || true
  readback_write_discard_probe="${readback_write_discard_probe:-off}"
  readback_write_discard_probe_source="${readback_write_discard_probe_file}"
else
  readback_write_discard_probe="off"
fi
case "${readback_write_discard_probe}" in
  off) ;;
  *)
    if [[ ! "${readback_write_discard_probe}" =~ ^0[xX][0-9a-fA-F]+$ ]] ||
       [[ "${readback_write_discard_probe}" =~ ^0[xX]0+$ ]]; then
      echo "Ignoring invalid write-discard coverage probe PC '${readback_write_discard_probe}'; expected off or a nonzero hex PC" >&2
      readback_write_discard_probe="off"
      readback_write_discard_probe_source="invalid-fallback"
    fi
    ;;
esac
gpu_performance_file="${SECOND_SON_GPU_PERFORMANCE_FILE:-${data_root}/gpu-performance-level.txt}"
gpu_performance_source="default"
if [[ -n "${SECOND_SON_GPU_PERFORMANCE_LEVEL:-}" ]]; then
  gpu_performance_requested="${SECOND_SON_GPU_PERFORMANCE_LEVEL}"
  gpu_performance_source="environment"
elif [[ -r "${gpu_performance_file}" ]]; then
  IFS= read -r gpu_performance_requested <"${gpu_performance_file}" || true
  gpu_performance_requested="${gpu_performance_requested:-auto}"
  gpu_performance_source="${gpu_performance_file}"
else
  gpu_performance_requested="auto"
fi
case "${gpu_performance_requested}" in
  auto|high) ;;
  *)
    echo "Ignoring invalid GPU performance level '${gpu_performance_requested}'; expected auto or high" >&2
    gpu_performance_requested="auto"
    gpu_performance_source="invalid-fallback"
    ;;
esac
source "${repo_dir}/deck_tools/deck_runtime.sh"
deck_runtime_detect

case "${variant}" in
  baseline)
    binary="/home/deck/Projects/shadPS4-baseline-0.18.0/Shadps4-sdl.AppImage"
    ;;
  fork)
    for candidate in "${repo_dir}/build-deck/shadps4" "${repo_dir}/build-deck/shadPS4"; do
      if [[ -x "${candidate}" ]]; then
        binary="${candidate}"
        break
      fi
    done
    binary="${binary:-${repo_dir}/build-deck/shadps4}"
    ;;
  *)
    echo "Usage: $0 [fork|baseline]" >&2
    exit 2
    ;;
esac

if [[ ! -x "${binary}" ]]; then
  echo "Missing executable: ${binary}" >&2
  exit 1
fi
if [[ ! -f "${eboot}" ]]; then
  echo "Missing installed game executable: ${eboot}" >&2
  echo "Set SECOND_SON_ROOT or finish installing CUSA00223 first." >&2
  exit 1
fi

profile_dir="${data_root}/profiles/${variant}"
xdg_data="${profile_dir}/xdg-data"
shad_user="${xdg_data}/shadPS4"
run_stamp="$(date +%Y%m%d-%H%M%S)"
run_dir="${data_root}/runs/${run_stamp}-${variant}"
mkdir -p "${shad_user}" "${run_dir}/logs" "${run_dir}/screenshots"
# A new isolated profile has no legacy saves to migrate.  Pre-create the default user's layout so
# the first foreground run cannot be blocked by the SDL migration dialog looking at empty old paths.
mkdir -p "${shad_user}/home/1000/savedata" "${shad_user}/home/1000/trophy" \
  "${shad_user}/home/1000/inputs" "${shad_user}/input_config"
# Every A/B run starts from the same controlled global profile.  Second Son requires Precise
# readbacks for gameplay lighting, particles, and its early graffiti interaction; stale profiles
# with readbacks disabled make the comparison invalid.
install -m 0644 "${repo_dir}/deck_tools/second_son_config.json" "${shad_user}/config.json"
install -m 0644 "${repo_dir}/deck_tools/second_son_global_input.ini" \
  "${shad_user}/input_config/global.ini"

touch "${run_dir}/started.marker"
ln -sfn "${run_dir}" "${data_root}/runs/current"

mangohud_config="${run_dir}/MangoHud.conf"
cat >"${mangohud_config}" <<EOF
fps
fps_color_change
frame_timing=1
frametime
cpu_stats
cpu_temp
cpu_power
gpu_stats
gpu_temp
gpu_power
ram
vram
battery
battery_watt
io_read
io_write
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
  echo "variant=${variant}"
  echo "binary=${binary}"
  echo "eboot=${eboot}"
  echo "precise_readback_stats=${readback_stats}"
  echo "precise_readback_stats_source=${readback_stats_source}"
  echo "precise_readback_stats_interval=${readback_stats_interval}"
  echo "precise_readback_phase_timing=${readback_phase_timing}"
  echo "precise_readback_phase_timing_source=${readback_phase_timing_source}"
  echo "precise_readback_work_budget=${readback_work_budget}"
  echo "precise_readback_work_budget_source=${readback_work_budget_source}"
  echo "spinlock_stats=${spinlock_stats}"
  echo "spinlock_stats_source=${spinlock_stats_source}"
  echo "precise_readback_window_kb=${readback_window_kb}"
  echo "precise_readback_window_source=${readback_window_source}"
  echo "precise_readback_write_site_window=${readback_write_site_window}"
  echo "precise_readback_write_site_window_source=${readback_write_site_window_source}"
  echo "precise_readback_write_discard_probe=${readback_write_discard_probe}"
  echo "precise_readback_write_discard_probe_source=${readback_write_discard_probe_source}"
  echo "sleepq_stats=${sleepq_stats}"
  echo "sleepq_stats_source=${sleepq_stats_source}"
  echo "sleepq_stats_interval=${sleepq_stats_interval}"
  echo "sleepq_stats_interval_source=${sleepq_stats_interval_source}"
  echo "job_worker_cpus=${job_worker_cpus}"
  echo "job_worker_cpus_source=${job_worker_cpus_source}"
  echo "gpu_performance_requested=${gpu_performance_requested}"
  echo "gpu_performance_source=${gpu_performance_source}"
  sha256sum "${binary}"
  uname -a
  free -h
  swapon --show
  deck_runtime_print
  if [[ "${variant}" == "fork" ]]; then
    git -C "${repo_dir}" rev-parse HEAD
    git -C "${repo_dir}" status --short --branch
    git -C "${repo_dir}" diff --stat
  fi
  vulkaninfo --summary 2>/dev/null || true
} >"${run_dir}/system.txt"
cp "${shad_user}/config.json" "${run_dir}/config.json"

affinity_pid=""
gpu_performance_active="0"
gpu_performance_initial=""
gpu_performance_log="${run_dir}/gpu-performance.log"
gpu_manager_service="com.steampowered.SteamOSManager1"
gpu_manager_path="/com/steampowered/SteamOSManager1"
gpu_manager_interface="com.steampowered.SteamOSManager1.GpuPerformanceLevel1"

read_gpu_performance_level() {
  local response
  response="$(busctl --user get-property "${gpu_manager_service}" "${gpu_manager_path}" \
    "${gpu_manager_interface}" GpuPerformanceLevel 2>/dev/null)" || return 1
  case "${response}" in
    's "'*)
      response="${response#s \"}"
      response="${response%\"}"
      printf '%s\n' "${response}"
      ;;
    *) return 1 ;;
  esac
}

restore_gpu_performance() {
  [[ "${gpu_performance_active}" == "1" ]] || return 0
  local observed="" restored="0"
  for _ in 1 2 3; do
    busctl --user set-property "${gpu_manager_service}" "${gpu_manager_path}" \
      "${gpu_manager_interface}" GpuPerformanceLevel s "${gpu_performance_initial}" \
      >/dev/null 2>&1 || true
    observed="$(read_gpu_performance_level || true)"
    if [[ "${observed}" == "${gpu_performance_initial}" ]]; then
      restored="1"
      break
    fi
    sleep 0.1
  done
  {
    echo "restore_requested=${gpu_performance_initial}"
    echo "restore_observed=${observed:-unavailable}"
    echo "restore_verified=${restored}"
  } >>"${gpu_performance_log}"
  {
    echo "gpu_performance_restored=${observed:-unavailable}"
    echo "gpu_performance_restore_verified=${restored}"
  } >>"${run_dir}/system.txt"
  gpu_performance_active="0"
}

enable_gpu_performance() {
  {
    echo "requested=${gpu_performance_requested}"
    echo "source=${gpu_performance_source}"
    echo "steam_deck=${SHADPS4_STEAM_DECK}"
  } >"${gpu_performance_log}"
  if [[ "${gpu_performance_requested}" != "high" ]]; then
    echo "result=unchanged" >>"${gpu_performance_log}"
    return 0
  fi
  if [[ "${SHADPS4_STEAM_DECK}" != "1" || ! -x /usr/bin/busctl ]]; then
    echo "result=unsupported" >>"${gpu_performance_log}"
    return 0
  fi
  gpu_performance_initial="$(read_gpu_performance_level || true)"
  if [[ -z "${gpu_performance_initial}" ]]; then
    echo "result=initial-read-failed" >>"${gpu_performance_log}"
    return 0
  fi
  gpu_performance_active="1"
  if ! busctl --user set-property "${gpu_manager_service}" "${gpu_manager_path}" \
    "${gpu_manager_interface}" GpuPerformanceLevel s high >/dev/null 2>&1; then
    echo "result=set-failed" >>"${gpu_performance_log}"
    restore_gpu_performance
    return 0
  fi
  local observed
  observed="$(read_gpu_performance_level || true)"
  if [[ "${observed}" != "high" ]]; then
    echo "result=verification-failed" >>"${gpu_performance_log}"
    restore_gpu_performance
    return 0
  fi
  {
    echo "initial=${gpu_performance_initial}"
    echo "active=${observed}"
    echo "result=enabled"
  } >>"${gpu_performance_log}"
  {
    echo "gpu_performance_initial=${gpu_performance_initial}"
    echo "gpu_performance_active=${observed}"
  } >>"${run_dir}/system.txt"
}

apply_deck_cpu_affinity() {
  local launcher_pid="$1"
  # The watcher runs helper binaries, not the game. Steam's mixed-architecture overlay preload
  # produces an ELF-class warning for each helper invocation, so keep it out of this subshell.
  unset LD_PRELOAD
  if [[ "${SHADPS4_STEAM_DECK}" != "1" ]]; then
    echo "Steam Deck not detected; leaving scheduler defaults"
    return
  fi
  if [[ ! -x /usr/bin/taskset || ! -r /sys/devices/system/cpu/cpu7/topology/thread_siblings_list ]]; then
    echo "Deck CPU affinity unavailable; leaving scheduler defaults"
    return
  fi
  if [[ "$(</sys/devices/system/cpu/cpu0/topology/thread_siblings_list)" != "0-1" ||
        "$(</sys/devices/system/cpu/cpu2/topology/thread_siblings_list)" != "2-3" ||
        "$(</sys/devices/system/cpu/cpu4/topology/thread_siblings_list)" != "4-5" ||
        "$(</sys/devices/system/cpu/cpu6/topology/thread_siblings_list)" != "6-7" ]]; then
    echo "Unexpected CPU topology; leaving scheduler defaults"
    return
  fi

  local game_pid="" candidate ancestor parent
  local -A pinned=()
  for _ in $(seq 1 300); do
    for candidate in $(pgrep -u "${UID}" -f "^${binary} --game ${eboot} " || true); do
      ancestor="${candidate}"
      while [[ "${ancestor}" =~ ^[0-9]+$ && "${ancestor}" -gt 1 &&
               -r "/proc/${ancestor}/status" ]]; do
        if [[ "${ancestor}" == "${launcher_pid}" ]]; then
          game_pid="${candidate}"
          break 2
        fi
        parent=""
        while read -r key value _; do
          if [[ "${key}" == "PPid:" ]]; then
            parent="${value}"
            break
          fi
        done <"/proc/${ancestor}/status"
        [[ -n "${parent}" ]] || break
        ancestor="${parent}"
      done
    done
    [[ -n "${game_pid}" ]] && break
    sleep 0.1
  done
  if [[ -z "${game_pid}" ]]; then
    echo "Deck CPU affinity could not find the launched emulator"
    return
  fi

  echo "Applying Deck CPU affinity to emulator PID ${game_pid}"
  while [[ -r "/proc/${game_pid}/status" ]]; do
    local task tid name desired process_state=""
    while read -r key value _; do
      if [[ "${key}" == "State:" ]]; then
        process_state="${value}"
        break
      fi
    done <"/proc/${game_pid}/status"
    if [[ "${process_state}" == "Z" || "${process_state}" == "X" ]]; then
      echo "Emulator entered terminal state ${process_state}; stopping affinity watcher"
      break
    fi
    for task in /proc/${game_pid}/task/*; do
      [[ -r "${task}/comm" ]] || continue
      tid="${task##*/}"
      name="$(<"${task}/comm")"
      case "${name}" in
        shadPS4:GpuComm) desired="2" ;;
        Game:Main) desired="4" ;;
        JobWorker*) desired="${job_worker_cpus}" ;;
        *) continue ;;
      esac
      [[ "${pinned[${tid}]:-}" == "${desired}" ]] && continue
      if taskset -pc "${desired}" "${tid}" >/dev/null 2>&1; then
        pinned[${tid}]="${desired}"
        echo "Pinned ${name} (${tid}) to CPU ${desired}"
      fi
    done
    sleep 0.25
  done
}

collect_results() {
  local exit_status="$1"
  restore_gpu_performance
  if [[ -n "${affinity_pid}" ]]; then
    kill "${affinity_pid}" 2>/dev/null || true
    wait "${affinity_pid}" 2>/dev/null || true
  fi
  echo "${exit_status}" >"${run_dir}/exit-status.txt"
  if [[ -d "${shad_user}/log" ]]; then
    find "${shad_user}/log" -maxdepth 1 -type f -newer "${run_dir}/started.marker" \
      -exec cp -t "${run_dir}/logs" -- {} + 2>/dev/null || true
  fi
  if [[ -d "${shad_user}/screenshots" ]]; then
    find "${shad_user}/screenshots" -maxdepth 1 -type f -newer "${run_dir}/started.marker" \
      -exec cp -t "${run_dir}/screenshots" -- {} + 2>/dev/null || true
  fi
  LD_PRELOAD="" python3 "${repo_dir}/deck_tools/summarize_mangohud.py" "${run_dir}" \
    >"${run_dir}/performance-summary.txt" 2>&1 || true
  if [[ "${sleepq_stats}" == "1" ]]; then
    LD_PRELOAD="" python3 "${repo_dir}/deck_tools/summarize_sleepq.py" "${run_dir}" --tail 8 \
      >"${run_dir}/sleepq-summary.txt" 2>&1 || true
  fi
  echo "Run evidence: ${run_dir}"
}

# Steam may terminate the shortcut's process group as soon as the emulator window disappears.
# Finalize evidence from EXIT as well as the usual return path, including signal-driven teardown.
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'status=$?; trap - EXIT HUP INT TERM; collect_results "${status}"' EXIT

enable_gpu_performance

launch=(mangohud "${binary}" --game "${eboot}" --same-process --fullscreen true --show-fps
        --config-global)

if [[ "${SHADPS4_GAMESCOPE}" == "1" ]]; then
  # Agent-launched commands do not inherit Gaming Mode's display variables. The game Xwayland is
  # :1 in the Steam Deck Gamescope session, and using it also permits scripted screenshot hotkeys.
  export DISPLAY="${DISPLAY:-:1}"
  export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
  command=("${launch[@]}")
else
  command=(gamescope -W 1280 -H 800 -w 1280 -h 720 -r 60 -f
           -T "${run_dir}/gamescope-stats.csv" -- "${launch[@]}")
fi

echo "Visible ${variant} run; evidence will be saved to ${run_dir}"
# SteamOS core-dump processing can retain several gigabytes for five minutes after an emulator
# assertion. Runtime logs and MangoHud evidence are preserved separately, so do not generate a core
# during normal foreground testing.
ulimit -c 0
if [[ "${variant}" == "fork" && "${SECOND_SON_CPU_AFFINITY:-1}" == "1" ]]; then
  apply_deck_cpu_affinity "$$" >"${run_dir}/affinity.log" 2>/dev/null &
  affinity_pid=$!
fi
set +e
XDG_DATA_HOME="${xdg_data}" MANGOHUD_CONFIGFILE="${mangohud_config}" \
  SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="${SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT:-0x28de/0x1205}" \
  SDL_JOYSTICK_HIDAPI_STEAMDECK="${SDL_JOYSTICK_HIDAPI_STEAMDECK:-1}" \
  SHADPS4_FORCE_STEREO_DOWNMIX="${SHADPS4_FORCE_STEREO_DOWNMIX:-1}" \
  SHADPS4_READONLY_FORMATTED_BUFFER_LIMIT_MB="${SHADPS4_READONLY_FORMATTED_BUFFER_LIMIT_MB:-256}" \
  SHADPS4_PRECISE_READBACK_STATS="${SHADPS4_PRECISE_READBACK_STATS:-${readback_stats}}" \
  SHADPS4_PRECISE_READBACK_STATS_INTERVAL="${SHADPS4_PRECISE_READBACK_STATS_INTERVAL:-${readback_stats_interval}}" \
  SHADPS4_PRECISE_READBACK_PHASE_TIMING="${SHADPS4_PRECISE_READBACK_PHASE_TIMING:-${readback_phase_timing}}" \
  SHADPS4_PRECISE_READBACK_WORK_BUDGET="${SHADPS4_PRECISE_READBACK_WORK_BUDGET:-${readback_work_budget}}" \
  SHADPS4_SPINLOCK_STATS="${SHADPS4_SPINLOCK_STATS:-${spinlock_stats}}" \
  SHADPS4_PRECISE_READBACK_WINDOW_KB="${SHADPS4_PRECISE_READBACK_WINDOW_KB:-${readback_window_kb}}" \
  SHADPS4_PRECISE_READBACK_WRITE_SITE_WINDOW="${SHADPS4_PRECISE_READBACK_WRITE_SITE_WINDOW:-${readback_write_site_window}}" \
  SHADPS4_PRECISE_READBACK_WRITE_DISCARD_PROBE_PC="${SHADPS4_PRECISE_READBACK_WRITE_DISCARD_PROBE_PC:-${readback_write_discard_probe}}" \
  SHADPS4_SLEEPQ_STATS="${SHADPS4_SLEEPQ_STATS:-${sleepq_stats}}" \
  SHADPS4_SLEEPQ_STATS_INTERVAL="${SHADPS4_SLEEPQ_STATS_INTERVAL:-${sleepq_stats_interval}}" \
  "${command[@]}" 2>&1 | tee "${run_dir}/console.log"
exit_status="${PIPESTATUS[0]}"
set -e
exit "${exit_status}"
