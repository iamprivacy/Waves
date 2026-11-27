"""The Lavender drawer's DOWNLOAD: the signed-manifest check and the installer fetch.

The fixture is Lavender v0.1.4's real SHA256SUMS and SHA256SUMS.sig, so the
signature check runs against Lavender's real release key. No network: the
fetch tests use a fake session.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from threading import Event

import pytest

from waves import lavender_get
from waves.waves_ui.signing import UPDATE_PUBLIC_KEY, verify_minisign

FIXTURE = Path(__file__).parent / "fixtures" / "lavender_v0.1.4"
SUMS = (FIXTURE / "SHA256SUMS").read_bytes()
SIG = (FIXTURE / "SHA256SUMS.sig").read_text().strip()
KEY = lavender_get.LAVENDER_PUBLIC_KEY


def _resign(edit) -> str:
    """The real signature file with one of its lines changed by ``edit``."""
    lines = base64.b64decode(SIG).decode().splitlines()
    return base64.b64encode("\n".join(edit(lines)).encode() + b"\n").decode()


def test_the_real_lavender_manifest_verifies():
    assert verify_minisign(SUMS, SIG, KEY)


@pytest.mark.parametrize(
    "message, sig, key",
    [
        (SUMS.replace(b"cfa5", b"cfa6", 1), SIG, KEY),  # one hash changed
        (SUMS + b"\n", SIG, KEY),  # one byte added
        (SUMS, _resign(lambda ls: [ls[0], ls[1], ls[2].replace("SHA256SUMS", "other"), ls[3]]), KEY),
        (SUMS, SIG, UPDATE_PUBLIC_KEY),  # Waves' own key, not Lavender's
        (SUMS, SIG, ""),
        (SUMS, "", KEY),
        (SUMS, "not base64 !", KEY),
        (SUMS, base64.b64encode(b"untrusted comment: x\n").decode(), KEY),
    ],
)
def test_anything_else_is_refused(message, sig, key):
    assert not verify_minisign(message, sig, key)


@pytest.mark.parametrize(
    "system, machine, tools, name",
    [
        ("darwin", "arm64", (), "Lavender_0.1.4_aarch64.dmg"),
        ("darwin", "x86_64", (), "Lavender_0.1.4_x64.dmg"),
        ("win32", "AMD64", (), "Lavender_0.1.4_x64-setup.exe"),
        ("win32", "ARM64", (), "Lavender_0.1.4_x64-setup.exe"),
        ("linux", "x86_64", ("dpkg", "rpm"), "Lavender_0.1.4_amd64.deb"),
        ("linux", "x86_64", ("rpm",), "Lavender-0.1.4-1.x86_64.rpm"),
    ],
)
def test_each_computer_gets_one_installer_lavender_can_be_found_from(system, machine, tools, name):
    pattern = lavender_get.installer_pattern(system, machine, which=lambda t: t if t in tools else None)
    import re

    table = [line.split()[-1] for line in SUMS.decode().splitlines() if not line.startswith("#")]
    assert [n for n in table if re.fullmatch(pattern, n)] == [name]


@pytest.mark.parametrize("system, machine, tools", [("linux", "x86_64", ()), ("linux", "aarch64", ("dpkg",))])
def test_no_installer_means_the_site(system, machine, tools):
    assert lavender_get.installer_pattern(system, machine, which=lambda t: t if t in tools else None) is None


class _Resp:
    def __init__(self, body: bytes, length: int | None = None):
        self.content = body
        self.text = body.decode("utf-8", "replace")
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self.content), 4):
            yield self.content[i : i + 4]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Session:
    def __init__(self, files: dict[str, _Resp]):
        self.files, self.asked = files, []

    def get(self, url, timeout, stream=False):
        self.asked.append(url)
        return self.files[url.rsplit("/", 1)[1]]


PAYLOAD = b"a lavender installer"
NAME = "Lavender_0.1.5_aarch64.dmg"


def _release(tag="v0.1.5", sha=None, length=None):
    sums = f"# lavender-version: {tag}\n{sha or hashlib.sha256(PAYLOAD).hexdigest()}  {NAME}\n".encode()
    return _Session({"SHA256SUMS": _Resp(sums), "SHA256SUMS.sig": _Resp(b"sig"), NAME: _Resp(PAYLOAD, length)})


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(lavender_get, "installer_pattern", lambda: r"Lavender_\d+(?:\.\d+){1,3}_aarch64\.dmg")
    monkeypatch.setattr(lavender_get, "verify_minisign", lambda *a: True)


def test_a_verified_installer_is_saved_under_its_own_name(mac, tmp_path):
    seen = []
    sess = _release()
    path = lavender_get.fetch(tmp_path, sess, progress_cb=seen.append)
    assert path == tmp_path / NAME and path.read_bytes() == PAYLOAD
    assert sorted(p.name for p in tmp_path.iterdir()) == [NAME]
    assert sess.asked[-1] == f"{lavender_get.RELEASES}/download/v0.1.5/{NAME}"
    assert seen and seen[-1] == 100.0


@pytest.mark.parametrize(
    "session, message",
    [
        (lambda: _release(sha="0" * 64), lavender_get.NOT_VERIFIED),
        (lambda: _release(length=lavender_get.MAX_BYTES + 1), lavender_get.NOT_VERIFIED),
        (lambda: _release(tag="v0.1.4"), lavender_get.NO_RELEASE),
        (lambda: _release(tag="v0.1.5/../../x"), lavender_get.NO_RELEASE),
    ],
)
def test_a_bad_release_leaves_no_installer(mac, tmp_path, session, message):
    with pytest.raises(lavender_get.LavenderGetError, match=message):
        lavender_get.fetch(tmp_path, session())
    assert list(tmp_path.iterdir()) == []  # no installer, and no half-written one


def test_a_cancel_leaves_no_installer(mac, tmp_path):
    abort = Event()
    abort.set()
    with pytest.raises(lavender_get.LavenderGetCanceled):
        lavender_get.fetch(tmp_path, _release(), abort=abort)
    assert list(tmp_path.iterdir()) == []  # no installer, and no half-written one


def test_an_unsigned_manifest_is_never_followed(monkeypatch, tmp_path):
    """The real signature check (not stubbed): a forged manifest stops before the payload."""
    monkeypatch.setattr(lavender_get, "installer_pattern", lambda: r"Lavender_\d+(?:\.\d+){1,3}_aarch64\.dmg")
    sess = _release()
    with pytest.raises(lavender_get.LavenderGetError, match=lavender_get.NOT_VERIFIED):
        lavender_get.fetch(tmp_path, sess)
    assert all(not url.endswith(NAME) for url in sess.asked)


def test_todays_real_release_is_too_old_for_waves(monkeypatch, tmp_path):
    """v0.1.4 is genuinely signed but has no lyrics worker: refused before the payload."""
    monkeypatch.setattr(lavender_get, "installer_pattern", lambda: r"Lavender_\d+(?:\.\d+){1,3}_aarch64\.dmg")
    sess = _Session({"SHA256SUMS": _Resp(SUMS), "SHA256SUMS.sig": _Resp(SIG.encode())})
    with pytest.raises(lavender_get.LavenderGetError, match=lavender_get.NO_RELEASE):
        lavender_get.fetch(tmp_path, sess)
    assert len(sess.asked) == 2
