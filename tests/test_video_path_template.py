"""The default video path: Videos/<artist>/[year] <title>.

The old shipped template pooled every video flat under ``Videos/`` with no
year anywhere, so a file explorer could not sort an artist's videos
chronologically. The default now folders per artist and leads the file name
with the bracketed release year via ``{video_year_optional}``, a
self-dressing token: "[2026] " when TIDAL has a release date, nothing at all
when it does not (never an empty "[]"). The artist folder uses
``{artist_name_primary}``, the FIRST credited artist only: joining all
credited artists would mint a new "A, B, C" folder for every collab
combination, while the full credit list already lives in the file's
metadata. At launch a stored template equal to ANY old shipped default
silently follows along; customized templates are never touched.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from tidalapi import Video

from waves.helper.path import format_path_media
from waves.model.cfg import Settings as CfgSettings
from waves.waves_ui.backend import _LEGACY_FORMAT_VIDEOS, WavesBridge


def _video(**over) -> Video:
    v = Video.__new__(Video)
    v.id = 1
    v.name = "Let The Good Times Roll"
    v.artists = [SimpleNamespace(name="Electric Callboy")]
    v.artist = SimpleNamespace(name="Electric Callboy")
    v.album = None
    v.explicit = False
    v.release_date = datetime(2026, 6, 6)
    for key, value in over.items():
        setattr(v, key, value)
    return v


def _default_template() -> str:
    return CfgSettings.__dataclass_fields__["format_video"].default


def test_the_default_video_path_folders_by_artist_and_leads_with_the_year():
    path = format_path_media(_default_template(), _video())
    assert path == "Videos/Electric Callboy/[2026] Let The Good Times Roll"


def test_a_dateless_video_gets_a_clean_bare_title():
    path = format_path_media(_default_template(), _video(release_date=None))
    assert path == "Videos/Electric Callboy/Let The Good Times Roll"
    assert "[" not in path, "no empty [] prefix when TIDAL has no release date"


def test_the_plain_year_and_date_tokens_resolve_for_videos():
    assert format_path_media("{video_year}", _video()) == "2026"
    assert format_path_media("x/{video_date}", _video()) == "x/2026-06-06"


def test_a_collab_video_folders_under_the_primary_artist_only():
    collab = _video(
        artists=[SimpleNamespace(name="DMX"), SimpleNamespace(name="Swizz Beatz")],
        artist=SimpleNamespace(name="DMX"),
    )
    path = format_path_media(_default_template(), collab)
    assert path == "Videos/DMX/[2026] Let The Good Times Roll"
    assert "Swizz" not in path, "featured artists stay in metadata, not the folder name"


def test_the_primary_artist_token_falls_back_to_the_first_of_the_list():
    no_primary = _video(
        artists=[SimpleNamespace(name="DMX"), SimpleNamespace(name="Swizz Beatz")],
        artist=None,
    )
    assert format_path_media("{artist_name_primary}", no_primary) == "DMX"


class _MigrateStub:
    _migrate_video_template = WavesBridge._migrate_video_template

    def __init__(self, stored: str):
        self.settings = SimpleNamespace(data=SimpleNamespace(format_video=stored))


def test_every_old_default_template_follows_the_new_shipped_default():
    for legacy in _LEGACY_FORMAT_VIDEOS:
        stub = _MigrateStub(legacy)
        assert stub._migrate_video_template() is True, legacy
        assert stub.settings.data.format_video == _default_template()


def test_a_customized_template_is_never_touched():
    stub = _MigrateStub("MyVideos/{track_title}")
    assert stub._migrate_video_template() is False
    assert stub.settings.data.format_video == "MyVideos/{track_title}"


def test_every_shipped_default_token_is_documented_in_app():
    """pathTemplateTokens ("Want to know more?") is the only in-product
    documentation of the template language. A token the shipped defaults use
    must be listed there, or a user who edits it away can never rebuild it."""
    import re

    from waves.waves_ui.backend import _TEMPLATE_TOKENS

    documented = {t[0] for t in _TEMPLATE_TOKENS}
    d = CfgSettings()
    for field in ("format_track", "format_album", "format_playlist", "format_mix", "format_video"):
        template = getattr(d, field, "")
        for token in re.findall(r"\{([a-z0-9_]+)\}", template):
            assert token in documented, f"{{{token}}} (in {field}) is missing from the in-app token reference"
