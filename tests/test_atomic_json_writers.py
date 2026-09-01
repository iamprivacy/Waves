"""The app's own JSON files are written whole, flushed, and swapped in.

Every one of these self-heals when it is damaged, so the cost of losing one is
a fresh crawl or a lost set of window preferences rather than lost music. That
is still a cost worth one flush: os.replace alone can be committed ahead of the
bytes it renames, so a power cut leaves an empty file under the real name.

The tile-art cache had no staging at all and truncated its real file on every
save.
"""

import json
import os
import pathlib
from unittest.mock import patch

import pytest

from waves.waves_ui.backend import _FACTORY_WIPE_FILES, _write_json_atomic, _write_text_atomic


class TestAtomicWriters:
    def test_the_payload_lands_complete(self, tmp_path):
        target = tmp_path / "cache.json"

        _write_json_atomic(str(target), {"a": [1, 2, 3]}, indent=1)

        assert json.loads(target.read_text(encoding="utf-8")) == {"a": [1, 2, 3]}

    def test_an_existing_file_survives_a_failed_write(self, tmp_path):
        target = tmp_path / "cache.json"
        target.write_text('{"kept": true}', encoding="utf-8")

        with (
            patch("waves.waves_ui.backend.os.replace", side_effect=OSError(28, "No space left on device")),
            pytest.raises(OSError, match="No space left on device"),
        ):
            _write_json_atomic(str(target), {"replacement": True})

        assert json.loads(target.read_text(encoding="utf-8")) == {"kept": True}

    def test_no_temp_sibling_is_left_behind(self, tmp_path):
        target = tmp_path / "cache.json"

        with (
            patch("waves.waves_ui.backend.os.replace", side_effect=OSError(28, "No space left on device")),
            pytest.raises(OSError),
        ):
            _write_json_atomic(str(target), {"a": 1})

        assert list(tmp_path.iterdir()) == []

    def test_the_bytes_are_flushed_before_the_swap(self, tmp_path):
        # Without the fsync a crash can land the rename ahead of the data, so
        # the real name ends up pointing at an empty file.
        target = tmp_path / "cache.json"
        order: list[str] = []
        fsync_real = os.fsync
        replace_real = os.replace

        def _fsync(fd):
            order.append("fsync")

            return fsync_real(fd)

        def _replace(src, dst):
            order.append("replace")

            return replace_real(src, dst)

        with (
            patch("waves.waves_ui.backend.os.fsync", _fsync),
            patch("waves.waves_ui.backend.os.replace", _replace),
        ):
            _write_text_atomic(str(target), "{}")

        assert order == ["fsync", "replace"]

    def test_every_temp_sibling_is_wiped_by_a_factory_reset(self):
        # The staging files live in the config folder, so the reset that clears
        # that folder has to know each of them by name.
        staged = {name for name in _FACTORY_WIPE_FILES if name.endswith(".json")}
        temps = {name for name in _FACTORY_WIPE_FILES if name.endswith(".json.tmp")}

        assert {f"{name}.tmp" for name in staged} <= temps


class TestTheWritersAreTheOnesUsed:
    def test_the_prefs_writer_leaves_a_readable_file(self, tmp_path):
        target = tmp_path / "waves.json"

        _write_json_atomic(str(target), {"window": {"w": 1280}}, indent=2)

        assert json.loads(target.read_text(encoding="utf-8"))["window"]["w"] == 1280
        assert not pathlib.Path(str(target) + ".tmp").exists()


# --------------------------------------------------------------------------- #
# gap-round G-11: the wipe must know the per-write staging shape too
# --------------------------------------------------------------------------- #
def test_a_mkstemp_staging_leftover_is_wiped_by_a_factory_reset():
    # A hard kill mid-save strands "<name>.<random>.tmp"; for token.json that
    # stray holds the session token document, the one thing a factory reset
    # promises to remove.
    from waves.waves_ui.backend import _FACTORY_WIPE_LOG_PATTERNS

    for base in ("settings.json", "token.json", "waves.json", "page_cache.json", "browse_tile_art.json"):
        stray = f"{base}.k3j9x2ab.tmp"
        assert any(p.match(stray) for p in _FACTORY_WIPE_LOG_PATTERNS), stray


def test_the_staging_pattern_cannot_eat_a_foreign_file():
    # Anchored on both ends: only a name Waves itself stages may ever match.
    from waves.waves_ui.backend import _FACTORY_WIPE_LOG_PATTERNS

    for foreign in (
        "notes.json.k3j9x2ab.tmp",
        "mytoken.json.k3j9x2ab.tmp",
        "token.json.tmp.bak",
        "token.json.k3j9x2ab.tmp2",
        "token.json.backup.of.mine.tmp",
    ):
        assert not any(p.match(foreign) for p in _FACTORY_WIPE_LOG_PATTERNS), foreign
