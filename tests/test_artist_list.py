"""Artist list files (waves.artist_list): what Import list reads and Export
list writes. Reading must take a list from anywhere (a text editor, Excel in
any locale, a UTF-16 export) without splitting a name like "Earth, Wind &
Fire"; writing must read back exactly."""

from __future__ import annotations

import os

import pytest

from waves import artist_list


def _write(tmp_path, name, data):
    p = tmp_path / name
    if isinstance(data, str):
        data = data.encode("utf-8")
    p.write_bytes(data)
    return str(p)


def _names(rows):
    return [r["name"] for r in rows]


def test_txt_is_one_name_a_line(tmp_path):
    p = _write(tmp_path, "a.txt", "Radiohead\n\n  Björk  \nEarth, Wind & Fire\r\nPortishead\n")
    assert _names(artist_list.read_artists(p)) == ["Radiohead", "Björk", "Earth, Wind & Fire", "Portishead"]


def test_txt_comments_and_duplicates(tmp_path):
    p = _write(tmp_path, "a.txt", "# my artists\nRadiohead\n#1 Dads\nradiohead\nRADIOHEAD\n#\nBjork\nBjörk\n")
    assert _names(artist_list.read_artists(p)) == ["Radiohead", "#1 Dads", "Bjork"]


def test_csv_artist_column_among_others(tmp_path):
    p = _write(
        tmp_path,
        "a.csv",
        'Album,Artist,Year\nOK Computer,Radiohead,1997\nThat\'s the Way of the World,"Earth, Wind & Fire",1975\n',
    )
    assert _names(artist_list.read_artists(p)) == ["Radiohead", "Earth, Wind & Fire"]


def test_csv_without_a_header_uses_the_first_column(tmp_path):
    p = _write(tmp_path, "a.csv", "Radiohead,OK Computer\nPortishead,Dummy\n")
    assert _names(artist_list.read_artists(p)) == ["Radiohead", "Portishead"]


def test_one_column_csv_never_splits_a_name(tmp_path):
    p = _write(tmp_path, "a.csv", "Radiohead\nEarth, Wind & Fire\nPortishead\nMassive Attack\n")
    assert _names(artist_list.read_artists(p)) == ["Radiohead", "Earth, Wind & Fire", "Portishead", "Massive Attack"]


def test_one_column_csv_never_splits_names_with_the_same_comma_count(tmp_path):
    # Every line holds one comma, so the sniffer sees a steady two-column
    # table; the space after each comma says they are names.
    p = _write(tmp_path, "a.csv", "Earth, Wind & Fire\nTyler, The Creator\n")
    assert _names(artist_list.read_artists(p)) == ["Earth, Wind & Fire", "Tyler, The Creator"]


def test_headed_csv_with_spaced_commas_is_still_a_table(tmp_path):
    p = _write(tmp_path, "a.csv", "Artist, Album\nRadiohead, OK Computer\nPortishead, Dummy\n")
    assert _names(artist_list.read_artists(p)) == ["Radiohead", "Portishead"]


def test_one_column_csv_drops_its_header_and_quotes(tmp_path):
    p = _write(tmp_path, "a.csv", 'Artist\n"Earth, Wind & Fire"\nRadiohead\n"The ""Real"" Band"\n')
    assert _names(artist_list.read_artists(p)) == ["Earth, Wind & Fire", "Radiohead", 'The "Real" Band']


def test_semicolon_csv(tmp_path):
    p = _write(tmp_path, "a.csv", "Interpret;Artist;Album\nx;Radiohead;OK Computer\ny;Björk;Post\n")
    assert _names(artist_list.read_artists(p)) == ["Radiohead", "Björk"]


def test_quoted_comma_name_in_a_table(tmp_path):
    p = _write(tmp_path, "a.csv", 'Artist,Albums\n"Earth, Wind & Fire",3\n"Crosby, Stills & Nash",2\nABBA,9\n')
    assert _names(artist_list.read_artists(p)) == ["Earth, Wind & Fire", "Crosby, Stills & Nash", "ABBA"]


def test_utf8_bom(tmp_path):
    p = _write(tmp_path, "a.txt", "﻿Björk\nSigur Rós\n".encode())
    assert _names(artist_list.read_artists(p)) == ["Björk", "Sigur Rós"]


def test_utf16_tab_separated_txt_from_excel(tmp_path):
    p = _write(tmp_path, "a.txt", "Artist\tAlbum\r\nBeyoncé\tLemonade\r\nSigur Rós\tTakk\r\n".encode("utf-16"))
    assert _names(artist_list.read_artists(p)) == ["Beyoncé", "Sigur Rós"]


def test_cp1252(tmp_path):
    p = _write(tmp_path, "a.txt", "Beyoncé\nMotörhead\n".encode("cp1252"))
    assert _names(artist_list.read_artists(p)) == ["Beyoncé", "Motörhead"]


def test_cap(tmp_path):
    p = _write(tmp_path, "a.txt", "\n".join(f"Artist {i}" for i in range(artist_list.MAX_NAMES + 50)))
    rows = artist_list.read_artists(p)
    assert len(rows) == artist_list.MAX_NAMES
    assert rows[-1]["name"] == f"Artist {artist_list.MAX_NAMES - 1}"


def test_binary_is_refused(tmp_path):
    p = _write(tmp_path, "a.txt", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    with pytest.raises(ValueError):
        artist_list.read_artists(p)


def test_too_big_is_refused(tmp_path):
    p = _write(tmp_path, "a.txt", b"a\n" * (artist_list.MAX_BYTES // 2 + 1))
    with pytest.raises(ValueError):
        artist_list.read_artists(p)


def test_empty_file_is_an_empty_list(tmp_path):
    assert artist_list.read_artists(_write(tmp_path, "a.txt", "")) == []
    assert artist_list.read_artists(_write(tmp_path, "b.csv", "\n\n# nothing\n")) == []


def test_missing_file_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        artist_list.read_artists(str(tmp_path / "nope.txt"))


def test_csv_id_column_keeps_digits_only(tmp_path):
    p = _write(tmp_path, "a.csv", "Artist,TIDAL artist id\nRadiohead,64518\nBjörk,abc\nPortishead,\nABBA,٣٤\n")
    assert artist_list.read_artists(p) == [
        {"name": "Radiohead", "id": "64518"},
        {"name": "Björk"},
        {"name": "Portishead"},
        {"name": "ABBA"},
    ]


def test_a_plain_artist_id_column_is_not_taken_for_tidal(tmp_path):
    # Another service's export: its numeric ids would follow strangers.
    p = _write(tmp_path, "a.csv", "Artist,Artist ID\nRadiohead,399\nBjörk,27\n")
    assert artist_list.read_artists(p) == [{"name": "Radiohead"}, {"name": "Björk"}]


def test_write_csv(tmp_path):
    p = str(tmp_path / "out.csv")
    n = artist_list.write_artists(
        p, [{"name": "Radiohead", "id": "64518"}, {"name": "Earth, Wind & Fire", "id": 3}, {"name": ""}]
    )
    assert n == 2
    raw = (tmp_path / "out.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines() == [
        "Artist,TIDAL artist id",
        '"Earth, Wind & Fire",3',
        "Radiohead,64518",
    ]


def test_write_txt(tmp_path):
    p = str(tmp_path / "out.txt")
    assert artist_list.write_artists(p, [{"name": "björk", "id": "1"}, {"name": "ABBA", "id": "2"}]) == 2
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ABBA\nbjörk\n"


@pytest.mark.parametrize("ext", [".csv", ".txt"])
def test_round_trip(tmp_path, ext):
    rows = [
        {"name": "Earth, Wind & Fire", "id": "3"},
        {"name": "Sigur Rós", "id": "7"},
        {"name": 'The "Real" Band', "id": "11"},
    ]
    p = str(tmp_path / f"out{ext}")
    artist_list.write_artists(p, rows)
    back = artist_list.read_artists(p)
    if ext == ".csv":
        assert sorted(back, key=lambda r: r["name"]) == sorted(rows, key=lambda r: r["name"])
    else:
        assert sorted(_names(back)) == sorted(r["name"] for r in rows)


_APOSTROPHE_NAMES = [
    ("Bell 'n' Arc", "Earth, Wind & Fire"),
    ("Salt 'n' Pepa", "Earth, Wind & Fire"),
    ("'Til Tuesday", "Guns N' Roses"),
    ("'68 Comeback", "Howlin' Wolf"),
    ("Bell 'n' Arc", 'The "Real" Band'),
]


@pytest.mark.parametrize("pair", _APOSTROPHE_NAMES)
@pytest.mark.parametrize("plain", [3, 320])
def test_round_trip_keeps_quoted_names_beside_apostrophes(tmp_path, pair, plain):
    # The sniffer can take an apostrophe for the quote character; a file
    # Export list wrote must still read back exactly, short or long.
    names = [*pair, "Crosby, Stills, Nash & Young"] + [f"Plain Artist {i:03d}" for i in range(plain)]
    rows = [{"name": n, "id": str(100 + i)} for i, n in enumerate(names)]
    p = str(tmp_path / "out.csv")
    artist_list.write_artists(p, rows)
    back = artist_list.read_artists(p)
    assert sorted(back, key=lambda r: r["name"]) == sorted(rows, key=lambda r: r["name"])


def test_headerless_table_with_an_apostrophe_keeps_double_quotes(tmp_path):
    # No header, so the sniffer picks the delimiter; the quote stays '"'.
    plain = [f"Plain Artist {i:02d},{i}" for i in range(40)]
    p = tmp_path / "a.csv"
    p.write_text("\n".join(["Bell 'n' Arc,100", '"Earth, Wind & Fire",101', *plain]) + "\n", encoding="utf-8")
    names = _names(artist_list.read_artists(str(p)))
    assert names[:2] == ["Bell 'n' Arc", "Earth, Wind & Fire"]
    assert len(names) == 42


def test_write_to_an_unwritable_path_raises(tmp_path):
    with pytest.raises(OSError):
        artist_list.write_artists(os.path.join(str(tmp_path), "no", "such", "dir", "a.csv"), [{"name": "ABBA"}])
