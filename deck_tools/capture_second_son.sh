#!/usr/bin/env bash
set -euo pipefail

mode="${1:-overlay}"
export DISPLAY="${DISPLAY:-:1}"
case "${mode}" in
  overlay)
    # shadPS4's default screenshot-with-overlays hotkey.
    xdotool key --clearmodifiers alt+F12
    ;;
  gamescope)
    # Gamescope's compositor screenshot hotkey.
    xdotool key --clearmodifiers super+s
    ;;
  *)
    echo "Usage: $0 [overlay|gamescope]" >&2
    exit 2
    ;;
esac
