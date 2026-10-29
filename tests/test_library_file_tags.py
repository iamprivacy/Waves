"""The library scan keeps every text tag a file carries (file_tags).

Real files, built here: a FLAC (Vorbis comments) and an MP3 (ID3 frames)
are read by the real reader, so the promises are mutagen's own behavior,
not a stub's: every text tag is kept in the format-neutral ``easy`` view
and the format's own ``raw`` keys, pictures and binary frames never are;
the scan writes one row per file with its size and mtime; a tag edited in
place (the folder's mtime does not move) is caught by the rolling re-stat
once the folder is due, and not before; a cache from before the capture
re-reads each folder once to backfill.
"""

from __future__ import annotations

import json
import os
import struct
import time
import types

import mutagen.flac
import mutagen.id3
import pytest

from waves import library_index
from waves.library_index import LibraryIndex, _read_album_tags


def _flac(path, **tags):
    """A tagless, frameless but valid FLAC, then tagged by mutagen."""
    streaminfo = struct.pack(">HH", 4096, 4096) + b"\x00" * 6
    # 44100 Hz, 2 channels, 16 bits, 0 samples: 20+3+5+36 bits, then the MD5.
    streaminfo += (44100 << 44 | 1 << 41 | 15 << 36).to_bytes(8, "big") + b"\x00" * 16
    with open(path, "wb") as fh:
        fh.write(b"fLaC" + bytes([0x80]) + len(streaminfo).to_bytes(3, "big") + streaminfo)
    f = mutagen.flac.FLAC(path)
    for key, value in tags.items():
        f[key] = value
    pic = mutagen.flac.Picture()
    pic.data = b"\x89PNG not really"
    f.add_picture(pic)
    f.save()


def _mp3(path):
    """A few silent MPEG-1 Layer III frames under an ID3 tag."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    with open(path, "wb") as fh:
        fh.write(frame * 8)
    id3 = mutagen.id3.ID3()
    id3.add(mutagen.id3.TIT2(encoding=3, text=["Song"]))
    id3.add(mutagen.id3.TALB(encoding=3, text=["Record"]))
    id3.add(mutagen.id3.TPE1(encoding=3, text=["Someone"]))
    id3.add(mutagen.id3.TCON(encoding=3, text=["Shoegaze"]))
    id3.add(mutagen.id3.TXXX(encoding=3, desc="MusicBrainz Artist Id", text=["mb-artist-1"]))
    id3.add(mutagen.id3.UFID(owner="http://musicbrainz.org", data=b"mb-recording-1"))
    id3.add(mutagen.id3.COMM(encoding=3, lang="eng", desc="", text=["ripped with care"]))
    id3.add(mutagen.id3.USLT(encoding=3, lang="eng", desc="", text="line one\nline two"))
    id3.add(mutagen.id3.TXXX(encoding=3, desc="LYRICS", text=["line one"]))
    id3.add(mutagen.id3.TXXX(encoding=3, desc="UNSYNCEDLYRICS", text=["line one"]))
    id3.add(mutagen.id3.APIC(encoding=3, mime="image/png", type=3, desc="", data=b"\x89PNG not really"))
    id3.add(mutagen.id3.PRIV(owner="someone", data=b"\x00\x01\x02"))
    id3.save(path)


def test_a_flac_keeps_every_text_tag_and_no_picture(tmp_path):
    path = str(tmp_path / "01.flac")
    _flac(
        path,
        title="Song",
        album="Record",
        artist="Someone",
        genre=["Dream Pop", "Shoegaze"],
        composer="A Writer",
        musicbrainz_artistid="mb-artist-1",
        lyrics="line one\nline two",
        unsyncedlyrics="line one\nline two",
        label="Indie",
    )
    tags = _read_album_tags(path)
    easy = tags["tags"]["easy"]
    assert easy["genre"] == ["Dream Pop", "Shoegaze"]
    assert easy["composer"] == ["A Writer"] and easy["label"] == ["Indie"]
    assert easy["musicbrainz_artistid"] == ["mb-artist-1"]
    # Lyrics stay in the file: nothing answers from the cache with them.
    assert "lyrics" not in easy and "unsyncedlyrics" not in easy
    assert not any("picture" in k.lower() for k in easy)
    assert tags["genre"] == "Dream Pop; Shoegaze"
    st = os.stat(path)
    assert tags["size"] == st.st_size and tags["mtime_ns"] == st.st_mtime_ns


def test_an_mp3_keeps_its_frames_and_neutral_names_but_no_binary(tmp_path):
    path = str(tmp_path / "01.mp3")
    _mp3(path)
    tags = _read_album_tags(path)
    assert tags is not None and tags["title"] == "Song"
    easy, raw = tags["tags"]["easy"], tags["tags"]["raw"]
    assert easy["genre"] == ["Shoegaze"] and easy["musicbrainz_artistid"] == ["mb-artist-1"]
    assert raw["TCON"] == ["Shoegaze"]
    assert raw["TXXX:MusicBrainz Artist Id"] == ["mb-artist-1"]
    # Lyrics in a user text frame stay in the file too.
    assert not any("lyrics" in k.lower() for k in raw), sorted(raw)
    assert raw["UFID:http://musicbrainz.org"] == ["mb-recording-1"]
    assert raw["COMM::eng"] == ["ripped with care"]
    assert not [k for k in raw if k.startswith(("APIC", "PRIV", "USLT"))]


def test_tag_text_keeps_text_and_drops_binary():
    assert library_index._tag_text((3, 12)) == "3/12"
    assert library_index._tag_text(b"utf-8 text") == "utf-8 text"
    assert library_index._tag_text(b"\x00\x01\x02") is None
    assert library_index._tag_text(b"\xff\xfe") is None
    assert library_index._tag_text("  ") is None
    assert len(library_index._tag_text("x" * 100_000)) == library_index._TAG_VALUE_CAP


def _album(tmp_path, genre="Shoegaze"):
    lib = tmp_path / "lib"
    folder = lib / "Someone" / "Record"
    folder.mkdir(parents=True)
    for n in (1, 2):
        _flac(str(folder / f"0{n}.flac"), title=f"Song {n}", album="Record", artist="Someone", genre=genre)
    return str(lib), str(folder)


def _rows(idx, folder):
    with idx._lock:
        return idx._conn.execute(
            "SELECT name, size, mtime_ns, genre, tags FROM file_tags WHERE folder_path = ? ORDER BY name", (folder,)
        ).fetchall()


def _age_check(idx, folder, seconds):
    with idx._lock:
        idx._conn.execute("UPDATE albums SET tags_checked = ? WHERE folder_path = ?", (time.time() - seconds, folder))
        idx._conn.commit()


def test_the_scan_stores_a_row_per_file_and_catches_a_tag_edited_in_place(tmp_path):
    lib, folder = _album(tmp_path)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    assert idx.refresh(lib) == 1
    rows = _rows(idx, folder)
    assert [r[0] for r in rows] == ["01.flac", "02.flac"]
    assert all(r[3] == "Shoegaze" and json.loads(r[4])["easy"]["genre"] == ["Shoegaze"] for r in rows)

    # Retag one file in place, then put the folder's mtime back: the walk
    # alone would call the folder unchanged.
    dir_times = os.stat(folder)
    time.sleep(0.01)
    f = mutagen.flac.FLAC(os.path.join(folder, "01.flac"))
    f["genre"] = "Slowcore"
    f.save()
    os.utime(folder, ns=(dir_times.st_atime_ns, dir_times.st_mtime_ns))

    # Checked moments ago: not due, so the cache keeps the old answer.
    idx.refresh(lib)
    assert _rows(idx, folder)[0][3] == "Shoegaze"
    # Due again: the re-stat sees the file moved and the folder is re-read.
    _age_check(idx, folder, library_index._TAG_RECHECK_S + 60)
    idx.refresh(lib)
    assert _rows(idx, folder)[0][3] == "Slowcore"
    # A due folder whose files did not move only has its clock restarted.
    _age_check(idx, folder, library_index._TAG_RECHECK_S + 60)
    reads = []
    idx._read_tags = lambda p: reads.append(p) or _read_album_tags(p)
    idx.refresh(lib)
    assert reads == []
    with idx._lock:
        checked = idx._conn.execute("SELECT tags_checked FROM albums").fetchone()[0]
    assert time.time() - checked < 60
    idx.close()


def test_lyrics_keys_are_known_in_every_format():
    for key in (
        "LYRICS",
        "UNSYNCEDLYRICS",
        "USLT::eng",
        "USLT:desc:eng",
        "SYLT::eng",
        "\xa9lyr",
        "WM/Lyrics",
        "TXXX:LYRICS",
        "TXXX:UNSYNCEDLYRICS",
        "----:com.apple.iTunes:LYRICS",
        "----:com.apple.iTunes:UNSYNCEDLYRICS",
    ):
        assert library_index._is_lyrics_key(key), key
    for key in ("title", "TXXX:Lyricist", "lyricist", "COMM::eng", "----:com.apple.iTunes:LYRICIST"):
        assert not library_index._is_lyrics_key(key), key


def test_a_changed_folder_is_read_before_the_cache_backfills_its_tags(tmp_path, monkeypatch):
    # One reader at a time, so the order the files are asked for is the
    # order they were queued in.
    monkeypatch.setattr(library_index, "_READ_WORKERS", 1)
    lib = tmp_path / "lib"
    old = lib / "Artist" / "a_old"
    new = lib / "Artist" / "b_new"
    for folder in (old, new):
        folder.mkdir(parents=True)
        for n in (1, 2):
            _flac(str(folder / f"0{n}.flac"), title=f"T{n}", album=folder.name, artist="Someone")
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    idx.refresh(str(lib))
    # The whole cache is from before file_tags; then one folder gains a file.
    with idx._lock:
        idx._conn.execute("DELETE FROM file_tags")
        idx._conn.commit()
    time.sleep(0.01)
    _flac(str(new / "03.flac"), title="T3", album="b_new", artist="Someone")
    reads = []
    idx._read_tags = lambda p: reads.append(os.path.basename(os.path.dirname(p))) or _read_album_tags(p)
    idx.refresh(str(lib))
    assert reads[:3] == ["b_new"] * 3 and reads[3:] == ["a_old"] * 2
    idx.close()


def test_lyrics_an_older_reader_cached_are_dropped_once(tmp_path):
    db = str(tmp_path / "library.sqlite3")
    idx = LibraryIndex(db, read_tags=_read_album_tags)
    old = {"easy": {"title": ["Song"]}, "raw": {"TXXX:LYRICS": ["line one"], "TXXX:Lyricist": ["A Writer"]}}
    with idx._lock:
        idx._conn.execute(
            "INSERT INTO file_tags (folder_path, name, tags) VALUES (?, ?, ?)", ("/lib/a", "01.mp3", json.dumps(old))
        )
        idx._conn.execute("DELETE FROM meta WHERE key = ?", (library_index._LYRICS_SCRUB_KEY,))
        idx._conn.commit()
    idx.close()
    idx = LibraryIndex(db, read_tags=_read_album_tags)
    with idx._lock:
        (raw,) = idx._conn.execute("SELECT tags FROM file_tags WHERE name = '01.mp3'").fetchone()
    assert json.loads(raw) == {"easy": {"title": ["Song"]}, "raw": {"TXXX:Lyricist": ["A Writer"]}}
    idx.close()


def test_a_cache_from_before_the_capture_backfills_once(tmp_path):
    lib, folder = _album(tmp_path)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    idx.refresh(lib)
    with idx._lock:
        idx._conn.execute("DELETE FROM file_tags")
        idx._conn.execute("UPDATE albums SET tags_checked = NULL")
        idx._conn.commit()
    reads = []
    idx._read_tags = lambda p: reads.append(p) or _read_album_tags(p)
    idx.refresh(lib)
    assert len(reads) == 2 and len(_rows(idx, folder)) == 2
    reads.clear()
    idx.refresh(lib)
    assert reads == []
    idx.close()


def test_a_folder_that_goes_takes_its_tags_with_it(tmp_path):
    lib, folder = _album(tmp_path)
    # A second album stays, so the walk is not an empty one (which never prunes).
    keep = tmp_path / "lib" / "Other" / "Kept"
    keep.mkdir(parents=True)
    _flac(str(keep / "01.flac"), title="Kept", album="Kept", artist="Other")
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    idx.refresh(lib)
    for name in os.listdir(folder):
        os.remove(os.path.join(folder, name))
    os.rmdir(folder)
    idx.refresh(lib)
    idx.refresh(lib)
    assert _rows(idx, folder) == [] and len(_rows(idx, str(keep))) == 1
    idx.close()


def test_a_file_name_that_is_not_utf8_is_stored_and_found_again(tmp_path, monkeypatch):
    # A POSIX library can hold a Latin-1 name, which Python hands over with
    # surrogate escapes and sqlite refuses. It must not break the scan: the
    # row is stored (as its bytes in hex) and the re-stat finds the file.
    lib, folder = _album(tmp_path)
    bad = "Caf\udce9.flac"
    stub = {"album": "Record", "artist": "Someone", "title": "Song", "size": 7, "mtime_ns": 11, "tags": {}}
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=lambda p: dict(stub))
    idx.refresh(lib)
    row = idx._read_row((folder, bad, 1.0, 1, (bad,)), lambda: True)
    idx._upsert([row])
    stored = _rows(idx, folder)
    assert [r[0] for r in stored] == [library_index._RAW_NAME + os.fsencode(bad).hex()]
    assert library_index._stored_name(stored[0][0]) == bad

    seen = []
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if not str(path).startswith(folder + os.sep):
            return real_stat(path, *args, **kwargs)
        seen.append(path)
        return types.SimpleNamespace(st_size=7, st_mtime_ns=0)

    _age_check(idx, folder, library_index._TAG_RECHECK_S + 60)
    monkeypatch.setattr(library_index.os, "stat", fake_stat)
    assert idx._tags_moved(lambda: True) == 1  # mtime_ns 0 != 11: moved
    assert seen == [os.path.join(folder, bad)]
    idx.close()


def test_a_batch_that_fails_to_write_leaves_nothing_half_written(tmp_path):
    lib, folder = _album(tmp_path)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    idx.refresh(lib)
    before = _rows(idx, folder)
    row = idx._read_row((folder, "01.flac", 1.0, 2, ("01.flac", "02.flac")), lambda: True)
    # A value sqlite cannot bind, in the batch's last statement group.
    broken = (*row[:18], [("01.flac", 1, 1, "", object())])
    with pytest.raises(Exception):  # noqa: B017 (whatever sqlite3 raises for the bind)
        idx._upsert([broken])
    assert not idx._conn.in_transaction
    assert _rows(idx, folder) == before
    idx.close()


def _wav(path):
    """A short silent WAV with an ID3 tag carrying a picture and a private frame."""
    import wave

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    import mutagen.wave

    f = mutagen.wave.WAVE(path)
    f.add_tags()
    f.tags.add(mutagen.id3.TIT2(encoding=3, text=["Song"]))
    f.tags.add(mutagen.id3.TCON(encoding=3, text=["Ambient"]))
    f.tags.add(mutagen.id3.APIC(encoding=3, mime="image/png", type=3, desc="", data=b"\x89PNG" + b"\x01" * 4000))
    f.tags.add(mutagen.id3.PRIV(owner="someone", data=b"\x00\x01\x02"))
    f.save()


def test_a_wav_keeps_its_text_frames_and_never_a_picture(tmp_path):
    path = str(tmp_path / "01.wav")
    _wav(path)
    tags = _read_album_tags(path)["tags"]
    assert tags["raw"]["TIT2"] == ["Song"] and tags["raw"]["TCON"] == ["Ambient"]
    blob = json.dumps(tags)
    assert "APIC" not in blob and "PRIV" not in blob and len(blob) < 500


def test_tag_text_never_stores_an_object_as_its_repr():
    import mutagen.apev2
    import mutagen.id3

    assert library_index._tag_text(mutagen.id3.ID3TimeStamp("2001-05-01")) == "2001-05-01"
    assert library_index._tag_text(mutagen.apev2.APEValue(b"\xff\xd8junk", mutagen.apev2.BINARY)) is None
    assert library_index._tag_text(mutagen.id3.PRIV(owner="x", data=b"\x00")) is None
    assert library_index._tag_text(object()) is None

    class ASFUnicodeAttribute:
        value = "A Writer"

    class ASFByteArrayAttribute:
        value = b"\x89PNG\x00\x01"

    assert library_index._tag_text(ASFUnicodeAttribute()) == "A Writer"
    assert library_index._tag_text(ASFByteArrayAttribute()) is None


def test_a_file_renamed_in_place_gets_its_folder_read_again(tmp_path):
    lib, folder = _album(tmp_path)
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"), read_tags=_read_album_tags)
    idx.refresh(lib)
    dir_times = os.stat(folder)
    os.rename(os.path.join(folder, "02.flac"), os.path.join(folder, "02 renamed.flac"))
    os.utime(folder, ns=(dir_times.st_atime_ns, dir_times.st_mtime_ns))
    _age_check(idx, folder, library_index._TAG_RECHECK_S + 60)
    idx.refresh(lib)
    assert [r[0] for r in _rows(idx, folder)] == ["01.flac", "02 renamed.flac"]
    idx.close()
