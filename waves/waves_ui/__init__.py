"""Waves UI, the QML front-end for the download engine.

This package is intentionally self-contained: it imports the engine modules
(config, download, helpers) through a narrow seam and keeps UI concerns out
of them. The seam discipline is inherited from the project's fork era, when
the engine had to merge cleanly against upstream releases; the merges are
retired, the layering stays because it keeps the engine auditable.
"""

# The app's user-facing version, the one the in-app updater
# (:mod:`waves.waves_ui.updater`) compares against the latest GitHub release
# tag. tests/test_package_version.py pins pyproject.toml to the same value.
# Bump both (and tag a matching ``vX.Y.Z`` release) on every shipped build.
__version__ = "0.1.26"
