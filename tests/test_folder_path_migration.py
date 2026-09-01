"""The one-time format_playlist upgrade that added {folder_path}.

Defaults are persisted verbatim (BaseConfig.read saves the full dataclass), so
every pre-folder install stores the old default string. Only that exact value
may be rewritten; any other stored template is a customized one the user owns.
"""

from waves.config import _migrate_settings
from waves.model.cfg import Settings

OLD_DEFAULT = "Playlists/{playlist_name}/{list_pos}. {artist_name} - {track_title}"


def _pre_folder_settings() -> Settings:
    data = Settings()
    data.replay_gain_default_migrated = True  # isolate the folder step
    data.format_playlist_folder_migrated = False
    return data


def test_stored_old_default_is_upgraded():
    data = _pre_folder_settings()
    data.format_playlist = OLD_DEFAULT
    assert _migrate_settings(data) is True
    assert data.format_playlist == Settings().format_playlist
    assert "{folder_path}" in data.format_playlist
    assert data.format_playlist_folder_migrated is True


def test_customized_template_is_never_touched():
    data = _pre_folder_settings()
    custom = "MyMusic/{playlist_name}/{artist_name} - {track_title}"
    data.format_playlist = custom
    assert _migrate_settings(data) is True  # marker write still persists
    assert data.format_playlist == custom
    assert data.format_playlist_folder_migrated is True


def test_marker_stops_a_second_rewrite():
    # The user removed {folder_path} again after the upgrade: their choice,
    # and it happens to equal the old default. The marker keeps it.
    data = _pre_folder_settings()
    data.format_playlist = OLD_DEFAULT
    _migrate_settings(data)
    data.format_playlist = OLD_DEFAULT
    assert _migrate_settings(data) is False
    assert data.format_playlist == OLD_DEFAULT


def test_new_default_contains_folder_path_token():
    assert "{folder_path}" in Settings().format_playlist
