"""Every release date on screen reads MM-DD-YYYY (files and sorting keep
YYYY-MM-DD): a Text that shows a date goes through the one formatter,
``dateText`` (or ``releasesDateLabel``, which falls back to it), so no
surface shows the stored form beside a neighbor that does not."""

from __future__ import annotations

import re
from pathlib import Path

QML_MAIN = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "Main.qml"
_DATE_TEXT = re.compile(r"\btext:\s*([^\n]*)")
_DATE_WORD = re.compile(r"\b(date|vcDate|listed|day)\b")
_FORMATTERS = ("dateText(", "releasesDateLabel(", "relEntryDate(")


def test_every_date_on_screen_goes_through_the_formatter() -> None:
    lines = QML_MAIN.read_text(encoding="utf-8").splitlines()
    seen, strays = 0, []
    for n, line in enumerate(lines, 1):
        code = line.split("//", 1)[0]
        for m in _DATE_TEXT.finditer(code):
            binding = m.group(1)
            formatted = any(f in binding for f in _FORMATTERS)
            if formatted:
                seen += 1
            elif _DATE_WORD.search(binding):
                strays.append(n)
    assert seen >= 5, "the scan found too few date texts to mean anything"
    assert not strays, f"Main.qml shows a date without dateText at lines {strays}"


def test_the_formatter_reads_month_day_year() -> None:
    code = QML_MAIN.read_text(encoding="utf-8")
    body = re.search(r"function dateText\(d\) \{(.*?)\n    \}", code, re.S).group(1)
    assert 'd.slice(5, 7) + "-" + d.slice(8, 10) + "-" + d.slice(0, 4)' in body
