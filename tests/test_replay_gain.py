"""ReplayGain tag writing: default-on, sentinel guard, and spec format.

metadata_replay_gain now defaults to True, so these pin the two things that make
on-by-default safe and correct: unmeasured values (the tidalapi 1.0 sentinel, or
None) are never written, and a real gain is emitted in the ReplayGain 2.0 writer
form ("-7.36 dB") while peak stays a bare linear amplitude.
"""

from tidaler.config import _migrate_settings
from tidaler.metadata import _replay_gain_tags, _rg_missing
from tidaler.model.cfg import Settings


def _tags(album_gain, album_peak, track_gain, track_peak):
    return dict(_replay_gain_tags(album_gain, album_peak, track_gain, track_peak))


def test_default_is_on():
    # The whole point of this change: a fresh Settings has ReplayGain enabled.
    assert Settings().metadata_replay_gain is True


def test_missing_sentinel_detected():
    assert _rg_missing(1.0)
    assert _rg_missing(None)
    assert not _rg_missing(-7.36)
    assert not _rg_missing(0.958)
    assert not _rg_missing(0.0)  # 0 dB is a real, valid "no adjustment" reading


def test_real_values_write_all_four_in_spec_format():
    tags = _tags(-6.12, 0.987654, -7.36, 0.958)
    assert tags == {
        "REPLAYGAIN_ALBUM_GAIN": "-6.12 dB",
        "REPLAYGAIN_ALBUM_PEAK": "0.987654",
        "REPLAYGAIN_TRACK_GAIN": "-7.36 dB",
        "REPLAYGAIN_TRACK_PEAK": "0.958",
    }


def test_gain_gets_unit_and_two_decimals():
    # A whole-number gain still carries the unit and two decimals.
    tags = _tags(-8.0, 0.5, 3.0, 0.5)
    assert tags["REPLAYGAIN_ALBUM_GAIN"] == "-8.00 dB"
    assert tags["REPLAYGAIN_TRACK_GAIN"] == "3.00 dB"


def test_peak_has_no_unit():
    tags = _tags(-6.0, 0.5, -6.0, 0.5)
    assert "dB" not in tags["REPLAYGAIN_ALBUM_PEAK"]
    assert "dB" not in tags["REPLAYGAIN_TRACK_PEAK"]


def test_all_sentinel_writes_nothing():
    # When TIDAL supplied no loudness data, tidalapi hands back 1.0 for every
    # field; we must write no ReplayGain tags at all (matching TIDAL's own
    # "no normalization when missing" fallback), not a phantom +1 dB / full scale.
    assert _tags(1.0, 1.0, 1.0, 1.0) == {}


def test_none_values_skipped():
    assert _tags(None, None, None, None) == {}


def test_mixed_skips_only_the_missing_field():
    # A real gain with a sentinel peak keeps the gain and drops the bogus peak,
    # and vice versa: the guard is per field.
    tags = _tags(-7.5, 1.0, 1.0, 0.9)
    assert tags == {
        "REPLAYGAIN_ALBUM_GAIN": "-7.50 dB",
        "REPLAYGAIN_TRACK_PEAK": "0.9",
    }


def test_migration_switches_an_existing_off_user_on():
    # An existing user's loaded config: ReplayGain explicitly off, migration not
    # yet run. The upgrade flips it on once and marks itself done.
    data = Settings()
    data.metadata_replay_gain = False
    data.replay_gain_default_migrated = False
    assert _migrate_settings(data) is True
    assert data.metadata_replay_gain is True
    assert data.replay_gain_default_migrated is True


def test_migration_runs_once_and_respects_a_later_off():
    # After the one-time flip, a user who turns ReplayGain back off stays off:
    # the migration must not fire a second time.
    data = Settings()
    _migrate_settings(data)  # first run marks it done
    data.metadata_replay_gain = False  # user opts out afterwards
    assert _migrate_settings(data) is False
    assert data.metadata_replay_gain is False


def test_migration_is_a_noop_once_marked():
    data = Settings()
    data.replay_gain_default_migrated = True
    data.metadata_replay_gain = False
    assert _migrate_settings(data) is False
    assert data.metadata_replay_gain is False
