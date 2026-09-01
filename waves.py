#!/usr/bin/env python
"""Packaged-build entry point for the Waves QML UI.

This is the compile target for a standalone Waves build (the binary that ships
in the public repo's Releases). It is a thin launcher, deliberately separate
from ``waves/waves_ui/app.py``: Nuitka compiles the entry file as ``__main__``,
so it must use *absolute* imports, ``waves_ui`` keeps its relative imports and
stays a normal package, untouched.

This file lives at the repo root, NOT inside the package: as ``waves/waves.py``
it would shadow the ``waves`` package for its own absolute imports, and Nuitka
names the build artifacts (``dist/waves.app`` / ``dist/waves.dist``) after this
file's basename, which the Makefile and CI key on, so neither its name nor its
location may change casually.

From a source checkout, ``python -m waves.waves_ui`` remains the way to run;
this file exists so ``make gui-waves`` can produce the frozen app.

The ``nuitka-project`` directives below are the canonical build recipe. They
target the QML front-end: the PySide6 plugin pulls
in Qt's QML runtime, and the bundled ``qml/`` and ``fonts/`` directories ride
along as data files so ``app.py`` finds them next to itself at runtime.
"""

# Compilation mode, support OS-specific options
# nuitka-project-if: {OS} in ("Darwin"):
#    nuitka-project: --macos-create-app-bundle
#    nuitka-project: --macos-app-icon={MAIN_DIRECTORY}/waves/ui/icon.icns
#    nuitka-project: --macos-signed-app-name=com.waves.app
#    nuitka-project: --macos-app-mode=gui
# nuitka-project-if: {OS} in ("Linux", "FreeBSD"):
#    nuitka-project: --linux-icon={MAIN_DIRECTORY}/waves/ui/icon512.png
# nuitka-project-if: {OS} in ("Windows"):
#    nuitka-project: --windows-icon-from-ico={MAIN_DIRECTORY}/waves/ui/icon.ico
#    nuitka-project: --file-description="Waves: saves music from your TIDAL account for offline listening."

# Debugging options, controlled via environment variable at compile time.
# nuitka-project-if: {OS} == "Windows" and os.getenv("DEBUG_COMPILATION", "no") == "yes":
#    nuitka-project: --windows-console-mode=hide
# nuitka-project-else:
#    nuitka-project: --windows-console-mode=disable
# nuitka-project-if: os.getenv("DEBUG_COMPILATION", "no") == "yes":
#    nuitka-project: --debug
#    nuitka-project: --debugger
#    nuitka-project: --experimental=allow-c-warnings
#    nuitka-project: --no-debug-immortal-assumptions
#    nuitka-project: --run
# nuitka-project-else:
#    nuitka-project: --assume-yes-for-downloads
# nuitka-project-if: os.getenv("DEPLOYMENT", "no") == "yes":
#    nuitka-project: --deployment

# The PySide6 plugin covers the Qt + QML runtime; the QML UI itself ships as
# data files alongside the compiled package.
# nuitka-project: --standalone
# nuitka-project: --output-dir=dist
# nuitka-project: --enable-plugin=pyside6
# The 'multimedia' plugin bundles the Qt6 media backend (an ffmpeg-based plugin
# under plugins/multimedia) so QML MediaPlayer/AudioOutput have a working engine
# for the in-app track/artist preview.
# nuitka-project: --include-qt-plugins=qml,multimedia
# Qt 6.11 added a Qt.labs.assetdownloader QML module that ships ONLY as a
# static library, which Nuitka cannot process. It arrives through the qml
# plugin scan rather than the DLL list, so --noinclude-dlls does not reach
# it; the Makefile prunes it from the build virtualenv beforehand instead
# (tools/prune_static_qml_plugins.py, run from the Makefile).
# Drop the heavy Qt modules Waves never loads, it is a QtQuick app that only
# imports QtQuick(.Controls.Basic/.Layouts/.Effects/.Shapes/.Dialogs) + QtCore.
# NOTE: on a macOS standalone build Nuitka names the Qt libraries without the
# "libQt6" prefix (e.g. "QtWebEngineCore"), so the patterns must match the bare
# module name, "libQt6WebEngine*" matches nothing here. By far the biggest win
# is QtWebEngineCore: a ~210 MB bundled Chromium that nothing in Waves touches.
# nuitka-project: --noinclude-dlls=*WebEngine*
# nuitka-project: --noinclude-dlls=*QtPdf*
# nuitka-project: --noinclude-dlls=*Qt3D*
# nuitka-project: --noinclude-dlls=*Quick3D*
# nuitka-project: --noinclude-dlls=*QtCharts*
# nuitka-project: --noinclude-dlls=*QtGraphs*
# nuitka-project: --noinclude-dlls=*DataVisualization*
# nuitka-project: --noinclude-dlls=*QtLocation*
# nuitka-project: --noinclude-dlls=*QtPositioning*
# QtMultimedia is now KEPT (needed by the in-app preview player). Only its
# 3D-audio sibling stays excluded, MediaPlayer/AudioOutput don't use it.
# nuitka-project: --noinclude-dlls=*SpatialAudio*
# nuitka-project: --noinclude-dlls=*QtSensors*
# nuitka-project: --noinclude-dlls=*QtSql*
# nuitka-project: --noinclude-dlls=*QtTest*
# nuitka-project: --noinclude-dlls=*QtWebSockets*
# nuitka-project: --noinclude-dlls=*QtWebChannel*
# nuitka-project: --noinclude-dlls=*QtWebView*
# nuitka-project: --noinclude-dlls=*VirtualKeyboard*
# nuitka-project: --noinclude-dlls=*RemoteObjects*
# nuitka-project: --noinclude-dlls=*Scxml*
# nuitka-project: --noinclude-dlls=*Bluetooth*
# nuitka-project: --noinclude-dlls=*QtNfc*
# nuitka-project: --noinclude-dlls=*SerialPort*
# nuitka-project: --noinclude-dlls=*SerialBus*
# nuitka-project: --noinclude-dlls=*TextToSpeech*
# Unused QtQuick Controls styles, the UI pins QtQuick.Controls.Basic, so only
# the Basic style is needed. Patterns cover both the implementation libraries
# (e.g. "QtQuickControls2Material") and the lower-cased qml style plugins.
# nuitka-project: --noinclude-dlls=*Controls2Material*
# nuitka-project: --noinclude-dlls=*Controls2Fusion*
# nuitka-project: --noinclude-dlls=*Controls2Imagine*
# nuitka-project: --noinclude-dlls=*Controls2Universal*
# nuitka-project: --noinclude-dlls=*controls2material*
# nuitka-project: --noinclude-dlls=*controls2fusion*
# nuitka-project: --noinclude-dlls=*controls2imagine*
# nuitka-project: --noinclude-dlls=*controls2universal*
# nuitka-project: --noinclude-dlls=*fluentwinui3*
# nuitka-project: --noinclude-dlls=*controls2ios*
# nuitka-project: --noinclude-dlls=*controls2macos*
# nuitka-project: --include-package=waves.waves_ui
# nuitka-project: --include-package=tidalapi
# requests imports charset_normalizer lazily, so import-following grabs only its
# compiled extensions and drops the pure-Python submodules, include the whole
# package so requests can detect response encodings instead of warning + falling
# back to a heuristic.
# nuitka-project: --include-package=charset_normalizer
# The data-dir DESTINATIONS deliberately do not mirror the package path: a
# waves/ directory beside the binary would collide with the Waves executable
# on the case-insensitive filesystems macOS and Windows ship with (the build
# died on exactly that). app.py's _data_dir() knows both this packaged layout
# and the in-package source layout.
# nuitka-project: --include-data-dir={MAIN_DIRECTORY}/waves/waves_ui/qml=waves_ui/qml
# nuitka-project: --include-data-dir={MAIN_DIRECTORY}/waves/waves_ui/fonts=waves_ui/fonts
# Only the PNG size ladder is read at runtime (app.py builds the window icon
# from it); the executable's own icon comes from --macos-app-icon /
# --windows-icon-from-ico at build time, so a bare icon* glob shipped a second
# unreachable icon.icns (and an icon.ico) inside every bundle. Windows alone
# also carries icon.ico: app.py falls back to it if the PNG ladder is ever
# missing, a net kept from the truncated-ico taskbar incident.
# nuitka-project: --include-data-files={MAIN_DIRECTORY}/waves/ui/icon*.png=ui/
# nuitka-project-if: {OS} in ("Windows"):
#    nuitka-project: --include-data-files={MAIN_DIRECTORY}/waves/ui/icon.ico=ui/icon.ico
# nuitka-project: --include-data-files=./pyproject.toml=pyproject.toml
# AGPL-3.0 requires the licence text to travel with the binary; ship it inside
# the bundle next to the Phosphor (qml/) and JetBrains Mono (fonts/) notices.
# nuitka-project: --include-data-files=./LICENSE=LICENSE
# nuitka-project: --company-name=Waves
# nuitka-project: --product-name=Waves
# nuitka-project: --copyright=(C) 2026 iamprivacy, licensed under AGPL-3.0


def main() -> int:
    """Launch the Waves QML UI and return its exit code."""
    try:
        from waves.waves_ui.app import waves_activate
    except ImportError as e:
        print(e)
        print("Qt dependencies missing. Cannot start Waves. Please read the 'README.md' carefully.")
        return 1

    return waves_activate()


if __name__ == "__main__":
    raise SystemExit(main())
