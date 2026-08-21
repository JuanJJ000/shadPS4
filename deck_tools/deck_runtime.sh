#!/usr/bin/env bash

# Shared Steam Deck and Gamescope detection for Deck launch tools.

deck_runtime_contains() {
  local path="$1" pattern="$2"
  [[ -r "${path}" ]] && grep -Eiq "${pattern}" "${path}"
}

deck_runtime_has_gamescope_socket() {
  local runtime_dir="$1" candidate
  for candidate in "${runtime_dir}"/gamescope-*; do
    [[ -S "${candidate}" ]] && return 0
  done
  return 1
}

deck_runtime_detect() {
  local runtime_dir desktop

  SHADPS4_STEAM_DECK=0
  SHADPS4_STEAM_DECK_SOURCE="none"
  SHADPS4_GAMESCOPE=0
  SHADPS4_GAMESCOPE_SOURCE="none"

  if deck_runtime_contains /etc/os-release 'steamdeck|steamos'; then
    SHADPS4_STEAM_DECK=1
    SHADPS4_STEAM_DECK_SOURCE="os-release"
  elif deck_runtime_contains /proc/sys/kernel/osrelease 'neptune'; then
    SHADPS4_STEAM_DECK=1
    SHADPS4_STEAM_DECK_SOURCE="kernel"
  elif deck_runtime_contains /sys/class/dmi/id/board_vendor '^Valve$' &&
       deck_runtime_contains /sys/class/dmi/id/board_name 'Jupiter|Galileo'; then
    SHADPS4_STEAM_DECK=1
    SHADPS4_STEAM_DECK_SOURCE="dmi"
  elif deck_runtime_contains /sys/class/dmi/id/product_name 'Jupiter|Galileo'; then
    SHADPS4_STEAM_DECK=1
    SHADPS4_STEAM_DECK_SOURCE="dmi"
  elif deck_runtime_contains /proc/cpuinfo '^cpu family[[:space:]]*:[[:space:]]*23$' &&
       deck_runtime_contains /proc/cpuinfo '^model[[:space:]]*:[[:space:]]*144$'; then
    SHADPS4_STEAM_DECK=1
    SHADPS4_STEAM_DECK_SOURCE="cpu"
  fi

  desktop="${XDG_CURRENT_DESKTOP:-}"
  runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  if [[ -n "${STEAM_GAMEPADUI:-}" ]]; then
    SHADPS4_GAMESCOPE=1
    SHADPS4_GAMESCOPE_SOURCE="steam-gamepadui"
  elif [[ "${desktop,,}" == *gamescope* ]]; then
    SHADPS4_GAMESCOPE=1
    SHADPS4_GAMESCOPE_SOURCE="desktop"
  elif deck_runtime_has_gamescope_socket "${runtime_dir}"; then
    SHADPS4_GAMESCOPE=1
    SHADPS4_GAMESCOPE_SOURCE="socket"
  elif ps -u "$(id -u)" -o comm= 2>/dev/null | grep -Eq '^gamescope(-wl)?$'; then
    SHADPS4_GAMESCOPE=1
    SHADPS4_GAMESCOPE_SOURCE="process"
  fi

  export SHADPS4_STEAM_DECK SHADPS4_STEAM_DECK_SOURCE
  export SHADPS4_GAMESCOPE SHADPS4_GAMESCOPE_SOURCE
}

deck_runtime_print() {
  printf 'steam_deck=%s\n' "${SHADPS4_STEAM_DECK}"
  printf 'steam_deck_source=%s\n' "${SHADPS4_STEAM_DECK_SOURCE}"
  printf 'gamescope=%s\n' "${SHADPS4_GAMESCOPE}"
  printf 'gamescope_source=%s\n' "${SHADPS4_GAMESCOPE_SOURCE}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  deck_runtime_detect
  deck_runtime_print
fi
