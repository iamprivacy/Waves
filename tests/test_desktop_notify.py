"""DesktopNotify (waves/waves_ui/notify.py): built once, a failed build only
retried after its pause, no failure ever reaches the caller, and True means
the OS really took the banner (dispatched, and authorized where the platform
can say): the update check remembers a version off that answer."""

from __future__ import annotations

import sys
import threading

import pytest

from waves.waves_ui.notify import DesktopNotify


class _Notifier:
    """The desktop-notifier package's observed contract: send never raises
    past a backend failure (it just never fires the dispatched callback),
    and on macOS a send the OS refused still dispatches, with only
    has_authorisation telling the truth."""

    def __init__(self, fail=False, swallow=False, authorized=True):
        self.sent = []
        self.fail = fail
        self.swallow = swallow
        self.authorized = authorized

    def send(self, *, title, message, on_dispatched=None):
        if self.fail:
            raise RuntimeError("no notification center")
        if self.swallow:
            return  # the package logged a backend failure and moved on
        self.sent.append((title, message))
        if on_dispatched is not None:
            on_dispatched()

    def has_authorisation(self):
        return self.authorized


def test_builds_once_and_sends():
    built = []
    n = _Notifier()

    def factory(name):
        built.append(name)
        return n

    d = DesktopNotify("Waves", factory=factory)
    assert d.send("New album from A", "X") is True
    assert d.send("2 new releases", "From A and B") is True
    assert built == ["Waves"]
    assert n.sent == [("New album from A", "X"), ("2 new releases", "From A and B")]


def test_a_failed_build_is_remembered_for_its_pause():
    calls = []

    def factory(name):
        calls.append(name)
        raise ImportError("desktop_notifier")

    d = DesktopNotify(factory=factory)
    assert d.send("t", "m") is False
    assert d.send("t", "m") is False
    assert calls == ["Waves"]


def test_a_failed_build_is_tried_again_after_the_pause(monkeypatch):
    # A Linux notification daemon that starts late: the first build fails,
    # and banners come back on a later send instead of waiting for a restart.
    from waves.waves_ui import notify

    monkeypatch.setattr(notify, "_BUILD_RETRY_S", -1.0)
    calls = []
    good = _Notifier()

    def factory(name):
        calls.append(name)
        if len(calls) == 1:
            raise RuntimeError("daemon not up yet")
        return good

    d = DesktopNotify(factory=factory)
    assert d.send("t", "m") is False
    assert d.send("t", "m") is True
    assert calls == ["Waves", "Waves"] and good.sent == [("t", "m")]


def test_a_failing_send_is_swallowed():
    d = DesktopNotify(factory=lambda name: _Notifier(fail=True))
    assert d.send("t", "m") is False


def test_a_send_the_package_swallowed_is_not_counted():
    # The package's own send never raises past a backend failure; without the
    # dispatched callback nothing was shown, so the caller hears False.
    d = DesktopNotify(factory=lambda name: _Notifier(swallow=True))
    assert d.send("t", "m") is False


def test_a_dispatched_but_unauthorized_send_is_not_counted():
    # macOS with notifications denied (or an unsigned run from source): the
    # OS takes the request and never shows it. False, so the update check
    # does not remember the version as announced.
    n = _Notifier(authorized=False)
    d = DesktopNotify(factory=lambda name: n)
    assert d.send("t", "m") is False
    assert n.sent == [("t", "m")]


def test_a_notifier_that_cannot_say_counts_as_authorized():
    class N:
        def send(self, *, title, message, on_dispatched=None):
            on_dispatched()

    assert DesktopNotify(factory=lambda name: N()).send("t", "m") is True


def test_a_banner_the_os_never_shows_keeps_the_version_unannounced():
    # The macOS denied scenario end to end: the startup check finds 0.3.0,
    # the banner is dispatched but never shown, and 0.3.0 stays unannounced,
    # so the check after permission is granted raises it.
    from test_notify_downloads import _stub

    s = _stub()
    denied = _Notifier(authorized=False)
    s._notifier = DesktopNotify(factory=lambda name: denied)
    s._notify_app_update("0.3.0")
    assert s._waves_prefs["notify_update_seen"] == ""
    denied.authorized = True
    s._notify_app_update("0.3.0")
    assert s._waves_prefs["notify_update_seen"] == "0.3.0"


def test_a_failed_linux_probe_closes_its_half_open_bus():
    from types import SimpleNamespace

    from waves.waves_ui import notify

    closed = []

    class _Sync:
        _async_api = SimpleNamespace(
            _backend=SimpleNamespace(bus=SimpleNamespace(disconnect=lambda: closed.append(True)))
        )

        def _run_coro_sync(self, coro):
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

    notify._close_quietly(_Sync())
    assert closed == [True]


def test_two_threads_build_one_notifier():
    built = []
    gate = threading.Event()

    def factory(name):
        gate.wait(1)
        built.append(name)
        return _Notifier()

    d = DesktopNotify(factory=factory)
    threads = [threading.Thread(target=d.send, args=("t", "m")) for _ in range(4)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(2)
    assert built == ["Waves"]


def test_without_the_package_it_is_a_quiet_no_op(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_notifier(name, *a, **k):
        if name.startswith("desktop_notifier"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_notifier)
    assert DesktopNotify().send("t", "m") is False


def test_settings_offer_the_notifications_section_only_with_the_package(monkeypatch):
    from test_discography_video_source import _schema_stub

    from waves.waves_ui import backend

    def sections():
        return {s["id"]: s for s in backend.WavesBridge.settingsSchema(_schema_stub())}

    monkeypatch.setattr(backend, "notifications_supported", lambda: False)
    secs = sections()
    assert "notifications" not in secs
    assert all(f["key"] != "releases_notify" for s in secs.values() for f in s["fields"])
    monkeypatch.setattr(backend, "notifications_supported", lambda: True)
    secs = sections()
    assert [f["key"] for f in secs["notifications"]["fields"]] == [
        "notify_enabled",
        "notify_downloads",
        "releases_notify",
        "notify_failed",
        "notify_app_update",
        "notify_in_front",
    ]
    assert all(f["key"] != "releases_notify" for f in secs["releases"]["fields"])


def test_supported_follows_the_installed_package(monkeypatch):
    import importlib.util

    from waves.waves_ui import notify

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert notify.notifications_supported() is False
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert notify.notifications_supported() is True


def _fake_package(monkeypatch, caps_error=None):
    """A stand-in desktop_notifier package recording how it was built."""
    import sys
    import types

    built = []

    class Icon:
        def __init__(self, path):
            self.path = path

    class DesktopNotifierSync:
        def __init__(self, app_name, app_icon=None):
            built.append({"app_name": app_name, "app_icon": app_icon})
            self.sent = []

        def get_capabilities(self):
            if caps_error:
                raise caps_error
            return frozenset()

        def send(self, *, title, message, on_dispatched=None):
            self.sent.append((title, message))
            if on_dispatched is not None:
                on_dispatched()

    mod = types.ModuleType("desktop_notifier")
    mod.Icon = Icon
    mod.DesktopNotifierSync = DesktopNotifierSync
    monkeypatch.setitem(sys.modules, "desktop_notifier", mod)
    return built


def test_banners_carry_the_waves_icon(monkeypatch):
    from pathlib import Path

    built = _fake_package(monkeypatch)
    assert DesktopNotify("Waves").send("t", "m") is True
    icon = built[0]["app_icon"]
    # Not the package's own default (its Python logo).
    assert icon is not None and Path(icon.path).name == "icon256.png"
    assert Path(icon.path).is_file()


def test_the_icon_ships_beside_the_packaged_binary():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "waves.py").read_text(encoding="utf-8")
    assert re.search(r"--include-data-files=\{MAIN_DIRECTORY\}/waves/ui/icon\*\.png=ui/", src)


def test_linux_without_a_notification_service_sends_nothing(monkeypatch):
    import sys

    built = _fake_package(monkeypatch, caps_error=RuntimeError("org.freedesktop.Notifications unknown"))
    monkeypatch.setattr(sys, "platform", "linux")
    d = DesktopNotify("Waves")
    assert d.send("t", "m") is False
    assert d.send("t", "m") is False
    # Asked once, then remembered: no new bus connection per banner.
    assert len(built) == 1


def test_linux_with_a_notification_service_sends(monkeypatch):
    import sys

    _fake_package(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    assert DesktopNotify("Waves").send("t", "m") is True


@pytest.mark.skipif(sys.platform != "darwin", reason="the UNUserNotificationCenter delegate is macOS only")
def test_macos_banners_show_while_waves_is_in_front():
    """Without a willPresentNotification answer macOS files the front app's
    banner in Notification Center unseen. The swapped-in delegate is driven
    through the real ObjC runtime and must answer banner, list and sound."""
    pytest.importorskip("desktop_notifier.backends.macos")
    from types import SimpleNamespace

    from rubicon.objc import Block, NSUInteger, objc_id, send_message

    from waves.waves_ui import notify

    class _Center:
        delegate = None

    backend = SimpleNamespace(nc=_Center(), nc_delegate=None)
    notify._present_in_front(SimpleNamespace(_async_api=SimpleNamespace(_backend=backend)))
    delegate = backend.nc_delegate
    assert backend.nc.delegate is delegate
    assert delegate.implementation is backend
    answered = []
    send_message(
        delegate,
        "userNotificationCenter:willPresentNotification:withCompletionHandler:",
        None,
        None,
        Block(answered.append, None, NSUInteger),
        restype=None,
        argtypes=[objc_id, objc_id, objc_id],
    )
    assert answered == [2 | 8 | 16]
    # A rebuilt notifier (after a failed build) reuses the registered class.
    notify._present_in_front(SimpleNamespace(_async_api=SimpleNamespace(_backend=backend)))
    assert backend.nc_delegate.objc_class.name == "WavesNotificationDelegate"
