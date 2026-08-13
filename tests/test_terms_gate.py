"""The first-run terms gate.

The gate's wording is canonical: it is the same text published at
getwaves.dev/terms/, and a divergence between the two makes it unclear which
terms a user actually accepted. These tests pin the clauses that carry legal
weight, the enforceability shape of the gate (full window, no way out but
acknowledging), and the version stamp that says WHICH terms were agreed to.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QML_MAIN = ROOT / "tidaler" / "waves_ui" / "qml" / "Main.qml"
README = ROOT / "README.md"

TERMS_VERSION = "1.0"
TERMS_STAMP = "Terms v1.0 (10 August 2026)"

# Every clause of the canonical text, in the exact wording that was chosen.
CANONICAL_CLAUSES = (
    "Waves is a personal, non-commercial tool for accessing your own TIDAL account.",
    "You will use Waves only for lawful, personal, non-commercial purposes, and only "
    "with content you are authorized to access.",
    "You will not use Waves to infringe copyright or to reproduce, distribute, or "
    "pirate any content. Respect the rights of artists and rights-holders.",
    "Your use of Waves may violate TIDAL's Terms of Service. You accept that risk and "
    "any consequences to your account, and you are responsible for complying with all "
    "laws that apply to you.",
    "without warranty of any kind, and to the fullest extent permitted by law its "
    "developers and contributors accept no liability for any damages arising from it. "
    "See sections 15 and 16 of the AGPL-3.0.",
    "You will indemnify and hold harmless the developers and contributors of Waves "
    "against any claim, loss or demand arising from your use of it or your breach of "
    "these terms.",
    "Waves is not affiliated with, endorsed by, or sponsored by TIDAL. TIDAL is a "
    "trademark of its owner, used here only to identify the service Waves works with.",
)


def _gate_source() -> str:
    src = QML_MAIN.read_text(encoding="utf-8")
    m = re.search(r"id: termsGate\b(.*?)\n    // =====", src, re.S)
    assert m, "the terms gate must exist in Main.qml"
    return m.group(1)


def _gate_body_text() -> str:
    """The gate's body string, QML concatenation collapsed."""
    gate = _gate_source()
    m = re.search(r'text: ("Waves is a personal.*?TIDAL account\..*?)\n\s*\}', gate, re.S)
    assert m, "the gate body text must be a plain concatenation of string literals"
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    return "".join(parts).replace('\\"', '"').replace("\\n", "\n")


def test_gate_text_is_the_canonical_wording():
    body = _gate_body_text()
    for clause in CANONICAL_CLAUSES:
        assert clause in body, f"the gate lost the canonical clause: {clause!r}"


def test_gate_drops_the_retired_framing():
    """ "Educational" is stock warez phrasing, and the old ToS line implied the
    developer had blessed a compliant use. Neither may come back."""
    body = _gate_body_text()
    lowered = body.lower()
    assert "educational" not in lowered
    assert "complying with tidal's terms of service" not in lowered


def test_gate_body_stays_plain_text():
    gate = _gate_source()
    m = re.search(r"id: termsBody\b(.*?)\n\s{20}\}", gate, re.S)
    assert m and "textFormat: Text.PlainText" in m.group(1)


def test_gate_cannot_be_escaped():
    gate = _gate_source()
    # Full window, and every click behind it eaten: no close control, no
    # dismiss-on-click-outside.
    assert "anchors.fill: parent" in gate
    assert "MouseArea { anchors.fill: parent; hoverEnabled: true }" in gate
    # The only way past is the affirmative checkbox plus the button.
    assert "enabled: ackChk.checked" in gate
    assert "onClicked: if (ackChk.checked) {" in gate
    # Wanted until accepted AT THIS VERSION, never on a timer or a dismissal
    # count (termsCurrentAccepted folds the stored version into the test). The
    # card rides the launch reveal itself, so the wordmark's zoom fades into
    # the agreement and the interface is never the thing revealed first.
    assert "readonly property bool wanted: waves.loggedIn && setupSettings.ffmpegSetupDone" in gate
    assert "&& !root.termsCurrentAccepted" in gate
    assert "visible: wanted && root.bootContentShown > 0" in gate
    assert "opacity: root.bootContentShown" in gate


def test_gate_body_scrolls_instead_of_pushing_the_button_off_screen():
    """A short window must still show the checkbox and the button."""
    gate = _gate_source()
    assert "id: termsBodyFlick" in gate, "the body must live in a Flickable"
    assert "Math.min(termsCol.implicitHeight + 40, termsGate.height - 32)" in gate
    assert "Layout.fillHeight: true" in gate


def test_accepted_version_is_persisted_alongside_the_flag():
    """A boolean cannot say WHICH terms were accepted; a later revision needs
    to re-prompt only the users who agreed to something older."""
    src = QML_MAIN.read_text(encoding="utf-8")
    assert f'readonly property string termsVersion: "{TERMS_VERSION}"' in src
    assert f'readonly property string termsVersionStamp: "{TERMS_STAMP}"' in src
    m = re.search(r"Settings \{\s*id: legalSettings;.*?\n    \}", src, re.S)
    assert m, "the legal Settings block must exist"
    block = m.group(0)
    assert "property bool termsAccepted: false" in block
    assert 'property string termsAcceptedVersion: ""' in block
    assert 'property string termsAcceptedDate: ""' in block

    gate = _gate_source()
    assert "legalSettings.termsAcceptedVersion = root.termsVersion" in gate
    assert "legalSettings.termsAcceptedDate = new Date().toISOString().slice(0, 10)" in gate


def test_version_stamp_is_visible_in_the_gate():
    gate = _gate_source()
    assert "text: root.termsVersionStamp" in gate


def test_an_older_accepted_version_re_prompts():
    """Storing the version is only half the promise: the gate must READ it, or
    bumping termsVersion re-prompts nobody (the boolean stays true forever).
    The gate keys on termsCurrentAccepted, which is the acceptance AND the
    stored version matching the current one, so an acceptance of 1.0 stops
    counting the day the terms become 1.1."""
    src = QML_MAIN.read_text(encoding="utf-8")
    m = re.search(
        r"readonly property bool termsCurrentAccepted: legalSettings\.termsAccepted\s*\n\s*"
        r"&& legalSettings\.termsAcceptedVersion === termsVersion",
        src,
    )
    assert m, "termsCurrentAccepted must AND the flag with a stored-version match"
    # Every gate that sequences around the terms must key on the same test, or
    # a revision re-prompt stacks with (or is masked by) its neighbours.
    assert (
        src.count("root.termsCurrentAccepted") >= 3
    ), "the terms gate, the ffmpeg gate and the update opt-in card must all read termsCurrentAccepted"
    for stale in ("&& legalSettings.termsAccepted\n", "&& !legalSettings.termsAccepted"):
        assert stale not in src, "a gate still reads the bare boolean and ignores the stored version"


def test_readme_disclaimer_matches_the_gate():
    disclaimer = README.read_text(encoding="utf-8").split("## Disclaimer", 1)[1]
    for phrase in (
        "not affiliated with, endorsed by, or sponsored by TIDAL",
        "TIDAL is a trademark of its owner",
        "personal, non-commercial tool",
        "it is not a way around a subscription",
        "Your use may violate TIDAL's Terms of Service, and you accept that risk",
        "sections 15 and 16",
        "indemnify and hold harmless",
        "https://getwaves.dev/terms/",
        "https://github.com/iamprivacy/Waves/issues",
    ):
        assert phrase in disclaimer, f"the README disclaimer lost: {phrase!r}"
    # The no-monitoring fact is a privacy statement, never a liability shield.
    assert "not an excuse" in disclaimer
    assert "educational" not in disclaimer.lower()


def test_no_takedown_address_is_published():
    """GitHub issues is the only published contact channel, deliberately: a
    takedown-specific address implies wrongdoing is anticipated. It must not
    come back in the README, the app, or any repo metadata."""
    for path in (README, QML_MAIN, ROOT / "CHANGELOG.md"):
        text = path.read_text(encoding="utf-8").lower()
        for banned in ("legal@", "abuse@", "dmca@", "takedown@"):
            assert banned not in text, f"{path.name} publishes a {banned} address"
