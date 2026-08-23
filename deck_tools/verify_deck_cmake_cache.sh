#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [CMakeCache.txt]" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache_file="${1:-${SHAD_BUILD_DIR:-${repo_dir}/build-deck}/CMakeCache.txt}"
expected_flags="-O2 -g -DNDEBUG"

if [[ ! -f "${cache_file}" ]]; then
  echo "Deck build validation failed: CMake cache not found: ${cache_file}" >&2
  exit 1
fi

for language in C CXX; do
  key="CMAKE_${language}_FLAGS_RELWITHDEBINFO"
  expected="${key}:STRING=${expected_flags}"
  if ! grep -Fqx -- "${expected}" "${cache_file}"; then
    actual="$(grep -E "^${key}(:[^=]*)?=" "${cache_file}" || true)"
    if [[ -z "${actual}" ]]; then
      actual="<missing>"
    fi
    echo "Deck build validation failed: expected ${expected}; found ${actual}" >&2
    exit 1
  fi
done

echo "Deck build validation passed: RelWithDebInfo uses ${expected_flags} for C and C++."
