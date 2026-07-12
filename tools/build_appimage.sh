#!/usr/bin/env bash
#
# build_appimage.sh: package the trimmed Nuitka standalone tree into a
# single-file AppImage.
#
# Usage: build_appimage.sh <dist-tree> <output.AppImage>
#   <dist-tree>        the trimmed standalone folder (CI calls it "Waves")
#   <output.AppImage>  where to write the finished AppImage
#
# Linux-only (CI's ubuntu legs). appimagetool is downloaded pinned by version
# AND sha256, fail-closed, the same trust rule as the FFmpeg manager and the
# in-app updater: a hash mismatch aborts the build rather than running an
# unverified packager binary.
set -euo pipefail

DIST_TREE="${1:?usage: build_appimage.sh <dist-tree> <output.AppImage>}"
OUT="${2:?usage: build_appimage.sh <dist-tree> <output.AppImage>}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$DIST_TREE" ] || { echo "error: dist tree '$DIST_TREE' not found" >&2; exit 1; }
[ -x "$DIST_TREE/Waves" ] || { echo "error: '$DIST_TREE/Waves' is not an executable" >&2; exit 1; }

# Pinned appimagetool 1.9.1 (github.com/AppImage/appimagetool), hashed 2026-07-12.
TOOL_VERSION="1.9.1"
case "$(uname -m)" in
  x86_64)
    TOOL_ARCH="x86_64"
    TOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
    ;;
  aarch64 | arm64)
    TOOL_ARCH="aarch64"
    TOOL_SHA256="f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158"
    ;;
  *)
    echo "error: unsupported architecture '$(uname -m)'" >&2
    exit 1
    ;;
esac

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# AppDir layout: AppRun + desktop entry + icon at the root, the untouched
# standalone tree under usr/lib/waves (AppRun execs usr/lib/waves/Waves).
APPDIR="$WORK/AppDir"
mkdir -p "$APPDIR/usr/lib"
cp -a "$DIST_TREE" "$APPDIR/usr/lib/waves"
install -m 0755 "$REPO_ROOT/tools/appimage/AppRun" "$APPDIR/AppRun"
install -m 0644 "$REPO_ROOT/tools/appimage/waves.desktop" "$APPDIR/waves.desktop"
install -m 0644 "$REPO_ROOT/tidaler/ui/icon512.png" "$APPDIR/waves.png"

TOOL="$WORK/appimagetool"
echo "→ fetching appimagetool ${TOOL_VERSION} (${TOOL_ARCH})"
curl -fsSL -o "$TOOL" \
  "https://github.com/AppImage/appimagetool/releases/download/${TOOL_VERSION}/appimagetool-${TOOL_ARCH}.AppImage"
echo "${TOOL_SHA256}  ${TOOL}" | sha256sum -c - >/dev/null \
  || { echo "error: appimagetool checksum mismatch; refusing to run it" >&2; exit 1; }
chmod +x "$TOOL"

# --appimage-extract-and-run: CI runners have no FUSE. ARCH tells appimagetool
# which embedded runtime to bundle into the output. No update information is
# embedded: Waves's own signed in-app updater replaces the file.
ARCH="$TOOL_ARCH" "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "✓ built $OUT"
