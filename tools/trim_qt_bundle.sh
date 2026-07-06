#!/usr/bin/env bash
#
# trim_qt_bundle.sh: strip Qt modules Waves never loads from a built bundle.
#
# Nuitka's PySide6 plugin copies the *entire* qml/ module tree (and every Qt
# library it links) into the standalone build. Waves is a small QtQuick app: it
# only imports QtQuick(.Controls.Basic / .Layouts / .Effects / .Shapes /
# .Dialogs) and QtCore, so the vast majority is dead weight, led by a ~210 MB
# bundled Chromium (QtWebEngineCore). Removing unreferenced modules is safe:
# nothing imports them, so nothing loads them.
#
# Works on all three packaged layouts; pass the bundle ROOT:
#   macOS:    dist/waves.app        (libs live under Contents/MacOS, bare names)
#   Linux:    dist/waves.dist       (libQt6Foo.so.6)
#   Windows:  dist/waves.dist       (Qt6Foo.dll)
# Qt library names differ per OS, so module matching is by SUBSTRING, which makes
# the one token list work everywhere. macOS is the only layout verified locally;
# the Linux/Windows legs are exercised on CI.
set -euo pipefail

DIR="${1:-dist/waves.app}"
[ -e "$DIR" ] || { echo "error: '$DIR' not found; build first (make gui-waves)" >&2; exit 1; }

# Resolve the directory that holds the Qt libraries + the PySide6/ tree.
if [ -d "$DIR/Contents/MacOS" ]; then
  LIBDIR="$DIR/Contents/MacOS"        # macOS .app bundle
elif [ "$(basename "$DIR")" = "MacOS" ]; then
  LIBDIR="$DIR"                       # already pointed at Contents/MacOS
else
  LIBDIR="$DIR"                       # Linux/Windows standalone .dist root
fi
[ -d "$LIBDIR/PySide6" ] || { echo "error: '$LIBDIR' doesn't look like a Waves bundle (no PySide6/)" >&2; exit 1; }

# QML modules under PySide6/qml/ that Waves never imports (same relative path on
# every OS).
# NOTE: QtMultimedia is deliberately NOT listed: the in-app preview player
# needs the QtMultimedia QML module and its media backend plugin. Its 3D-audio
# sibling (QtSpatialAudio) is still dropped.
QML_MODULES=(
  QtWebEngine QtQuick3D Qt3D Qt5Compat QtGraphs QtCharts QtDataVisualization
  QtTest QtLocation QtPositioning QtTextToSpeech QtWebSockets
  QtSensors QtWebView QtRemoteObjects QtScxml QtWebChannel QtSpatialAudio
  QtNfc QtBluetooth QtSerialPort QtSerialBus QtStateMachine QtPdf
  QtVirtualKeyboard
)

# QtQuick.Controls styles we don't use: the UI pins QtQuick.Controls.Basic, so
# only Basic (plus the shared impl/ and the Controls plugin itself) is needed.
CONTROLS_STYLES=(FluentWinUI3 iOS macOS designer Material Fusion Universal Imagine)

# PySide6 Python bindings for modules a QML-only app never imports. Confirmed
# leaf nodes by audit (the Waves graph uses only PySide6 QtCore/QtGui/QtQml).
PYSIDE_BINDINGS=(QtWidgets QtOpenGL)

# SUBSTRING tokens for the top-level Qt *library* files to remove. Matched as
# substrings so one list covers macOS (QtWebEngineCore), Linux
# (libQt6WebEngineCore.so.6) and Windows (Qt6WebEngineCore.dll). "3D" also
# matches QtQuick3D*; "Widgets" matches both QtWidgets and QtOpenGLWidgets.
#
# HARD INVARIANT: never match the bare QtOpenGL library. QtQuick hard-links it on
# every OS even on the Metal/D3D RHI backend, so removing it makes the core
# libqtquick2plugin fail to load and the app dies at launch. There is NO "OpenGL"
# token below, and the keep-guard in remove_libs() refuses any *OpenGL* file that
# is not *OpenGLWidgets*.
#
# "Multimedia" is intentionally absent so the QtMultimedia library survives; the
# "Widgets" token below still removes the unused QtMultimediaWidgets.
MODULE_TOKENS=(
  WebEngine 3D Charts Graphs DataVisualization Location Positioning
  SpatialAudio Pdf Sensors Sql Test WebSockets WebChannel WebView
  VirtualKeyboard RemoteObjects Scxml StateMachine Bluetooth Nfc SerialPort
  SerialBus TextToSpeech 5Compat Widgets
  QuickControls2Material QuickControls2Fusion QuickControls2Imagine
  QuickControls2Universal QuickControls2FluentWinUI3 QuickControls2IOS
  QuickControls2MacOS
)

before=$(du -sm "$DIR" | cut -f1)

for m in "${QML_MODULES[@]}"; do
  rm -rf "$LIBDIR/PySide6/qml/$m"
done
for s in "${CONTROLS_STYLES[@]}"; do
  rm -rf "$LIBDIR/PySide6/qml/QtQuick/Controls/$s"
done
for b in "${PYSIDE_BINDINGS[@]}"; do
  rm -f "$LIBDIR/PySide6/$b.so" "$LIBDIR/PySide6/$b.pyd" "$LIBDIR/PySide6/$b"*.so
done
# The Qt.labs.platform QML module and the QWidget-only style plugins both pull
# QtWidgets and are never used by a QtQuick.Controls.Basic app.
rm -rf "$LIBDIR/PySide6/qml/Qt/labs/platform" "$LIBDIR/PySide6/qt-plugins/styles" "$LIBDIR/PySide6/Qt/plugins/styles"

shopt -s nullglob
for token in "${MODULE_TOKENS[@]}"; do
  for f in "$LIBDIR"/*"$token"*; do
    base=$(basename "$f")
    case "$base" in
      *penGLWidgets*) : ;;   # QtOpenGLWidgets is removable; fall through to rm
      *penGL*) continue ;;   # any other *OpenGL* is the framework QtQuick needs; KEEP
    esac
    rm -rf "$f"
  done
done
shopt -u nullglob

after=$(du -sm "$DIR" | cut -f1)
echo "trim_qt_bundle: ${before} MB -> ${after} MB (removed $((before - after)) MB) in $DIR"
