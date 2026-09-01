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

# Submodule dirs under PySide6/qml/QtQuick/ that Waves never imports. The app's
# QML uses Controls(.Basic), Dialogs, Effects, Layouts, Shapes, Templates and
# Window; everything below is scenery for apps we are not (audited by grepping
# every .qml import and the running app's plugin loads).
QTQUICK_QML_DIRS=(
  NativeStyle Particles Pdf Scene2D Scene3D Timeline VectorImage
  VirtualKeyboard LocalStorage tooling
  Shapes/DesignHelpers
)

# Qt.labs.* QML modules Waves never imports. `settings` stays: Main.qml uses
# Settings {} via the QtCore import, whose plugin lives beside these.
QT_LABS_DIRS=(
  StyleKit animation folderlistmodel qmlmodels sharedimage synchronizer
  wavefrontmesh
)

# QtQuick.Controls styles we don't use: the UI pins QtQuick.Controls.Basic, so
# only Basic (plus the shared impl/ and the Controls plugin itself) is needed.
CONTROLS_STYLES=(FluentWinUI3 iOS macOS designer Material Fusion Universal Imagine)

# PySide6 Python bindings for modules a QML-only app never imports. Confirmed
# leaf nodes by audit (the Waves graph uses only PySide6
# QtCore/QtGui/QtNetwork/QtQml; the QML runtime links the Qt QUICK library
# directly, never the QtQuick Python binding).
PYSIDE_BINDINGS=(QtWidgets QtOpenGL QtQuick)

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
# ShaderTools: nothing in the bundle links it (QtQuick.Effects ships pre-baked
# shaders inside QtQuickEffects; verified by an otool -L reverse scan of every
# Mach-O). Svg: only pulled by the unused VectorImage/svg-icon chain, all of
# which goes too: the QML dir above, the QuickVectorImage libraries via their
# token, and the svg imageformat/iconengine plugins below.
# The Labs* tokens name each library individually; never add a bare "Labs"
# token, QtLabsSettings must survive (QtCore's Settings {} in Main.qml).
# unicodedata.so is NOT dead weight and must never be listed here: idna (a
# requests dependency in the bundle) imports unicodedata at module scope.
MODULE_TOKENS=(
  WebEngine 3D Charts Graphs DataVisualization Location Positioning
  SpatialAudio Pdf Sensors Sql Test WebSockets WebChannel WebView
  VirtualKeyboard RemoteObjects Scxml StateMachine Bluetooth Nfc SerialPort
  SerialBus TextToSpeech 5Compat Widgets
  QuickControls2Material QuickControls2Fusion QuickControls2Imagine
  QuickControls2Universal QuickControls2FluentWinUI3 QuickControls2IOS
  QuickControls2MacOS
  ShaderTools Svg
  QuickVectorImage QmlLocalStorage
  LabsAnimation LabsFolderListModel LabsPlatform LabsQmlModels
  LabsSharedImage LabsStyleKit LabsSynchronizer LabsWavefrontMesh
)

before=$(du -sm "$DIR" | cut -f1)

for m in "${QML_MODULES[@]}"; do
  rm -rf "$LIBDIR/PySide6/qml/$m"
done
for s in "${CONTROLS_STYLES[@]}"; do
  rm -rf "$LIBDIR/PySide6/qml/QtQuick/Controls/$s"
done
for d in "${QTQUICK_QML_DIRS[@]}"; do
  rm -rf "$LIBDIR/PySide6/qml/QtQuick/$d"
done
for d in "${QT_LABS_DIRS[@]}"; do
  rm -rf "$LIBDIR/PySide6/qml/Qt/labs/$d"
done
# The StateMachine token below removes the QtStateMachine libraries; their QML
# plugin has to go with them or it dangles against deleted libraries (the
# otool sweep in the perf-pass baseline caught it dangling in shipped builds).
rm -rf "$LIBDIR/PySide6/qml/QtQml/StateMachine"
for b in "${PYSIDE_BINDINGS[@]}"; do
  rm -f "$LIBDIR/PySide6/$b.so" "$LIBDIR/PySide6/$b.pyd" "$LIBDIR/PySide6/$b"*.so
done
# The Qt.labs.platform QML module and the QWidget-only style plugins both pull
# QtWidgets and are never used by a QtQuick.Controls.Basic app.
rm -rf "$LIBDIR/PySide6/qml/Qt/labs/platform" "$LIBDIR/PySide6/qt-plugins/styles" "$LIBDIR/PySide6/Qt/plugins/styles"

# .qmltypes are qmllint/Qt Creator metadata; the QML engine reads qmldir, never
# these. 2.2 MB across ~56 files.
find "$LIBDIR/PySide6/qml" -name '*.qmltypes' -delete 2>/dev/null || true

# Image format plugins: TIDAL cover art is JPEG and the app icon is PNG, and
# PNG decoding is built into QtGui, so qjpeg is the only plugin the app can
# ever exercise. The svg icon engine goes with the Svg library above.
for pdir in "$LIBDIR/PySide6/qt-plugins/imageformats" "$LIBDIR/PySide6/Qt/plugins/imageformats"; do
  [ -d "$pdir" ] || continue
  for f in "$pdir"/*; do
    case "$(basename "$f")" in
      *qjpeg*) : ;;
      *) rm -f "$f" ;;
    esac
  done
done
rm -rf "$LIBDIR/PySide6/qt-plugins/iconengines" "$LIBDIR/PySide6/Qt/plugins/iconengines"

# The darwin media backend is idle: Qt 6 defaults to the ffmpeg backend and
# Waves sets no QT_MEDIA_BACKEND override, so libffmpegmediaplugin (kept) is
# the one that loads.
rm -f "$LIBDIR/PySide6/qt-plugins/multimedia/libdarwinmediaplugin.dylib" \
      "$LIBDIR/PySide6/Qt/plugins/multimedia/libdarwinmediaplugin.dylib"

# pycryptodome native modules: the app's ONLY Crypto surface is the updater's
# Ed25519 verify() (waves_ui/signing.py, raw 32-byte key, no PEM), so of the
# ~2.6 MB of Crypto .so Nuitka copies, six files cover it, proven by the
# perf-pass sandbox probe (delete the rest, sign a manifest with the full
# venv Crypto, verify it in the gutted copy: real signature True, tampered
# False, shipped-key walk False without raising). Kept: _ed25519 (the curve),
# _SHA512 + _keccak (eddsa imports SHA512 and SHAKE256 at module level),
# _modexp (Integer backend on machines without GMP; small, never risk it),
# _strxor + _cpuid_c (Util plumbing loaded by the raw-lib loader). The
# producer half (keygen/sign) runs only from source and CI, never from the
# bundle. Extensions differ per OS (.so / .pyd), match both.
if [ -d "$LIBDIR/Crypto" ]; then
  find "$LIBDIR/Crypto" \( -name '*.so' -o -name '*.pyd' \) -print0 | while IFS= read -r -d '' f; do
    case "$(basename "$f")" in
      _ed25519.*|_SHA512.*|_keccak.*|_modexp.*|_strxor.*|_cpuid_c.*) : ;;
      *) rm -f "$f" ;;
    esac
  done
fi

# On the arm64 macOS legs Nuitka copies BOTH the versioned Homebrew OpenSSL
# libraries and their unversioned symlink twins; only the versioned pair is
# linked (same install ID, checked with otool -L). The Intel legs already
# avoid this pre-build in CI; this covers local builds and is a no-op there.
if [ -e "$LIBDIR/libcrypto.3.dylib" ]; then rm -f "$LIBDIR/libcrypto.dylib"; fi
if [ -e "$LIBDIR/libssl.3.dylib" ]; then rm -f "$LIBDIR/libssl.dylib"; fi

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

# Per-item size table in every build log (local and CI) so a size regression
# shows up as a plain diff between two logs.
bash "$(dirname "$0")/bundle_size_report.sh" "$DIR"
