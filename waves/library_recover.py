"""Recover the folders an untrusted share listing left out.

A plain function, shared by the scanner process (waves.library_worker) and
the in-process fallback in the bridge: it needs the scan cache, the share
relist helper and nothing from Qt.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

from waves.waves_ui import smb_relist

logger = logging.getLogger("waves.library")


def recover_untrusted(
    lib,
    root: str,
    config_dir: str,
    alive: Callable[[], bool],
    on_progress=None,
    *,
    lock_wait: float = 20.0,
) -> int:
    """(scanner, straight after a scan) Ask a freshly mounted copy of the
    share for the folder names its own mount would not list, and index
    whatever comes back. Returns how many folders were recovered.

    Only ever reached when the scan just flagged a listing as untrusted, so a
    healthy library pays one boolean for this. Everything after that gate is
    best effort: no better listing, no credential, a server asleep, a fresh
    listing that repeated itself too, all return 0 and leave the app behaving
    exactly as it does today. The probe by name behind every badge miss stays
    in place either way, and still covers whatever this misses.

    The names go to probe_folders as their own spellings, because they came
    off the disk: no naming-settings guesswork is needed for a name the
    filesystem just handed over. That call stats each one under every flagged
    folder, skipping the ones the cache already holds, walks the subtree of
    each hit and writes it under the flagged folder as parent, which is
    exactly what the scan would have done had the listing named them.
    ``on_progress`` is the scan's own progress sink: a share's worth of
    recovered artists is minutes of reading, and the bar must keep moving
    through it."""
    if not bool(getattr(lib, "last_scan_partial", False)):
        return 0
    if not alive():
        return 0
    try:
        targets = list(lib.unreliable_dirs())
        if not targets:
            return 0
        # A mount point a crashed run left behind is cleaned up here rather
        # than at startup: this is the first moment one could get in the way,
        # and boot has better things to do than stat a directory for a case
        # almost nobody is in.
        smb_relist.sweep_stale(config_dir)
        recovered = smb_relist.relist_folders(
            targets,
            config_dir=config_dir,
            known={t: lib.child_names(t) for t in targets},
        )
        if not recovered or not alive():
            return 0
        wanted = sorted({name for names in recovered.values() for name in names})
        found = lib.probe_folders(
            root,
            wanted,
            alive,
            candidates=lambda name: (name,),
            timeout=lock_wait,
            on_progress=on_progress,
        )
    except Exception:
        logger.debug("Recovering an untrusted listing failed; leaving the scan as it was", exc_info=True)
        return 0
    hits = int(found or 0)
    # Whether anything the fresh mount named is STILL missing from the cache.
    # The listing stays untrusted either way (the probe by name stays armed
    # behind every badge miss), but a folder that is fully recovered is not an
    # incomplete library, and Settings must not warn about badges that are all
    # there. Checked against the cache, not against the hit count: most of
    # these names were already indexed by an earlier recovery, so a run that
    # indexes nothing new is the normal steady state, not a failure.
    try:
        complete = all(lib.listing_holds_all(target, names) for target, names in recovered.items())
    except Exception:
        logger.debug("Checking a recovered listing against the cache failed", exc_info=True)
        complete = False
    with contextlib.suppress(Exception):  # a stub index without the setter
        lib.note_listing_reconciled(complete)
    logger.info(
        "untrusted listing recovery: %d names read from a fresh mount, %d folders indexed, nothing left out: %s",
        len(wanted),
        hits,
        complete,
    )
    return hits
