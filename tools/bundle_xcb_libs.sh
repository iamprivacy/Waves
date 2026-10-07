#!/usr/bin/env bash
#
# bundle_xcb_libs.sh: copy the X11 helper libraries Qt's xcb platform plugin
# needs into the trimmed Linux standalone tree.
#
# Usage: bundle_xcb_libs.sh <dist-tree>
#
# Since Qt 6.5 the xcb plugin links libxcb-cursor, libxkbcommon-x11 and the
# other xcb-util libraries. A stock Ubuntu 22.04 desktop lacks some of them,
# and without them Qt finds no platform plugin and Waves exits before showing
# a window ("could not load the Qt platform plugin xcb"). Nuitka does not
# bundle them because they are system libraries, so this step does: every
# direct dependency of the xcb plugin pieces that matches the xcb-util family
# is copied next to the Qt libraries (where Nuitka's $ORIGIN rpath finds it)
# and given an $ORIGIN rpath of its own, so its own xcb-util dependencies
# resolve inside the tree too.
#
# Left to the host on purpose: libxcb.so.1, libX11 and the GL/DRI pieces
# (they must match the running X server and graphics driver), plus libc and
# friends. Any other host dependency is printed as a CI warning so a new Qt
# release that grows one is noticed rather than shipped silently.
#
# Linux-only (CI's ubuntu legs). Needs the xcb-util packages installed on the
# build host and patchelf on PATH.
set -euo pipefail

TREE="${1:?usage: bundle_xcb_libs.sh <dist-tree>}"
[ -d "$TREE" ] || { echo "error: dist tree '$TREE' not found" >&2; exit 1; }
command -v patchelf >/dev/null || { echo "error: patchelf is not installed" >&2; exit 1; }

# Copied into the tree when the xcb plugin pieces need them.
BUNDLE_RE='^lib(xcb-(cursor|icccm|image|keysyms|render-util|util|xkb|xinerama|xinput|randr|render|shape|shm|sync|xfixes)\.so\.[0-9]+|xkbcommon(-x11)?\.so\.0)$'
# Provided by every X11 desktop, and tied to its X server, GL driver or libc.
HOST_RE='^lib(c|m|dl|rt|pthread|stdc\+\+|gcc_s|X11|X11-xcb|Xau|Xdmcp|xcb|xcb-(glx|dri2|dri3|present)|EGL|GL|GLX|OpenGL|fontconfig|freetype|glib-2\.0|gthread-2\.0|gobject-2\.0|dbus-1|ICE|SM|z)\.so(\.[0-9]+)*$'

seeds=()
while IFS= read -r f; do seeds+=("$f"); done < <(
  find "$TREE" -maxdepth 1 -name 'libQt6XcbQpa.so*' -type f
  find "$TREE" -path '*/platforms/libqxcb.so' -type f
  find "$TREE" -path '*/xcbglintegrations/*.so' -type f
)
[ "${#seeds[@]}" -gt 0 ] || { echo "error: no Qt xcb plugin found under '$TREE'" >&2; exit 1; }

queue=("${seeds[@]}")
copied=0
while [ "${#queue[@]}" -gt 0 ]; do
  obj="${queue[0]}"
  queue=("${queue[@]:1}")
  # ldd resolves through the object's own rpath, so tree copies win. Both
  # listings go through variables: a failing patchelf inside a process
  # substitution would be invisible to set -e, and a grep -q on a pipe can
  # close it early and fail the whole pipeline.
  resolved="$(ldd "$obj")"
  needed="$(patchelf --print-needed "$obj")"
  while IFS= read -r soname; do
    [ -n "$soname" ] || continue
    [ -e "$TREE/$soname" ] && continue
    if [[ "$soname" =~ $HOST_RE ]]; then continue; fi
    # The dynamic loader is named without a lib prefix and resolves without
    # a "=>" line.
    case "$soname" in ld-linux*|ld64*|linux-vdso*) continue ;; esac
    path="$(awk -v n="$soname" '$1 == n && $2 == "=>" { print $3 }' <<<"$resolved")"
    if [ -z "$path" ] || [ "$path" = "not" ]; then
      echo "error: $soname (needed by ${obj#"$TREE"/}) is not installed on the build host" >&2
      exit 1
    fi
    if [[ "$soname" =~ $BUNDLE_RE ]]; then
      cp -L "$path" "$TREE/$soname"
      # Executable like the Qt libraries beside it, so ldd stays quiet.
      chmod 0755 "$TREE/$soname"
      # shellcheck disable=SC2016 # a literal $ORIGIN for the loader
      patchelf --set-rpath '$ORIGIN' "$TREE/$soname"
      echo "bundled $soname"
      copied=$((copied + 1))
      queue+=("$TREE/$soname")
    else
      echo "::warning::bundle_xcb_libs: ${obj#"$TREE"/} needs $soname from the host; bundle it or list it as host-provided"
    fi
  done <<<"$needed"
done

# Every seed and every copy must now resolve completely, and every library of
# the bundled family must resolve to the copy in the tree, not to the host's
# (the host has them all installed, so "nothing missing" alone proves
# nothing about what a user's desktop will load).
tree_abs="$(cd "$TREE" && pwd -P)"
for obj in "${seeds[@]}" "$TREE"/libxcb-*.so.* "$TREE"/libxkbcommon*.so.*; do
  [ -e "$obj" ] || continue
  resolved="$(ldd "$obj")"
  # A host-provided library may be absent on a bare build runner (CI
  # installs the GL stack only for the smoke launch); that is the user's
  # desktop's business, not a bundling failure.
  missing="$(awk '$2 == "=>" && $3 == "not" { print $1 }' <<<"$resolved")"
  unresolved=""
  while IFS= read -r soname; do
    [ -n "$soname" ] || continue
    if [[ "$soname" =~ $HOST_RE ]]; then continue; fi
    unresolved="$unresolved $soname"
  done <<<"$missing"
  if [ -n "$unresolved" ]; then
    echo "error: ${obj#"$TREE"/} still has unresolved libraries:$unresolved" >&2
    exit 1
  fi
  while IFS= read -r soname path; do
    [ -n "$soname" ] || continue
    if [[ "$soname" =~ $BUNDLE_RE ]] && [ "$(dirname "$path")" != "$tree_abs" ]; then
      echo "error: ${obj#"$TREE"/} loads $soname from $path, not from the tree" >&2
      exit 1
    fi
  done < <(awk '$2 == "=>" { print $1, $3 }' <<<"$resolved")
done
echo "✓ bundled $copied xcb helper libraries into $TREE"
