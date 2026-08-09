"""A click that is parked behind a prerequisite must not look like a download.

Three entry points answer a click before anything is queued: a metadata
re-fetch (the object aged out of the browse registry), a playlist-folder-tree
warm (``{folder_path}`` would resolve blind), and the best-of-both edition
scan. All three used to publish "running" for the immediate button feedback,
which drew the progress bar: a dot matrix at 0% for a download that had not
started, torn down again a moment later when ``_download`` published "queued".
Clicking download on a Browse playlist showed the bar flash and snap to the
queued pill.

They publish "preparing" instead, which the buttons draw exactly like queued,
so the hand-over to a real queue row is the cancel X arriving and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from tidaler.waves_ui.backend import WavesBridge

QML = Path(__file__).resolve().parents[1] / "tidaler" / "waves_ui" / "qml" / "Main.qml"


class _Emit:
    def __init__(self):
        self.calls: list = []

    def emit(self, *args):
        self.calls.append(args)


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


class _RefetchStub:
    _refetch_for_download = WavesBridge._refetch_for_download

    def __init__(self):
        self._refetch_inflight: set = set()
        self._logged_in = True
        self._browse_gen = 0
        self.downloadState = _Emit()
        self.statuses: list = []
        self.threadpool = SimpleNamespace(start=lambda w: None)

    def _set_status(self, text):
        self.statuses.append(text)


def test_a_refetch_never_lights_the_progress_bar():
    stub = _RefetchStub()
    stub._refetch_for_download("album", "alb-9")

    assert stub.downloadState.calls == [("alb-9", "preparing")]


class _WarmStub:
    _warm_folder_tree = WavesBridge._warm_folder_tree
    _on_folder_tree_warmed = WavesBridge._on_folder_tree_warmed
    _current_folder_tree = WavesBridge._current_folder_tree
    downloadPlaylist = WavesBridge.downloadPlaylist
    _playlist_template = WavesBridge._playlist_template
    _needs_folder_tree = WavesBridge._needs_folder_tree

    def __init__(self):
        self._folder_tree = None
        self._media_lists_lock = Lock()
        self._logged_in = True
        self._tree_warm_waiting: list = []
        self._tree_warm_inflight = False
        # Parks the sweep instead of running it, so the button state published
        # for the wait is the only thing under test.
        self.threadpool = SimpleNamespace(start=lambda w: None)
        self.downloadState = _Emit()
        self._objs = {"playlist": {"p1": SimpleNamespace(id="p1", name="Road Songs")}}
        self.settings = SimpleNamespace(data=SimpleNamespace(format_playlist="Playlists/{folder_path}{playlist_name}"))

    def _set_busy(self, on):
        pass


def test_a_playlist_waiting_on_the_folder_sweep_never_lights_the_progress_bar():
    stub = _WarmStub()
    WavesBridge.downloadPlaylist(stub, "p1")

    assert stub.downloadState.calls == [("p1", "preparing")]


def test_every_pre_queue_hand_off_uses_the_same_word():
    """No entry point may go back to "running" for a click it has not queued."""
    src = (Path(__file__).resolve().parents[1] / "tidaler" / "waves_ui" / "backend.py").read_text()
    # The three pre-queue acknowledgements, each immediately before a return or
    # a worker dispatch. Any of them saying "running" is the progress-bar flash.
    assert src.count('"preparing")') == 4, "refetch, playlist warm, category warm, edition scan"


def test_the_buttons_draw_preparing_as_a_wait_not_a_download():
    src = QML.read_text()
    # Each of the three surfaces that shows a download state derives one flag,
    # so a state that is not yet queued can never fall through to the idle or
    # the running arm.
    assert 'st === "queued" || st === "preparing"' in src
    assert 'di.st === "queued" || di.st === "preparing"' in src
    assert 'bc.dlSt === "queued" || bc.dlSt === "preparing"' in src
    # The dot matrix stays pinned to a real download.
    assert re.search(r'active:\s*db\.st === "running"', src)


def test_only_a_real_queue_row_can_be_cancelled():
    """The X keeps its space while preparing (so the label does not shift when
    the row lands) but is invisible and inert: there is nothing to cancel yet,
    and a press that silently does nothing is worse than no X at all."""
    src = QML.read_text()
    body = src.split("component DownloadButton", 1)[1].split("component FolderTile", 1)[0]
    assert 'opacity: db.st === "queued" ? 1 : 0' in body
    assert 'enabled: db.st === "queued"' in body
