"""Every settings section's switches fill whole rows of equal tiles.

The flag grid lays switches three across when their count divides by three,
otherwise two across, and every tile has the same width: a switch is never
stretched over a row of its own. So every section's count must divide into
full rows, with or without the optional banner switch. The tiles hold a fixed
number of lines, so each help stays short enough for its tile at the
narrowest window (the offscreen elide check found these limits;
test_settings_fit renders every section at the minimum window and proves
nothing elides).
"""

from __future__ import annotations

import pytest

from tests.test_releases_settings_placement import _schema_stub
from waves.waves_ui.backend import WavesBridge

# Characters a help can hold at the narrowest window: four lines of a third,
# three of a half; one line fewer while a nested checkbox shows.
_THIRD, _THIRD_CHILD, _HALF = 140, 105, 190


def _switches(section):
    # The page's boolFields: a switch drawn as a row (rowToggle) is no tile,
    # and neither is a section's master (it rides beside the title).
    return [
        f
        for f in section["fields"]
        if f.get("type") == "bool"
        and f.get("embedded") is not True
        and f.get("rowToggle") is not True
        and f.get("master") is not True
    ]


@pytest.fixture(params=[False, True], ids=["no-banner", "banner"])
def schema(request, monkeypatch):
    monkeypatch.setattr("waves.waves_ui.backend.notifications_supported", lambda: request.param)
    return WavesBridge.settingsSchema(_schema_stub())


def test_no_switch_is_left_alone_on_a_row(schema):
    for section in schema:
        n = len(_switches(section))
        cols = 3 if n % 3 == 0 else 2
        assert n % cols == 0, f"{section['id']}: {n} switches leave one alone on a row"


def test_the_crowded_sections_fill_rows_of_three(schema):
    counts = {s["id"]: len(_switches(s)) for s in schema}
    # The Genre switch is a row among the tag-field rows, not a tenth tile.
    assert counts["metadata"] == 9
    assert counts["discography"] == 6
    assert counts["downloads"] == counts["files"] == counts["processing"] == 3
    assert counts["appearance"] == 4


def test_every_help_fits_its_tile(schema):
    for section in schema:
        flags = _switches(section)
        third = len(flags) % 3 == 0
        for f in flags:
            cap = (_THIRD_CHILD if f.get("child_key") else _THIRD) if third else _HALF
            assert len(f.get("help", "")) <= cap, f"{section['id']}/{f['key']}: {len(f['help'])} > {cap}"


def test_artist_page_editions_ride_inside_most_complete(schema):
    disco = next(s for s in schema if s["id"] == "discography")
    keys = [f["key"] for f in disco["fields"]]
    assert "artist_page_all_editions" not in keys
    collapse = next(f for f in disco["fields"] if f["key"] == "collapse_editions")
    assert collapse["child_key"] == "artist_page_all_editions"


def test_moved_switches_landed(schema):
    by_id = {s["id"]: [f["key"] for f in s["fields"]] for s in schema}
    # The album-artist tag switch sits with the other tags, its folder twin
    # with the other names.
    assert "clean_album_artist" in by_id["metadata"] and "clean_album_artist" not in by_id["files"]
    assert "use_primary_album_artist" in by_id["files"]
    for key in ("downsample_enabled", "downsample_target"):
        assert key in by_id["processing"]
    assert "advanced" not in by_id


def test_the_genre_switch_is_a_row_and_its_separator_follows_it(schema):
    fields = {f["key"]: f for s in schema if s["id"] == "metadata" for f in s["fields"]}
    assert fields["metadata_genre"].get("rowToggle") is True
    sep = fields["metadata_delimiter_genre"]
    assert sep.get("inline") is True and sep.get("third") is False, "a full row under the switch, not a third"
    assert sep.get("depends_on") == "metadata_genre"
    keys = [f["key"] for s in schema if s["id"] == "metadata" for f in s["fields"]]
    assert "metadata_genre_artist_fallback" not in fields, "the fallback is the Genre row's nested key, not a row"
    assert fields["metadata_genre"]["child_key"] == "metadata_genre_artist_fallback"
    assert keys.index("metadata_delimiter_genre") == keys.index("metadata_genre") + 1


def test_notifications_master_rides_beside_the_title(schema):
    notify = next((s for s in schema if s["id"] == "notifications"), None)
    if notify is None:
        pytest.skip("no banner support, no Notifications section")
    masters = [f for f in notify["fields"] if f.get("master")]
    assert [f["key"] for f in masters] == ["notify_enabled"]
    # It is no tile, and it says what off means and what SAVE will do.
    assert "notify_enabled" not in [f["key"] for f in _switches(notify)]
    assert masters[0]["off_note"] and masters[0]["arm_note"]
    # The only master on the page: every other section has none.
    assert not [f["key"] for s in schema if s is not notify for f in s["fields"] if f.get("master")]
