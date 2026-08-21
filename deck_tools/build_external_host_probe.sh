#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${repo_dir}/build-deck/vulkan_external_host_probe"

mkdir -p "$(dirname "${output}")"
flatpak --user run --command=sh --filesystem=/home/deck \
  --env=PATH=/usr/lib/sdk/llvm20/bin:/usr/bin \
  org.freedesktop.Sdk//25.08 -c \
  "clang++ -std=c++20 -O2 -Wall -Wextra -Werror \
    -Wno-missing-designated-field-initializers \
    '${repo_dir}/deck_tools/vulkan_external_host_probe.cpp' -lvulkan -o '${output}'"

echo "${output}"
