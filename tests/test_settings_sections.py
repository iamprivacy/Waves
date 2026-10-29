"""The Settings sections: what each one holds, and that the rail can show them.

The page shows one section at a time, picked from a rail grouped under Music,
Files and App. The rail's grouping lives in SettingsPage.qml (railGroups) and
the sections themselves in the backend's settingsSchema(), so the two must
name the same ids in the same order: a section the rail does not list can
never be opened, and a rail entry with no section is an empty row.

Every setting must still be reachable after a regroup, exactly once, so the
full set of keys is pinned here. Adding a setting means adding its key below,
which is the point: it forces a decision about where it lives.
"""

from __future__ import annotations

import pathlib
import re
from unittest import mock

from waves.waves_ui.backend import WavesBridge

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_QML = _ROOT / "waves" / "waves_ui" / "qml" / "SettingsPage.qml"
_MAIN = _ROOT / "waves" / "waves_ui" / "qml" / "Main.qml"

_ORDER = [
    "downloads",
    "library",
    "releases",
    "discography",
    "files",
    "metadata",
    "processing",
    "appearance",
    "notifications",
    "pacing",
    "updates",
    "diagnostics",
]

_KEYS = {
    "downloads": [
        "download_base_path",
        "quality_audio",
        "quality_video",
        "download_dolby_atmos",
        "skip_existing",
        "confirm_category_download",
    ],
    # The library card is one composite row: its marker key, then the prefs
    # its switches, source and folder save under.
    "library": [
        "library",
        "library_source",
        "library_folder",
        "library_enabled",
        "library_bulk_skip",
        "library_mb_arbiter",
        "library_mb_lookup",
        "releases_auto_import",
        "releases_import_min_tracks",
    ],
    "releases": [
        "releases_auto_download",
        "follow_mirror_tidal",
        "releases_fresh_hints",
        "releases_musicbrainz",
        "releases_window_months",
        "releases_videos",
        "releases_featured",
        "releases_auto_kinds",
    ],
    "discography": [
        "explicit_mode",
        "edition_conflict",
        "disco_albums",
        "disco_eps",
        "disco_featured",
        "disco_appears_on",
        "video_download",
        "collapse_editions",
        "artist_page_all_editions",
    ],
    "files": [
        "format_track",
        "format_album",
        "format_playlist",
        "format_video",
        "format_mix",
        "album_track_num_pad_min",
        "filename_illegal_replacement",
        "filename_illegal_map",
        "filename_delimiter_artist",
        "filename_delimiter_album_artist",
        "use_primary_album_artist",
        "symlink_to_track",
        "playlist_create",
    ],
    "metadata": [
        "metadata_cover_dimension",
        "metadata_cover_file_dimension",
        "metadata_target_upc",
        "initial_key_format",
        "metadata_genre",
        "metadata_genre_artist_fallback",
        "metadata_delimiter_genre",
        "metadata_cover_embed",
        "cover_album_file",
        "cover_single_track_file",
        "mark_explicit",
        "lyrics_embed",
        "lyrics_file",
        "lyrics_file_synced_only",
        "lyrics_prefer_lrclib",
        "clean_album_artist",
        "metadata_replay_gain",
        "metadata_write_url",
    ],
    "processing": [
        "path_binary_ffmpeg",
        "downsample_target",
        "video_convert_mp4",
        "extract_flac",
        "downsample_enabled",
        "ffmpeg_auto_update",
        "ffmpeg_update_cadence",
    ],
    "appearance": [
        "motion_background",
        "hover_control_motion",
        "art_hover_tilt",
        "video_hover_peek",
        "queue_ledger_rows",
    ],
    # The delay switch carries its min/max, and the batch size its pause.
    "pacing": [
        "downloads_concurrent_max",
        "downloads_simultaneous_per_track_max",
        "download_delay",
        "download_delay_sec_min",
        "download_delay_sec_max",
        "api_rate_limit_batch_size",
        "api_rate_limit_delay_sec",
    ],
    # The whole section is gated on desktop-notifier being importable; the
    # schema helper below pins support ON so this list never depends on what
    # the test venv happens to have installed.
    "notifications": [
        "notify_enabled",
        "notify_downloads",
        "releases_notify",
        "notify_failed",
        "notify_app_update",
        "notify_in_front",
    ],
    "updates": ["auto_update", "update_cadence"],
    "diagnostics": ["verbose_diagnostics", "diagnostics_redact_content"],
}


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _schema_stub():
    """Just enough state for settingsSchema(), on a defaults-only config."""
    from waves.model.cfg import HelpSettings
    from waves.model.cfg import Settings as CfgSettings

    class _Cfg:
        data = CfgSettings()
        help = HelpSettings()

    stub = _Stub()
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    stub._waves_prefs = stub._default_waves_prefs()
    stub._waves_pref_bool = _bind(stub, "_waves_pref_bool")
    stub.settings = _Cfg()
    stub._help = HelpSettings()
    stub._help_for = _bind(stub, "_help_for")
    stub._ffmpeg_flag_prefs = {}
    stub.ffmpegState = lambda: {"status": "none", "source": "none", "path": ""}
    stub._user_ffmpeg_path = lambda: ""
    stub._ffmpeg_detected_path = lambda: ""
    return stub


def _schema():
    with mock.patch("waves.waves_ui.backend.notifications_supported", return_value=True):
        return WavesBridge.settingsSchema(_schema_stub())


# The other keys a composite row saves under, in the order they are listed.
_SUB_KEYS = ("file_key", "child_key", "enabled_key", "bulk_key", "mb_key", "lookup_key", "follow_key", "min_tracks_key")


def _keys(section):
    out = []
    for f in section["fields"]:
        out.append(f["key"])
        out.extend(f[k] for k in _SUB_KEYS if f.get(k))
        for half in ("lo", "hi"):
            if f.get(half) and f[half]["key"] != f["key"]:
                out.append(f[half]["key"])
    return out


def test_sections_come_in_rail_order():
    assert [s["id"] for s in _schema()] == _ORDER


def test_the_rail_lists_every_section_and_nothing_else():
    src = _QML.read_text(encoding="utf-8")
    block = re.search(r"readonly property var railGroups: \[(.*?)\n    \]", src, re.S)
    assert block, "railGroups not found in SettingsPage.qml"
    ids = re.findall(r'"([a-z]+)"', "".join(re.findall(r"ids: \[([^\]]*)\]", block.group(1))))
    assert ids == _ORDER


def test_each_section_holds_its_settings():
    by_id = {s["id"]: s for s in _schema()}
    assert sorted(_KEYS) == sorted(_ORDER), "every section's keys are pinned"
    for sid, keys in _KEYS.items():
        assert _keys(by_id[sid]) == keys, sid


def test_every_setting_shows_exactly_once():
    seen = [k for s in _schema() for k in _keys(s)]
    dupes = {k for k in seen if seen.count(k) > 1}
    assert not dupes, f"shown twice: {sorted(dupes)}"


def test_section_names_are_title_case():
    for s in _schema():
        for word in s["group"].split():
            assert word == "&" or word[0].isupper(), s["group"]


def test_no_advanced_section_is_left():
    assert all(s["id"] != "advanced" for s in _schema())


def test_pacing_pairs_ride_one_row_each():
    pacing = next(s for s in _schema() if s["id"] == "pacing")
    rows = {f["key"]: f for f in pacing["fields"]}
    delay = rows["download_delay"]
    assert delay["type"] == "range" and delay["toggle"] is True
    assert (delay["lo"]["key"], delay["hi"]["key"]) == ("download_delay_sec_min", "download_delay_sec_max")
    batch = rows["api_rate_limit_batch_size"]
    assert batch["type"] == "range" and batch["toggle"] is False
    assert (batch["lo"]["key"], batch["hi"]["key"]) == ("api_rate_limit_batch_size", "api_rate_limit_delay_sec")
    # The steppers keep their own types and bounds.
    assert delay["lo"]["type"] == delay["hi"]["type"] == "float"
    assert batch["lo"]["type"] == "int" and batch["hi"]["type"] == "float"


def test_every_deep_link_still_lands():
    """Main.qml opens Settings at a section or card by id; each must resolve."""
    targets = set(re.findall(r'jumpToCard\("([a-z]+)"\)', _MAIN.read_text(encoding="utf-8")))
    assert targets, "no deep links found in Main.qml"
    schema = _schema()
    reachable = {s["id"] for s in schema} | {s["card"] for s in schema if s.get("card")}
    # "flac" lands on the FLAC card inside FFmpeg & FLAC (SettingsPage.jumpToCard).
    reachable.add("flac")
    assert targets <= reachable, f"unresolved: {sorted(targets - reachable)}"
    src = _QML.read_text(encoding="utf-8")
    assert 'jumpToCard("processing")' in src
