"""Opt-in baseline JPEG covers: the rewrite itself, and its hook in the tag writer.

Every image here is synthetic, built with Qt in the test. Served covers are
progressive JPEGs (several scans); small decoders read only the baseline kind
(one frame, one scan), which is what the option promises to hand them.
"""

import pathlib
import random
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QImageWriter

from tests.test_tag_truths import _album, _download, _RecMeta, _track
from waves.constants import CoverBaseline, CoverDimensions
from waves.model.cfg import Settings
from waves.waves_ui import cover_baseline
from waves.waves_ui.cover_baseline import is_baseline, scan, to_baseline

_MODULE = "waves.waves_ui.cover_baseline"
_STREAM = SimpleNamespace(
    album_replay_gain=None, album_peak_amplitude=None, track_replay_gain=None, track_peak_amplitude=None
)


def _picture(side: int = 96, gray: bool = False) -> QImage:
    image = QImage(side, side, QImage.Format.Format_RGB888)
    rnd = random.Random(11)
    for y in range(side):
        for x in range(side):
            if gray:
                v = (x * 2 + y + rnd.randrange(24)) % 256
                image.setPixelColor(x, y, QColor(v, v, v))
            else:
                image.setPixelColor(
                    x,
                    y,
                    QColor((x * 5 + rnd.randrange(30)) % 256, (y * 3) % 256, (x + y * 2 + rnd.randrange(60)) % 256),
                )
    return image.convertToFormat(QImage.Format.Format_Grayscale8) if gray else image


def _encode(image: QImage, fmt: bytes = b"jpeg", *, progressive: bool = False, quality: int = 85) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buffer, fmt)
    if fmt == b"jpeg":
        writer.setQuality(quality)
        writer.setProgressiveScanWrite(progressive)
    assert writer.write(image)
    return bytes(buffer.data().data())


# Qt stores color at half resolution up to quality 90 and at full resolution
# from 91, which is how the two source kinds are made here.
PROGRESSIVE_420 = _encode(_picture(), progressive=True, quality=85)
PROGRESSIVE_444 = _encode(_picture(), progressive=True, quality=95)
PROGRESSIVE_GRAY = _encode(_picture(gray=True), progressive=True, quality=85)
BASELINE = _encode(_picture(), progressive=False, quality=85)
PNG = _encode(_picture(32), b"png")


# --------------------------------------------------------------------------- #
# The marker walk
# --------------------------------------------------------------------------- #
def test_the_fixtures_are_what_they_claim():
    assert scan(PROGRESSIVE_420).sof == 0xC2 and scan(PROGRESSIVE_420).scans > 1
    assert scan(PROGRESSIVE_420).luma_sampling == (2, 2)
    assert scan(PROGRESSIVE_444).luma_sampling == (1, 1)
    assert scan(PROGRESSIVE_GRAY).components == 1
    assert is_baseline(scan(BASELINE))
    assert not is_baseline(scan(PROGRESSIVE_420))


@pytest.mark.parametrize("junk", [b"", b"not a JPEG", b"\xff\xd8", b"\xff\xd8\xff", b"\x00" * 64, PNG, "", None, 7])
def test_what_is_not_a_jpeg_scans_as_none(junk):
    assert scan(junk) is None


def test_a_cut_off_file_is_not_complete():
    info = scan(PROGRESSIVE_420[: len(PROGRESSIVE_420) // 2])
    assert info is not None and not info.complete
    assert not is_baseline(scan(BASELINE[:-2]))


# --------------------------------------------------------------------------- #
# The rewrite
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["small", "faithful"])
@pytest.mark.parametrize("source", [PROGRESSIVE_420, PROGRESSIVE_444, PROGRESSIVE_GRAY], ids=["420", "444", "gray"])
def test_a_progressive_cover_comes_out_baseline(source, mode):
    data, outcome = to_baseline(source, mode)
    assert outcome == "converted"
    info = scan(data)
    assert is_baseline(info)
    assert (info.sof, info.precision, info.scans) == (0xC0, 8, 1)
    assert info.components == scan(source).components
    out = QImage.fromData(data)
    assert (out.width(), out.height()) == (96, 96)


def test_small_always_stores_color_at_half_resolution():
    assert scan(to_baseline(PROGRESSIVE_444, "small").data).luma_sampling == (2, 2)
    assert scan(to_baseline(PROGRESSIVE_420, "small").data).luma_sampling == (2, 2)


def test_faithful_keeps_full_resolution_color_where_the_source_has_it():
    assert scan(to_baseline(PROGRESSIVE_444, "faithful").data).luma_sampling == (1, 1)
    # Nothing to keep on a half-resolution source: same output as small.
    assert to_baseline(PROGRESSIVE_420, "faithful").data == to_baseline(PROGRESSIVE_420, "small").data


def test_small_is_the_smaller_of_the_two():
    assert len(to_baseline(PROGRESSIVE_444, "small").data) < len(to_baseline(PROGRESSIVE_444, "faithful").data)


def test_an_already_baseline_cover_is_handed_back_untouched():
    data, outcome = to_baseline(BASELINE, "small")
    assert data is BASELINE
    assert outcome == "already-baseline"


@pytest.mark.parametrize(
    ("data", "outcome"),
    [
        (b"", "not-jpeg"),
        (b"not a JPEG", "not-jpeg"),
        (PNG, "not-jpeg"),
        ("", "not-jpeg"),
        (PROGRESSIVE_420[: len(PROGRESSIVE_420) // 2], "truncated"),
        (PROGRESSIVE_420[:-2], "truncated"),
    ],
    ids=["empty", "junk", "png", "empty-str", "half", "no-eoi"],
)
def test_anything_else_goes_back_as_it_came(data, outcome):
    result = to_baseline(data, "small")
    assert result.data is data
    assert result.outcome == outcome


@pytest.mark.parametrize("mode", ["off", "", "SMALL", None])
def test_an_unknown_mode_changes_nothing(mode):
    result = to_baseline(PROGRESSIVE_420, mode)
    assert result.data is PROGRESSIVE_420
    assert result.outcome == "unknown-mode"


def test_damaged_input_never_raises_and_never_yields_a_non_baseline_file():
    rnd = random.Random(5)
    converted = 0
    for source in (PROGRESSIVE_420, PROGRESSIVE_444, BASELINE, PNG):
        for _ in range(150):
            damaged = bytearray(source)
            for _ in range(rnd.randrange(1, 10)):
                damaged[rnd.randrange(len(damaged))] = rnd.randrange(256)
            if rnd.random() < 0.3:
                damaged = damaged[: rnd.randrange(1, len(damaged))]
            damaged = bytes(damaged)
            data, outcome = to_baseline(damaged, rnd.choice(["small", "faithful"]))
            if outcome == "converted":
                converted += 1
                assert is_baseline(scan(data))
            else:
                assert data is damaged
    assert converted, "the loop should exercise the rewrite, not only the refusals"


def test_a_failing_decoder_hands_the_input_back(monkeypatch):
    import PySide6.QtGui as QtGui

    def boom(*_a, **_k):
        raise RuntimeError("decoder gone")

    monkeypatch.setattr(QtGui, "QImageReader", boom)
    result = to_baseline(PROGRESSIVE_420, "small")
    assert result.data is PROGRESSIVE_420
    assert result.outcome == "error"


def test_the_module_keeps_qt_out_of_its_top_level():
    source = pathlib.Path(cover_baseline.__file__).read_text(encoding="utf-8")
    top = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert not any("PySide6" in line for line in top), top


# --------------------------------------------------------------------------- #
# The hook in the tag writer
# --------------------------------------------------------------------------- #
def _engine(mode, *, embed=True, cover_file=False, file_dim="follow"):
    dl = _download()
    data = dl.settings.data
    data.metadata_cover_embed = embed
    data.cover_album_file = cover_file
    data.metadata_cover_dimension = CoverDimensions.Px320
    data.metadata_cover_file_dimension = file_dim
    if mode is not None:
        data.metadata_cover_baseline = mode
    fetched = []

    def fake_cover_data(url=None, path_file=None):
        fetched.append(url)
        return PROGRESSIVE_444 if "1280" in str(url) else PROGRESSIVE_420

    dl.cover_data = fake_cover_data
    album = _album(12)
    album.image = lambda size: f"cover:{int(size) if size != CoverDimensions.PxORIGIN else 'origin'}"
    return dl, _track(album), fetched


def _write(dl, track, folder, is_parent_album=True):
    with patch("waves.download.Metadata", _RecMeta):
        return dl.metadata_write(track, folder / "t.flac", is_parent_album, _STREAM)


def test_the_setting_is_off_by_default():
    assert Settings().metadata_cover_baseline is CoverBaseline.OFF


@pytest.mark.parametrize("mode", [CoverBaseline.OFF, "off", None, "nonsense"], ids=["enum", "str", "mock", "junk"])
def test_off_hands_the_fetched_bytes_through_and_never_loads_the_converter(mode, tmp_path, monkeypatch):
    """None leaves the MagicMock settings attribute in place: a test double
    standing in for the settings must read as off, like every existing one."""
    monkeypatch.delitem(sys.modules, _MODULE, raising=False)
    dl, track, _ = _engine(mode, cover_file=True)
    _result, _lyrics, _suffix, path_cover = _write(dl, track, tmp_path)
    assert _RecMeta.last.kw["cover_data"] is PROGRESSIVE_420
    assert pathlib.Path(path_cover).read_bytes() == PROGRESSIVE_420
    assert _MODULE not in sys.modules
    assert not any(str(key).startswith("baseline:") for key in dl._cover_cache)


@pytest.mark.parametrize("mode", [CoverBaseline.SMALL, CoverBaseline.FAITHFUL, "small"])
def test_on_embeds_and_saves_a_baseline_cover(mode, tmp_path):
    dl, track, _ = _engine(mode, cover_file=True)
    _result, _lyrics, _suffix, path_cover = _write(dl, track, tmp_path)
    embedded = _RecMeta.last.kw["cover_data"]
    assert is_baseline(scan(embedded))
    # Same size for both: one rewrite, the same bytes in the tag and the file.
    assert pathlib.Path(path_cover).read_bytes() == embedded


def test_the_cached_original_stays_untouched(tmp_path):
    dl, track, _ = _engine(CoverBaseline.SMALL)
    _write(dl, track, tmp_path)
    assert dl._cover_cache["cover:320"] is PROGRESSIVE_420
    assert is_baseline(scan(dl._cover_cache["baseline:small:cover:320"]))


def test_an_album_is_fetched_and_rewritten_once(tmp_path):
    dl, track, fetched = _engine(CoverBaseline.SMALL, cover_file=True)
    calls = []
    real = cover_baseline.to_baseline

    def counting(data, mode):
        calls.append(mode)
        return real(data, mode)

    with patch(f"{_MODULE}.to_baseline", counting):
        embedded = []
        for _ in range(12):
            _write(dl, track, tmp_path)
            embedded.append(_RecMeta.last.kw["cover_data"])
    assert fetched == ["cover:320"]
    assert calls == ["small"]
    assert all(e is embedded[0] for e in embedded)


def test_a_cover_file_at_its_own_size_gets_its_own_rewrite(tmp_path):
    dl, track, fetched = _engine(CoverBaseline.FAITHFUL, cover_file=True, file_dim="Px1280")
    _result, _lyrics, _suffix, path_cover = _write(dl, track, tmp_path)
    saved = pathlib.Path(path_cover).read_bytes()
    embedded = _RecMeta.last.kw["cover_data"]
    assert fetched == ["cover:320", "cover:1280"]
    assert is_baseline(scan(embedded)) and is_baseline(scan(saved))
    assert saved != embedded
    # The 1280 fixture carries full-resolution color and faithful keeps it.
    assert scan(saved).luma_sampling == (1, 1)


def test_embedding_off_still_rewrites_the_saved_cover(tmp_path):
    dl, track, _ = _engine(CoverBaseline.SMALL, embed=False, cover_file=True)
    _result, _lyrics, _suffix, path_cover = _write(dl, track, tmp_path)
    assert _RecMeta.last.kw["cover_data"] is None
    assert is_baseline(scan(pathlib.Path(path_cover).read_bytes()))


def test_a_converter_that_raises_still_lands_the_track_with_the_served_cover(tmp_path):
    dl, track, _ = _engine(CoverBaseline.SMALL, cover_file=True)

    def boom(_data, _mode):
        raise RuntimeError("converter gone")

    with patch(f"{_MODULE}.to_baseline", boom):
        result, _lyrics, _suffix, path_cover = _write(dl, track, tmp_path)
    assert result is True
    assert _RecMeta.last.kw["cover_data"] is PROGRESSIVE_420
    assert pathlib.Path(path_cover).read_bytes() == PROGRESSIVE_420


def test_a_cover_kept_as_served_leaves_a_breadcrumb_without_its_address(tmp_path, caplog):
    dl, track, _ = _engine(CoverBaseline.SMALL)
    dl.cover_data = lambda url=None, path_file=None: PROGRESSIVE_420[:-2]
    with caplog.at_level("INFO", logger="waves.covers"):
        _write(dl, track, tmp_path)
    assert _RecMeta.last.kw["cover_data"] == PROGRESSIVE_420[:-2]
    lines = [r.getMessage() for r in caplog.records if r.name == "waves.covers"]
    assert lines == ["Cover kept as served (truncated)."]
    assert "cover:320" not in " ".join(lines)


def test_the_rewrite_shares_the_cover_cache_bound(tmp_path):
    dl = _download()
    for n in range(dl._COVER_CACHE_MAX + 4):
        dl._cover_baseline(f"cover:{n}", PROGRESSIVE_420, "small")
    assert len(dl._cover_cache) == dl._COVER_CACHE_MAX


def test_the_mode_reader_takes_only_the_two_real_modes():
    dl = _download()
    for value, expected in [
        (CoverBaseline.SMALL, "small"),
        (CoverBaseline.FAITHFUL, "faithful"),
        ("small", "small"),
        (CoverBaseline.OFF, ""),
        ("", ""),
        (True, ""),
        (MagicMock(), ""),
    ]:
        dl.settings.data.metadata_cover_baseline = value
        assert dl._cover_baseline_mode() == expected
