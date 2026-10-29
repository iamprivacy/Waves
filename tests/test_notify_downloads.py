"""The download and update banners behind the Notifications settings section.

Every queue row that ends on the GUI thread is counted by _notify_job_ended;
what becomes a banner is decided only when a banner is due, from the prefs as
they stand then. The master switch (notify_enabled, off by default) gates
every banner at _notify_send, and the settings save that turns it on sends
one test banner (the off-to-on edge only). "every" coalesces the rows that
ended over the last half minute into one banner, "queue" waits for the whole
queue to run dry, "never" sends nothing, and the failure switch rides across
all three: failures are named while it is on and silent while it is off. The
update banner fires once per version the automatic check finds, never for a
manual check.

Qt-free and hermetic like test_releases_backend.py: the real, unbound bridge
methods are bound onto a minimal _Stub carrying only what they touch.
"""

from __future__ import annotations

import logging

from conftest import _InlinePool

from waves.waves_ui.backend import WavesBridge, _downloads_banner, _queue_banner


class _FakeNotifier:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    def send(self, title, message):
        self.sent.append((title, message))
        return self.ok


class _Timer:
    def __init__(self):
        self._active = False
        self.starts: list = []

    def isActive(self):
        return self._active

    def start(self, ms=None):
        self._active = True
        self.starts.append(ms)

    def stop(self):
        self._active = False


class _Stub:
    pass


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, _Stub)


def _stub(prefs=None):
    s = _Stub()
    s._waves_prefs = {
        # Armed: these tests are about what an armed Waves sends. The
        # master-switch tests below flip it themselves.
        "notify_enabled": True,
        "notify_downloads": "queue",
        "notify_failed": True,
        "notify_app_update": True,
        "notify_in_front": True,
        "notify_update_seen": "",
        **(prefs or {}),
    }
    s._waves_pref_bool = _bind(s, "_waves_pref_bool")
    # Waves in front, the case notify_in_front is about.
    s._notify_waves_in_front = lambda: True
    s._notifier = _FakeNotifier()
    s._notify_pool = _InlinePool()
    s._notify_send = _bind(s, "_notify_send")
    s._notify_job_ended = _bind(s, "_notify_job_ended")
    s._on_dl_notify = _bind(s, "_on_dl_notify")
    s._notify_queue_idle = _bind(s, "_notify_queue_idle")
    s._notify_app_update = _bind(s, "_notify_app_update")
    s._on_update_banner_sent = _bind(s, "_on_update_banner_sent")
    s._notify_forget_retried = _bind(s, "_notify_forget_retried")
    s._notify_forget_run = _bind(s, "_notify_forget_run")
    s._maybe_notify_idle = _bind(s, "_maybe_notify_idle")
    # The pool runs inline here, so the hop back to the GUI thread is a call.
    s._emit_from_worker = lambda name, *args: {"_updateBannerSent": s._on_update_banner_sent}[name](*args)
    s._dl_notify_done = []
    s._dl_notify_failed = []
    s._dl_notify_done_n = 0
    s._dl_notify_failed_n = 0
    s._dl_queue_done = 0
    s._dl_queue_failed = 0
    s._dl_queue_failed_qids = set()
    # The idle test's inputs, all idle.
    s._running_qid = None
    s._paused = False
    s._scans_in_flight = 0
    s._pending_downloads = []
    s._dl_notify_timer = _Timer()
    s._save_waves_prefs = lambda: None
    return s


def _end(s, name, status, qid=None):
    s._notify_job_ended({"name": name, "status": status, "qid": qid})


# ----- the pure wording ---------------------------------------------------- #


def test_downloads_banner_wording():
    assert _downloads_banner([], []) == ("", "")
    assert _downloads_banner(["Album A"], []) == ("Download finished", "Album A")
    assert _downloads_banner(["A", "B"], []) == ("2 downloads finished", "A and B")
    assert _downloads_banner(["A", "B", "C", "A"], []) == ("4 downloads finished", "A, B and 1 more")
    assert _downloads_banner([], ["Album X"]) == ("Download failed", "Album X")
    assert _downloads_banner([], ["X", "Y", "Z"]) == ("3 downloads failed", "X, Y and 1 more")
    # A mixed banner names the failures, the part worth a look.
    assert _downloads_banner(["A", "B", "C"], ["X"]) == ("3 downloads finished, 1 failed", "X")


def test_queue_banner_wording():
    assert _queue_banner(0, 0) == ("", "")
    assert _queue_banner(1, 0) == ("All downloads finished", "1 download done")
    assert _queue_banner(12, 0) == ("All downloads finished", "12 downloads done")
    assert _queue_banner(0, 2) == ("2 downloads failed", "Retry them from the queue")
    assert _queue_banner(9, 3) == ("Downloads finished", "9 done, 3 failed")


# ----- counting ended rows -------------------------------------------------- #


def test_only_done_and_failed_rows_count():
    s = _stub()
    _end(s, "A", "done")
    _end(s, "B", "failed")
    _end(s, "C", "cancelled")  # the user's own STOP never banners
    s._notify_job_ended(None)  # a held row was withdrawn before this point
    assert s._dl_notify_done == ["A"] and s._dl_notify_failed == [(None, "B")]
    assert (s._dl_queue_done, s._dl_queue_failed) == (1, 1)
    # The coalesce window opens once; later rows ride the same one.
    _end(s, "D", "done")
    assert len(s._dl_notify_timer.starts) == 1


# ----- the "every download" mode -------------------------------------------- #


def test_every_mode_sends_one_coalesced_banner():
    s = _stub(prefs={"notify_downloads": "every"})
    _end(s, "A", "done")
    _end(s, "B", "done")
    _end(s, "X", "failed")
    s._on_dl_notify()
    assert s._notifier.sent == [("2 downloads finished, 1 failed", "X")]
    assert s._dl_notify_done == [] and s._dl_notify_failed == []


def test_every_mode_reads_the_pref_when_the_banner_is_due():
    s = _stub(prefs={"notify_downloads": "every"})
    _end(s, "A", "done")
    s._waves_prefs["notify_downloads"] = "never"
    s._waves_prefs["notify_failed"] = False
    s._on_dl_notify()
    assert s._notifier.sent == []
    # Switched off, the batch is dropped, not carried into the next banner.
    assert s._dl_notify_done == []


def test_every_mode_keeps_failures_quiet_while_the_switch_is_off():
    s = _stub(prefs={"notify_downloads": "every", "notify_failed": False})
    _end(s, "A", "done")
    _end(s, "X", "failed")
    s._on_dl_notify()
    assert s._notifier.sent == [("Download finished", "A")]


def test_queue_mode_leaves_the_coalesced_banner_to_the_drain():
    s = _stub()
    _end(s, "A", "done")
    s._on_dl_notify()
    assert s._notifier.sent == []


# ----- the "when the queue finishes" mode ------------------------------------ #


def test_queue_mode_banners_once_the_queue_runs_dry():
    s = _stub()
    for status in ("done", "done", "failed"):
        _end(s, "row", status)
    s._notify_queue_idle()
    assert s._notifier.sent == [("Downloads finished", "2 done, 1 failed")]
    # The counters reset with the banner: the next run answers for itself.
    assert (s._dl_queue_done, s._dl_queue_failed) == (0, 0)
    s._notify_queue_idle()
    assert len(s._notifier.sent) == 1


def test_queue_mode_says_nothing_about_a_stopped_only_run():
    s = _stub()
    _end(s, "A", "cancelled")
    s._notify_queue_idle()
    assert s._notifier.sent == []


def test_queue_mode_keeps_failures_quiet_while_the_switch_is_off():
    s = _stub(prefs={"notify_failed": False})
    _end(s, "A", "done")
    _end(s, "X", "failed")
    s._notify_queue_idle()
    assert s._notifier.sent == [("All downloads finished", "1 download done")]
    # Nothing but failures and the switch off: no banner at all.
    _end(s, "Y", "failed")
    s._notify_queue_idle()
    assert len(s._notifier.sent) == 1


def test_counters_reset_even_when_the_mode_is_not_queue():
    s = _stub(prefs={"notify_downloads": "every"})
    _end(s, "A", "done")
    s._notify_queue_idle()
    assert s._notifier.sent == []
    s._waves_prefs["notify_downloads"] = "queue"
    s._notify_queue_idle()
    # The mode switched after the drain never answers for the old rows.
    assert s._notifier.sent == []


# ----- "never", with the failure switch on ----------------------------------- #


def test_never_mode_still_names_failures_while_the_switch_is_on():
    s = _stub(prefs={"notify_downloads": "never"})
    _end(s, "A", "done")
    _end(s, "X", "failed")
    s._on_dl_notify()
    assert s._notifier.sent == [("Download failed", "X")]
    s._notify_queue_idle()
    assert len(s._notifier.sent) == 1


def test_never_mode_with_the_switch_off_sends_nothing():
    s = _stub(prefs={"notify_downloads": "never", "notify_failed": False})
    _end(s, "A", "done")
    _end(s, "X", "failed")
    s._on_dl_notify()
    s._notify_queue_idle()
    assert s._notifier.sent == []


# ----- the app-update banner -------------------------------------------------- #


def test_update_banner_fires_once_per_version():
    s = _stub()
    s._notify_app_update("0.3.0")
    assert s._notifier.sent == [("Waves 0.3.0 is available", "Install it from Settings, Updates.")]
    assert s._waves_prefs["notify_update_seen"] == "0.3.0"
    s._notify_app_update("0.3.0")
    assert len(s._notifier.sent) == 1
    s._notify_app_update("0.4.0")
    assert len(s._notifier.sent) == 2


def test_update_banner_stays_quiet_for_an_empty_version_and_the_pref():
    s = _stub()
    s._notify_app_update("")
    s._waves_prefs["notify_app_update"] = False
    s._notify_app_update("0.3.0")
    assert s._notifier.sent == []
    # Off means not remembered either: switching on later still announces it.
    assert s._waves_prefs["notify_update_seen"] == ""


def test_a_refused_update_banner_is_not_remembered():
    s = _stub()
    s._notifier.ok = False  # no notification service, permission refused
    s._notify_app_update("0.3.0")
    assert s._waves_prefs["notify_update_seen"] == ""
    s._notifier.ok = True
    s._notify_app_update("0.3.0")
    assert s._waves_prefs["notify_update_seen"] == "0.3.0"
    assert len(s._notifier.sent) == 2


# ----- breadcrumbs ------------------------------------------------------------ #


def test_banner_breadcrumbs_carry_counts_never_names(caplog):
    s = _stub(prefs={"notify_downloads": "every"})
    log = logging.getLogger("waves.notify")
    log.addHandler(caplog.handler)
    try:
        with caplog.at_level("INFO", logger="waves.notify"):
            _end(s, "Secret Album", "done")
            s._on_dl_notify()
            for status in ("done", "failed"):
                _end(s, "Secret Album", status)
            s._waves_prefs["notify_downloads"] = "queue"
            s._notify_queue_idle()
        assert "handed to the OS" in caplog.text
        assert "Secret Album" not in caplog.text
    finally:
        log.removeHandler(caplog.handler)


def test_defaults_queue_mode_with_every_switch_on():
    defaults = WavesBridge._default_waves_prefs.__get__(_Stub(), _Stub)()
    # The master switch ships OFF: not a banner of any kind until the user
    # arms it under Settings, Notifications.
    assert defaults["notify_enabled"] is False
    assert defaults["notify_downloads"] == "queue"
    assert defaults["notify_failed"] is True
    assert defaults["notify_app_update"] is True
    assert defaults["notify_in_front"] is True
    assert defaults["notify_update_seen"] == ""


# ----- the master switch ------------------------------------------------------ #


def test_the_master_switch_off_silences_every_banner():
    s = _stub(prefs={"notify_enabled": False, "notify_downloads": "every"})
    _end(s, "A", "done")
    _end(s, "X", "failed")
    s._on_dl_notify()
    s._waves_prefs["notify_downloads"] = "queue"
    _end(s, "B", "done")
    s._notify_queue_idle()
    s._notify_app_update("0.3.0")
    assert s._notifier.sent == []
    # A refused update banner is not remembered: armed later, it announces.
    assert s._waves_prefs["notify_update_seen"] == ""


def test_arming_the_master_switch_sends_one_test_banner():
    s = _stub(prefs={"notify_enabled": False})
    s._notify_master_armed = _bind(s, "_notify_master_armed")
    # The save that leaves it off shows nothing.
    s._notify_master_armed(False)
    assert s._notifier.sent == []
    # The save that turns it on sends the test banner (and on macOS the
    # first send is what raises the OS permission prompt).
    s._waves_prefs["notify_enabled"] = True
    s._notify_master_armed(False)
    assert s._notifier.sent == [
        ("Notifications are on", "Waves will announce new releases, finished downloads and updates here.")
    ]
    # Every later save in the visit resubmits the same True: no second one.
    s._notify_master_armed(True)
    assert len(s._notifier.sent) == 1


def test_in_front_off_holds_banners_only_while_waves_is_in_front():
    s = _stub(prefs={"notify_in_front": False, "notify_downloads": "every"})
    _end(s, "A", "done")
    s._on_dl_notify()
    s._notify_app_update("0.3.0")
    assert s._notifier.sent == []
    # Held, not refused by the OS: the update is still announced later.
    assert s._waves_prefs["notify_update_seen"] == ""
    # In another app, the same banners go out.
    s._notify_waves_in_front = lambda: False
    _end(s, "B", "done")
    s._on_dl_notify()
    s._notify_app_update("0.3.0")
    assert [t for t, _m in s._notifier.sent] == ["Download finished", "Waves 0.3.0 is available"]


def test_the_test_banner_ignores_the_in_front_switch():
    # Arming happens in Settings, so Waves is always in front for it.
    s = _stub(prefs={"notify_enabled": False, "notify_in_front": False})
    s._notify_master_armed = _bind(s, "_notify_master_armed")
    s._waves_prefs["notify_enabled"] = True
    s._notify_master_armed(False)
    assert [t for t, _m in s._notifier.sent] == ["Notifications are on"]


def test_apply_settings_arms_from_the_edge():
    import inspect

    src = inspect.getsource(WavesBridge.applySettings)
    # Snapshot before the loop writes the prefs, judged after it.
    assert 'notify_before = self._waves_pref_bool("notify_enabled")' in src
    assert "self._notify_master_armed(notify_before)" in src


# ----- only the automatic check may raise the update banner ------------------ #


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _Updater:
    def update_available(self):
        return True, "0.2.0", "0.3.0"


def _check_stub():
    s = _Stub()
    s.appUpdateChecked = _Signal()
    s._autoUpdateFound = _Signal()
    s._updater = _Updater()
    s.threadpool = _InlinePool()
    s._emit_from_worker = _bind(s, "_emit_from_worker")
    s.checkAppUpdate = _bind(s, "checkAppUpdate")
    return s


def test_only_the_startup_check_hands_on_a_found_version():
    s = _check_stub()
    s.checkAppUpdate(True)  # the Updates card
    s.checkAppUpdate(False)  # the status bar's version mark
    assert len(s.appUpdateChecked.emitted) == 2
    assert s._autoUpdateFound.emitted == []
    s.checkAppUpdate(auto=True)  # startupUpdateCheck
    assert s._autoUpdateFound.emitted == [("0.3.0",)]


def test_the_startup_check_is_the_automatic_one():
    import inspect

    src = inspect.getsource(WavesBridge.startupUpdateCheck)
    assert "self.checkAppUpdate(auto=True)" in src


# ----- STOP, RETRY and big batches ------------------------------------------ #


def test_stop_leaves_the_drain_banner_quiet():
    s = _stub()
    for _ in range(3):
        _end(s, "row", "done")
    s._notify_forget_run()  # stopAll, before the aborted job reports in
    _end(s, "running row", "cancelled")
    s._maybe_notify_idle()
    assert s._notifier.sent == []
    # The next run answers only for itself.
    _end(s, "row", "done")
    s._maybe_notify_idle()
    assert s._notifier.sent == [("All downloads finished", "1 download done")]


def test_stop_forgets_the_run():
    import inspect

    src = inspect.getsource(WavesBridge.stopAll)
    assert "self._notify_forget_run()" in src


def test_a_retried_failure_leaves_the_run_count():
    s = _stub()
    _end(s, "A", "done", qid=1)
    _end(s, "X", "failed", qid=2)
    s._notify_forget_retried([2])  # RETRY dropped row 2
    _end(s, "X", "done", qid=3)  # and its retry landed
    s._notify_queue_idle()
    assert s._notifier.sent == [("All downloads finished", "2 downloads done")]


def test_a_retry_that_fails_again_still_counts_once():
    s = _stub()
    _end(s, "X", "failed", qid=2)
    s._notify_forget_retried([2])
    s._notify_forget_retried([2])  # a second press over the same row
    _end(s, "X", "failed", qid=3)
    s._notify_queue_idle()
    assert s._notifier.sent == [("1 download failed", "Retry them from the queue")]


def test_a_retry_of_an_old_run_leaves_this_run_alone():
    s = _stub()
    _end(s, "X", "failed", qid=2)
    s._notify_queue_idle()  # that run's banner went out
    _end(s, "Y", "failed", qid=5)
    s._notify_forget_retried([2])  # RETRY over the older failure
    s._notify_queue_idle()
    assert s._notifier.sent[-1] == ("1 download failed", "Retry them from the queue")


def test_a_retry_inside_the_window_is_not_named_as_failed():
    # Album X fails, the user retries it within the half minute and it lands:
    # the coalesced banner counts it finished and does not still name it.
    s = _stub(prefs={"notify_downloads": "every"})
    _end(s, "A", "done", qid=1)
    _end(s, "X", "failed", qid=2)
    s._notify_forget_retried([2])
    _end(s, "X", "done", qid=3)
    s._on_dl_notify()
    assert s._notifier.sent == [("2 downloads finished", "A and X")]


def test_replayed_holds_that_all_refuse_settle_the_run():
    # Held downloads replayed after the folder came back can all be refused
    # on the spot (no row, no new hold): the queue is idle again, so the
    # run's counts go into their own drain banner now, not the next run's.
    import threading

    s = _stub()
    _end(s, "A", "done", qid=1)
    s._pending_lock = threading.Lock()
    s._pending_downloads = [("m1", lambda: None)]
    _bind(s, "_run_pending_downloads")()
    assert s._notifier.sent == [("All downloads finished", "1 download done")]
    assert (s._dl_queue_done, s._dl_queue_failed) == (0, 0)


def test_retry_paths_take_the_failure_back():
    import inspect

    assert "self._notify_forget_retried([qid])" in inspect.getsource(WavesBridge.retryQueueItem)
    assert "self._notify_forget_retried(restarted)" in inspect.getsource(WavesBridge._retry_all_with_status)


def test_every_mode_counts_past_the_kept_names():
    from waves.waves_ui.backend import _DL_NOTIFY_KEEP

    s = _stub(prefs={"notify_downloads": "every"})
    for i in range(_DL_NOTIFY_KEEP + 300):
        _end(s, f"Album {i}", "done")
    assert len(s._dl_notify_done) == _DL_NOTIFY_KEEP
    s._on_dl_notify()
    title, _ = s._notifier.sent[0]
    assert title == f"{_DL_NOTIFY_KEEP + 300} downloads finished"
    assert (s._dl_notify_done_n, s._dl_notify_failed_n) == (0, 0)


def test_downloads_banner_takes_true_counts():
    assert _downloads_banner(["A"], [], 800, 0) == ("800 downloads finished", "A")
    assert _downloads_banner(["A"], ["X"], 800, 40) == ("800 downloads finished, 40 failed", "X")


# ----- when the queue counts as finished ------------------------------------- #


def test_the_drain_banner_waits_for_a_truly_idle_queue():
    s = _stub()
    _end(s, "A", "done")
    for attr, busy in (
        ("_running_qid", 7),  # the next row already started
        ("_paused", True),
        ("_scans_in_flight", 1),  # a discography scan still adding rows
        ("_pending_downloads", [("id", None)]),  # a held replay owes a row
    ):
        idle = getattr(s, attr)
        setattr(s, attr, busy)
        s._maybe_notify_idle()
        assert s._notifier.sent == [], attr
        setattr(s, attr, idle)
    s._maybe_notify_idle()
    assert s._notifier.sent == [("All downloads finished", "1 download done")]


def test_a_job_ending_into_the_next_row_sends_nothing():
    s = _stub()
    s._queue = {1: {"qid": 1, "name": "A", "status": "done"}}
    s._queue_item = lambda qid: s._queue.get(qid)
    s._pct_last = {}
    s._reap_stranded_groups = lambda: None
    s._running_qid = 1

    def pump():
        s._running_qid = s._next_qid

    s._pump_queue = pump
    s._next_qid = 2
    _bind(s, "_on_job_finished")(1)
    assert s._notifier.sent == []
    s._queue[2] = {"qid": 2, "name": "B", "status": "done"}
    s._next_qid = None
    _bind(s, "_on_job_finished")(2)
    assert s._notifier.sent == [("All downloads finished", "2 downloads done")]


def test_idleness_without_a_job_ending_is_noticed():
    import inspect

    init = inspect.getsource(WavesBridge.__init__)
    assert "self.scanningChanged.connect(self._maybe_notify_idle)" in init
    assert "self._maybe_notify_idle()" in inspect.getsource(WavesBridge.resumeQueue)
    assert "self._maybe_notify_idle()" in inspect.getsource(WavesBridge.dismissDownloadFolderNudge)
