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
if [[ ! -f "${shad_user}/config.json" ]]; then
  install -m 0644 "${repo_dir}/deck_tools/second_son_config.json" "${shad_user}/config.json"
fi

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
benchmark_percentiles=97,AVG,1,0.1
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

collect_results() {
  local exit_status="$1"
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
set +e
XDG_DATA_HOME="${xdg_data}" MANGOHUD_CONFIGFILE="${mangohud_config}" \
  "${command[@]}" 2>&1 | tee "${run_dir}/console.log"
exit_status="${PIPESTATUS[0]}"
set -e
collect_results "${exit_status}"
exit "${exit_status}"
