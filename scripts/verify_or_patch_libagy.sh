#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JNI_DIR="${PROJECT_ROOT}/app/src/main/jniLibs/arm64-v8a"
LIB_LAUNCHER="${JNI_DIR}/libagy_launcher.so"
LIB_AGY="${JNI_DIR}/libagy.so"
SRC_LAUNCHER="${PROJECT_ROOT}/app/src/main/cpp/agy_launcher.c"

# In v1.7.0+, agy binary is unbundled and managed on-demand.
# Remove legacy bundled libagy.so if present to keep APK size minimal (~35MB).
if [ -f "$LIB_AGY" ]; then
    echo "Unbundling: Removing legacy $LIB_AGY from APK jniLibs..."
    rm -f "$LIB_AGY"
fi

mkdir -p "$JNI_DIR"

# Ensure libagy_launcher.so is built from source, non-musl PT_INTERP, and 16KB aligned
NEEDS_REBUILD=0
if [ ! -f "$LIB_LAUNCHER" ] || [ "$SRC_LAUNCHER" -nt "$LIB_LAUNCHER" ] || [ "$0" -nt "$LIB_LAUNCHER" ]; then
    NEEDS_REBUILD=1
elif command -v readelf >/dev/null 2>&1; then
    if readelf -l "$LIB_LAUNCHER" 2>/dev/null | grep -q "ld-musl"; then
        echo "Detected non-Android musl PT_INTERP in libagy_launcher.so; forcing rebuild..."
        NEEDS_REBUILD=1
    elif readelf -l "$LIB_LAUNCHER" 2>/dev/null | awk '/LOAD/{print $NF}' | grep -vq "0x4000"; then
        echo "Detected non-16KB LOAD alignment in libagy_launcher.so; forcing rebuild..."
        NEEDS_REBUILD=1
    fi
fi

if [ "$NEEDS_REBUILD" -eq 1 ]; then
    echo "Compiling native launcher bridge: libagy_launcher.so..."
    CC_BIN="${CC:-gcc}"
    if ! command -v "$CC_BIN" >/dev/null 2>&1; then
        if command -v clang >/dev/null 2>&1; then
            CC_BIN="clang"
        fi
    fi
    if ! $CC_BIN -static -O2 -Wall \
      -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384 \
      "$SRC_LAUNCHER" -o "$LIB_LAUNCHER" 2>/dev/null; then
        echo "Static libc unavailable; building PIE Bionic launcher..."
        $CC_BIN -pie -fPIE -O2 -Wall \
          -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384 \
          "$SRC_LAUNCHER" -o "$LIB_LAUNCHER"
        patchelf --remove-rpath "$LIB_LAUNCHER" 2>/dev/null || true
    fi
    chmod 755 "$LIB_LAUNCHER"
    llvm-strip --strip-unneeded "$LIB_LAUNCHER" 2>/dev/null || strip "$LIB_LAUNCHER" 2>/dev/null || true
    if command -v readelf >/dev/null 2>&1 && readelf -l "$LIB_LAUNCHER" 2>/dev/null | grep -q "ld-musl"; then
        echo "ERROR: libagy_launcher.so linked against non-Android ld-musl!" >&2
        exit 1
    fi
    echo "Successfully compiled libagy_launcher.so"
fi

if [ ! -f "$LIB_LAUNCHER" ]; then
    echo "ERROR: libagy_launcher.so could not be built!" >&2
    exit 1
fi

echo "libagy_launcher.so verified successfully (unbundled engine architecture)."
