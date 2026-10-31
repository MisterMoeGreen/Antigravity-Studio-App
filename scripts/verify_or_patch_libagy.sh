#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB_AGY="${PROJECT_ROOT}/app/src/main/jniLibs/arm64-v8a/libagy.so"
AGY_SRC="/data/data/com.termux/files/usr/bin/agy.va39"
TARGET_PKG="ai.agy.app"

is_corrupt() {
  if [ ! -f "$LIB_AGY" ]; then
    return 0
  fi
  if command -v llvm-readelf >/dev/null 2>&1; then
    if llvm-readelf -d "$LIB_AGY" 2>&1 | grep -qi "unable to parse DT_STRTAB"; then
      return 0
    fi
  elif command -v aarch64-linux-gnu-readelf >/dev/null 2>&1; then
    if aarch64-linux-gnu-readelf -d "$LIB_AGY" 2>&1 | grep -qi "unable to parse DT_STRTAB"; then
      return 0
    fi
  elif command -v readelf >/dev/null 2>&1; then
    if readelf -d "$LIB_AGY" 2>&1 | grep -qi "unable to parse DT_STRTAB"; then
      return 0
    fi
  fi
  return 1
}

if is_corrupt; then
  echo "libagy.so is missing or has corrupted DT_STRTAB. Re-patching from $AGY_SRC..."
  if [ ! -f "$AGY_SRC" ]; then
    echo "ERROR: Upstream $AGY_SRC not found!" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$LIB_AGY")"
  cp -f "$AGY_SRC" "$LIB_AGY"
  chmod 755 "$LIB_AGY"
  patchelf --page-size 16384 \
    --set-interpreter "/data/data/${TARGET_PKG}/files/usr/glibc/lib/ld-linux-aarch64.so.1" \
    --force-rpath --set-rpath "/data/data/${TARGET_PKG}/files/usr/glibc/lib" \
    "$LIB_AGY"
  python3 -c "path='$LIB_AGY'; d=open(path,'rb').read(); open(path,'wb').write(d.replace(b'/data/data/com.termux',b'/data/data/${TARGET_PKG}'))"
  echo "Successfully patched libagy.so"
else
  echo "libagy.so ELF integrity verified."
fi
