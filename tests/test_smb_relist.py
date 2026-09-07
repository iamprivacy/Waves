"""Recovering the folders an SMB mount refuses to list
(waves/waves_ui/smb_relist.py, wired in bridge_library._library_recover_untrusted).

THE BUG THIS FENCES OFF
-----------------------
The macOS SMB client fills a directory cache with ten parallel queries. On the
affected pairing all ten come back with the FIRST page and all ten are kept, so
a folder holding 1345 subfolders lists as 10000 entries of 1000 distinct names,
and the OS reports success. Every application on the machine is affected,
Finder included. Waves believed it, so 304 artists were never indexed: every
badge read "not in library" for music the user owned, and duplicates were
downloaded on that answer.

Mounting the same share a second time lists all of it, in about a second. The
recovery here does that, reads the names, unmounts, and hands the names to the
by-name indexer the cache already had.

Pinned here, in the order a reviewer should care about them:

- The whole sequence end to end: a scan whose listing hid three artists, a
  fresh mount that names all six, and an index that afterwards holds all six.
- The mount is read only, invisible, soft, and NEVER prompts for a password
  (a background scan that pops a password sheet would be worse than the bug).
- A fresh listing is refused unless it is trustworthy: it must repeat no name
  (a second broken mount is not believed just because it is new) and must keep
  every folder the index already holds (a listing that LOST folders is not a
  better listing).
- The mount is always unmounted, including when its listing is refused, and a
  mount point that still has anything in it is left alone rather than walked.
- Nothing happens at all off macOS, off SMB, or on a healthy library.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from test_library_listing_truncation import _CM, _fake_listing, _mk

from waves.library_index import LibraryIndex
from waves.waves_ui import smb_relist

ARTISTS = ("Aphex", "Boards", "Clark", "Dopplereffekt", "Eno", "Fennesz")
HIDDEN = ("Dopplereffekt", "Eno", "Fennesz")


@pytest.fixture(autouse=True)
def _pretend_macos(monkeypatch):
    """Every test drives the macOS-only path, on whatever the runner is."""
    monkeypatch.setattr(smb_relist, "_on_macos", lambda: True)


# ---- the pure parts ------------------------------------------------------------


def test_the_share_url_keeps_the_user_and_never_carries_a_password():
    # The user picks the right saved credential when a server holds several.
    assert smb_relist.share_url("//pat@nas._smb._tcp.local/Media") == "//pat@nas._smb._tcp.local/Media"
    assert smb_relist.share_url("//nas/Media") == "//nas/Media"
    assert smb_relist.share_url("//WORK;pat@nas/Media") == "//WORK;pat@nas/Media"
    # A password would otherwise land in every process table on the machine.
    assert smb_relist.share_url("//pat:hunter2@nas/Media") == "//pat@nas/Media"
    # A from-name for a submount names the share, not the subfolder.
    assert smb_relist.share_url("//pat@nas/Media/Music") == "//pat@nas/Media"
    # An empty user is dropped rather than passed on as a bare "@".
    assert smb_relist.share_url("//@nas/Media") == "//nas/Media"


def test_the_share_url_refuses_what_is_not_a_share():
    for bad in ("", "/dev/disk3s1", "//nas", "//nas/", "//", "///Media", "//pat@/Media"):
        assert smb_relist.share_url(bad) == "", bad


def test_a_folder_is_placed_relative_to_its_mount_point():
    assert smb_relist.subpath_within("/Volumes/Media", "/Volumes/Media") == ""
    assert smb_relist.subpath_within("/Volumes/Media/", "/Volumes/Media") == ""
    assert smb_relist.subpath_within("/Volumes/Media/Music/Lib", "/Volumes/Media") == "Music/Lib"
    # Not under it at all, and the near miss that a plain startswith would take.
    assert smb_relist.subpath_within("/Volumes/Other", "/Volumes/Media") is None
    assert smb_relist.subpath_within("/Volumes/MediaBackup", "/Volumes/Media") is None
    assert smb_relist.subpath_within("", "/Volumes/Media") is None


def test_a_listing_that_repeats_a_name_is_refused():
    assert smb_relist.trusted_names(["A", "B", "C"]) == ["A", "B", "C"]
    # The shape the broken mount hands over: one page, over and over.
    assert smb_relist.trusted_names(["A", "B", "A", "B"]) is None
    # Case and unicode composition are the same folder to the server.
    assert smb_relist.trusted_names(["Bjork", "bjork"]) is None
    assert smb_relist.trusted_names(["Björk", "Björk"]) is None
    # Dot entries never count, so they cannot fake a repeat either.
    assert smb_relist.trusted_names([".DS_Store", "A", ".hidden"]) == ["A"]


def test_the_mount_never_prompts_never_writes_and_never_shows(monkeypatch):
    seen = []
    monkeypatch.setattr(smb_relist, "_run", lambda argv, timeout: seen.append((argv, timeout)) or True)
    assert smb_relist.mount_share("//pat@nas/Media", "/tmp/point") is True
    argv, timeout = seen[0]
    assert argv[0] == "/sbin/mount_smbfs"
    # -N is load-bearing: without it a background scan can raise a password
    # sheet nobody is there to answer, or block until the timeout.
    assert "-N" in argv
    opts = argv[argv.index("-o") + 1].split(",")
    assert "ro" in opts  # nothing can be written or deleted through this view
    assert "nobrowse" in opts  # never on the desktop or in a file dialog
    assert "soft" in opts  # a sleeping NAS fails, it does not hang
    assert argv[-2:] == ["//pat@nas/Media", "/tmp/point"]
    assert timeout > 0


def test_an_unmount_that_is_refused_is_forced(monkeypatch):
    calls = []

    def run(argv, timeout):
        calls.append(argv)
        return "-f" in argv

    monkeypatch.setattr(smb_relist, "_run", run)
    assert smb_relist.unmount_share("/tmp/point") is True
    assert [c[1] for c in calls] == ["/tmp/point", "-f"]


def test_a_mount_point_with_anything_in_it_is_left_alone(tmp_path):
    # Waves never deletes user files. If an unmount somehow failed, the mount
    # point still holds the share, and rmdir refusing is the whole safety net.
    point = tmp_path / "point"
    point.mkdir()
    (point / "an-artist-folder").mkdir()
    smb_relist._drop_point(str(point))
    assert point.exists() and (point / "an-artist-folder").exists()
    (point / "an-artist-folder").rmdir()
    smb_relist._drop_point(str(point))
    assert not point.exists()


def test_a_mount_point_a_crash_left_behind_is_swept(tmp_path):
    base = smb_relist.mounts_dir(str(tmp_path))
    stale = os.path.join(base, "pid-99999")
    os.makedirs(stale)
    unmounted = []
    assert smb_relist.sweep_stale(str(tmp_path), unmount=lambda p: unmounted.append(p) or True) == 1
    assert unmounted == [stale]
    assert not os.path.exists(stale)
    # Nothing left behind means nothing to do, and no crash for the common case.
    assert smb_relist.sweep_stale(str(tmp_path), unmount=lambda p: True) == 0


# ---- relist_folders ------------------------------------------------------------


def _mounter(names, *, ok=True):
    """A stand-in for mount_smbfs: the private mount point comes up holding
    ``names`` as folders, exactly as the real share's folders would appear, and
    goes back to empty on unmount so the point can be removed."""
    state = {"mounted": [], "unmounted": []}

    def mount(url, point):
        if not ok:
            return False
        state["mounted"].append((url, point))
        for name in names:
            os.makedirs(os.path.join(point, name), exist_ok=True)
        return True

    def unmount(point):
        state["unmounted"].append(point)
        for name in list(names):
            d = os.path.join(point, name)
            if os.path.isdir(d):
                os.rmdir(d)
        return True

    return mount, unmount, state


def _on_smb(monkeypatch, mount_point, url="//pat@nas/Media"):
    monkeypatch.setattr(smb_relist.netmount, "mount_info", lambda path: ("smbfs", url, mount_point))


def test_a_fresh_mount_supplies_the_names_the_share_hid(tmp_path, monkeypatch):
    target = str(tmp_path / "Volumes" / "Media")
    os.makedirs(target)
    _on_smb(monkeypatch, target)
    mount, unmount, state = _mounter(ARTISTS)
    got = smb_relist.relist_folders(
        [target],
        config_dir=str(tmp_path / "cfg"),
        known={target: ARTISTS[:3]},
        mount=mount,
        unmount=unmount,
    )
    assert got == {target: sorted(ARTISTS)}
    assert state["mounted"][0][0] == "//pat@nas/Media"
    # Nothing is left mounted, and the mount point is gone with it.
    assert state["unmounted"] == [state["mounted"][0][1]]
    assert not os.path.exists(state["mounted"][0][1])


def test_a_library_below_the_share_root_is_read_at_its_own_subpath(tmp_path, monkeypatch):
    mount_point = str(tmp_path / "Volumes" / "Media")
    target = os.path.join(mount_point, "Music", "Library")
    os.makedirs(target)
    _on_smb(monkeypatch, mount_point)

    def mount(url, point):
        deep = os.path.join(point, "Music", "Library")
        for name in ARTISTS:
            os.makedirs(os.path.join(deep, name), exist_ok=True)
        return True

    def unmount(point):
        for root, dirs, _files in os.walk(point, topdown=False):
            for d in dirs:
                os.rmdir(os.path.join(root, d))
        return True

    got = smb_relist.relist_folders([target], config_dir=str(tmp_path / "cfg"), mount=mount, unmount=unmount)
    assert got == {target: sorted(ARTISTS)}


def test_a_listing_that_lost_a_folder_the_index_holds_is_refused(tmp_path, monkeypatch):
    target = str(tmp_path / "Volumes" / "Media")
    os.makedirs(target)
    _on_smb(monkeypatch, target)
    mount, unmount, state = _mounter(ARTISTS)
    got = smb_relist.relist_folders(
        [target],
        config_dir=str(tmp_path / "cfg"),
        # The index holds an artist this listing never names: whatever else it
        # gained, it is not a better listing, and acting on it would retire a
        # folder the app has already read.
        known={target: [*ARTISTS, "Gescom"]},
        mount=mount,
        unmount=unmount,
    )
    assert got == {}
    # Refused, but still unmounted and cleaned up.
    assert state["unmounted"] == [state["mounted"][0][1]]
    assert not os.path.exists(state["mounted"][0][1])


def test_a_second_broken_mount_is_refused_too(tmp_path, monkeypatch):
    target = str(tmp_path / "Volumes" / "Media")
    os.makedirs(target)
    _on_smb(monkeypatch, target)
    real = os.scandir

    def doubling(path=".", *a, **k):
        with real(path, *a, **k) as it:
            entries = list(it)
        return _CM(entries * 2) if entries else _CM(entries)

    monkeypatch.setattr(smb_relist.os, "scandir", doubling)
    mount, unmount, state = _mounter(ARTISTS)
    got = smb_relist.relist_folders([target], config_dir=str(tmp_path / "cfg"), mount=mount, unmount=unmount)
    assert got == {}
    assert state["unmounted"]


def test_a_refused_mount_changes_nothing(tmp_path, monkeypatch):
    target = str(tmp_path / "Volumes" / "Media")
    os.makedirs(target)
    _on_smb(monkeypatch, target)
    mount, unmount, state = _mounter(ARTISTS, ok=False)
    assert smb_relist.relist_folders([target], config_dir=str(tmp_path / "cfg"), mount=mount, unmount=unmount) == {}
    assert state["mounted"] == []


def test_nothing_is_mounted_for_a_folder_that_is_not_on_a_share(tmp_path, monkeypatch):
    target = str(tmp_path / "local")
    os.makedirs(target)
    monkeypatch.setattr(smb_relist.netmount, "mount_info", lambda path: ("apfs", "/dev/disk3s1", "/"))
    mount, unmount, state = _mounter(ARTISTS)
    assert smb_relist.relist_folders([target], config_dir=str(tmp_path / "cfg"), mount=mount, unmount=unmount) == {}
    assert state["mounted"] == []


def test_nothing_happens_off_macos(tmp_path, monkeypatch):
    target = str(tmp_path / "Volumes" / "Media")
    os.makedirs(target)
    _on_smb(monkeypatch, target)
    monkeypatch.setattr(smb_relist, "_on_macos", lambda: False)
    mount, unmount, state = _mounter(ARTISTS)
    assert smb_relist.relist_folders([target], config_dir=str(tmp_path / "cfg"), mount=mount, unmount=unmount) == {}
    assert state["mounted"] == []
    assert smb_relist.sweep_stale(str(tmp_path)) == 0


def test_two_flagged_folders_on_one_share_cost_one_mount(tmp_path, monkeypatch):
    mount_point = str(tmp_path / "Volumes" / "Media")
    first = os.path.join(mount_point, "Music")
    second = os.path.join(mount_point, "Bootlegs")
    os.makedirs(first)
    os.makedirs(second)
    _on_smb(monkeypatch, mount_point)

    def mount(url, point):
        for sub in ("Music", "Bootlegs"):
            for name in ARTISTS:
                os.makedirs(os.path.join(point, sub, name), exist_ok=True)
        return True

    def unmount(point):
        for root, dirs, _files in os.walk(point, topdown=False):
            for d in dirs:
                os.rmdir(os.path.join(root, d))
        return True

    mounted = []
    got = smb_relist.relist_folders(
        [first, second],
        config_dir=str(tmp_path / "cfg"),
        mount=lambda u, p: mounted.append(u) or mount(u, p),
        unmount=unmount,
    )
    assert sorted(got) == sorted([first, second])
    assert len(mounted) == 1


# ---- end to end, against a real scan -------------------------------------------


def _tagged_library(tmp_path):
    """Six artists on disk, one album each."""
    root = str(tmp_path / "lib")
    os.makedirs(root, exist_ok=True)
    tags = {}
    for name in ARTISTS:
        d = _mk(root, f"{name}/[2020] Record", ["1.flac"])
        tags[d] = {"album": "Record", "artist": name, "date": "2020", "title": "Song"}
    return root, tags


def _indexed_artists(idx):
    return sorted({a["artist"] for a in idx.iter_albums()})


def test_a_flagged_scan_recovers_the_artists_its_listing_hid(tmp_path, monkeypatch):
    root, tags = _tagged_library(tmp_path)
    shown = set(ARTISTS[:3])
    # The broken mount's shape: only the first page, handed over twice.
    _fake_listing(monkeypatch, {root: lambda e: [x for x in e if x.name in shown] * 2})
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: tags.get(os.path.dirname(p)))

    assert idx.refresh(root) == 3
    assert idx.last_scan_partial is True
    assert idx.unreliable_dirs() == [root]
    assert _indexed_artists(idx) == sorted(shown)
    for hidden in HIDDEN:
        assert hidden not in _indexed_artists(idx)
    # What the index knows is exactly what the short listing named.
    assert sorted(idx.child_names(root)) == sorted(shown)

    _on_smb(monkeypatch, root)
    mount, unmount, _state = _mounter(ARTISTS)
    recovered = smb_relist.relist_folders(
        [root],
        config_dir=str(tmp_path / "cfg"),
        known={root: idx.child_names(root)},
        mount=mount,
        unmount=unmount,
    )
    assert recovered == {root: sorted(ARTISTS)}

    # The names came off the disk, so each is its own spelling. The probe
    # reports through the scan's own progress events: a whole share's worth
    # of recovered artists is minutes of reading, and the bar must move.
    events: list[dict] = []
    hits = idx.probe_folders(
        root, recovered[root], candidates=lambda n: (n,), on_progress=events.append, progress_interval=0.0
    )
    assert hits == len(HIDDEN)
    assert _indexed_artists(idx) == sorted(ARTISTS)
    reads = [e for e in events if e.get("phase") == "read"]
    assert reads, events
    assert reads[0]["done"] == 0 and reads[0]["total"] == len(HIDDEN)
    assert reads[-1]["done"] == reads[-1]["total"] == len(HIDDEN)
    walks = [e for e in events if e.get("phase") == "walk"]
    assert walks and walks[-1]["found"] == len(HIDDEN)
    # The walk's count only ever climbs: each hit's own tally is lifted onto
    # the folders found before it, never restarted.
    assert [w["found"] for w in walks] == sorted(w["found"] for w in walks)

    # And the recovery survives the next scan of the same still-broken share,
    # which is the whole point of writing under the flagged folder.
    assert idx.refresh(root) == len(ARTISTS)
    assert _indexed_artists(idx) == sorted(ARTISTS)


def test_a_healthy_library_never_mounts_anything(tmp_path, monkeypatch):
    root, tags = _tagged_library(tmp_path)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: tags.get(os.path.dirname(p)))
    assert idx.refresh(root) == len(ARTISTS)
    assert idx.last_scan_partial is False
    assert idx.unreliable_dirs() == []
    mount, unmount, state = _mounter(ARTISTS)
    assert (
        smb_relist.relist_folders(idx.unreliable_dirs(), config_dir=str(tmp_path), mount=mount, unmount=unmount) == {}
    )
    assert state["mounted"] == []


# ---- the bridge wiring ---------------------------------------------------------


def test_the_bridge_recovers_straight_after_a_flagged_scan(tmp_path, monkeypatch):
    """The scan seam itself: bridge_library calls this before it builds the
    index, so a recovery lands in the very first publish."""
    from waves.waves_ui.backend import WavesBridge

    root, tags = _tagged_library(tmp_path)
    shown = set(ARTISTS[:3])
    _fake_listing(monkeypatch, {root: lambda e: [x for x in e if x.name in shown] * 2})
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: tags.get(os.path.dirname(p)))
    assert idx.refresh(root) == 3

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    stub = SimpleNamespace(settings=SimpleNamespace(file_path=str(cfg / "settings.json")))
    _on_smb(monkeypatch, root)
    mount, unmount, state = _mounter(ARTISTS)
    monkeypatch.setattr(smb_relist, "mount_share", mount)
    monkeypatch.setattr(smb_relist, "unmount_share", unmount)

    events: list[dict] = []
    hits = WavesBridge._library_recover_untrusted(stub, idx, root, lambda: True, events.append)
    assert hits == len(HIDDEN)
    assert _indexed_artists(idx) == sorted(ARTISTS)
    assert state["mounted"] and state["unmounted"]
    # The scan worker's progress sink hears the recovery's reads, so the
    # Settings bar does not sit on the scan's last count for minutes.
    assert [e["done"] for e in events if e.get("phase") == "read"][-1] == len(HIDDEN)
    # The private mount point lived under the app's own config directory.
    assert state["mounted"][0][1].startswith(str(cfg))


def test_a_full_recovery_is_recorded_so_settings_stops_warning(tmp_path, monkeypatch):
    """The listing stays untrusted (the probe by name stays armed), but the
    library is complete, and Settings must say the one instead of the other."""
    from waves.waves_ui.backend import WavesBridge

    root, tags = _tagged_library(tmp_path)
    shown = set(ARTISTS[:3])
    _fake_listing(monkeypatch, {root: lambda e: [x for x in e if x.name in shown] * 2})
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: tags.get(os.path.dirname(p)))
    idx.refresh(root)
    assert idx.last_listing_reconciled is False

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    stub = SimpleNamespace(settings=SimpleNamespace(file_path=str(cfg / "settings.json")))
    _on_smb(monkeypatch, root)
    mount, unmount, _state = _mounter(ARTISTS)
    monkeypatch.setattr(smb_relist, "mount_share", mount)
    monkeypatch.setattr(smb_relist, "unmount_share", unmount)

    WavesBridge._library_recover_untrusted(stub, idx, root, lambda: True)
    assert idx.last_scan_partial is True  # the share still lists badly
    assert idx.last_listing_reconciled is True  # but nothing is missing
    # A second run indexes nothing new, because there is nothing left to find.
    # That is the steady state, not a failure, so the verdict must hold.
    WavesBridge._library_recover_untrusted(stub, idx, root, lambda: True)
    assert idx.last_listing_reconciled is True


def test_a_recovery_that_cannot_reach_everything_keeps_warning(tmp_path, monkeypatch):
    """The fresh mount names an artist the recovery then fails to index: the
    library IS short of a folder, and the note has to keep saying so."""
    from waves.waves_ui.backend import WavesBridge

    root, tags = _tagged_library(tmp_path)
    shown = set(ARTISTS[:3])
    _fake_listing(monkeypatch, {root: lambda e: [x for x in e if x.name in shown] * 2})
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: tags.get(os.path.dirname(p)))
    idx.refresh(root)

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    stub = SimpleNamespace(settings=SimpleNamespace(file_path=str(cfg / "settings.json")))
    _on_smb(monkeypatch, root)
    mount, unmount, _state = _mounter(ARTISTS)
    monkeypatch.setattr(smb_relist, "mount_share", mount)
    monkeypatch.setattr(smb_relist, "unmount_share", unmount)
    monkeypatch.setattr(idx, "probe_folders", lambda *a, **k: 0)  # every name refuses to index

    WavesBridge._library_recover_untrusted(stub, idx, root, lambda: True)
    assert idx.last_listing_reconciled is False


def test_the_bridge_leaves_a_healthy_scan_alone(tmp_path, monkeypatch):
    from waves.waves_ui.backend import WavesBridge

    root, tags = _tagged_library(tmp_path)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: tags.get(os.path.dirname(p)))
    idx.refresh(root)
    stub = SimpleNamespace(settings=SimpleNamespace(file_path=str(tmp_path / "settings.json")))
    mount, unmount, state = _mounter(ARTISTS)
    monkeypatch.setattr(smb_relist, "mount_share", mount)
    monkeypatch.setattr(smb_relist, "unmount_share", unmount)
    # The scan-was-clean flag is checked FIRST and on its own: a healthy
    # library pays one boolean for this feature, not a database query.
    asked = []
    real_dirs = idx.unreliable_dirs
    monkeypatch.setattr(idx, "unreliable_dirs", lambda: asked.append(1) or real_dirs())
    assert WavesBridge._library_recover_untrusted(stub, idx, root, lambda: True) == 0
    assert asked == []
    assert state["mounted"] == []


def test_the_bridge_swallows_a_recovery_that_goes_wrong(tmp_path, monkeypatch):
    """Every failure here must land as "carry on exactly as before"."""
    from waves.waves_ui.backend import WavesBridge

    root, tags = _tagged_library(tmp_path)
    shown = set(ARTISTS[:3])
    _fake_listing(monkeypatch, {root: lambda e: [x for x in e if x.name in shown] * 2})
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: tags.get(os.path.dirname(p)))
    idx.refresh(root)
    stub = SimpleNamespace(settings=SimpleNamespace(file_path=str(tmp_path / "cfg" / "settings.json")))

    def boom(*a, **k):
        raise OSError("the share went away mid-recovery")

    monkeypatch.setattr(smb_relist, "relist_folders", boom)
    assert WavesBridge._library_recover_untrusted(stub, idx, root, lambda: True) == 0
    assert _indexed_artists(idx) == sorted(shown)


def test_one_platform_gate_and_only_one():
    """_on_macos is the module's ONLY platform test, so a later edit cannot
    half-enable a macOS workaround on Linux or Windows."""
    with open(smb_relist.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert src.count("sys.platform") == 1
    assert 'return sys.platform == "darwin"' in src
