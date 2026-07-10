"""Waves UI, a new QML front-end for the tidaler backend.

This package is intentionally self-contained: it imports the existing
``tidaler`` backend (config, download, helpers) but never modifies it, so
upstream updates can be merged without touching the UI.
"""

# Waves' own version, independent of the inherited tidaler package version. The
# in-app updater (:mod:`tidaler.waves_ui.updater`) compares this against the
# latest GitHub release tag. Bump it (and tag a matching ``vX.Y.Z`` release) on
# every shipped build.
__version__ = "0.1.2"
