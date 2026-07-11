"""The segment-download HTTP session is process-wide (Download._shared_http):
a per-instance session meant a cold connection pool for every queued album, so
each album start paid its TLS setup all at once (an all-core CPU spike per
click of Download, worst on modest Windows boxes).

Two properties keep that spike gone and must not regress:

1. Every connection shares ONE preloaded SSLContext. requests' default
   cert_verify hands urllib3 a CA bundle path per connection, which makes
   urllib3 build a fresh SSLContext and re-parse the whole certifi corpus on
   every TLS connect. _SharedContextAdapter suppresses that path.
2. The pool is small and blocking (pool_block=True, _HTTP_POOL_MAXSIZE), so a
   cold start opens at most _HTTP_POOL_MAXSIZE connections concurrently no
   matter how many segment worker threads are running.
"""

from __future__ import annotations

import pytest

from tidaler.download import Download, _SharedContextAdapter, pooled_session


@pytest.fixture(autouse=True)
def _reset_shared_session():
    """Isolate each test from the process-wide singleton."""
    Download._http_shared = None
    yield
    Download._http_shared = None


def test_same_session_across_calls():
    s1 = Download._shared_http()
    s2 = Download._shared_http()
    assert s1 is s2


def test_both_schemes_mounted_with_shared_context_adapter():
    s = Download._shared_http()
    https = s.get_adapter("https://example.com")
    http = s.get_adapter("http://example.com")
    assert isinstance(https, _SharedContextAdapter)
    assert isinstance(http, _SharedContextAdapter)


def test_pool_is_small_and_blocking():
    """Worker threads beyond the cap must queue for a free connection, not
    each open (and TLS-handshake) their own."""
    adapter = Download._shared_http().get_adapter("https://example.com")
    assert adapter._pool_maxsize == Download._HTTP_POOL_MAXSIZE
    assert adapter._pool_block is True


def test_one_ssl_context_shared_by_all_pools():
    """The preloaded context must reach urllib3's pools so connections skip
    the per-connection SSLContext build + certifi re-parse."""
    adapter = Download._shared_http().get_adapter("https://example.com")
    pool = adapter.poolmanager.connection_from_host("example.com", 443, scheme="https")
    assert pool.conn_kw.get("ssl_context") is adapter._ssl_context
    # Certifi is loaded: the context can actually verify (non-empty CA store).
    assert adapter._ssl_context.cert_store_stats()["x509_ca"] > 0


def test_cert_verify_skips_ca_path_for_default_verify():
    """Setting conn.ca_certs is exactly what triggers urllib3's
    load_verify_locations per connection; the default verify=True path must
    leave it alone."""

    class _Conn:
        pass

    adapter = Download._shared_http().get_adapter("https://example.com")
    conn = _Conn()
    adapter.cert_verify(conn, "https://example.com", verify=True, cert=None)
    assert not hasattr(conn, "ca_certs")


def test_pooled_session_defaults_fail_fast():
    """pooled_session() serves latency-sensitive one-shot callers (the video
    bandwidth probe): shared preloaded context, but single-attempt and
    non-blocking, unlike the download engine's retrying, blocking pool."""
    s = pooled_session()
    adapter = s.get_adapter("https://example.com")
    assert isinstance(adapter, _SharedContextAdapter)
    assert adapter._pool_block is False
    assert adapter.max_retries.total == 0
    assert adapter._ssl_context.cert_store_stats()["x509_ca"] > 0


def test_cert_verify_falls_back_for_custom_verify(tmp_path):
    """A custom CA bundle path must still go through the stock requests
    behaviour (correctness beats the fast path)."""
    import certifi

    adapter = Download._shared_http().get_adapter("https://example.com")

    class _Conn:
        pass

    conn = _Conn()
    adapter.cert_verify(conn, "https://example.com", verify=certifi.where(), cert=None)
    assert conn.ca_certs == certifi.where()
