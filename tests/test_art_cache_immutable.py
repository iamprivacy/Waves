"""A held cover is fresh, whatever the CDN's max-age said.

The cover CDN sends ``Cache-Control: max-age=3600``; an hour after a cover
is stored Qt's cache-first read deems it stale and revalidates it over the
network (a 304 round trip per cover, ~100 ms each) before painting. The
URLs are immutable, so the app's cache reports every held cover as fresh
and the read is a disk hit, for covers stored before the fix as well.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QDateTime, QUrl  # noqa: E402
from PySide6.QtNetwork import QNetworkCacheMetaData, QNetworkDiskCache  # noqa: E402

from waves.waves_ui.app import _ArtCacheFactory, _ImmutableArtCache  # noqa: E402

URL = "https://img.test/cover/320x320.jpg"


def _store(cache_dir, ttl_s: int) -> None:
    """Lay down one cover the way Qt does from the CDN's max-age (Qt refuses
    to store an entry that is already expired, so a short ttl plus a wait is
    how a test gets an expired one)."""
    plain = QNetworkDiskCache()
    plain.setCacheDirectory(str(cache_dir))
    md = QNetworkCacheMetaData()
    md.setUrl(QUrl(URL))
    md.setSaveToDisk(True)
    # The headers the CDN really sends. Not decoration: Qt's reader rejects
    # an entry with no raw headers and deletes its file on the next read.
    md.setRawHeaders([(b"Content-Type", b"image/jpeg"), (b"Cache-Control", b"max-age=%d" % ttl_s)])
    md.setExpirationDate(QDateTime.currentDateTimeUtc().addSecs(ttl_s))
    dev = plain.prepare(md)
    dev.write(b"x" * 64)
    plain.insert(dev)


def _plain_expiry(cache_dir):
    plain = QNetworkDiskCache()
    plain.setCacheDirectory(str(cache_dir))
    md = plain.metaData(QUrl(URL))
    assert md.isValid(), "the fixture wrote a cover Qt can read back"
    return md.expirationDate()


def test_a_cover_stored_under_the_cdn_max_age_reads_back_as_never_expiring(tmp_path):
    _store(tmp_path, 3600)
    now = QDateTime.currentDateTimeUtc()
    assert _plain_expiry(tmp_path) < now.addSecs(3700), "Qt kept the CDN's one hour"

    cache = _ImmutableArtCache()
    cache.setCacheDirectory(str(tmp_path))
    md = cache.metaData(QUrl(URL))
    assert md.isValid() and md.saveToDisk()
    assert md.expirationDate() > now.addYears(_ImmutableArtCache._FRESH_YEARS - 1), "held means fresh"
    assert md.url() == QUrl(URL)


def test_a_cover_qt_already_deems_stale_reads_back_fresh(tmp_path):
    _store(tmp_path, 1)
    time.sleep(1.3)
    now = QDateTime.currentDateTimeUtc()
    assert _plain_expiry(tmp_path) < now, "the fixture is a cover Qt would revalidate over the network"

    cache = _ImmutableArtCache()
    cache.setCacheDirectory(str(tmp_path))
    assert cache.metaData(QUrl(URL)).expirationDate() > now.addYears(1)


def test_a_cover_never_stored_is_still_a_miss(tmp_path):
    cache = _ImmutableArtCache()
    cache.setCacheDirectory(str(tmp_path))
    assert not cache.metaData(QUrl(URL)).isValid(), "a miss must go to the network, not be invented"


def test_the_qml_image_loader_gets_the_immutable_cache(tmp_path):
    nam = _ArtCacheFactory(str(tmp_path)).create(None)
    assert isinstance(nam.cache(), _ImmutableArtCache)
    assert nam.cache().cacheDirectory().rstrip("/") == str(tmp_path).rstrip("/")
