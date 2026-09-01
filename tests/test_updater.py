"""Unit tests for the in-app self-updater (no network, no Qt).

Covers the pure logic (version compare, platform asset selection, status
states, the configured/frozen gates) and a mocked download→verify→stage flow
that asserts a checksum mismatch never corrupts anything. The real platform
swap (``_apply``) targets the live executable, so it is stubbed here; it is only
exercisable against real CI artifacts.
"""

import errno
import hashlib
import io
import os
import pathlib
import re
import sys
import zipfile
from pathlib import Path

import pytest

from waves.waves_ui import signing
from waves.waves_ui import updater as u
from waves.waves_ui.updater import AppUpdater, Release, UpdaterError


def _helper_text(up):
    """The one swap helper the updater just armed.

    Named per pid so a re-armed helper can never overwrite one another copy of
    Waves is executing, so tests look it up rather than spelling the name.
    """
    scripts = sorted(up.staging_dir.glob("apply_update_*.bat"))
    assert len(scripts) == 1, scripts
    return scripts[0].read_text()


# ---- cross-platform apply (EXDEV / rollback) --------------------------------
def test_apply_unix_tree_is_cross_device_safe(tmp_path, monkeypatch):
    """The staged tree usually lives on a different filesystem than the install
    (e.g. ~/.config vs /opt or an AppImage mount). rename(2) can't cross
    devices, so a bare os.replace(new_tree, install_root) raised EXDEV and left
    the app uninstalled. The fix lands the tree on the install volume first, so
    the final swap is a same-device rename. Simulate EXDEV for any os.replace
    whose source is the staged tree; the new flow must never make that call."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    install_root = tmp_path / "app"
    install_root.mkdir()
    (install_root / "Waves").write_text("OLD")
    (install_root / "lib.so").write_text("oldlib")
    target = install_root / "Waves"
    staged = tmp_path / "staging" / "Waves.dist"
    staged.mkdir(parents=True)
    (staged / "Waves").write_text("NEW")
    (staged / "lib.so").write_text("newlib")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src) == str(staged):  # the cross-device move the old code did
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    up._apply_unix_tree(staged, target, lambda *a, **k: None)

    assert (install_root / "Waves").read_text() == "NEW"
    assert (install_root / "lib.so").read_text() == "newlib"
    assert not (tmp_path / "app.old").exists()
    assert not (tmp_path / "app.new").exists()
    assert not staged.exists()


def test_apply_unix_tree_rolls_back_on_swap_failure(tmp_path, monkeypatch):
    """If the swap fails partway, the live install must be restored from backup
    rather than left deleted."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    install_root = tmp_path / "app"
    install_root.mkdir()
    (install_root / "Waves").write_text("LIVE")
    target = install_root / "Waves"
    staged = tmp_path / "staging" / "Waves.dist"
    staged.mkdir(parents=True)
    (staged / "Waves").write_text("NEW")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src).endswith(".new"):  # the staged→install swap blows up
            raise OSError(errno.EIO, "boom")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    with pytest.raises(OSError):
        up._apply_unix_tree(staged, target, lambda *a, **k: None)
    assert install_root.exists() and (install_root / "Waves").read_text() == "LIVE"


# ---- the install folder is not only ours (foreign-file rescue) --------------
def _install_with_foreign_files(tmp_path):
    """An install folder holding the build AND things the user put there: a
    download folder aimed inside it and a loose note beside the executable."""
    install_root = tmp_path / "Waves"
    (install_root / "music" / "Some Artist" / "Some Album").mkdir(parents=True)
    (install_root / "music" / "Some Artist" / "Some Album" / "01 - Track.flac").write_text("MUSIC")
    (install_root / "my-notes.txt").write_text("NOTES")
    (install_root / "Waves").write_text("OLD")
    (install_root / "lib.so").write_text("oldlib")
    (install_root / "dropped.so").write_text("gone in the next build")
    return install_root, install_root / "Waves"


def _staged_tree(tmp_path):
    staged = tmp_path / "staging" / "Waves.dist"
    staged.mkdir(parents=True)
    (staged / "Waves").write_text("NEW")
    (staged / "lib.so").write_text("newlib")
    return staged


def test_apply_unix_tree_keeps_files_that_were_not_part_of_the_build(tmp_path):
    """The swap replaces the WHOLE install directory and then deletes the
    backup, so anything the user kept in there (a download folder pointed at
    it, a zip unpacked over a folder that already held other files) went with
    it: no Recycle Bin, no warning. Waves never deletes a user's files, so
    every path the new tree does not have is carried back in first."""
    install_root, target = _install_with_foreign_files(tmp_path)
    staged = _staged_tree(tmp_path)
    kept = []

    u.AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")._apply_unix_tree(staged, target, kept.append)

    assert (install_root / "Waves").read_text() == "NEW"  # the update still applied
    assert (install_root / "lib.so").read_text() == "newlib"
    assert (install_root / "my-notes.txt").read_text() == "NOTES"
    assert (install_root / "music" / "Some Artist" / "Some Album" / "01 - Track.flac").read_text() == "MUSIC"
    assert not (tmp_path / "Waves.old").exists()  # nothing left behind once it all came back
    assert any("not part of Waves" in m for m in kept)


def _refuse_to_move(allow_src):
    """shutil.move that works for the staging step and fails for the rescue."""
    real = u.shutil.move

    def fake(src, dst, *a, **k):
        if str(src) == allow_src:
            return real(src, dst, *a, **k)
        raise OSError(errno.EACCES, "denied")

    return fake


def test_apply_unix_tree_keeps_the_backup_when_a_file_cannot_be_moved_back(tmp_path, monkeypatch):
    """A stale .old folder is recoverable, a deleted music library is not: if
    even one item cannot be moved back, the backup is kept whole."""
    install_root, target = _install_with_foreign_files(tmp_path)
    staged = _staged_tree(tmp_path)
    monkeypatch.setattr(u.shutil, "move", _refuse_to_move(str(staged)))
    said = []

    u.AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")._apply_unix_tree(staged, target, said.append)

    backup = tmp_path / "Waves.old"
    assert (install_root / "Waves").read_text() == "NEW"  # the update still applied
    assert (backup / "my-notes.txt").read_text() == "NOTES"  # and nothing was destroyed
    assert any("still in Waves.old" in m for m in said)


def test_foreign_leftovers_reports_a_whole_directory_once(tmp_path):
    """A download folder with ten thousand files under it is ONE move, not ten
    thousand, so the rescue never descends into a directory the build lacks."""
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "music" / "a" / "b").mkdir(parents=True)
    (old / "music" / "a" / "b" / "t.flac").write_text("x")
    (old / "lib" / "extra.so").mkdir(parents=True)
    (new / "lib").mkdir(parents=True)

    assert u._foreign_leftovers(old, new) == [pathlib.Path("lib/extra.so"), pathlib.Path("music")]


def test_the_swap_never_clears_a_sibling_folder_that_is_not_ours(tmp_path, monkeypatch):
    """The swap stages at Waves.new and backs up to Waves.old, wiping whatever
    sits at those names first. Nothing says a folder called Waves.old next to
    the install belongs to the app, and Waves never deletes a user's files, so
    a name holding anything but our own tree is skipped for the next one."""
    install_root, target = _install_with_foreign_files(tmp_path)
    staged = _staged_tree(tmp_path)
    squatter_new = install_root.with_name("Waves.new")
    squatter_new.mkdir()
    (squatter_new / "mixtape.flac").write_text("MINE")
    squatter_old = install_root.with_name("Waves.old")
    squatter_old.mkdir()
    (squatter_old / "notes.txt").write_text("ALSO MINE")

    u.AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")._apply_unix_tree(staged, target, lambda *a, **k: None)

    assert (install_root / "Waves").read_text() == "NEW"  # the update still applied
    assert (squatter_new / "mixtape.flac").read_text() == "MINE"
    assert (squatter_old / "notes.txt").read_text() == "ALSO MINE"


def test_a_staging_sibling_we_left_behind_IS_reused(tmp_path, monkeypatch):
    """The other half of the rule: a leftover of our own (it holds the app
    executable) is cleared rather than piling up a -1, -2, -3 of stale trees."""
    install_root, target = _install_with_foreign_files(tmp_path)
    staged = _staged_tree(tmp_path)
    ours = install_root.with_name("Waves.new")
    ours.mkdir()
    (ours / "Waves").write_text("A TREE WE STAGED LAST TIME")

    u.AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")._apply_unix_tree(staged, target, lambda *a, **k: None)

    assert (install_root / "Waves").read_text() == "NEW"
    assert not install_root.with_name("Waves.new-1").exists()


def test_apply_macos_keeps_the_backup_rather_than_writing_into_the_bundle(tmp_path, monkeypatch):
    """Same exposure inside a .app, opposite answer: moving foreign files into
    the new bundle would break its code signature and the app would stop
    launching, so the old bundle is kept instead of deleted."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)  # no real xattr
    bundle = tmp_path / "Applications" / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    target = bundle / "Contents" / "MacOS" / "Waves"
    target.write_text("OLD")
    (bundle / "Contents" / "my-notes.txt").write_text("NOTES")
    staged = tmp_path / "staging" / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text("NEW")
    said = []

    up._apply_macos(staged, target, said.append)

    backup = tmp_path / "Applications" / "Waves.app.old"
    assert target.read_text() == "NEW"
    assert (backup / "Contents" / "my-notes.txt").read_text() == "NOTES"
    assert not (bundle / "Contents" / "my-notes.txt").exists()  # never written into the bundle
    assert any("not part of Waves" in m for m in said)
    # Recorded, not only logged: the log line is a passing status that
    # "Updated to vX. Restart to finish." overwrites a moment later, so a user
    # could accumulate a whole extra copy of the app per update and never be
    # told. The NAME only, never the path (it sits under the user's home).
    assert up.kept_backup == "Waves.app.old"
    assert "/" not in up.kept_backup and str(tmp_path) not in up.kept_backup


def test_apply_macos_deletes_a_backup_that_held_nothing_but_the_build(tmp_path, monkeypatch):
    """The common case is unchanged: no leftovers, no .old folder left behind."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)
    bundle = tmp_path / "Applications" / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    target = bundle / "Contents" / "MacOS" / "Waves"
    target.write_text("OLD")
    staged = tmp_path / "staging" / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text("NEW")

    up._apply_macos(staged, target, lambda *a, **k: None)

    assert target.read_text() == "NEW"
    assert not (tmp_path / "Applications" / "Waves.app.old").exists()
    assert up.kept_backup == "", "nothing was kept, so there is nothing to tell the user about"


def test_windows_helper_spawn_contract(tmp_path, monkeypatch):
    """The swap helper must get a hidden console (CREATE_NO_WINDOW), never
    DETACHED_PROCESS: detached cmd has no console at all and the batch never
    ran (tasklist/find/start are console programs), so updates downloaded but
    were never applied. The cwd is pinned to the staging dir so the helper
    cannot hold a lock inside the install folder it renames."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    calls = {}

    def fake_popen(args, **kw):
        calls["args"], calls["kw"] = args, kw

    monkeypatch.setattr(u.subprocess, "Popen", fake_popen)
    target = tmp_path / "install" / "Waves.exe"
    target.parent.mkdir()
    target.write_text("OLD")
    up.staging_dir.mkdir(parents=True, exist_ok=True)
    staged = up.staging_dir / "Waves.exe"
    staged.write_text("NEW")
    monkeypatch.setattr(u.os, "getpid", lambda: 4242)

    up._apply_windows(staged, target, lambda *a, **k: None)

    assert calls["kw"]["creationflags"] == 0x08000000  # CREATE_NO_WINDOW
    assert calls["kw"]["cwd"] == str(up.staging_dir)
    bat = _helper_text(up)
    assert "PID eq 4242" in bat
    assert "update.log" in bat  # every step is diagnosable in the field
    assert ":swap" in bat and "mtries" in bat  # bounded retry while the exe unlocks
    assert bat.count('start "" ') >= 2  # every failure path still relaunches


def test_apply_macos_is_cross_device_safe(tmp_path, monkeypatch):
    """The staged `.app` usually lives under ~/.config while the install sits in
    /Applications (a different volume), so a bare os.replace(staged, bundle) would
    raise EXDEV. The new flow lands the bundle on the install volume first, so the
    final swap is a same-device rename and never calls os.replace on the staged
    path directly."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)  # no real xattr
    apps = tmp_path / "Applications"
    bundle = apps / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    target = bundle / "Contents" / "MacOS" / "Waves"
    target.write_text("OLD")
    staged = tmp_path / "staging" / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text("NEW")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src) == str(staged):  # the cross-device move the old code did
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    up._apply_macos(staged, target, lambda *a, **k: None)

    assert (bundle / "Contents" / "MacOS" / "Waves").read_text() == "NEW"
    assert not (apps / "Waves.app.old").exists()
    assert not (apps / "Waves.app.new").exists()
    assert not staged.exists()


def test_apply_macos_rolls_back_on_swap_failure(tmp_path, monkeypatch):
    """If the bundle swap fails partway, the live `.app` must be restored from its
    backup rather than left deleted."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)
    apps = tmp_path / "Applications"
    bundle = apps / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    target = bundle / "Contents" / "MacOS" / "Waves"
    target.write_text("LIVE")
    staged = tmp_path / "staging" / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text("NEW")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src).endswith(".app.new"):  # the staged→bundle swap blows up
            raise OSError(errno.EIO, "boom")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    with pytest.raises(OSError):
        up._apply_macos(staged, target, lambda *a, **k: None)
    assert (bundle / "Contents" / "MacOS" / "Waves").read_text() == "LIVE"


def test_apply_windows_helper_backs_up_and_restores(tmp_path, monkeypatch):
    """The detached .bat that swaps a single .exe must back the old exe up and, on
    a failed move, restore it and relaunch; never leave `target` missing."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    spawned = {}
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.update(cmd=cmd, kw=kw))
    install = tmp_path / "app"
    install.mkdir()
    target = install / "Waves.exe"
    staged = tmp_path / "staging" / "Waves.exe"
    staged.parent.mkdir(parents=True)
    staged.write_text("NEW")

    up._apply_windows(staged, target, lambda *a, **k: None)

    script = _helper_text(up)
    assert 'move /Y "%TARGET%" "%BACKUP%"' in script  # back the live exe up
    assert 'move /Y "%BACKUP%" "%TARGET%"' in script  # restore it on failure
    assert "exit /b 1" in script
    # the paths reach the script through the environment, never interpolated
    # into it and never on a command line cmd would expand percent pairs in
    assert str(target) not in script
    env = spawned["kw"]["env"]
    # The backup is named per pid: two copies of Waves can each have a helper
    # waiting, and a shared ".old" let the loser delete the winner's only copy
    # of the old build (see test_two_helpers_cannot_collide_over_one_backup).
    assert [env[f"WAVES_UPDATE_{i}"] for i in (1, 3)] == [str(target), f"{target}.new"]
    assert env["WAVES_UPDATE_2"] == f"{target}.old-{os.getpid()}"


def test_apply_windows_is_cross_device_safe(tmp_path, monkeypatch):
    """The three other apply paths all survive staging and install sitting on
    different volumes; this one did a bare os.replace from the app data dir to
    the install dir, which on Windows is MoveFileExW without COPY_ALLOWED and
    fails outright. Config on C: with the app on D: is an ordinary setup, and
    the user would have watched a whole download end in "update failed"."""
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    install = tmp_path / "app"
    install.mkdir()
    target = install / "Waves.exe"
    target.write_text("OLD")
    staged = up.staging_dir / "Waves.exe"
    staged.write_text("NEW")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src) == str(staged):  # the volume boundary
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    up._apply_windows(staged, target, lambda *a, **k: None)

    # The new exe is beside the target, on the install volume, so the helper's
    # own move is a same-volume rename; the live exe is untouched until then.
    assert (install / "Waves.exe.new").read_text() == "NEW"
    assert target.read_text() == "OLD"
    assert not staged.exists()


def test_apply_windows_tree_helper_backs_up_and_restores(tmp_path, monkeypatch):
    """The detached .bat that mirrors a whole .dist tree must rename the live
    install to .old first and restore it if robocopy reports a real failure."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    new_tree = tmp_path / "staging" / "Waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves.exe").write_text("NEW")

    up._apply_windows_tree(new_tree, target, lambda *a, **k: None)

    script = _helper_text(up)
    assert 'move "%INSTALL%" "%BACKUP%"' in script  # back the install up
    assert 'move "%BACKUP%" "%INSTALL%"' in script  # restore on failure
    assert "GEQ 8" in script  # only give up on a real robocopy failure


def test_two_helpers_cannot_collide_over_one_backup(tmp_path, monkeypatch):
    """The window the shared ".old" name left open, and what it cost.

    Two copies of Waves can each end up with a helper waiting: one arms at
    install time and exits, the other re-arms at its next launch. Both derived
    the SAME backup path from the install root. The loser then wakes between
    the winner's `move INSTALL -> BACKUP` and its `move NEWTREE -> INSTALL`,
    when NEWTREE still exists so the "already applied elsewhere" recheck
    passes, and its very next line is an unconditional rmdir of BACKUP: at that
    instant the only copy of the user's foreign files, which the winner has not
    reclaimed yet. Naming the backup per pid is what closes it.
    """
    spawns: list[dict] = []
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawns.append(kw))
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"

    backups = []
    for n, pid in enumerate((4242, 5353)):
        monkeypatch.setattr(u.os, "getpid", lambda pid=pid: pid)
        up = AppUpdater(tmp_path / f"cfg{n}", "1.0.0", repo="owner/Waves")
        up.staging_dir.mkdir(parents=True)
        new_tree = tmp_path / f"staging{n}" / "Waves"
        new_tree.mkdir(parents=True)
        (new_tree / "Waves.exe").write_text("NEW")
        up._apply_windows_tree(new_tree, target, lambda *a, **k: None)
        backups.append(spawns[-1]["env"]["WAVES_UPDATE_2"])

    assert backups[0] != backups[1], "two waiting helpers still share one backup folder"
    assert backups[0].endswith(".old-4242") and backups[1].endswith(".old-5353")
    # And the dangerous line is still there, which is the point: it is only
    # safe because it can no longer reach anyone else's backup.
    assert 'rmdir /S /Q "%BACKUP%"' in _helper_text(AppUpdater(tmp_path / "cfg1", "1.0.0", repo="owner/Waves"))


def _tree_helper_script(tmp_path, monkeypatch, *, exe_bytes=b"NEW"):
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    install_root = tmp_path / "Waves"
    install_root.mkdir(exist_ok=True)
    target = install_root / "Waves.exe"
    new_tree = tmp_path / "staging" / "Waves"
    new_tree.mkdir(parents=True, exist_ok=True)
    if exe_bytes is not None:
        (new_tree / "Waves.exe").write_bytes(exe_bytes)
    up._apply_windows_tree(new_tree, target, lambda *a, **k: None)
    return up, install_root, target, new_tree, _helper_text(up)


def test_apply_windows_tree_refuses_a_staged_tree_without_the_exe(tmp_path, monkeypatch):
    """Field report: an update left the install folder empty. A staged tree with
    no executable (or an empty one, the antivirus having eaten it) must be
    refused before any helper is written, never mirrored over the live install."""
    with pytest.raises(UpdaterError, match="no Waves.exe"):
        _tree_helper_script(tmp_path, monkeypatch, exe_bytes=None)
    with pytest.raises(UpdaterError, match="no Waves.exe"):
        _tree_helper_script(tmp_path, monkeypatch, exe_bytes=b"")
    assert list((tmp_path / "updates").glob("apply_update_*.bat")) == []


def test_apply_windows_tree_helper_never_deletes_the_last_good_copy(tmp_path, monkeypatch):
    """The helper may delete the .old backup only after the mirrored folder
    holds the executable (robocopy exits 0 for 'source empty, nothing copied'),
    and every failure path restores the backup on its own labelled branch. The
    old one-line `if ... & move & start & exit` chain bound the restore INTO the
    `if exist`, so a mirror that created no folder fell through to deleting the
    backup: that is the empty install folder seen in the field."""
    _, install_root, target, new_tree, script = _tree_helper_script(tmp_path, monkeypatch)
    lines = script.replace("\r\n", "\n").split("\n")
    # the staged tree is checked before the swap, the executable after it
    assert 'if not exist "%NEWTREE%" (echo nothing staged' in script
    assert 'if not exist "%TARGET%" (echo swap left no Waves.exe' in script
    # the backup is deleted on exactly one line, and only after the exe check
    deletes = [i for i, ln in enumerate(lines) if ln.startswith('rmdir /S /Q "%BACKUP%"')]
    assert len(deletes) == 1
    exe_check = next(i for i, ln in enumerate(lines) if 'if not exist "%TARGET%"' in ln)
    assert exe_check < deletes[0]
    # the restore is a plain sequence, not a consequent of `if exist`
    assert any(ln.startswith('move "%BACKUP%" "%INSTALL%"') for ln in lines)
    assert ":restore" in lines and "goto restore" in script
    # relaunch only what exists; no `start` anywhere else
    starts = [ln for ln in lines if 'start ""' in ln]
    assert starts == [
        'if exist "%TARGET%" (start "" "%TARGET%" & echo relaunched >> "%LOG%") '
        'else (echo nothing to relaunch >> "%LOG%")'
    ]


def test_apply_windows_tree_helper_reclaims_foreign_files_before_deleting_the_backup(tmp_path, monkeypatch):
    """The swap-in brings only the new tree, so anything the user kept in the
    install folder ends up in the backup that the next line deletes. A robocopy
    moves back every path the swapped-in install does not have (/XC /XN /XO
    leave only the missing ones), and a failure there keeps the backup folder
    instead of deleting it."""
    _, install_root, target, _, script = _tree_helper_script(tmp_path, monkeypatch)
    lines = script.replace("\r\n", "\n").split("\n")

    reclaim = next(i for i, ln in enumerate(lines) if ln.startswith('robocopy "%BACKUP%" "%INSTALL%"'))
    assert "/XC /XN /XO" in lines[reclaim]  # copy back only what the new tree lacks
    assert "/MOVE" in lines[reclaim]  # a rename per file: no second copy of a music library
    assert "/XJ" in lines[reclaim]  # never walk into a junction
    # the one backup delete comes after the reclaim, and a failed reclaim skips it
    delete = next(i for i, ln in enumerate(lines) if ln.startswith('rmdir /S /Q "%BACKUP%"'))
    assert reclaim < delete
    assert lines[reclaim + 1].startswith("if %ERRORLEVEL% GEQ 8 (echo could not reclaim")
    assert "goto relaunch)" in lines[reclaim + 1]


def test_windows_helpers_are_pure_ascii_whatever_the_paths_are(tmp_path, monkeypatch):
    """cmd.exe decodes a .bat in the console's OEM code page, not UTF-8. A path
    interpolated into the script therefore arrived as mojibake on any machine
    whose account name is not ASCII: the first `if not exist` tested a path
    that cannot exist, the helper applied nothing and deleted itself, and the
    UI had already said "Updated, restart to finish". Every path now reaches
    the script as a command-line argument (UTF-16 all the way), so the script
    body is ASCII and the code page cannot touch it."""
    app_dir = tmp_path / "\u041c\u0430\u0440\u0438\u044f" / "AppData" / "Roaming" / "Waves"
    app_dir.mkdir(parents=True)
    up = AppUpdater(app_dir, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    spawned = {}
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.update(cmd=cmd, kw=kw))
    install_root = tmp_path / "\u041f\u0440\u043e\u0433\u0440\u0430\u043c\u043c\u044b" / "Waves"
    install_root.mkdir(parents=True)
    target = install_root / "Waves.exe"
    new_tree = up.staging_dir / "staged" / "Waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves.exe").write_bytes(b"NEW")

    up._apply_windows_tree(new_tree, target, lambda *a, **k: None)

    script = _helper_text(up).encode("utf-8")
    script.decode("ascii")  # raises if a single non-ASCII byte got in
    assert script[:3] != b"\xef\xbb\xbf"  # no BOM either: cmd would echo it
    # the non-ASCII paths did reach the helper, through the environment
    env = spawned["kw"]["env"]
    assert env["WAVES_UPDATE_1"] == str(install_root)
    assert env["WAVES_UPDATE_3"] == str(install_root.with_name("Waves.new"))


def test_windows_helper_paths_survive_every_character_a_folder_may_hold(tmp_path, monkeypatch):
    """A Windows folder name may hold "&" ("Rock & Roll"), "^", "!" and even a
    matched pair of percent signs. Quoting the paths on the command line made
    the ampersand data but did nothing about cmd's OWN percent expansion: a
    folder named with an existing variable's pair was rewritten before %~1
    could capture it, the helper found nothing staged, and the restart landed
    on the old build with the UI already saying it had updated. The paths
    travel in the environment now, where a value is substituted once and never
    rescanned, so the command line carries no path at all."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    spawned = {}
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.update(cmd=cmd, kw=kw))
    hostile = "Rock & Roll %TEMP% ^ mixes!"
    install_root = tmp_path / hostile / "Waves"
    install_root.mkdir(parents=True)
    target = install_root / "Waves.exe"
    new_tree = up.staging_dir / "staged" / "Waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves.exe").write_bytes(b"NEW")

    up._apply_windows_tree(new_tree, target, lambda *a, **k: None)

    assert spawned["cmd"] == f"cmd /c apply_update_{os.getpid()}.bat"
    assert hostile not in spawned["cmd"]  # nothing for cmd to expand or split
    assert spawned["kw"]["cwd"] == str(up.staging_dir)
    env = spawned["kw"]["env"]
    assert env["WAVES_UPDATE_1"] == str(install_root)
    assert env["WAVES_UPDATE_4"] == str(target)
    # and the script reads them from there, not from %~1
    script = _helper_text(up)
    assert "%~1" not in script and "%~2" not in script
    assert 'set "INSTALL=%WAVES_UPDATE_1%"' in script


def test_apply_windows_tree_lands_the_new_tree_on_the_install_volume_first(tmp_path, monkeypatch):
    """The helper used to robocopy hundreds of megabytes at the one moment most
    likely to be a Windows shutdown: the app exiting. A shutdown killed the
    mirror halfway and left the install broken with the only good copy stranded
    at .old, unrepaired. The copy now happens here, while the app still runs,
    so the helper does two same-volume renames and nothing else."""
    _, install_root, target, new_tree, script = _tree_helper_script(tmp_path, monkeypatch)

    staged_same_dev = install_root.with_name(install_root.name + ".new")
    assert staged_same_dev.is_dir()  # landed next to the install, before arming
    assert (staged_same_dev / "Waves.exe").read_bytes() == b"NEW"
    assert not new_tree.exists()  # moved, not copied: no third copy on disk
    # the helper mirrors nothing; it renames twice
    assert "/MIR" not in script
    assert 'move "%INSTALL%" "%BACKUP%"' in script
    assert 'move "%NEWTREE%" "%INSTALL%"' in script


def test_apply_windows_tree_cleans_up_a_half_landed_tree(tmp_path, monkeypatch):
    """If the copy onto the install volume fails (a full disk), no helper is
    armed and the partial .new folder does not survive to confuse the next
    attempt."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    spawned = []
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    new_tree = up.staging_dir / "staged" / "Waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves.exe").write_bytes(b"NEW")

    def half_move(src, dst, *a, **k):
        pathlib.Path(dst).mkdir(parents=True, exist_ok=True)
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(u.shutil, "move", half_move)
    with pytest.raises(OSError):
        up._apply_windows_tree(new_tree, target, lambda *a, **k: None)

    assert not install_root.with_name("Waves.new").exists()
    assert spawned == []  # nothing armed
    assert (install_root / "Waves.exe").exists() is False and install_root.is_dir()


def test_windows_helpers_never_start_a_second_instance_on_giveup(tmp_path, monkeypatch):
    """The helper is armed at install time, not at Restart: if the user keeps
    the app open past the wait, giving up must not `start` a duplicate of the
    still-running app (the old 150 s cap did, and the later restart then swapped
    nothing). It waits hours and, when it does give up, only exits."""
    _, _, _, _, tree_script = _tree_helper_script(tmp_path, monkeypatch)
    giveup = tree_script[tree_script.index("\n:giveup") :]
    assert 'start ""' not in giveup
    assert f"GTR {AppUpdater._HELPER_WAIT_TICKS}" in tree_script
    assert AppUpdater._HELPER_WAIT_TICKS >= 3600

    up = AppUpdater(tmp_path / "single", "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    install = tmp_path / "single" / "app"
    install.mkdir(parents=True)
    staged = tmp_path / "single" / "staging" / "Waves.exe"
    staged.parent.mkdir(parents=True)
    staged.write_text("NEW")
    up._apply_windows(staged, install / "Waves.exe", lambda *a, **k: None)
    single = _helper_text(up)
    giveup_line = next(ln for ln in single.replace("\r\n", "\n").split("\n") if "gave up waiting" in ln)
    assert 'start ""' not in giveup_line
    assert f"GTR {AppUpdater._HELPER_WAIT_TICKS}" in single


def _wait_seconds(script: str) -> float:
    """How long the helper's wait loop actually lasts, read off the script.

    The loop counts TICKS; what makes a tick take about a second is the ping on
    the line that goes back to :wait ("ping -n 2" sends one packet, waits one
    second for the second). Nothing else in the loop sleeps, so dropping that
    ping (or making it "-n 1") leaves both the tick count and the promise of
    hours in place while the helper gives up in minutes: the exact regression
    the four-hour wait was introduced to fix.
    """
    line = next(ln for ln in script.replace("\r\n", "\n").split("\n") if "goto wait" in ln)
    match = re.search(r"ping -n (\d+) ", line)
    assert match, f"the wait loop has no delay at all: {line}"
    return AppUpdater._HELPER_WAIT_TICKS * (int(match.group(1)) - 1)


def test_the_wait_loop_really_lasts_hours(tmp_path, monkeypatch):
    """Both helpers, in seconds rather than in ticks."""
    _, _, _, _, tree_script = _tree_helper_script(tmp_path, monkeypatch)
    up = AppUpdater(tmp_path / "single2", "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    install = tmp_path / "single2" / "app"
    install.mkdir(parents=True)
    staged = tmp_path / "single2" / "staging" / "Waves.exe"
    staged.parent.mkdir(parents=True)
    staged.write_text("NEW")
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    up._apply_windows(staged, install / "Waves.exe", lambda *a, **k: None)

    for script in (tree_script, _helper_text(up)):
        assert _wait_seconds(script) >= 3 * 3600, "a music app is regularly open longer than this"


def test_install_with_an_armed_windows_helper_does_not_stage_twice(tmp_path, monkeypatch):
    """Once a Windows helper is waiting for this process to exit, a second
    install in the same session must return the staged result untouched: a
    second helper would race the first over the same .old folder and staged
    tree, and a re-extraction would empty the tree the first one mirrors."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key = "windows"
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    calls = []
    monkeypatch.setattr(up, "latest", lambda *a, **k: calls.append("latest"))
    up._armed_result = {"ok": True, "version": "v2.0.0", "applied_to": "x", "relaunch": True}
    logs = []
    # What comes back is the staged result as it was staged, plus the flag that
    # says it IS one (see _staged_result): the version in it is the one the
    # restart will land, not necessarily the release this call was asked for.
    assert up.install(log_cb=logs.append) == {**up._armed_result, "already_staged": True, "requested_version": ""}
    assert calls == []  # no network, no download, no extraction, no helper
    assert any("already staged" in m for m in logs)


# ---- a staged swap that has not happened yet (resume + cross-process) -------
def _staged_but_unapplied(tmp_path, monkeypatch, *, staged_version="v2.0.0", running="1.0.0"):
    """An install folder whose swap was staged and armed but never ran: the
    marker is on disk and the new tree is sitting next to the install."""
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    target.write_text("OLD")
    new_tree = install_root.with_name("Waves.new")
    new_tree.mkdir()
    (new_tree / "Waves.exe").write_text("NEW")
    monkeypatch.setattr(u, "_current_exe", lambda: target)
    up = AppUpdater(tmp_path / "config", running, repo="owner/Waves")
    up.os_key = "windows"
    up.staging_dir.mkdir(parents=True)
    up._write_armed_marker({"ok": True, "version": staged_version, "applied_to": str(target), "relaunch": True})
    return up, install_root, target, new_tree


def test_a_staged_swap_that_never_ran_is_re_armed_at_the_next_launch(tmp_path, monkeypatch):
    """The helper waits for the process that armed it and gives up after a few
    hours; a session that ends in a shutdown never wakes it at all. install()
    had already said "Updated, restart to finish", so the user quit, relaunched
    into the old version and was told nothing. The next launch re-arms it."""
    up, install_root, target, _ = _staged_but_unapplied(tmp_path, monkeypatch)
    spawned = {}
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.update(cmd=cmd, kw=kw))

    pending = up.resume_pending_apply()

    assert pending is not None and pending["version"] == "v2.0.0"
    assert _helper_text(up)  # a fresh helper, armed against THIS process
    assert spawned["kw"]["env"]["WAVES_UPDATE_3"] == str(install_root.with_name("Waves.new"))
    # and the UI can see it without being told
    assert up.status()["pending_restart"] is True
    assert up.status()["pending_version"] == "v2.0.0"


def test_resume_clears_the_marker_once_the_swap_has_landed(tmp_path, monkeypatch):
    """Running the staged version means the swap happened: drop the marker and
    the leftover tree instead of arming a helper for an update already made."""
    up, _, _, new_tree = _staged_but_unapplied(tmp_path, monkeypatch, staged_version="v2.0.0", running="2.0.0")
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: pytest.fail("armed a helper"))

    assert up.resume_pending_apply() is None
    assert up._read_armed_marker() is None
    assert not new_tree.exists()


def test_resume_gives_up_when_the_staged_tree_is_gone(tmp_path, monkeypatch):
    """Nothing left to apply (another copy's helper already mirrored it, or the
    folder was cleaned): clear the marker rather than arm a helper that would
    find nothing."""
    up, _, _, new_tree = _staged_but_unapplied(tmp_path, monkeypatch)
    (new_tree / "Waves.exe").unlink()
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: pytest.fail("armed a helper"))

    assert up.resume_pending_apply() is None
    assert up._read_armed_marker() is None


def test_resume_stands_down_while_another_copy_owns_the_update(tmp_path, monkeypatch):
    """Two copies of Waves share one updates/ folder. Only the copy holding the
    staging lock may arm a helper: two helpers racing the same .old folder is
    how the app ends up deleted."""
    up, _, _, _ = _staged_but_unapplied(tmp_path, monkeypatch)
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: pytest.fail("armed a second helper"))
    other = u._StagingLock(up.staging_dir / up._LOCK_NAME)
    assert other.try_acquire()
    try:
        assert up.resume_pending_apply() is None
    finally:
        other.release()


def test_a_marker_that_is_not_an_object_is_not_a_marker(tmp_path, monkeypatch):
    """Valid JSON that is not an object must not crash a launch."""
    up, _, _, _ = _staged_but_unapplied(tmp_path, monkeypatch)
    up._armed_marker().write_text("[1, 2, 3]", encoding="utf-8")
    assert up._read_armed_marker() is None
    assert up.resume_pending_apply() is None


def test_a_second_copy_cannot_stage_over_an_armed_update(tmp_path, monkeypatch):
    """The armed guard used to be a per-process attribute, so a second copy of
    Waves re-extracted over the staged tree the first one's helper was waiting
    to swap in and rewrote the helper script mid-execution. The staging lock is
    held for the whole life of a process that armed a helper, so the second
    copy is told to restart instead."""
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    first, _ = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    first.os_key = "windows"
    assert first.install(session=object())["ok"]
    assert first._armed_lock is not None  # the lock outlives install() on Windows

    second, _ = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    second.os_key = "windows"
    with pytest.raises(UpdaterError, match="Another copy of Waves"):
        second.install(session=object())
    first._armed_lock.release()


def test_a_later_copy_takes_a_staged_swap_over_instead_of_installing_again(tmp_path, monkeypatch):
    """Once the copy that armed the helper is gone the lock is free, but the
    swap is still staged and nothing is waiting for anyone to quit. A fresh
    copy arms a helper of its own and reports "restart to finish", rather than
    downloading and extracting over the tree that helper is about to move. It
    does not even go to the network to find that out."""
    up, _, _, _ = _staged_but_unapplied(tmp_path, monkeypatch)
    spawned = {}
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.update(cmd=cmd))
    up.latest = lambda *a, **k: pytest.fail("resolved a release for an update already staged")
    logs = []

    result = up.install(session=object(), log_cb=logs.append)

    assert result["version"] == "v2.0.0"
    assert any("already staged" in m for m in logs)
    assert spawned  # a helper is now waiting on THIS process
    assert up._armed_lock is not None
    up._armed_lock.release()


def test_the_helper_rechecks_the_staged_tree_after_the_wait(tmp_path, monkeypatch):
    """Hours pass between arming and the swap, and another copy's helper may
    have consumed the staged tree in that time. The re-check runs before
    anything is renamed or deleted, so the losing helper just exits."""
    _, _, _, _, script = _tree_helper_script(tmp_path, monkeypatch)
    lines = script.replace("\r\n", "\n").split("\n")

    exited = next(i for i, ln in enumerate(lines) if ln.startswith("echo app exited after"))
    recheck = next(i for i, ln in enumerate(lines) if "already applied elsewhere" in ln)
    first_touch = next(i for i, ln in enumerate(lines) if "rmdir" in ln or ln.startswith("move "))
    assert exited < recheck < first_touch
    # Ordering alone would still pass with the jump gone, and the very next
    # line deletes the winner's backup folder, which can still hold the user's
    # own files awaiting the reclaim. The re-check has to LEAVE.
    assert "goto done" in lines[recheck], "the losing helper falls through into the swap"
    assert lines[first_touch].strip().endswith('>> "%LOG%" 2>&1')


# ---- version parse / compare ------------------------------------------------
@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.2.3", (1, 2, 3)),
        ("1.2", (1, 2)),
        ("v2", (2,)),
        ("v1.2.0-beta.1", (1, 2, 0)),  # pre-release suffix ignored
        ("waves-3.4.5", (3, 4, 5)),
        ("nope", ()),
        ("", ()),
    ],
)
def test_parse_version(tag, expected):
    assert u._parse_version(tag) == expected


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("v1.3.0", "1.2.9", True),
        ("v1.2.1", "1.2.0", True),
        ("v2.0.0", "1.9.9", True),
        ("v1.2.0", "1.2.0", False),  # equal
        ("1.2", "1.2.0", False),  # 1.2 == 1.2.0
        ("v0.9.0", "1.0.0", False),  # older
        ("", "1.0.0", False),  # unparseable latest → never "newer"
    ],
)
def test_is_newer(latest, current, expected):
    assert u._is_newer(latest, current) is expected


# ---- asset selection --------------------------------------------------------
_ASSETS = [
    {"name": "Waves-macos-arm64.dmg", "browser_download_url": "mac-arm"},
    {"name": "Waves-macos-arm64.dmg.sha256", "browser_download_url": "mac-arm-sha"},
    {"name": "Waves-macos-x64.dmg", "browser_download_url": "mac-x64"},
    {"name": "Waves-linux-x86_64.zip", "browser_download_url": "lin-x64"},
    {"name": "Waves-windows-x64.zip", "browser_download_url": "win-x64"},
    {"name": "checksums.txt", "browser_download_url": "sums"},
]


def test_select_macos_arm_with_sha():
    assert u._select_asset(_ASSETS, "macos", "arm64", want_legacy=False) == (
        "Waves-macos-arm64.dmg",
        "mac-arm",
        "mac-arm-sha",
    )


def test_select_prefers_correct_arch():
    name, url, _ = u._select_asset(_ASSETS, "macos", "amd64", want_legacy=False)
    assert (name, url) == ("Waves-macos-x64.dmg", "mac-x64")


def test_select_windows_no_sidecar():
    assert u._select_asset(_ASSETS, "windows", "amd64") == ("Waves-windows-x64.zip", "win-x64", None)


def test_select_never_picks_wrong_arch():
    # Only an x86_64 linux build exists; an arm64 machine must get nothing
    # rather than an incompatible binary.
    assert u._select_asset(_ASSETS, "linux", "arm64") == ("", "", None)


def test_select_linux_amd64():
    name, url, _ = u._select_asset(_ASSETS, "linux", "amd64")
    assert (name, url) == ("Waves-linux-x86_64.zip", "lin-x64")


def test_select_arch_agnostic_fallback():
    assets = [{"name": "Waves-linux.tar.gz", "browser_download_url": "uni"}]
    assert u._select_asset(assets, "linux", "arm64")[0] == "Waves-linux.tar.gz"


def test_select_arch_agnostic_appimage_still_needs_the_flag():
    # Format follows install: even a release carrying ONLY an AppImage must not
    # be installed over a zip/dist-tree copy (and vice versa).
    assets = [{"name": "Waves-linux.AppImage", "browser_download_url": "uni"}]
    assert u._select_asset(assets, "linux", "arm64")[0] == ""
    assert u._select_asset(assets, "linux", "arm64", prefer_appimage=True)[0] == "Waves-linux.AppImage"


def test_select_shipped_macos_names():
    # Pin the REAL CI asset names: "intel" is an amd64 token, so the Intel zip
    # matches amd64 and is skipped on arm64; the apple-silicon zip carries no
    # arch token and is picked on arm64 via the arch-agnostic fallback.
    assets = [
        {"name": "waves_macos-intel.zip", "browser_download_url": "mac-intel"},
        {"name": "waves_macos-intel.zip.sha256", "browser_download_url": "mac-intel-sha"},
        {"name": "waves_macos-apple-silicon.zip", "browser_download_url": "mac-as"},
        {"name": "waves_macos-apple-silicon.zip.sha256", "browser_download_url": "mac-as-sha"},
        {"name": "waves_linux-x64.zip", "browser_download_url": "lin-x64"},
        {"name": "waves_windows-arm64.zip", "browser_download_url": "win-arm"},
    ]
    assert u._select_asset(assets, "macos", "arm64", want_legacy=False) == (
        "waves_macos-apple-silicon.zip",
        "mac-as",
        "mac-as-sha",
    )
    assert u._select_asset(assets, "macos", "amd64", want_legacy=False) == (
        "waves_macos-intel.zip",
        "mac-intel",
        "mac-intel-sha",
    )


# The four macOS assets a dual-flavor release actually ships. The "_legacy"
# underscore is load-bearing: pre-dual updaters in the field pick by name sort,
# and ".zip" < "_legacy.zip" keeps them on the regular bundle.
_DUAL_MACOS_ASSETS = [
    {"name": "waves_macos-intel.zip", "browser_download_url": "mac-intel"},
    {"name": "waves_macos-intel.zip.sha256", "browser_download_url": "mac-intel-sha"},
    {"name": "waves_macos-intel_legacy.zip", "browser_download_url": "mac-intel-legacy"},
    {"name": "waves_macos-intel_legacy.zip.sha256", "browser_download_url": "mac-intel-legacy-sha"},
    {"name": "waves_macos-apple-silicon.zip", "browser_download_url": "mac-as"},
    {"name": "waves_macos-apple-silicon.zip.sha256", "browser_download_url": "mac-as-sha"},
    {"name": "waves_macos-apple-silicon_legacy.zip", "browser_download_url": "mac-as-legacy"},
    {"name": "waves_macos-apple-silicon_legacy.zip.sha256", "browser_download_url": "mac-as-legacy-sha"},
]


def test_select_flavor_follows_the_host():
    # A macOS 15+ machine gets the regular bundle, a 12-14 machine the legacy
    # one, on both arches; neither may ever cross over (the regular bundle is
    # dyld-killed below 15, and a silent downgrade to legacy loses Qt fixes).
    sel = u._select_asset
    assert sel(_DUAL_MACOS_ASSETS, "macos", "amd64", want_legacy=False)[0] == "waves_macos-intel.zip"
    assert sel(_DUAL_MACOS_ASSETS, "macos", "amd64", want_legacy=True) == (
        "waves_macos-intel_legacy.zip",
        "mac-intel-legacy",
        "mac-intel-legacy-sha",
    )
    assert sel(_DUAL_MACOS_ASSETS, "macos", "arm64", want_legacy=False)[0] == "waves_macos-apple-silicon.zip"
    assert sel(_DUAL_MACOS_ASSETS, "macos", "arm64", want_legacy=True)[0] == "waves_macos-apple-silicon_legacy.zip"


def test_select_legacy_host_gets_nothing_without_a_legacy_asset():
    # Fail closed: if a release ships no legacy bundle, a Monterey machine must
    # see "no build for this platform" rather than a bundle it cannot launch.
    assets = [a for a in _DUAL_MACOS_ASSETS if "legacy" not in a["name"]]
    assert u._select_asset(assets, "macos", "amd64", want_legacy=True) == ("", "", None)


def test_select_old_updater_name_sort_stays_on_regular():
    # The exact behavior of ALREADY-SHIPPED updaters (no want_legacy concept):
    # with both flavors attached, the name sort must keep resolving to the
    # regular zip. Sorting two literals written here would pin nothing: the
    # names that matter are the ones the release workflow publishes, so they
    # are read from it. Rename a leg to "macos-intel-legacy" (a hyphen sorts
    # before the dot) and every old updater in the field downgrades itself.
    workflow = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release-or-test-build.yml"
    ).read_text(encoding="utf-8")
    names = {}
    for key in (
        "OUT_NAME_FILE",
        "ASSET_EXTENSION",
        "ARCH_MACOS_X64",
        "ARCH_MACOS_X64_LEGACY",
        "ARCH_MACOS_ARM64",
        "ARCH_MACOS_ARM64_LEGACY",
    ):
        match = re.search(rf'^  {key}: "([^"]*)"$', workflow, re.M)
        assert match, f"{key} is gone from the release workflow"
        names[key] = match.group(1)

    for regular, legacy, arch in (
        ("ARCH_MACOS_X64", "ARCH_MACOS_X64_LEGACY", "amd64"),
        ("ARCH_MACOS_ARM64", "ARCH_MACOS_ARM64_LEGACY", "arm64"),
    ):
        stem, ext = names["OUT_NAME_FILE"], names["ASSET_EXTENSION"]
        regular_name = f"{stem}_{names[regular]}{ext}"
        legacy_name = f"{stem}_{names[legacy]}{ext}"
        pool = sorted([regular_name, legacy_name])
        assert pool[0] == regular_name, f"{names[legacy]} sorts first: old updaters would downgrade"
        # And the sort is only half of it. The CURRENT updater partitions on
        # the literal substring "legacy" (_select_asset), which no ordering
        # assertion can see: rename the workflow's legacy leg to
        # "macos-intel_old" and the sort above is still correct while every
        # Mac on 12, 13 and 14 is told "no build for this platform" and stops
        # updating, with CI green. So drive the real selector against the real
        # published names, both ways.
        assets = [{"name": n, "browser_download_url": n} for n in (regular_name, legacy_name)]
        assert (
            u._select_asset(assets, "macos", arch, want_legacy=True)[0] == legacy_name
        ), f"{names[legacy]} carries no token _select_asset recognises: macOS 12-14 would get nothing"
        assert (
            u._select_asset(assets, "macos", arch, want_legacy=False)[0] == regular_name
        ), f"{names[regular]} reads as a legacy asset: macOS 15+ would be downgraded"


def test_macos_wants_legacy_parses_versions(monkeypatch):
    monkeypatch.setattr(u.platform, "system", lambda: "Darwin")
    for ver, want in (("15.5", False), ("26.0", False), ("14.7.1", True), ("12.7.6", True), ("10.16", True)):
        monkeypatch.setattr(u.platform, "mac_ver", lambda v=ver: (v, ("", "", ""), ""))
        assert u._macos_wants_legacy() is want, ver
    # Unparseable output fails toward legacy, the flavor that runs everywhere.
    monkeypatch.setattr(u.platform, "mac_ver", lambda: ("", ("", "", ""), ""))
    assert u._macos_wants_legacy() is True
    # Non-macOS never asks for legacy assets.
    monkeypatch.setattr(u.platform, "system", lambda: "Linux")
    assert u._macos_wants_legacy() is False


def test_select_no_os_match():
    assert u._select_asset([{"name": "readme.txt", "browser_download_url": "x"}], "macos", "arm64") == ("", "", None)


def test_select_ignores_lone_sidecar():
    assets = [{"name": "Waves-macos-arm64.dmg.sha256", "browser_download_url": "s"}]
    assert u._select_asset(assets, "macos", "arm64") == ("", "", None)


# ---- status / configuration gates ------------------------------------------
def test_status_not_configured_when_repo_blank():
    up = AppUpdater("/tmp/x", "1.0.0", repo="")
    st = up.status()
    assert st["state"] == "not_configured" and not st["configured"] and not st["can_self_install"]


def test_status_source_when_configured_but_not_frozen(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: False)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "source" and st["configured"] and not st["can_self_install"]
    assert st["releases_url"] == "https://github.com/owner/Waves/releases"


def test_status_ready_when_frozen_and_configured(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "ready" and st["can_self_install"]


def test_update_available_no_network_when_unconfigured():
    up = AppUpdater("/tmp/x", "1.0.0", repo="")
    # Must NOT hit the network when there's no repo.
    up.latest = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network!"))
    assert up.update_available() == (False, "1.0.0", "")


def test_update_available_compares_versions():
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(version="v1.1.0", asset="a", url="u")
    assert up.update_available() == (True, "1.0.0", "1.1.0")  # bare version, no tag "v"
    up.latest = lambda *a, **k: Release(version="v1.0.0", asset="a", url="u")
    assert up.update_available() == (False, "1.0.0", "1.0.0")


# ---- install gates ----------------------------------------------------------
def test_install_blocked_when_unconfigured():
    up = AppUpdater("/tmp/x", "1.0.0", repo="")
    with pytest.raises(UpdaterError, match="aren't configured"):
        up.install()


def test_install_blocked_from_source(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: False)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    with pytest.raises(UpdaterError, match="packaged builds"):
        up.install()


# ---- mocked download → verify → stage (signed manifest; real swap stubbed) --
_ASSET = "Waves.bin"


def _manifest(payload: bytes, asset: str = _ASSET, version: str = "v2.0.0") -> bytes:
    """A signed-manifest body: the CI ``# waves-version`` line (anti-rollback) plus a
    coreutils-style SHA256SUMS line pinning ``payload``'s digest to ``asset``."""
    line = f"{hashlib.sha256(payload).hexdigest()}  {asset}\n"
    return (f"# waves-version: {version}\n{line}").encode()


def _prep(monkeypatch, tmp_path, *, payload, manifest, signature, pubkey, asset=_ASSET):
    """Wire an AppUpdater whose download writes ``payload`` and whose signed
    SHA256SUMS manifest + signature + embedded public key are as given. The
    platform swap is stubbed so a passing case records the call without touching
    the live executable."""
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "UPDATE_PUBLIC_KEY", pubkey)
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(
        version="v2.0.0",
        asset=asset,
        url="http://x/" + asset,
        sha256sums_url="http://x/SHA256SUMS",
        sig_url="http://x/SHA256SUMS.sig",
    )

    def fake_download(self, sess, url, dest, progress_cb, abort):
        with open(dest, "wb") as fh:
            fh.write(payload)
        if progress_cb:
            progress_cb(100.0)

    monkeypatch.setattr(AppUpdater, "_download", fake_download, raising=True)
    monkeypatch.setattr(AppUpdater, "_fetch_manifest", lambda self, sess, url: manifest, raising=True)
    monkeypatch.setattr(AppUpdater, "_fetch_signature", lambda self, sess, url: signature, raising=True)
    applied = {}
    monkeypatch.setattr(
        AppUpdater, "_apply", lambda self, p, rel, log, abort=None: applied.setdefault("path", p) or p, raising=True
    )
    return up, applied


def _staged(tmp_path):
    """Leftover download temp files (must be empty after any install attempt)."""
    return list((tmp_path / "updates").glob("*-" + _ASSET))


def test_install_happy_path(monkeypatch, tmp_path):
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    result = up.install(session=object())
    assert result["ok"] and result["version"] == "v2.0.0" and result["relaunch"] is True
    assert "path" in applied  # the swap was invoked with the verified payload
    assert not _staged(tmp_path)  # temp payload cleaned up


def test_install_checksum_mismatch_aborts(monkeypatch, tmp_path):
    # Signature is VALID over the manifest, but the downloaded bytes don't match the
    # hash the (authentic) manifest pins → integrity failure, abort.
    pub, priv = signing.keygen()
    manifest = _manifest(b"genuine")
    up, applied = _prep(
        monkeypatch,
        tmp_path,
        payload=b"tampered",
        manifest=manifest,
        signature=signing.sign(manifest, priv),
        pubkey=pub,
    )
    with pytest.raises(UpdaterError, match="Checksum mismatch"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_tampered_manifest_aborts(monkeypatch, tmp_path):
    # Sign the genuine manifest, then serve a manifest whose hash line was swapped:
    # the signature no longer covers these bytes → authenticity failure, abort.
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    genuine = _manifest(payload)
    sig = signing.sign(genuine, priv)
    forged = _manifest(b"attacker-payload")  # different hash, same asset name
    up, applied = _prep(monkeypatch, tmp_path, payload=payload, manifest=forged, signature=sig, pubkey=pub)
    with pytest.raises(UpdaterError, match="signature is invalid"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_bad_signature_aborts(monkeypatch, tmp_path):
    pub, _ = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature="not-a-valid-signature", pubkey=pub
    )
    with pytest.raises(UpdaterError, match="signature is invalid"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_wrong_key_aborts(monkeypatch, tmp_path):
    # Signed with key A but the binary embeds key B's public key → reject.
    _, priv_a = signing.keygen()
    pub_b, _ = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch,
        tmp_path,
        payload=payload,
        manifest=manifest,
        signature=signing.sign(manifest, priv_a),
        pubkey=pub_b,
    )
    with pytest.raises(UpdaterError, match="signature is invalid"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_missing_signature_aborts(monkeypatch, tmp_path):
    # Manifest present and hash correct, but the signature couldn't be fetched
    # (missing/404). Fail-closed: no signature → never install.
    pub, _priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=None, pubkey=pub)
    with pytest.raises(UpdaterError, match="could not fetch"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_unconfigured_key_aborts(monkeypatch, tmp_path):
    # An otherwise-valid signed update must still refuse when this build ships no
    # public key (the dormant UPDATE_PUBLIC_KEY="" default).
    _pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=""
    )
    with pytest.raises(UpdaterError, match="no update-signing key"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_downgrade_refused(monkeypatch, tmp_path):
    # Anti-rollback: a perfectly-signed manifest for an OLDER version than the one
    # installed (current is 1.0.0) must be refused, so a replayed old release can't
    # roll the user back to a build with known holes.
    pub, priv = signing.keygen()
    payload = b"old-waves-binary"
    manifest = _manifest(payload, version="v0.5.0")
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    with pytest.raises(UpdaterError, match="downgrade protection"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_missing_version_line_refused(monkeypatch, tmp_path):
    # A signed manifest lacking the version line is refused (fail-closed): the
    # downgrade protection must rest on an authenticated version being present.
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = f"{hashlib.sha256(payload).hexdigest()}  {_ASSET}\n".encode()  # no "# waves-version"
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    with pytest.raises(UpdaterError, match="no version line"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_asset_not_in_manifest_aborts(monkeypatch, tmp_path):
    # Valid signature, but our asset isn't listed in the signed manifest → abort.
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload, asset="SomethingElse.zip")
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    with pytest.raises(UpdaterError, match="not in the signed manifest"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


# ---- extraction: symlink-preserving + escape-rejecting ----------------------
def _zip_with(members):
    """members: list of (name, data, mode); mode's file-type bits choose file vs symlink."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data, mode in members:
            zi = zipfile.ZipInfo(name)
            zi.external_attr = mode << 16
            zf.writestr(zi, data)
    return buf.getvalue()


def test_safe_extractall_preserves_symlinks_and_exec_bit(tmp_path):
    # A macOS .app relies on framework symlinks (Versions/Current -> A); plain
    # zipfile.extractall would flatten them into broken files. The exec bit on the
    # main binary must also survive, or the swapped bundle won't launch.
    zp = tmp_path / "a.zip"
    zp.write_bytes(
        _zip_with(
            [
                ("waves.app/Contents/MacOS/Waves", b"BINARY", 0o100755),  # regular, rwxr-xr-x
                ("waves.app/Contents/Frameworks/Foo.framework/Versions/Current", b"A", 0o120755),  # symlink
            ]
        )
    )
    out = tmp_path / "out"
    with zipfile.ZipFile(zp) as zf:
        AppUpdater._safe_extractall(zf, out)
    exe = out / "waves.app/Contents/MacOS/Waves"
    link = out / "waves.app/Contents/Frameworks/Foo.framework/Versions/Current"
    assert exe.read_bytes() == b"BINARY" and os.access(exe, os.X_OK)
    assert link.is_symlink() and os.readlink(link) == "A"


def test_safe_extractall_rejects_path_traversal(tmp_path):
    zp = tmp_path / "e.zip"
    zp.write_bytes(_zip_with([("../escape.txt", b"x", 0o100644)]))
    out = tmp_path / "out"
    with zipfile.ZipFile(zp) as zf, pytest.raises(UpdaterError, match="unsafe archive member"):
        AppUpdater._safe_extractall(zf, out)


def test_safe_extractall_rejects_escaping_symlink(tmp_path):
    zp = tmp_path / "s.zip"
    zp.write_bytes(_zip_with([("evil", b"/etc/passwd", 0o120755)]))  # absolute symlink target
    out = tmp_path / "out"
    with zipfile.ZipFile(zp) as zf, pytest.raises(UpdaterError, match="unsafe symlink"):
        AppUpdater._safe_extractall(zf, out)


# ---- apply: Linux/Windows swap the whole standalone .dist tree --------------
def test_apply_unix_tree_swaps_whole_directory(tmp_path):
    # Nuitka --standalone ships a multi-file tree; the new binary must run against
    # its OWN bundled libs, so the entire install dir is replaced, not just the exe.
    install_root = tmp_path / "waves.dist"
    install_root.mkdir()
    (install_root / "Waves").write_bytes(b"OLD-EXE")
    (install_root / "libQt6Core.so.6").write_bytes(b"OLD-LIB")
    target = install_root / "Waves"

    new_tree = tmp_path / "staged" / "waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves").write_bytes(b"NEW-EXE")
    (new_tree / "libQt6Core.so.6").write_bytes(b"NEW-LIB")

    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key = "linux"
    assert up._apply_unix_tree(new_tree, target, lambda *a: None) == target
    assert (install_root / "Waves").read_bytes() == b"NEW-EXE"
    assert (install_root / "libQt6Core.so.6").read_bytes() == b"NEW-LIB"  # the lib was swapped too
    assert os.access(install_root / "Waves", os.X_OK)


# ---- managed install channels (package-manager-owned copies) -----------------
@pytest.fixture
def _no_channel_env(monkeypatch):
    """Neutralize every channel signal so each test enables exactly one."""
    for var in ("SNAP", "FLATPAK_ID", "APPIMAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(u, "_current_exe", lambda: __import__("pathlib").Path("/Applications/waves.app/x"))
    monkeypatch.setattr(u.os.path, "exists", lambda p: False if p == "/.flatpak-info" else os.path.exists(p))


def _point_config_at(monkeypatch, tmp_path):
    import waves.helper.path as path_helper

    monkeypatch.setattr(path_helper, "path_config_base", lambda: str(tmp_path))


def test_managed_channel_empty_by_default(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    assert u.managed_channel() == ""


def test_managed_channel_from_container_env(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    monkeypatch.setenv("SNAP", "/snap/waves/1")
    assert u.managed_channel() == "snap"
    monkeypatch.delenv("SNAP")
    monkeypatch.setenv("FLATPAK_ID", "app.waves.Waves")
    assert u.managed_channel() == "flatpak"


def test_appimage_is_not_a_managed_channel(_no_channel_env, monkeypatch, tmp_path):
    # An AppImage self-updates (the updater swaps the $APPIMAGE file), so it
    # must NOT read as managed, which would down-rank it to notify-only.
    _point_config_at(monkeypatch, tmp_path)
    monkeypatch.setenv("APPIMAGE", "/home/u/Waves.AppImage")
    assert u.managed_channel() == ""


def test_managed_channel_from_scoop_path(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    monkeypatch.setattr(
        u, "_current_exe", lambda: __import__("pathlib").Path("C:/Users/u/scoop/apps/waves/current/Waves.exe")
    )
    assert u.managed_channel() == "scoop"


def test_managed_channel_from_sentinel_file(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    (tmp_path / "install_channel").write_text("homebrew-cask\n")
    assert u.managed_channel() == "homebrew-cask"


def test_sentinel_content_is_sanitized(_no_channel_env, monkeypatch, tmp_path):
    # The sentinel is a plain file anyone could edit; it must never inject
    # markup/whitespace into UI strings. One lowercase token, bounded length.
    _point_config_at(monkeypatch, tmp_path)
    (tmp_path / "install_channel").write_text("  HomeBrew-Cask extra words <b>x</b>\n")
    assert u.managed_channel() == "homebrew-cask"
    (tmp_path / "install_channel").write_text("   \n")
    assert u.managed_channel() == ""


def test_status_managed_when_channel_owns_install(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "managed"
    assert st["can_self_install"] is False
    assert st["channel"] == "homebrew-cask"
    assert st["channel_label"] == "Homebrew"
    assert st["update_hint"] == "brew upgrade --cask waves"


def test_status_ready_reports_no_channel(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "ready" and st["can_self_install"]
    assert st["channel"] == "" and st["channel_label"] == "" and st["update_hint"] == ""


def test_source_state_wins_over_managed(monkeypatch):
    # A dev running from source on a machine that also has a brew install:
    # "source" is the truer state (self-install impossible either way).
    monkeypatch.setattr(u, "is_frozen", lambda: False)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    assert up.status()["state"] == "source"


def test_install_blocked_on_managed_channel_without_manager_binary(monkeypatch):
    # Homebrew-managed but brew itself is gone: nothing runnable, so the old
    # refusal with the manual hint is what the user gets.
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    with pytest.raises(UpdaterError, match=r"managed by Homebrew.*brew upgrade --cask waves"):
        up.install()


def test_install_blocked_on_unknown_channel_without_hint(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "nixpkgs")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    with pytest.raises(UpdaterError, match=r"managed by nixpkgs"):
        up.install()


# ---- managed upgrade runner (the manager's own command does the update) ------
class _FakeProc:
    """subprocess.Popen stand-in that streams canned output lines."""

    def __init__(self, lines, code=0, hang_after=None, on_terminate=None):
        import io

        self._lines = lines
        self._code = code
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.terminated = False
        self._on_terminate = on_terminate

    def wait(self):
        return self._code

    def poll(self):
        return self._code

    def terminate(self):
        self.terminated = True
        if self._on_terminate:
            self._on_terminate()


def _managed_up(monkeypatch, proc, latest=None):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: proc)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = (lambda *a, **k: latest) if latest is not None else (lambda *a, **k: None)
    return up


def test_managed_upgrade_runs_brew_and_reports_done(monkeypatch):
    argv_seen = {}
    proc = _FakeProc(["==> Downloading waves", "==> Upgrading waves 1.0.0 -> 1.1.0", "🍺  waves was upgraded"])
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")

    def fake_popen(argv, **kw):
        argv_seen["argv"] = argv
        return proc

    monkeypatch.setattr(u.subprocess, "Popen", fake_popen)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(version="v1.1.0", asset="a", url="u")

    pcts, logs = [], []
    result = up.install(progress_cb=pcts.append, log_cb=logs.append)

    assert argv_seen["argv"] == ["/opt/homebrew/bin/brew", "upgrade", "--cask", "iamprivacy/waves/waves"]
    assert result["ok"] is True and result["version"] == "v1.1.0" and result["relaunch"] is True
    assert pcts[-1] == 100.0
    assert any("Upgrading" in m for m in logs), "the manager's output reaches the UI"


def test_managed_upgrade_failure_surfaces_output_tail(monkeypatch):
    up = _managed_up(monkeypatch, _FakeProc(["Error: some cask problem"], code=1))
    with pytest.raises(UpdaterError, match=r"Homebrew reported an error[\s\S]*some cask problem"):
        up.install()


def test_managed_upgrade_stale_tap_is_a_clear_error(monkeypatch):
    # brew exits 0 but did nothing: its tap clone hasn't seen the release yet.
    up = _managed_up(monkeypatch, _FakeProc(["Warning: waves already installed, it's already up-to-date"], code=0))
    with pytest.raises(UpdaterError, match=r"does not see the new version"):
        up.install()


def test_managed_upgrade_abort_cancels(monkeypatch):
    from threading import Event as _Event

    abort = _Event()
    abort.set()  # cancelled before/while output streams
    proc = _FakeProc(["==> Downloading waves"], code=1)
    up = _managed_up(monkeypatch, proc)
    with pytest.raises(u.UpdateCancelled):
        up.install(abort=abort)


def test_status_can_managed_install_tracks_brew_presence(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "managed" and st["can_managed_install"] is True and st["can_self_install"] is False
    monkeypatch.setattr(u, "_find_brew", lambda: "")
    assert up.status()["can_managed_install"] is False


def test_status_snap_has_no_managed_install(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "snap")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    assert up.status()["can_managed_install"] is False


# ---- AppImage: format follows install -----------------------------------------
def _assets(*names):
    return [{"name": n, "browser_download_url": "http://x/" + n} for n in names]


def test_select_asset_zip_install_never_gets_an_appimage():
    # ".AppImage" sorts before ".zip" alphabetically; without hard partitioning
    # every zip user would silently be switched to an AppImage payload.
    name, _url, _ = u._select_asset(_assets("waves_linux-x64.AppImage", "waves_linux-x64.zip"), "linux", "amd64")
    assert name == "waves_linux-x64.zip"


def test_select_asset_appimage_install_gets_only_appimage():
    name, _url, sha = u._select_asset(
        _assets("waves_linux-x64.AppImage", "waves_linux-x64.AppImage.sha256", "waves_linux-x64.zip"),
        "linux",
        "amd64",
        prefer_appimage=True,
    )
    assert name == "waves_linux-x64.AppImage"
    assert sha == "http://x/waves_linux-x64.AppImage.sha256"


def test_select_asset_appimage_missing_yields_nothing():
    # An old release without AppImage assets: better no update than a zip tree
    # smeared over a single file.
    name, url, _ = u._select_asset(_assets("waves_linux-x64.zip"), "linux", "amd64", prefer_appimage=True)
    assert name == "" and url == ""


def test_apply_targets_the_appimage_file(monkeypatch, tmp_path):
    appimage_path = tmp_path / "Waves.AppImage"
    appimage_path.write_bytes(b"OLD")
    monkeypatch.setenv("APPIMAGE", str(appimage_path))
    # The AppImage claim is only honoured when this process runs out of the
    # advertised mount (see _running_appimage), so point APPDIR at ourselves.
    monkeypatch.setenv("APPDIR", str(pathlib.Path(sys.executable).parent))
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key = "linux"
    payload = tmp_path / "dl.bin"
    payload.write_bytes(b"NEW-APPIMAGE")
    rel = Release(version="v2.0.0", asset="waves_linux-x64.AppImage", url="http://x")
    applied = up._apply(payload, rel, lambda *a: None)
    assert applied == appimage_path
    assert appimage_path.read_bytes() == b"NEW-APPIMAGE"
    assert os.access(appimage_path, os.X_OK), "the swapped AppImage must stay executable"


def test_apply_without_appimage_env_untouched(monkeypatch, tmp_path):
    # Sanity: no $APPIMAGE → the normal exe-relative path is used (covered in
    # depth by the tree/single-file apply tests above).
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert u._running_appimage() == ""


def test_managed_channel_from_app_dir_sentinel(_no_channel_env, monkeypatch, tmp_path):
    # System packages (AUR) plant the sentinel next to the binary, where a
    # root-owned install CAN write and per-user config CANNOT.
    _point_config_at(monkeypatch, tmp_path / "config-without-sentinel")
    exe_dir = tmp_path / "opt" / "waves"
    exe_dir.mkdir(parents=True)
    (exe_dir / "install_channel").write_text("aur\n")
    monkeypatch.setattr(u, "_current_exe", lambda: exe_dir / "Waves")
    assert u.managed_channel() == "aur"
