#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

mode="${1:-overlay}"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

send_wayland_key() {
  if ! command -v ydotool >/dev/null 2>&1 || ! command -v ydotoold >/dev/null 2>&1; then
    echo "Wayland capture needs the existing ydotool and ydotoold commands" >&2
    return 1
  fi

  mkdir -p "${repo_dir}/scratch"
  # Keep the Unix socket path relative so the long project path does not exceed sun_path.
  local socket="scratch/ydt-${BASHPID}"
  local socket_absolute="${repo_dir}/${socket}"
  local daemon_pid
  (cd "${repo_dir}" && exec ydotoold --socket-path="${socket}" --socket-perm=0600 \
    --mouse-off >/dev/null 2>&1) &
  daemon_pid=$!
  cleanup_ydotool() {
    trap - RETURN
    kill "${daemon_pid}" 2>/dev/null || true
    wait "${daemon_pid}" 2>/dev/null || true
    if [[ -S "${socket_absolute}" ]]; then
      unlink "${socket_absolute}"
    fi
  }
  trap cleanup_ydotool RETURN

  for _ in {1..20}; do
    [[ -S "${socket_absolute}" ]] && break
    sleep 0.05
  done
  if [[ ! -S "${socket_absolute}" ]]; then
    echo "Temporary ydotool keyboard did not start" >&2
    return 1
  fi

  # Give the compositor time to register the ephemeral virtual keyboard.
  sleep 0.5
  (cd "${repo_dir}" && YDOTOOL_SOCKET="${socket}" ydotool key "$@")
}

send_key() {
  if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    send_wayland_key "$@"
  else
    export DISPLAY="${DISPLAY:-:1}"
    case "${mode}" in
      overlay) xdotool key --clearmodifiers alt+F12 ;;
      gamescope) xdotool key --clearmodifiers super+s ;;
    esac
  fi
}

case "${mode}" in
  overlay)
    # Linux input key codes: left Alt (56) + F12 (88).
    send_key 56:1 88:1 88:0 56:0
    ;;
  gamescope)
    # Linux input key codes: left Meta (125) + S (31).
    send_key 125:1 31:1 31:0 125:0
    ;;
  *)
    echo "Usage: $0 [overlay|gamescope]" >&2
    exit 2
    ;;
esac
