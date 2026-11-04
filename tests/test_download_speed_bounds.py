"""The Speed & Pacing whole-number settings have real floors and ceilings.

Concurrent track downloads and Parallel chunks per track used to go to the
stepper's own 1..9999: a value past the shared HTTP pool's ten connections
only ever cost threads. The new Tracks finishing in the background count
bounds the ffmpeg processes and temp copies a slow machine is asked to carry
at once, and 0 turns background finishing off. The schema carries the bounds
so the stepper stops at them, and the save clamps so a hand-edited config
lands inside them too.
"""

from __future__ import annotations

from tests.test_settings_save_guard import _apply_bridge
from tests.test_settings_sections import _schema
from waves.model.cfg import HelpSettings
from waves.model.cfg import Settings as CfgSettings
from waves.waves_ui.backend import _INT_BOUNDS


def _pacing_fields() -> dict:
    section = next(s for s in _schema() if s["id"] == "pacing")
    return {f["key"]: f for f in section["fields"]}


def test_the_bounded_steppers_carry_their_bounds():
    fields = _pacing_fields()
    for key, (low, high) in _INT_BOUNDS.items():
        assert fields[key]["type"] == "int", key
        assert (fields[key]["minimum"], fields[key]["maximum"]) == (low, high), key


def test_the_finishing_count_sits_under_the_stream_count_with_its_label():
    section = next(s for s in _schema() if s["id"] == "pacing")
    keys = [f["key"] for f in section["fields"]]
    assert keys.index("downloads_finishing_max") == keys.index("downloads_concurrent_max") + 1
    field = _pacing_fields()["downloads_finishing_max"]
    assert field["label"] == "Tracks finishing in the background"
    assert field["value"] == 3


def test_the_bounds_are_what_the_design_promises():
    promised = {
        "downloads_concurrent_max": (1, 10),
        "downloads_finishing_max": (0, 8),
        "downloads_simultaneous_per_track_max": (1, 10),
    }
    assert promised == _INT_BOUNDS


def test_a_save_clamps_each_number_into_its_range():
    stub = _apply_bridge()
    stub.applySettings(
        {
            "downloads_concurrent_max": 99,
            "downloads_finishing_max": -3,
            "downloads_simultaneous_per_track_max": 0,
        }
    )
    d = stub.settings.data
    assert (d.downloads_concurrent_max, d.downloads_finishing_max, d.downloads_simultaneous_per_track_max) == (10, 0, 1)
    stub.applySettings({"downloads_concurrent_max": 0, "downloads_finishing_max": 50})
    assert (d.downloads_concurrent_max, d.downloads_finishing_max) == (1, 8)


def test_a_save_keeps_an_in_range_number_as_typed():
    stub = _apply_bridge()
    stub.applySettings({"downloads_concurrent_max": 5, "downloads_finishing_max": 2})
    d = stub.settings.data
    assert (d.downloads_concurrent_max, d.downloads_finishing_max) == (5, 2)


def test_the_new_setting_ships_on_with_a_short_help_that_names_off():
    assert CfgSettings().downloads_finishing_max == 3
    help_text = HelpSettings().downloads_finishing_max
    assert len(help_text) <= 115, "a stepper row's help must fit its row at the minimum window"
    assert "0 turns it off" in help_text
    assert chr(0x2014) not in help_text, "no em dashes in UI copy"


def test_a_hand_edited_config_lands_inside_the_bounds_on_load():
    """The save clamps, but a config file edited by hand never passes through
    it, and the engine and the queue read these numbers raw."""
    from waves.config import _clamp_bounded_numbers

    d = CfgSettings()
    d.downloads_concurrent_max = 50
    d.downloads_finishing_max = 100
    d.downloads_simultaneous_per_track_max = -2
    assert _clamp_bounded_numbers(d) is True
    assert (d.downloads_concurrent_max, d.downloads_finishing_max, d.downloads_simultaneous_per_track_max) == (10, 8, 1)


def test_an_in_range_config_is_left_alone_on_load():
    from waves.config import _clamp_bounded_numbers

    d = CfgSettings()
    d.downloads_finishing_max = 0
    assert _clamp_bounded_numbers(d) is False
    assert d.downloads_finishing_max == 0


def test_a_non_finite_number_in_a_hand_edited_config_resets_that_field_alone(tmp_path):
    """json reads Infinity and NaN, and from_json raised on them in a
    whole-number field: Infinity crashed the launch, NaN threw the whole file
    away. Only the field goes back to its default now."""
    import json

    from waves.config import BaseConfig

    raw = json.loads(CfgSettings().to_json())
    raw["downloads_finishing_max"] = float("inf")
    raw["downloads_concurrent_max"] = float("nan")
    raw["api_rate_limit_delay_sec"] = float("-inf")
    raw["download_base_path"] = "/somewhere/kept"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    cfg = BaseConfig.__new__(BaseConfig)
    cfg.cls_model = CfgSettings
    cfg.file_path = str(path)
    cfg.path_base = str(tmp_path)
    assert cfg.read(str(path)) is True
    stock = CfgSettings()
    d = cfg.data
    assert d.downloads_finishing_max == stock.downloads_finishing_max
    assert d.downloads_concurrent_max == stock.downloads_concurrent_max
    assert d.api_rate_limit_delay_sec == stock.api_rate_limit_delay_sec
    assert d.download_base_path == "/somewhere/kept", "every other setting survives"
    assert not list(tmp_path.glob("*.bak*")), "the file was set aside as broken"


def test_a_wrong_shaped_value_in_a_hand_edited_config_resets_that_field_alone(tmp_path):
    """dataclasses_json reads a number field with int(value) or float(value),
    so a word, an empty string, a list or an object in one raised out of
    from_json and the whole file was set aside: download folder, templates
    and toggles back to stock. A bool in a number field, and a list in a
    text field, were taken as 1 or as the literal "['...']". Each goes back
    to its own default now and the rest of the file survives."""
    import json

    from waves.config import BaseConfig

    raw = json.loads(CfgSettings().to_json())
    raw["downloads_finishing_max"] = "three"
    raw["downloads_concurrent_max"] = ""
    raw["downloads_simultaneous_per_track_max"] = [3]
    raw["download_delay_sec_min"] = {}
    raw["download_delay_sec_max"] = True
    raw["format_album"] = ["Albums/{album_title}"]
    raw["download_base_path"] = "/somewhere/kept"
    raw["format_mix"] = "7"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    cfg = BaseConfig.__new__(BaseConfig)
    cfg.cls_model = CfgSettings
    cfg.file_path = str(path)
    cfg.path_base = str(tmp_path)
    assert cfg.read(str(path)) is True
    stock = CfgSettings()
    d = cfg.data
    assert d.downloads_finishing_max == stock.downloads_finishing_max
    assert d.downloads_concurrent_max == stock.downloads_concurrent_max
    assert d.downloads_simultaneous_per_track_max == stock.downloads_simultaneous_per_track_max
    assert d.download_delay_sec_min == stock.download_delay_sec_min
    assert d.download_delay_sec_max == stock.download_delay_sec_max
    assert d.format_album == stock.format_album
    assert d.download_base_path == "/somewhere/kept", "every other setting survives"
    assert d.format_mix == "7", "a number spelled as text in a text field is still text"
    assert not list(tmp_path.glob("*.bak*")), "the file was set aside as broken"
