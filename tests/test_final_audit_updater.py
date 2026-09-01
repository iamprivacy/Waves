"""Final-audit regressions for the self-updater and the bundle size report.

Five defects, all of them about what the updater does to files it did not
create: a backup kept BECAUSE it held the user's own files, and then reclaimed
by the next update; a staged single-file swap no launch could resume; a Windows
pid handed out twice; a build's own dropped files counted as the user's; and an
install that reported a version it had not staged. Plus one build-script
pipeline that failed the whole build once the bundle grew.

The real platform swap targets the live executable, so the same rules as
``test_updater.py`` apply: the apply paths are driven directly against a
tmp_path install, and the Windows helpers are asserted as script text.
"""

from __future__ import annotations

import errno
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from waves.waves_ui import updater as u
from waves.waves_ui.updater import AppUpdater, Release


def _helper_text(up):
    """The one swap helper the updater just armed (named per pid, so look it up)."""
    scripts = sorted(up.staging_dir.glob("apply_update_*.bat"))
    assert len(scripts) == 1, scripts
    return scripts[0].read_text()


def _install_with_user_files(tmp_path):
    """An install folder holding the build AND things the user put there."""
    install_root = tmp_path / "Waves"
    (install_root / "music" / "Some Artist" / "Some Album").mkdir(parents=True)
    (install_root / "music" / "Some Artist" / "Some Album" / "01 - Track.flac").write_text("MUSIC")
    (install_root / "my-notes.txt").write_text("NOTES")
    (install_root / "Waves").write_text("OLD")
    (install_root / "lib.so").write_text("oldlib")
    return install_root, install_root / "Waves"


def _staged_tree(tmp_path, where="staging", exe="NEW"):
    staged = tmp_path / where / "Waves.dist"
    staged.mkdir(parents=True)
    (staged / "Waves").write_text(exe)
    (staged / "lib.so").write_text("newlib")
    return staged


def _refuse_to_move(allow_src):
    """shutil.move that works for the staging step and fails for the rescue."""
    real = u.shutil.move

    def fake(src, dst, *a, **k):
        if str(src) == str(allow_src):
            return real(src, dst, *a, **k)
        raise OSError(errno.EACCES, "denied")

    return fake


def _macos_bundle(tmp_path):
    bundle = tmp_path / "Applications" / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    target = bundle / "Contents" / "MacOS" / "Waves"
    target.write_text("OLD")
    return bundle, target


def _macos_staged(tmp_path, where, exe="NEW"):
    staged = tmp_path / where / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text(exe)
    return staged


# ---- a backup kept for the user is never reclaimed by the next update -------
def test_the_next_macos_update_does_not_delete_the_backup_this_one_kept(tmp_path, monkeypatch):
    """The keep is a promise, and the next update used to break it.

    An update that finds files in the old bundle keeps the WHOLE bundle at
    Waves.app.old and tells the user so, by name. The next update then asks
    _spare_sibling for a backup slot, which hands out any sibling holding the
    executable, and a kept backup always holds it: the old build never left.
    Its very next statement is a recursive delete, so the files the app said it
    had kept were gone the moment another release shipped, silently and with no
    record of what was lost.
    """
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)  # no real xattr
    bundle, target = _macos_bundle(tmp_path)
    (bundle / "Contents" / "my-notes.txt").write_text("NOTES")

    up._apply_macos(_macos_staged(tmp_path, "staging1"), target, lambda *a, **k: None)

    kept = tmp_path / "Applications" / "Waves.app.old"
    assert up.kept_backup == "Waves.app.old"
    assert (kept / "Contents" / "my-notes.txt").read_text() == "NOTES"

    up._apply_macos(_macos_staged(tmp_path, "staging2", exe="NEWER"), target, lambda *a, **k: None)

    assert target.read_text() == "NEWER"  # the second update still applied
    assert (kept / "Contents" / "my-notes.txt").read_text() == "NOTES"  # and kept the promise
    assert not (tmp_path / "Applications" / "Waves.app.old-1").exists()  # its own backup went and came back


def test_the_next_tree_update_does_not_delete_the_backup_this_one_kept(tmp_path, monkeypatch):
    """The Linux half of the same promise: a backup kept because a file could
    not be moved back out of it (a stale folder is recoverable, a deleted music
    library is not) must survive the update after it, not be picked as its
    backup slot and wiped."""
    install_root, target = _install_with_user_files(tmp_path)
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    said = []

    staged = _staged_tree(tmp_path, "staging1")
    with monkeypatch.context() as mp:
        mp.setattr(u.shutil, "move", _refuse_to_move(staged))
        up._apply_unix_tree(staged, target, said.append)

    kept = tmp_path / "Waves.old"
    track = kept / "music" / "Some Artist" / "Some Album" / "01 - Track.flac"
    assert track.read_text() == "MUSIC"
    assert any("still in Waves.old" in m for m in said)

    up._apply_unix_tree(_staged_tree(tmp_path, "staging2", exe="NEWER"), target, said.append)

    assert (install_root / "Waves").read_text() == "NEWER"  # the second update still applied
    assert track.read_text() == "MUSIC"  # and did not take the kept folder for its backup
    assert not (tmp_path / "Waves.old-1").exists()


def test_every_keep_the_app_performs_itself_names_the_folder(tmp_path, monkeypatch):
    """macOS recorded the kept folder, the tree path only logged it, and that
    log line is a passing status the "Updated to vX. Restart to finish."
    message overwrites a moment later. A Linux user could accumulate a kept
    copy of the whole install per update with nothing on screen ever telling
    them where their files went, or that they were still there at all."""
    install_root, target = _install_with_user_files(tmp_path)
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    staged = _staged_tree(tmp_path)
    monkeypatch.setattr(u.shutil, "move", _refuse_to_move(staged))

    up._apply_unix_tree(staged, target, lambda *a, **k: None)

    assert up.kept_backup == "Waves.old"  # the same record the macOS keep makes
    assert up.kept_unprotected is False
    assert up.status()["kept_backup"] == "Waves.old"  # and a surface the card can read
    assert "/" not in up.kept_backup and str(tmp_path) not in up.kept_backup


def test_a_marked_backup_is_never_claimed_however_much_of_ours_it_holds(tmp_path):
    """The rule itself. A kept backup is indistinguishable from a disposable
    leftover by content (both hold the executable), so the keep leaves a mark
    inside the folder, where it cannot outlive what it protects."""
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    (install_root / "Waves").write_text("LIVE")
    leftover = tmp_path / "Waves.old"
    leftover.mkdir()
    (leftover / "Waves").write_text("a backup we left behind last time")

    assert u._spare_sibling(install_root, ".old", "Waves") == leftover  # reused, as before

    where, protected = u._mark_kept(leftover)

    assert (where, protected) == (leftover, True)
    assert (leftover / u._KEPT_MARKER).is_file()
    assert u._spare_sibling(install_root, ".old", "Waves") == tmp_path / "Waves.old-1"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores the read-only folder")
def test_a_kept_backup_that_cannot_be_marked_is_renamed_out_of_reach(tmp_path):
    """The mark is the only thing making a kept backup unclaimable, and it is a
    write into a folder on the volume the swap has just filled with two copies
    of the app: ENOSPC lands exactly there. Swallowing that failure left the
    pre-fix state (the next update claims the folder and deletes it) behind a
    message promising the user their files were kept. A same-directory rename
    needs no space and puts the folder outside every name _spare_sibling can
    generate, so the promise holds without the marker file at all.

    A read-only install folder stands in for the full disk here: it is the same
    OSError out of the same call, and it also makes the reclaim itself fail,
    which is what produces a kept backup in the first place.
    """
    install_root, target = _install_with_user_files(tmp_path)
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    said = []
    install_root.chmod(0o500)  # no room for even a 150-byte marker
    kept = tmp_path / ("Waves.old" + u._KEPT_SUFFIX)
    try:
        up._apply_unix_tree(_staged_tree(tmp_path), target, said.append)

        assert (install_root / "Waves").read_text() == "NEW"  # the update still applied
        assert kept.is_dir() and not (tmp_path / "Waves.old").exists()
        assert (kept / "my-notes.txt").read_text() == "NOTES"  # nothing was destroyed
        assert up.kept_backup == kept.name  # and the user is told where it IS
        assert up.kept_unprotected is False, "a rename is protection, not a consolation"
        assert any(kept.name in m for m in said)
        # the whole point: no spelling of the backup slot can ever reach it
        assert all(u._spare_sibling(install_root, s, "Waves") != kept for s in (".old", ".new", ".old-4128"))
    finally:
        for path in (install_root, tmp_path / "Waves.old", kept):
            if path.exists():
                path.chmod(0o700)


def test_a_kept_backup_that_cannot_be_protected_at_all_says_so(tmp_path, monkeypatch):
    """And if even the rename fails, the one thing that must not happen is
    silence: an unprotected kept folder is one the next update deletes, so the
    result carries it and the log says to move the files out by hand."""
    backup = tmp_path / "Waves.old"
    backup.mkdir()
    (backup / "my-mixtape.flac").write_text("MINE")

    def no_writes(*a, **k):
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(u.Path, "write_text", no_writes)
    monkeypatch.setattr(u.os, "replace", no_writes)

    where, protected = u._mark_kept(backup)

    assert (where, protected) == (backup, False), "an unprotected keep must never report success"


def test_a_recycled_windows_pid_cannot_take_over_a_kept_backup(tmp_path, monkeypatch):
    """Windows recycles pids, so ".old-<pid>" is not the identity it reads as.

    A helper whose reclaim failed keeps Waves.old-4128 with the user's files in
    it. Weeks later another session happens to be pid 4128, arms its own
    helper, and that helper's first post-wait act is an unconditional
    `rmdir /S /Q` of the backup it was given. The mark is what pushes the new
    backup onto the next spelling instead.
    """
    spawns: list[dict] = []
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawns.append(kw))
    monkeypatch.setattr(u.os, "getpid", lambda: 4128)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    kept = tmp_path / "Waves.old-4128"
    kept.mkdir()
    (kept / "Waves.exe").write_text("OLD")  # the old build never left the backup
    (kept / "my-mixtape.flac").write_text("MINE")
    u._mark_kept(kept)

    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    new_tree = tmp_path / "staging" / "Waves"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves.exe").write_text("NEW")
    up._apply_windows_tree(new_tree, target, lambda *a, **k: None)

    backup = spawns[-1]["env"]["WAVES_UPDATE_2"]
    assert backup != str(kept), "the new helper was handed a backup that is the user's files"
    assert backup.endswith(".old-4128-1")
    assert (kept / "my-mixtape.flac").read_text() == "MINE"


def test_the_tree_helper_marks_what_it_keeps_and_clears_only_what_it_may(tmp_path, monkeypatch):
    """The two halves inside the helper: the folder it keeps after a failed
    reclaim is marked (Python cannot do it, the keep happens minutes later,
    after this process is gone), and it looks for that mark before its own
    rmdir, for the window where a mark is written between one helper being
    armed and another finishing."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    new_tree = tmp_path / "staging" / "Waves"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves.exe").write_bytes(b"NEW")

    up._apply_windows_tree(new_tree, install_root / "Waves.exe", lambda *a, **k: None)

    script = _helper_text(up)
    lines = script.replace("\r\n", "\n").split("\n")
    assert 'set "KEPT=%BACKUP%' in script and u._KEPT_MARKER in script
    guard = next(i for i, ln in enumerate(lines) if ln.startswith('if exist "%KEPT%"'))
    clear = next(i for i, ln in enumerate(lines) if ln.startswith('if exist "%BACKUP%" rmdir'))
    assert guard < clear, "the helper clears the backup before it looks at whose it is"
    assert "goto relaunch)" in lines[guard], "a marked backup must end the swap, not fall into it"
    reclaim_failed = next(i for i, ln in enumerate(lines) if "could not reclaim" in ln)
    assert '> "%KEPT%"' in lines[reclaim_failed], "the folder it keeps is left unmarked"
    script.encode("utf-8").decode("ascii")  # still pure ASCII, code page cannot touch it


# ---- a staged single-file swap can be resumed -------------------------------
def _onefile_staged_but_unapplied(tmp_path, monkeypatch, *, staged_version="v2.0.0", running="1.0.0"):
    """A single-exe install whose swap was staged and armed but never ran: the
    marker is on disk and Waves.exe.new is sitting beside the executable."""
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    target.write_text("OLD")
    new_exe = install_root / "Waves.exe.new"
    new_exe.write_text("NEW")
    monkeypatch.setattr(u, "_current_exe", lambda: target)
    up = AppUpdater(tmp_path / "config", running, repo="owner/Waves")
    up.os_key = "windows"
    up.staging_dir.mkdir(parents=True)
    # Written exactly as the install that staged it wrote it: a fresh install
    # then, so the flag on disk says this was not a previously staged swap.
    up._write_armed_marker(
        {
            "ok": True,
            "version": staged_version,
            "applied_to": str(target),
            "relaunch": True,
            "already_staged": False,
        }
    )
    return up, target, new_exe


def test_a_staged_single_file_swap_is_re_armed_at_the_next_launch(tmp_path, monkeypatch):
    """install() arms a helper and writes the marker for EVERY Windows layout,
    but only the standalone tree could be resumed: the single-exe layout stages
    Waves.exe.new beside the executable, and the resume looked exclusively for
    a Waves.new directory. It found none, concluded the staged build was gone,
    deleted the marker, and the update the UI had already reported as installed
    vanished for good."""
    up, target, new_exe = _onefile_staged_but_unapplied(tmp_path, monkeypatch)
    spawned = {}
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.update(cmd=cmd, kw=kw))

    pending = up.resume_pending_apply()

    try:
        assert pending is not None and pending["version"] == "v2.0.0"
        assert up._read_armed_marker() is not None  # not thrown away
        assert new_exe.is_file()  # nor is the staged build
        assert _helper_text(up)  # a fresh helper, armed against THIS process
        env = spawned["kw"]["env"]
        assert [env["WAVES_UPDATE_1"], env["WAVES_UPDATE_3"]] == [str(target), str(new_exe)]
        assert up.status()["pending_restart"] is True
        assert up.status()["pending_version"] == "v2.0.0"
    finally:
        up._armed_lock.release()


def test_a_single_file_swap_that_landed_clears_its_marker_and_leftover(tmp_path, monkeypatch):
    """The other side of the same branch: running the staged version means the
    swap happened, so the marker and the spent Waves.exe.new go, and no helper
    is armed for an update already made."""
    up, _, new_exe = _onefile_staged_but_unapplied(tmp_path, monkeypatch, staged_version="v2.0.0", running="2.0.0")
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: pytest.fail("armed a helper"))

    assert up.resume_pending_apply() is None
    assert up._read_armed_marker() is None
    assert not new_exe.exists()


def test_a_backup_kept_by_the_windows_helper_is_noticed_at_the_next_launch(tmp_path, monkeypatch):
    """The helper's own keep happens minutes after this process is gone (its
    reclaim robocopy failed), and its only voice is update.log: nobody is
    running to put it on screen. The launch that finds the swap landed is the
    last place that can still say so, so it looks for the mark the helper left
    and reports the folder the way every other keep is reported."""
    up, _, _ = _onefile_staged_but_unapplied(tmp_path, monkeypatch, staged_version="v2.0.0", running="2.0.0")
    kept = tmp_path / "Waves.old-4128"
    kept.mkdir()
    (kept / "Waves.exe").write_text("the old build, which never left the backup")
    (kept / "my-mixtape.flac").write_text("MINE")
    u._mark_kept(kept)

    assert up.resume_pending_apply() is None  # the swap landed, nothing to arm

    assert up.kept_backup == "Waves.old-4128"
    assert up.status()["kept_backup"] == "Waves.old-4128"
    assert (kept / "my-mixtape.flac").read_text() == "MINE"


def test_a_swap_resumed_from_an_earlier_session_reports_itself_as_staged(tmp_path, monkeypatch):
    """The marker on disk was written when the swap WAS this session's fresh
    install, so it carries already_staged false. Handing that back at the next
    launch describes the one case the flag exists for as its opposite: what
    resume returns was staged by a session that is gone, and the install that
    follows returns the same thing."""
    up, _, _ = _onefile_staged_but_unapplied(tmp_path, monkeypatch)
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: None)

    pending = up.resume_pending_apply()

    try:
        assert pending["already_staged"] is True
        assert up._armed_result["already_staged"] is True
        assert up.install(session=object())["already_staged"] is True
    finally:
        up._armed_lock.release()


# ---- what the OLD build shipped is not the user's ---------------------------
def test_a_file_the_new_release_dropped_is_not_carried_back_into_the_install(tmp_path):
    """The foreign-file net was a bare set-difference of the backup against the
    fresh install, with nothing saying what the old build had shipped. Every
    file a release legitimately drops (a Qt library the trim sweep sheds, a
    dependency replaced by a shim) therefore read as a file the user had put in
    the install folder, and was moved back into the brand-new install, where it
    stayed for good: a dangling plugin, a dead library, one release's worth
    every time."""
    install_root, target = _install_with_user_files(tmp_path)
    (install_root / "dropped.so").write_text("gone in the next build")
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    up._record_shipped(["Waves", "lib.so", "dropped.so"], "v1.0.0")

    up._apply_unix_tree(_staged_tree(tmp_path), target, lambda *a, **k: None)

    assert (install_root / "Waves").read_text() == "NEW"
    assert not (install_root / "dropped.so").exists(), "the release dropped this file; it stays dropped"
    assert (install_root / "my-notes.txt").read_text() == "NOTES"  # the user's still comes back
    assert (install_root / "music" / "Some Artist" / "Some Album" / "01 - Track.flac").read_text() == "MUSIC"
    assert not (tmp_path / "Waves.old").exists()


def test_a_user_file_inside_a_folder_the_release_dropped_still_comes_back(tmp_path):
    """A folder the old build shipped and the new one does not is not judged
    whole: the user may have put something in there, and only what the old
    build shipped inside it is the release's to lose."""
    install_root, target = _install_with_user_files(tmp_path)
    (install_root / "plugins").mkdir()
    (install_root / "plugins" / "qsvg.so").write_text("a plugin the next build sheds")
    (install_root / "plugins" / "my-mixtape.flac").write_text("MINE")
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    up._record_shipped(["Waves", "lib.so", "plugins/", "plugins/qsvg.so"], "v1.0.0")

    up._apply_unix_tree(_staged_tree(tmp_path), target, lambda *a, **k: None)

    assert (install_root / "plugins" / "my-mixtape.flac").read_text() == "MINE"
    assert not (install_root / "plugins" / "qsvg.so").exists()
    assert not (tmp_path / "Waves.old").exists()


def test_macos_does_not_strand_a_whole_bundle_over_a_file_the_release_dropped(tmp_path, monkeypatch):
    """The macOS cost of the same mistake: the whole ~150 MB backup bundle kept
    forever, behind a notice ("files that were not part of Waves") that is not
    true of a library the release itself removed."""
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)
    bundle, target = _macos_bundle(tmp_path)
    (bundle / "Contents" / "Frameworks").mkdir()
    (bundle / "Contents" / "Frameworks" / "Qt6Svg.dylib").write_text("trimmed out of the next build")
    up._record_shipped(
        [
            "Contents/",
            "Contents/MacOS/",
            "Contents/MacOS/Waves",
            "Contents/Frameworks/",
            "Contents/Frameworks/Qt6Svg.dylib",
        ],
        "v1.0.0",
    )
    said = []

    up._apply_macos(_macos_staged(tmp_path, "staging"), target, said.append)

    assert target.read_text() == "NEW"
    assert not (tmp_path / "Applications" / "Waves.app.old").exists()
    assert up.kept_backup == ""
    assert not any("not part of Waves" in m for m in said)


def test_a_shipped_record_is_trusted_only_for_the_version_that_is_running(tmp_path):
    """A record left by an update whose swap never landed, or by an install the
    user replaced by hand, describes files that are not the ones on disk.
    Trusting it would count a user's file as the old build's, which is the one
    mistake in here that deletes something."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    assert up._shipped_by_the_old_build() is None  # nothing recorded yet

    up._record_shipped(["Waves", "dropped.so"], "v0.9.0")
    assert up._shipped_by_the_old_build() is None

    up._record_shipped(["Waves", "dropped.so"], "v1.0.0")
    assert up._shipped_by_the_old_build() == {"Waves", "dropped.so"}

    (up.staging_dir / up._SHIPPED_NAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert up._shipped_by_the_old_build() is None


def test_the_apply_records_what_the_build_it_just_installed_ships(tmp_path, monkeypatch):
    """Read off the staged copy, the one moment the build's files are separable
    from whatever the user keeps in the install folder, and written only once
    the apply has returned. It describes the NEW version, so the update after
    this one is the first to read it."""
    install_root, target = _install_with_user_files(tmp_path)
    staged = _staged_tree(tmp_path)
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    up.os_key = "linux"
    monkeypatch.setattr(u, "_current_exe", lambda: target)
    monkeypatch.setattr(AppUpdater, "_extract_payload", lambda self, p, a, log: staged / "Waves", raising=True)

    up._apply(tmp_path / "payload", Release(version="v2.0.0", asset="a", url="u"), lambda *a, **k: None)

    assert up._shipped_by_the_old_build() is None, "recorded for v2.0.0, and v1.0.0 is what is running"
    up.current_version = "2.0.0"
    assert up._shipped_by_the_old_build() == {"Waves", "lib.so"}
    # never the user's files, which is exactly why it is taken off the staged
    # tree and not off the install folder the reclaim has just written into
    assert not any(p.endswith(".flac") or p == "my-notes.txt" for p in up._shipped_by_the_old_build())


def test_tree_paths_keeps_folders_and_files_apart(tmp_path):
    """The trailing slash matters in one direction: a user who replaced a
    folder the build shipped with a file of their own must not have that file
    read as the folder we are entitled to delete."""
    root = tmp_path / "tree"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins" / "qsvg.so").write_text("x")
    (root / "Waves").write_text("x")

    assert sorted(u._tree_paths(root)) == ["Waves", "plugins/", "plugins/qsvg.so"]

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "plugins").write_text("the user's file, where the build had a folder")
    assert u._foreign_leftovers(backup, tmp_path / "gone", shipped=set(u._tree_paths(root))) == [
        pathlib.Path("plugins")
    ]


# ---- install() never passes a staged version off as the one asked for -------
def test_install_says_when_it_hands_back_a_previously_staged_version(tmp_path, monkeypatch):
    """A Windows swap staged yesterday is what the restart lands, whatever
    release the user clicks Install on today (staging a second one would race
    the armed helper over the same backup folder, so the short circuit is
    deliberate). The result therefore has to say so, or a caller renders
    "v0.1.27 installed, restart to finish" over an update that will restart
    into v0.1.26."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key = "windows"
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    up._armed_result = {"ok": True, "version": "v0.1.26", "applied_to": "x", "relaunch": True}

    result = up.install(release=Release(version="v0.1.27", asset="a", url="u"))

    assert result["already_staged"] is True
    assert result["version"] == "v0.1.26", "the version the restart will really land"
    assert result["requested_version"] == "v0.1.27"
    assert up.status()["pending_version"] == "v0.1.26"


def test_taking_over_a_staged_swap_reports_it_as_previously_staged_too(tmp_path, monkeypatch):
    """The same for the other short circuit: a swap staged by a copy of Waves
    that has since exited is taken over, not downloaded again, and what comes
    back is still a version this call did not stage."""
    up, _, _ = _onefile_staged_but_unapplied(tmp_path, monkeypatch)
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: None)
    up.latest = lambda *a, **k: pytest.fail("resolved a release for an update already staged")

    result = up.install(session=object())

    try:
        assert result["already_staged"] is True
        assert result["version"] == "v2.0.0"
        assert result["requested_version"] == ""  # this caller named none; the UI knows what it offered
    finally:
        up._armed_lock.release()


# ---- the size report must never fail a build --------------------------------
@pytest.mark.skipif(sys.platform.startswith("win") or not shutil.which("bash"), reason="needs bash + du")
def test_the_bundle_size_report_survives_a_wide_bundle_root(tmp_path):
    """`set -o pipefail` plus a `head -25` on the end of the top-items pipeline.

    head leaves as soon as it has its 25 lines; once sort's output no longer
    fits in the pipe buffer it is still writing at that moment, dies of
    SIGPIPE, and exits 141. pipefail makes that a failed pipeline and `set -e`
    makes it a failed script, so a build died with nothing wrong with the
    bundle: it had merely grown enough top-level entries to fill 64 KB of du
    output. The report is a report; it must never be what fails a build.
    """
    script = pathlib.Path(__file__).resolve().parents[1] / "tools" / "bundle_size_report.sh"
    root = tmp_path / "waves.dist"
    (root / "PySide6").mkdir(parents=True)
    wide = "w" * 200  # long names: 64 KB of du output without 60k files
    for i in range(1200):
        (root / f"{wide}{i:05d}").write_text("x")

    proc = subprocess.run(["bash", str(script), str(root)], capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.rstrip().endswith("=== end bundle size report ===")
    assert "file_count 1200" in proc.stdout  # the sections after that pipeline still ran
    # and the table is still a top-25, the cap having moved into awk. Its rows
    # are printf "%6d  %s", so a row is a line whose first field is the size.
    table = proc.stdout.split("--- top 25 items", 1)[1].split("--- PySide6", 1)[0]
    assert len([ln for ln in table.splitlines() if ln[:6].strip().isdigit()]) == 25


def test_the_size_report_caps_its_table_without_cutting_the_pipe(tmp_path):
    """The cap is awk's now, so it has to still be a cap, and it has to stay
    the kind of cap that reads its input to the end."""
    text = (pathlib.Path(__file__).resolve().parents[1] / "tools" / "bundle_size_report.sh").read_text()
    assert "NR <= 25" in text
    assert "| head -" not in text, "a head on a pipe is what kills the producer behind it"
