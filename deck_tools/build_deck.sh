#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${SHAD_BUILD_DIR:-${repo_dir}/build-deck}"
build_jobs="${SHAD_BUILD_JOBS:-4}"

priority=()
if pgrep -f '/rpcs3\.AppImage' >/dev/null; then
  # Keep a concurrent known-good inFAMOUS session responsive on the Deck's four CPU cores.
  priority=(nice -n 15 ionice -c 3)
fi

"${priority[@]}" flatpak --user run --share=network --command=sh --filesystem=/home/deck \
  --env=PATH=/usr/lib/sdk/llvm20/bin:/usr/bin \
  --env=CC=clang --env=CXX=clang++ \
  org.freedesktop.Sdk//25.08 -c \
  "cmake -S '${repo_dir}' -B '${build_dir}' -GNinja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DCMAKE_C_FLAGS_RELWITHDEBINFO='-O2 -g -DNDEBUG' \
    -DCMAKE_CXX_FLAGS_RELWITHDEBINFO='-O2 -g -DNDEBUG'"

"${repo_dir}/deck_tools/verify_deck_cmake_cache.sh" "${build_dir}/CMakeCache.txt"

"${priority[@]}" flatpak --user run --share=network --command=sh --filesystem=/home/deck \
  --env=PATH=/usr/lib/sdk/llvm20/bin:/usr/bin \
  --env=CC=clang --env=CXX=clang++ \
  org.freedesktop.Sdk//25.08 -c \
  "cmake --build '${build_dir}' --parallel '${build_jobs}'"
