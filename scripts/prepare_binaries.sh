#!/usr/bin/env bash
# FILE: scripts/prepare_binaries.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
JNI_DIR="${PROJECT_ROOT}/app/src/main/jniLibs/arm64-v8a"
ASSETS_DIR="${PROJECT_ROOT}/app/src/main/assets"
# Use internal ext4 filesystem for staging to ensure symlink support (avoiding Android shared storage FUSE limitations)
STAGING_DIR="/data/data/com.termux/files/usr/tmp/staging_usr"

TARGET_PKG="ai.agy.app" # Exact 10-character package geometry
TARGET_PREFIX="/data/data/${TARGET_PKG}/files/usr"
SOURCE_PREFIX="/data/data/com.termux/files/usr"

# Exact byte-safe binary path patcher (guarantees zero ELF offset drift)
patch_elf_bytes() {
  local file="$1"
  python3 - "$file" "$TARGET_PKG" <<'PYEOF'
import sys
path, pkg = sys.argv[1], sys.argv[2]
old_bytes = b"/data/data/com.termux"
new_bytes = f"/data/data/{pkg}".encode("ascii")
assert len(old_bytes) == len(new_bytes) == 21, "Package ID must be exactly 10 bytes"
with open(path, "rb") as f:
    data = f.read()
if old_bytes in data:
    patched = data.replace(old_bytes, new_bytes)
    assert len(patched) == len(data), "File length modified!"
    with open(path, "wb") as f:
        f.write(patched)
PYEOF
}

echo "=== Staging Antigravity Android Binaries ==="
rm -rf "$JNI_DIR" "$STAGING_DIR"
mkdir -p "$JNI_DIR" "$ASSETS_DIR" "$STAGING_DIR"
MANIFEST_FILE="${STAGING_DIR}/.symlinks.manifest"
: > "$MANIFEST_FILE"

# 1. Compile Bionic C Virtualization Shim
echo "[1/7] Compiling C Virtualization Shim (libagy_shim.so)..."
CC_BIN="${CC:-clang}"
$CC_BIN -shared -fPIC -O2 -Wall \
  "${PROJECT_ROOT}/app/src/main/cpp/agy_shim.c" \
  -o "${JNI_DIR}/libagy_shim.so" -ldl
llvm-strip --strip-unneeded "${JNI_DIR}/libagy_shim.so" 2>/dev/null || strip --strip-unneeded "${JNI_DIR}/libagy_shim.so"
patchelf --remove-rpath "${JNI_DIR}/libagy_shim.so" 2>/dev/null || true
patch_elf_bytes "${JNI_DIR}/libagy_shim.so"
echo "lib/libagy_shim.so|libagy_shim.so" >> "$MANIFEST_FILE"

# 2. Stage & Patch agy.va39 -> libagy.so
echo "[2/7] Patching agy.va39 -> libagy.so..."
AGY_VA39_SRC="${SOURCE_PREFIX}/bin/agy.va39"
if [ ! -f "$AGY_VA39_SRC" ]; then
  echo "Error: agy.va39 not found at $AGY_VA39_SRC" >&2
  exit 1
fi
cp -L "$AGY_VA39_SRC" "${JNI_DIR}/libagy.so"
chmod 755 "${JNI_DIR}/libagy.so"

# Enforce 16KB page size for Android 15/16 (targetSdk 35) & --force-rpath (DT_RPATH)
patchelf --page-size 16384 \
  --set-interpreter "${TARGET_PREFIX}/glibc/lib/ld-linux-aarch64.so.1" \
  --force-rpath --set-rpath "${TARGET_PREFIX}/glibc/lib" \
  "${JNI_DIR}/libagy.so"
patch_elf_bytes "${JNI_DIR}/libagy.so"

# 3. Stage Glibc Runtime Libraries
echo "[3/7] Staging Glibc runtime libraries..."
GLIBC_SRC="${SOURCE_PREFIX}/glibc/lib"
declare -A GLIBC_MAP=(
  ["ld-linux-aarch64.so.1"]="libglibc_ld.so"
  ["libc.so.6"]="libglibc_c.so"
  ["libdl.so.2"]="libglibc_dl.so"
  ["libpthread.so.0"]="libglibc_pthread.so"
  ["libresolv.so.2"]="libglibc_resolv.so"
  ["librt.so.1"]="libglibc_rt.so"
  ["libm.so.6"]="libglibc_m.so"
  ["libgcc_s.so.1"]="libglibc_gcc_s.so"
  ["libnss_files.so.2"]="libglibc_nss_files.so"
  ["libnss_dns.so.2"]="libglibc_nss_dns.so"
)

for src_name in "${!GLIBC_MAP[@]}"; do
  dst_name="${GLIBC_MAP[$src_name]}"
  src_file="${GLIBC_SRC}/${src_name}"
  if [ -f "$src_file" ]; then
    cp -L "$src_file" "${JNI_DIR}/${dst_name}"
    chmod 755 "${JNI_DIR}/${dst_name}"
    patch_elf_bytes "${JNI_DIR}/${dst_name}"
    echo "glibc/lib/${src_name}|${dst_name}" >> "$MANIFEST_FILE"
  fi
done

# 4. Stage Developer Toolchain Binaries
echo "[4/7] Staging developer toolchain binaries..."
declare -A TOOL_MAP=(
  ["bash"]="libbash.so"
  ["busybox"]="libbusybox.so"
  ["git"]="libgit.so"
  ["rg"]="librg.so"
  ["curl"]="libcurl_bin.so"
  ["ssh"]="libssh_bin.so"
  ["ssh-keygen"]="libssh_keygen.so"
)

for tool in "${!TOOL_MAP[@]}"; do
  src_bin="${SOURCE_PREFIX}/bin/${tool}"
  dst_so="${JNI_DIR}/${TOOL_MAP[$tool]}"
  if [ -f "$src_bin" ]; then
    cp -L "$src_bin" "$dst_so"
    chmod 755 "$dst_so"
    patch_elf_bytes "$dst_so"
  else
    echo "Warning: binary $src_bin not found!" >&2
  fi
done

# Stage Python binary as libpython3_bin.so
PY_SRC=""
if [ -f "${SOURCE_PREFIX}/bin/python3" ]; then
  PY_SRC="${SOURCE_PREFIX}/bin/python3"
elif [ -f "${SOURCE_PREFIX}/bin/python" ]; then
  PY_SRC="${SOURCE_PREFIX}/bin/python"
fi
if [ -n "$PY_SRC" ]; then
  cp -L "$PY_SRC" "${JNI_DIR}/libpython3_bin.so"
  chmod 755 "${JNI_DIR}/libpython3_bin.so"
  patch_elf_bytes "${JNI_DIR}/libpython3_bin.so"
fi

if [ -f "${SOURCE_PREFIX}/libexec/git-core/git-remote-https" ]; then
  cp -L "${SOURCE_PREFIX}/libexec/git-core/git-remote-https" "${JNI_DIR}/libgit_remote_https.so"
  chmod 755 "${JNI_DIR}/libgit_remote_https.so"
  patch_elf_bytes "${JNI_DIR}/libgit_remote_https.so"
fi

# 5. Bundle Read-Only Assets
echo "[5/7] Staging Python stdlib, Git templates, Certs, and Web UI..."
mkdir -p "${STAGING_DIR}/etc/tls" "${STAGING_DIR}/share/agy-ui" \
         "${STAGING_DIR}/share/git-core" "${STAGING_DIR}/lib"

if [ -f "${SOURCE_PREFIX}/etc/tls/cert.pem" ]; then
  cp -L "${SOURCE_PREFIX}/etc/tls/cert.pem" "${STAGING_DIR}/etc/tls/cert.pem"
fi

if [ -d "${SOURCE_PREFIX}/share/git-core/templates" ]; then
  cp -rL "${SOURCE_PREFIX}/share/git-core/templates" "${STAGING_DIR}/share/git-core/"
fi

AGY_UI_DIR="${PROJECT_ROOT}/agy-ui"
if [ ! -d "$AGY_UI_DIR" ]; then
  echo "Error: Required web assets directory not found at $AGY_UI_DIR" >&2
  exit 1
fi

cp -L "${AGY_UI_DIR}/index.html" "${STAGING_DIR}/share/agy-ui/index.html"
cp -L "${AGY_UI_DIR}/sw.js" "${STAGING_DIR}/share/agy-ui/sw.js"
cp -L "${AGY_UI_DIR}/server.py" "${STAGING_DIR}/share/agy-ui/server.py"
cp -L "${AGY_UI_DIR}/ask_question.py" "${STAGING_DIR}/share/agy-ui/ask_question.py"
chmod 0755 "${STAGING_DIR}/share/agy-ui/ask_question.py"
mkdir -p "${STAGING_DIR}/bin"
ln -sf "../share/agy-ui/ask_question.py" "${STAGING_DIR}/bin/ask_question"
if [ -d "${AGY_UI_DIR}/skills" ]; then
  mkdir -p "${STAGING_DIR}/share/agy-ui/skills"
  cp -rL "${AGY_UI_DIR}/skills"/* "${STAGING_DIR}/share/agy-ui/skills/" 2>/dev/null || true
fi
cp -L "${AGY_UI_DIR}/icon-192.png" "${STAGING_DIR}/share/agy-ui/icon-192.png" 2>/dev/null || true
cp -L "${AGY_UI_DIR}/icon-512.png" "${STAGING_DIR}/share/agy-ui/icon-512.png" 2>/dev/null || true
cp -L "${AGY_UI_DIR}/manifest.json" "${STAGING_DIR}/share/agy-ui/manifest.json" 2>/dev/null || true
cp -L "${AGY_UI_DIR}/default_app_prompt.md" "${STAGING_DIR}/share/agy-ui/default_app_prompt.md" 2>/dev/null || true

PY_DIR=""
for d in "${SOURCE_PREFIX}/lib/python3."*; do
  if [ -d "$d" ]; then
    PY_DIR="$d"
    break
  fi
done

if [ -n "$PY_DIR" ] && [ -d "$PY_DIR" ]; then
  py_base=$(basename "$PY_DIR")
  mkdir -p "${STAGING_DIR}/lib/${py_base}"
  cp -rL "$PY_DIR"/* "${STAGING_DIR}/lib/${py_base}/" || true
  
  if [ "$py_base" != "python3.12" ]; then
    ln -sf "${py_base}" "${STAGING_DIR}/lib/python3.12"
  fi

  find "${STAGING_DIR}/lib/${py_base}" -type d \( -name "test" -o -name "tests" -o -name "__pycache__" -o -name "idlelib" -o -name "tkinter" -o -name "turtledemo" \) -prune -exec rm -rf {} + 2>/dev/null || true
  find "${STAGING_DIR}/lib/${py_base}" -type f -name "*.pyc" -delete 2>/dev/null || true
  find "${STAGING_DIR}/lib/${py_base}" -type f -name "*.a" -delete 2>/dev/null || true
fi

# 6. Relocate Python C-extensions to jniLibs (SELinux W^X Compliance)
echo "[6/7] Relocating Python C-extension .so modules to jniLibs..."
ext_idx=0
while IFS= read -r -d '' py_so; do
  rel_path="${py_so#${STAGING_DIR}/}"
  ext_idx=$((ext_idx + 1))
  jni_name="libpyext_${ext_idx}.so"
  mv "$py_so" "${JNI_DIR}/${jni_name}"
  chmod 755 "${JNI_DIR}/${jni_name}"
  patch_elf_bytes "${JNI_DIR}/${jni_name}"
  echo "${rel_path}|${jni_name}" >> "$MANIFEST_FILE"
done < <(find "${STAGING_DIR}"/lib/python* -type f -name "*.so*" -print0)

# 7. Recursive DT_NEEDED Shared Library Crawler
echo "[7/7] Resolving transitive Bionic shared libraries..."
READELF_BIN="readelf"
command -v llvm-readelf >/dev/null && READELF_BIN="llvm-readelf"

declare -A STAGED_DEPS=()
queue=()
for elf in "${JNI_DIR}"/libbash.so "${JNI_DIR}"/libbusybox.so "${JNI_DIR}"/libgit.so \
           "${JNI_DIR}"/libgit_remote_https.so "${JNI_DIR}"/librg.so \
           "${JNI_DIR}"/libcurl_bin.so "${JNI_DIR}"/libpython3_bin.so \
           "${JNI_DIR}"/libssh_bin.so "${JNI_DIR}"/libssh_keygen.so \
           "${JNI_DIR}"/libpyext_*.so; do
  [ -f "$elf" ] && queue+=("$elf")
done

while [ "${#queue[@]}" -gt 0 ]; do
  current="${queue[0]}"
  queue=("${queue[@]:1}")
  while IFS= read -r needed; do
    [ -z "$needed" ] && continue
    case "$needed" in
      libc.so|libm.so|libdl.so|liblog.so|libz.so) continue ;;
    esac
    if [ -z "${STAGED_DEPS[$needed]+x}" ] && [ -f "${SOURCE_PREFIX}/lib/${needed}" ]; then
      safe_base=$(echo "$needed" | sed -E 's/[^a-zA-Z0-9_]/_/g; s/_so.*$/.so/')
      [[ "$safe_base" != lib* ]] && safe_base="lib_${safe_base}"
      [[ "$safe_base" != *.so ]] && safe_base="${safe_base}.so"

      # Prevent collision between busybox executable and its shared library
      if [ "$needed" = "libbusybox.so.1.38.0" ] || [ "$safe_base" = "libbusybox.so" ]; then
        safe_base="libbusybox_lib.so"
      fi

      cp -L "${SOURCE_PREFIX}/lib/${needed}" "${JNI_DIR}/${safe_base}"
      chmod 755 "${JNI_DIR}/${safe_base}"
      patch_elf_bytes "${JNI_DIR}/${safe_base}"
      STAGED_DEPS["$needed"]="$safe_base"
      echo "lib/${needed}|${safe_base}" >> "$MANIFEST_FILE"
      queue+=("${JNI_DIR}/${safe_base}")
    fi
  done < <($READELF_BIN -d "$current" 2>/dev/null | awk -F'[][]' '/\(NEEDED\)/ {print $2}')
done

# Patch text configs in staging
find "$STAGING_DIR" -type f \( -name "*.py" -o -name "*.cfg" -o -name "*.json" -o -name "*.sh" \) -exec \
  sed -i "s|/data/data/com.termux|/data/data/${TARGET_PKG}|g" {} + 2>/dev/null || true

tar --zstd -cf "${ASSETS_DIR}/usr-data.tar.zst" -C "$STAGING_DIR" .
rm -rf "$STAGING_DIR"

echo "=== Binary Preparation Complete ==="
echo "Total staged jniLibs size: $(du -sh "$JNI_DIR" | cut -f1)"
echo "Total staged assets size:  $(du -sh "${ASSETS_DIR}/usr-data.tar.zst" | cut -f1)"
