"""The search sort control is remembered across launches.

The order is stored by NAME (relevance, date, name, popularity) rather than
by index, so the option list can change without a saved choice landing on
the wrong entry, and the direction is a real bool. ``setWavesPref`` coerces
against the default's type, so both survive a round trip through it.
"""

from __future__ import annotations

from types import SimpleNamespace

from waves.waves_ui import backend


def _bridge():
    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b._waves_prefs = backend.WavesBridge._default_waves_prefs(b)
    b._save_waves_prefs = lambda: None
    b._factory_reset = False
    b.settings = SimpleNamespace(data=SimpleNamespace())
    return b


def test_the_sort_prefs_default_to_relevance_descending():
    prefs = backend.WavesBridge._default_waves_prefs(_bridge())
    assert prefs["search_sort"] == "relevance"
    assert prefs["search_sort_asc"] is False


def test_the_sort_prefs_round_trip_with_their_types():
    b = _bridge()
    b.setWavesPref("search_sort", "popularity")
    b.setWavesPref("search_sort_asc", True)
    assert b.wavesPref("search_sort") == "popularity"
    assert b.wavesPref("search_sort_asc") is True
    # The QML side hands the bool over as a real bool, but a string form
    # (the str-coercing path) must not turn "false" into a truthy value.
    b.setWavesPref("search_sort_asc", "false")
    assert b.wavesPref("search_sort_asc") is False


def test_quality_round_trips_by_name():
    """Issue #52: Quality is the fifth sort, stored by name like the rest."""
    b = _bridge()
    b.setWavesPref("search_sort", "quality")
    assert b.wavesPref("search_sort") == "quality"


def test_every_sort_label_has_its_key_in_the_same_place():
    """The box shows labels and stores keys by index; a list one longer than
    the other would save the wrong name for every entry past the gap."""
    import json
    import re
    from pathlib import Path

    src = (Path(backend.__file__).parent / "qml" / "Main.qml").read_text(encoding="utf-8")
    keys = json.loads(re.search(r"readonly property var sortKeys: (\[[^\]]*\])", src).group(1))
    box = src[src.index("id: sortBox") :]
    labels = json.loads(re.search(r"model: (\[[^\]]*\])", box).group(1))
    assert keys == ["relevance", "date", "name", "popularity", "quality"]
    assert len(labels) == len(keys)
    assert labels[keys.index("quality")] == "Quality"
