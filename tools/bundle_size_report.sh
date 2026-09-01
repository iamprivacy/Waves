#!/usr/bin/env bash
#
# bundle_size_report.sh: print a stable, per-item size table for a built bundle.
#
# Emitted at the end of every trim (tools/trim_qt_bundle.sh calls this), so any
# build log, local or CI, carries the same table and a size regression is
# visible by diffing two logs. Plain text, stable sort, sizes in MB.
#
# Pass the bundle ROOT, same as trim_qt_bundle.sh:
#   macOS:    dist/waves.app
#   Linux:    dist/waves.dist
#   Windows:  dist/waves.dist
set -euo pipefail

DIR="${1:-dist/waves.app}"
[ -e "$DIR" ] || { echo "error: '$DIR' not found; build first (make gui-waves)" >&2; exit 1; }

if [ -d "$DIR/Contents/MacOS" ]; then
  LIBDIR="$DIR/Contents/MacOS"
elif [ "$(basename "$DIR")" = "MacOS" ]; then
  LIBDIR="$DIR"
else
  LIBDIR="$DIR"
fi
[ -d "$LIBDIR/PySide6" ] || { echo "error: '$LIBDIR' doesn't look like a Waves bundle (no PySide6/)" >&2; exit 1; }

echo "=== bundle size report: $DIR ==="
echo "total_mb $(du -sm "$DIR" | cut -f1)"
for exe in "$LIBDIR/Waves" "$LIBDIR/Waves.exe" "$LIBDIR/waves" "$LIBDIR/waves.bin" "$LIBDIR/waves.exe"; do
  if [ -f "$exe" ]; then
    echo "binary_mb $(du -sm "$exe" | cut -f1) ($(basename "$exe"))"
    break
  fi
done

echo "--- top 25 items in $(basename "$LIBDIR") (MB) ---"
# One du per top-level entry (file or dir), largest first, name as tiebreaker
# so equal-size rows never reorder between runs.
#
# awk keeps the first 25 rows itself instead of a `head -25` on the end of the
# pipe. head leaves as soon as it has its 25 lines, and once sort has more
# output than fits in the pipe buffer it is still writing at that moment: it
# dies of SIGPIPE, exits 141, and with `set -o pipefail` that is a failed
# pipeline, which `set -e` turns into a failed build. Nothing was wrong with
# the bundle, it had just grown enough top-level entries to fill 64 KB of du
# output. awk reads to the end, so no producer is ever cut off.
find "$LIBDIR" -mindepth 1 -maxdepth 1 -print0 \
  | xargs -0 du -sm 2>/dev/null \
  | sort -k1,1nr -k2,2 \
  | awk -F'\t' 'NR <= 25 {n=split($2,p,"/"); printf "%6d  %s\n", $1, p[n]}'

echo "--- PySide6 subtree (MB) ---"
for sub in qml qt-plugins "Qt/plugins"; do
  if [ -d "$LIBDIR/PySide6/$sub" ]; then
    echo "$(du -sm "$LIBDIR/PySide6/$sub" | cut -f1)  PySide6/$sub"
  fi
done
echo "file_count $(find "$DIR" -type f | wc -l | tr -d ' ')"
echo "=== end bundle size report ==="
