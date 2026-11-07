#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS_DIR="${PROJECT_ROOT}/app/src/main/assets"
ARCHIVE="${ASSETS_DIR}/usr-data.tar.zst"
AGY_UI_DIR="${PROJECT_ROOT}/agy-ui"
TARGET_PKG="ai.agy.app"

if [ ! -f "$ARCHIVE" ] || [ ! -d "$AGY_UI_DIR" ]; then
  exit 0
fi

TAR_CMD="tar"
if ! tar --help 2>&1 | grep -q -- "--zstd"; then
  if command -v agy-sh >/dev/null 2>&1; then
    TAR_CMD="agy-sh tar"
  fi
fi

mkdir -p "${HOME}/tmp"
TEMP_STAGING=$(mktemp -d -p "${HOME}/tmp")
trap 'rm -rf "$TEMP_STAGING"' EXIT

# Extract existing archive
$TAR_CMD --zstd -xf "$ARCHIVE" -C "$TEMP_STAGING"

# Copy updated agy-ui files into share/agy-ui
DEST_UI="${TEMP_STAGING}/share/agy-ui"
mkdir -p "$DEST_UI"

for item in index.html sw.js server.py ask_question.py agy-device default_app_prompt.md icon-192.png icon-512.png manifest.json; do
  if [ -f "${AGY_UI_DIR}/${item}" ]; then
    cp -f "${AGY_UI_DIR}/${item}" "${DEST_UI}/${item}"
    case "$item" in
      *.py|*.html|*.js|*.json|*.md|agy-device)
        sed -i "s|/data/data/com.termux|/data/data/${TARGET_PKG}|g" "${DEST_UI}/${item}" 2>/dev/null || true
        ;;
    esac
  fi
done

if [ -f "${DEST_UI}/ask_question.py" ]; then
  chmod 0755 "${DEST_UI}/ask_question.py"
fi

if [ -f "${DEST_UI}/agy-device" ]; then
  chmod 0755 "${DEST_UI}/agy-device"
  mkdir -p "${TEMP_STAGING}/bin"
  cp -f "${DEST_UI}/agy-device" "${TEMP_STAGING}/bin/agy-device"
  chmod 0755 "${TEMP_STAGING}/bin/agy-device"
fi

if [ -d "${AGY_UI_DIR}/skills" ]; then
  mkdir -p "${DEST_UI}/skills"
  cp -rf "${AGY_UI_DIR}/skills"/* "${DEST_UI}/skills/" 2>/dev/null || true
fi

# Repack archive
$TAR_CMD --zstd -cf "$ARCHIVE" -C "$TEMP_STAGING" .
