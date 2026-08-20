#!/usr/bin/env bash
set -euo pipefail

pkg="${SECOND_SON_PKG:-/home/deck/Games/shadPS4-import/CUSA00223_00-SECONDSON.pkg}"
installer="${SHAD_PKG_INSTALLER:-/home/deck/Projects/shadPS4-pkg-installer-0.7.0/Shadps4-qt.AppImage}"
xdg_data="${SHAD_PKG_XDG_DATA:-/home/deck/Games/shadPS4-pkg-installer-xdg}"

if pgrep -f '/rpcs3\.AppImage' >/dev/null; then
  echo "Refusing to steal Gamescope focus while RPCS3 is running." >&2
  exit 3
fi
if [[ ! -f "${pkg}" ]]; then
  echo "Missing staged package: ${pkg}" >&2
  exit 1
fi
if [[ ! -x "${installer}" ]]; then
  echo "Missing package installer: ${installer}" >&2
  exit 1
fi

mkdir -p "${xdg_data}"
export XDG_DATA_HOME="${xdg_data}"
export DISPLAY="${DISPLAY:-:1}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

echo "Launching the visible package installer in Gamescope."
echo "Select: ${pkg}"
exec "${installer}"
