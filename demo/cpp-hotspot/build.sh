#!/bin/sh
set -eu

output_path="${1:-/out/cpp-hotspot}"
mkdir -p "$(dirname "$output_path")"

# Keep a deterministic, frame-pointer-based symbol image for the interview
# target.  The same script is also used by the Analyzer image so `perf script`
# can resolve user-space frames from the target container by build ID.
exec g++ -std=c++17 -O2 -g -pthread \
  -fno-omit-frame-pointer -fno-optimize-sibling-calls \
  -rdynamic -no-pie -static-libgcc -static-libstdc++ \
  -o "$output_path" main.cpp
