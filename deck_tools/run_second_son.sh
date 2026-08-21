#!/usr/bin/env bash
set -euo pipefail

variant="${1:-fork}"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
game_root="${SECOND_SON_ROOT:-/home/deck/Games/shadPS4-games/CUSA00223}"
eboot="${SECOND_SON_EBOOT:-${game_root}/eboot.bin}"
data_root="${SECOND_SON_DATA_ROOT:-/home/deck/Games/shadPS4-second-son}"

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
  sha256sum "${binary}"
  uname -a
  free -h
  swapon --show
  if [[ "${variant}" == "fork" ]]; then
    git -C "${repo_dir}" rev-parse HEAD
    git -C "${repo_dir}" status --short --branch
    git -C "${repo_dir}" diff --stat
  fi
  vulkaninfo --summary 2>/dev/null || true
} >"${run_dir}/system.txt"
cp "${shad_user}/config.json" "${run_dir}/config.json"

affinity_pid=""

apply_deck_cpu_affinity() {
  # The watcher runs helper binaries, not the game. Steam's mixed-architecture overlay preload
  # produces an ELF-class warning for each helper invocation, so keep it out of this subshell.
  unset LD_PRELOAD
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

  local game_pid=""
  local -A pinned=()
  for _ in $(seq 1 300); do
    game_pid="$(pgrep -u "${UID}" -n -f "^${binary} --game ${eboot} " || true)"
    [[ -n "${game_pid}" ]] && break
    sleep 0.1
  done
  if [[ -z "${game_pid}" ]]; then
    echo "Deck CPU affinity could not find the launched emulator"
    return
  fi

  echo "Applying Deck CPU affinity to emulator PID ${game_pid}"
  while [[ -d "/proc/${game_pid}/task" ]]; do
    local task tid name desired
    for task in /proc/${game_pid}/task/*; do
      [[ -r "${task}/comm" ]] || continue
      tid="${task##*/}"
      name="$(<"${task}/comm")"
      case "${name}" in
        shadPS4:GpuComm) desired="2" ;;
        Game:Main) desired="4" ;;
        JobWorker*) desired="0,1,6,7" ;;
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
  python3 "${repo_dir}/deck_tools/summarize_mangohud.py" "${run_dir}" \
    >"${run_dir}/performance-summary.txt" 2>&1 || true
  echo "Run evidence: ${run_dir}"
}

# Steam may terminate the shortcut's process group as soon as the emulator window disappears.
# Finalize evidence from EXIT as well as the usual return path, including signal-driven teardown.
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'status=$?; trap - EXIT HUP INT TERM; collect_results "${status}"' EXIT

launch=(mangohud "${binary}" --game "${eboot}" --same-process --fullscreen true --show-fps
        --config-global)

desktop_name="${XDG_CURRENT_DESKTOP:-}"
outer_gamescope_socket="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/gamescope-0"
if [[ -n "${STEAM_GAMEPADUI:-}" || "${desktop_name,,}" == *gamescope* ||
      -S "${outer_gamescope_socket}" ]]; then
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
  apply_deck_cpu_affinity >"${run_dir}/affinity.log" 2>&1 &
  affinity_pid=$!
fi
set +e
XDG_DATA_HOME="${xdg_data}" MANGOHUD_CONFIGFILE="${mangohud_config}" \
  SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT="${SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT:-0x28de/0x1205}" \
  SDL_JOYSTICK_HIDAPI_STEAMDECK="${SDL_JOYSTICK_HIDAPI_STEAMDECK:-1}" \
  SHADPS4_FORCE_STEREO_DOWNMIX="${SHADPS4_FORCE_STEREO_DOWNMIX:-1}" \
  SHADPS4_READONLY_FORMATTED_BUFFER_LIMIT_MB="${SHADPS4_READONLY_FORMATTED_BUFFER_LIMIT_MB:-256}" \
  "${command[@]}" 2>&1 | tee "${run_dir}/console.log"
exit_status="${PIPESTATUS[0]}"
set -e
exit "${exit_status}"
