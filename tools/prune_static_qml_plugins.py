"""Remove QML modules from the BUILD VIRTUALENV that ship only a static library.

Qt 6.11 added ``Qt.labs.assetdownloader``, whose plugin exists only as
``libqmlassetdownloaderprivateplugin.a`` (``.lib`` on Windows) with no shared
object beside it. Nuitka treats every plugin under ``qml/`` as loadable and
dies trying to resolve the archive, differently on each platform:

    Linux    FATAL ... patchelf: not an ELF executable
    macOS    FATAL ... failed to find path ...(mocs_compilation.cpp.o)
    Windows  Nuitka-Inclusion:WARNING ... TypeError: 'NoneType' object is not iterable

``--noinclude-dlls`` does not reach it (the file arrives through the pyside6
plugin's qml scan, not the DLL list) and ``--noinclude-qt-plugins`` only takes
family names such as "qml", which the app needs. So the module is pruned here,
before Nuitka walks the tree.

The rule is generic rather than a name match, so the next static-only module Qt
adds is handled too: a directory qualifies only when it holds a static plugin
library AND no shared one, which means Qt shipped nothing loadable there. A
module Waves actually imports would carry a shared library and be kept.

Only the build virtualenv's PySide6 tree is touched. Run it with the same
interpreter that will run Nuitka.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

_STATIC_SUFFIXES = (".a", ".lib")
_SHARED_SUFFIXES = (".so", ".dylib", ".dll")


def qml_root() -> pathlib.Path | None:
    """The PySide6 QML module tree, or None when PySide6 is not installed."""
    try:
        import PySide6
    except Exception:
        return None

    base = pathlib.Path(PySide6.__file__).parent
    # macOS wheels keep the framework layout (PySide6/Qt/qml); Linux and
    # Windows wheels put the modules directly under PySide6/qml.
    for candidate in (base / "Qt" / "qml", base / "qml"):
        if candidate.is_dir():
            return candidate
    return None


def static_only_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """Plugin directories under root whose only plugin library is static."""
    found: list[pathlib.Path] = []
    for path in root.rglob("*plugin.*"):
        if path.suffix not in _STATIC_SUFFIXES or not path.is_file():
            continue
        directory = path.parent
        if directory in found:
            continue
        has_shared = any(sibling.suffix in _SHARED_SUFFIXES for sibling in directory.iterdir() if sibling.is_file())
        if not has_shared:
            found.append(directory)
    return found


def main() -> int:
    root = qml_root()
    if root is None:
        print("No PySide6 qml tree found; nothing to prune.")
        return 0

    pruned = 0
    for directory in static_only_dirs(root):
        print(f"Pruning static-only QML module: {directory.relative_to(root)}")
        shutil.rmtree(directory)
        pruned += 1

    print(f"Pruned {pruned} static-only QML module(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
