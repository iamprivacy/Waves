"""Settings help text keeps its commas.

A blanket em-dash sweep once rewrote the normaliser itself from `" — "` to
`", "`, so every description on the settings page had its commas turned into
semicolons: "16 Bit, 44,1 kHz" rendered as "16 Bit; 44,1 kHz", and the artist
delimiter fields advertised a default of "; " while actually shipping ", ".
Only the em dash may be rewritten.
"""

from __future__ import annotations

from waves.model.cfg import HelpSettings
from waves.waves_ui.backend import WavesBridge


class _Stub:
    pass


def _help_for(text: str) -> str:
    stub = _Stub()

    class _Help:
        probe = text

    stub._help = _Help()
    return WavesBridge._help_for(stub, "probe")


def test_commas_survive():
    assert _help_for("16 Bit, 44,1 kHz, stereo") == "16 Bit, 44,1 kHz, stereo"


def test_em_dash_becomes_plain_punctuation():
    assert "—" not in _help_for("Downsample — never upsample")
    assert _help_for("Downsample — never upsample") == "Downsample; never upsample"


def test_delimiter_help_advertises_the_real_default():
    real_default = ", "
    help_text = _help_for(HelpSettings().filename_delimiter_artist)
    assert f"'{real_default}'" in help_text, help_text


def test_missing_key_is_empty():
    assert WavesBridge._help_for(_Stub_with_no_help(), "nope") == ""


def _Stub_with_no_help():
    stub = _Stub()

    class _Empty:
        pass

    stub._help = _Empty()
    return stub
