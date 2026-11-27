"""Lyrics through an installed Lavender (waves/lyrics_lavender.py).

A stand-in worker script speaks Lavender's headless protocol, so these tests
never need Lavender itself.
"""

from __future__ import annotations

import sys
import textwrap
import threading
import time
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import waves.download as download_mod
import waves.lyrics_lavender as lav
from waves.download import Download
from waves.lyrics import lyrics_file_choice
from waves.model.cfg import Settings

FAKE_WORKER = textwrap.dedent(r"""
    import json, sys, time, pathlib
    mode = sys.argv[1]
    counter = pathlib.Path(sys.argv[2])
    starts = int(counter.read_text() or 0) + 1 if counter.exists() else 1
    counter.write_text(str(starts))
    assert sys.argv[-1] == "--waves-lyrics-worker"
    if mode == "old" or (mode == "slow_start" and starts == 1):
        time.sleep(30)  # an old Lavender opens its window and never says hello
        sys.exit(0)
    if mode == "late_hello":
        time.sleep(1)
    version = "/Users/someone/Lavender" if mode == "odd" else "9.9.9"
    print(json.dumps({"protocol": 2 if mode == "future" else 1, "app": "Lavender", "version": version}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        if mode == "crash_first" and starts == 1:
            sys.exit(3)
        if mode == "slow":
            time.sleep(5)
        reply = {"id": req["id"], "status": "not_found", "synced": "", "plain": "",
                 "instrumental": False, "word_synced": False, "settings": "lavender"}
        title = req["title"]
        if title == "Song (Live)":
            pass
        elif title in ("Song", "Bare"):
            reply.update(status="found", synced="[00:01.00] hi", plain="hi")
        elif title == "Words":
            reply.update(status="found", synced="[00:01.00] hi",
                         synced_enhanced="[00:01.00] <00:01.00> hi", plain="hi", word_synced=True)
        elif title == "Plain":
            reply.update(status="found", plain="just text")
        elif title == "Instrumental":
            reply.update(status="found", instrumental=True)
        elif title == "Broken":
            reply.update(status="error", error="local_db_unavailable")
        print(json.dumps(reply), flush=True)
    """)


@pytest.fixture
def worker(tmp_path):
    script = tmp_path / "fake_lavender.py"
    script.write_text(FAKE_WORKER)
    counter = tmp_path / "starts"
    clients: list[lav.LavenderClient] = []

    def make(mode: str = "normal") -> lav.LavenderClient:
        client = lav.LavenderClient([sys.executable, str(script), mode, str(counter)])
        clients.append(client)
        return client

    make.starts = lambda: int(counter.read_text()) if counter.exists() else 0
    yield make
    for client in clients:
        client.close()


def test_found_reply_carries_timed_and_plain_lyrics(worker):
    client = worker()
    answer = client.lookup("Artist", "Song", "Album", 200)
    assert answer.found and answer.synced == "[00:01.00] hi" and answer.plain == "hi"
    assert client.version() == "9.9.9"


def test_word_timed_lyrics_arrive_as_enhanced_lrc_when_lavender_exports_them(worker):
    answer = worker().lookup("Artist", "Words", "Album", 200)
    assert answer.synced == "[00:01.00] <00:01.00> hi"


def test_a_miss_retries_with_the_bare_title(worker):
    client = worker()
    answer = lav.fetch_lavender_lyrics("Artist", "Song (Live)", "Album", 200, title_bare="Bare", client=client)
    assert answer.found and answer.synced == "[00:01.00] hi"


def test_a_miss_on_both_titles_is_not_found(worker):
    client = worker()
    answer = lav.fetch_lavender_lyrics("Artist", "Song (Live)", "Album", 200, title_bare="Song (Live)", client=client)
    assert answer.status == "not_found"


def test_an_error_reply_reads_as_unavailable(worker):
    assert worker().lookup("Artist", "Broken", "Album", 200).status == "unavailable"


def test_missing_signature_fields_never_start_the_worker(worker):
    client = worker()
    assert client.lookup("", "Song", "Album", 200).status == "unavailable"
    assert client.lookup("Artist", "Song", "Album", 0).status == "unavailable"
    assert worker.starts() == 0


def test_one_worker_serves_many_requests(worker):
    client = worker()
    for _ in range(3):
        assert client.lookup("Artist", "Song", "Album", 200).found
    assert worker.starts() == 1


def test_a_crashed_worker_is_restarted_once(worker):
    client = worker("crash_first")
    assert client.lookup("Artist", "Song", "Album", 200).found
    assert worker.starts() == 2


def test_an_old_lavender_without_the_worker_is_closed_and_skipped(worker, monkeypatch):
    monkeypatch.setattr(lav, "_HELLO_TIMEOUT_SEC", 0.5)
    client = worker("old")
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    # It is not started again for every track.
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    assert worker.starts() == 1


def test_an_old_lavender_reads_as_old_after_two_silent_starts_until_its_files_change(worker, monkeypatch):
    monkeypatch.setattr(lav, "_HELLO_TIMEOUT_SEC", 0.5)
    monkeypatch.setattr(lav, "_RETRY_AFTER_SEC", 0.0)
    client = worker("old")
    assert client.state() == "ok"
    client.lookup("Artist", "Song", "Album", 200)
    # Silent once can be only slow: not called old yet.
    assert client.state() == "ok"
    client.lookup("Artist", "Song", "Album", 200)
    assert client.state() == "old" and worker.starts() == 2
    # Old: never started again for this install.
    client.lookup("Artist", "Song", "Album", 200)
    assert worker.starts() == 2
    # An update (new files) is tried again without restarting Waves.
    monkeypatch.setattr(lav, "_stamp", lambda _command: 123.0)
    assert client.state() == "ok"
    client.lookup("Artist", "Song", "Album", 200)
    client.lookup("Artist", "Song", "Album", 200)
    assert worker.starts() == 4 and client.state() == "old"


def test_a_slow_first_start_is_not_called_old(worker, monkeypatch):
    # A freshly downloaded app can take a while to start the first time
    # (macOS checks it), so it says nothing within the hello wait.
    monkeypatch.setattr(lav, "_HELLO_TIMEOUT_SEC", 0.5)
    monkeypatch.setattr(lav, "_RETRY_AFTER_SEC", 0.0)
    client = worker("slow_start")
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    assert client.state() == "ok"
    assert client.lookup("Artist", "Song", "Album", 200).found
    assert worker.starts() == 2 and client.state() == "ok"


def test_a_busy_worker_does_not_hold_a_track_longer_than_one_request(worker, monkeypatch):
    monkeypatch.setattr(lav, "_REQUEST_TIMEOUT_SEC", 0.3)
    client = worker()
    client._lock.acquire()  # another download's request is in flight
    try:
        assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    finally:
        client._lock.release()
    assert client.lookup("Artist", "Song", "Album", 200).found


def test_a_newer_protocol_is_not_spoken(worker):
    client = worker("future")
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    # Not started per track, and not called old: it is Waves that is behind.
    assert worker.starts() == 1 and client.state() == "ok"


def test_a_slow_answer_times_out(worker, monkeypatch):
    monkeypatch.setattr(lav, "_REQUEST_TIMEOUT_SEC", 0.3)
    client = worker("slow")
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    # Alive but silent: not asked twice, and the next track does not wait on it.
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"
    assert worker.starts() == 1


def test_closing_never_waits_for_a_lookup_in_flight(worker):
    """The app's quit: the lookup ends as unavailable, and no new worker is started."""
    client = worker("slow")
    answers: list[str] = []
    thread = threading.Thread(target=lambda: answers.append(client.lookup("Artist", "Song", "Album", 200).status))
    thread.start()
    while worker.starts() == 0 or not client._lock.locked():
        time.sleep(0.05)
    time.sleep(0.3)  # the request is with the worker
    began = time.monotonic()
    client.close()
    thread.join(4)
    assert answers == ["unavailable"] and time.monotonic() - began < 4
    assert worker.starts() == 1
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"


def test_a_quit_during_the_hello_leaves_no_worker_running(worker):
    client = worker("late_hello")
    answers: list[str] = []
    thread = threading.Thread(target=lambda: answers.append(client.lookup("Artist", "Song", "Album", 200).status))
    thread.start()
    while worker.starts() == 0:
        time.sleep(0.05)
    client.close()  # before the worker's hello: close() has no worker to stop yet
    thread.join(5)
    assert answers == ["unavailable"] and client._worker is None


def test_a_lookup_after_the_quit_starts_no_lavender(worker, monkeypatch):
    started = worker()
    monkeypatch.setattr(lav, "_shared", None)
    monkeypatch.setattr(lav, "_candidates", lambda: [started._command])
    lav.close_shared()  # the app's quit, before any download asked Lavender
    assert lav.fetch_lavender_lyrics("Artist", "Song", "Album", 200).status == "unavailable"
    assert worker.starts() == 0


def test_parallel_downloads_share_one_worker(worker):
    client = worker()
    results: list[bool] = []
    threads = [
        threading.Thread(target=lambda: results.append(client.lookup("Artist", "Song", "Album", 200).found))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [True] * 8
    assert worker.starts() == 1


def test_not_installed_is_unavailable(monkeypatch):
    monkeypatch.setattr(lav, "_candidates", lambda: [])
    client = lav.LavenderClient()
    assert not client.installed()
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"


def test_an_install_made_while_waves_runs_is_noticed(monkeypatch, tmp_path):
    found: list[list[str]] = []
    monkeypatch.setattr(lav, "_candidates", lambda: found)
    client = lav.LavenderClient()
    assert client.state() == "missing"
    found.append([str(tmp_path / "Lavender")])
    assert client.installed() and client.state() == "ok"
    found.clear()
    assert client.state() == "missing"


def test_a_flatpak_install_is_found_by_its_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    if not lav.Path("/var/lib/flatpak/app", lav.APP_ID).is_dir():
        assert lav._flatpak_dir() is None
    active = tmp_path / "flatpak" / "app" / lav.APP_ID / "current" / "active"
    active.mkdir(parents=True)
    assert lav._flatpak_dir() is not None
    if lav._flatpak_dir() == tmp_path / "flatpak" / "app" / lav.APP_ID:
        assert lav._stamp(["/usr/bin/flatpak", "run"]) == active.stat().st_mtime


def test_only_lavenders_known_codes_reach_the_log(worker, caplog):
    caplog.set_level("INFO", logger="waves.lyrics.lavender")
    leak = "/Users/someone/dump.sqlite3"
    assert lav._answer({"status": "error", "error": leak}, "label").status == "unavailable"
    assert lav._answer({"status": "not_found", "warning": leak}, "label").warning == "other"
    assert lav._answer({"status": "error", "error": "local_db_unavailable"}, "label").status == "unavailable"
    client = worker("odd")
    assert client.lookup("Artist", "Song", "Album", 200).found and client.version() == ""
    assert "someone" not in caplog.text and "local_db_unavailable" in caplog.text


def test_a_missing_binary_is_unavailable(tmp_path):
    client = lav.LavenderClient([str(tmp_path / "nope")])
    assert client.lookup("Artist", "Song", "Album", 200).status == "unavailable"


def test_lavender_answers_map_to_sidecar_files(worker):
    client = worker()
    timed = client.lookup("Artist", "Song", "Album", 200)
    assert lyrics_file_choice(timed.synced, timed.plain, False) == ("[00:01.00] hi", ".lrc")
    plain = client.lookup("Artist", "Plain", "Album", 200)
    assert lyrics_file_choice(plain.synced, plain.plain, False) == ("just text", ".txt")
    assert lyrics_file_choice(plain.synced, plain.plain, True) == ("", "")


def test_setting_defaults_off():
    assert Settings().lyrics_use_lavender is False


# The download's lyrics step


def _lyrics_download(monkeypatch, *, lavender, lrclib=("", ""), tidal=None, use_lavender=True):
    dl = Download.__new__(Download)
    dl.fn_logger = MagicMock()
    dl._names_reserved_lock = Lock()
    dl.settings = SimpleNamespace(data=SimpleNamespace(lyrics_prefer_lrclib=True, lyrics_use_lavender=use_lavender))
    dl._shared_http = lambda: None
    asked: list[str] = []

    def fake_lrclib(*_a, **_kw):
        asked.append("lrclib")
        return lrclib

    def fake_lavender(**_kw):
        asked.append("lavender")
        return lavender

    def fake_tidal():
        asked.append("tidal")
        if tidal is None:
            raise RuntimeError("404")
        return tidal

    monkeypatch.setattr(download_mod, "fetch_lrclib_lyrics", fake_lrclib)
    monkeypatch.setattr(download_mod, "fetch_lavender_lyrics", fake_lavender)
    track = SimpleNamespace(
        artist=None, album=None, duration=200, name="Song", full_name="Song", artists=[], lyrics=fake_tidal
    )
    return dl, track, asked


def test_lavender_timed_lyrics_skip_lrclib_and_tidal(monkeypatch):
    dl, track, asked = _lyrics_download(
        monkeypatch, lavender=lav.LavenderAnswer("found", synced="[00:01.00] lav", plain="lav")
    )
    assert dl._retrieve_lyrics(track) == ("[00:01.00] lav", "[00:01.00] lav", "lav")
    assert asked == ["lavender"]


def test_a_lavender_miss_falls_back_to_tidal_not_lrclib(monkeypatch):
    dl, track, asked = _lyrics_download(
        monkeypatch,
        lavender=lav.LavenderAnswer("not_found"),
        tidal=SimpleNamespace(text="tidal", subtitles="[00:02.00] tidal"),
    )
    assert dl._retrieve_lyrics(track) == ("[00:02.00] tidal", "[00:02.00] tidal", "tidal")
    assert asked == ["lavender", "tidal"]


def test_an_instrumental_from_lavender_writes_no_lyrics(monkeypatch):
    dl, track, asked = _lyrics_download(monkeypatch, lavender=lav.LavenderAnswer("found", instrumental=True))
    assert dl._retrieve_lyrics(track) == ("", "", "")
    assert asked == ["lavender"]


def test_an_unavailable_lavender_hands_over_to_lrclib(monkeypatch):
    dl, track, asked = _lyrics_download(monkeypatch, lavender=lav.UNAVAILABLE, lrclib=("[00:03.00] lrclib", "lrclib"))
    assert dl._retrieve_lyrics(track) == ("[00:03.00] lrclib", "[00:03.00] lrclib", "lrclib")
    assert asked == ["lavender", "lrclib"]


def test_lavender_is_not_asked_when_the_setting_is_off(monkeypatch):
    dl, track, asked = _lyrics_download(
        monkeypatch, lavender=lav.UNAVAILABLE, lrclib=("[00:03.00] lrclib", "lrclib"), use_lavender=False
    )
    dl._retrieve_lyrics(track)
    assert asked == ["lrclib"]


def test_lavender_is_not_asked_when_lrclib_is_not_preferred(monkeypatch):
    dl, track, asked = _lyrics_download(
        monkeypatch, lavender=lav.UNAVAILABLE, tidal=SimpleNamespace(text="t", subtitles="")
    )
    dl.settings.data.lyrics_prefer_lrclib = False
    dl._retrieve_lyrics(track)
    assert asked == ["tidal"]
