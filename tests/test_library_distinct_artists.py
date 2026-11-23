"""LibraryIndex.distinct_artists / artist_titles / first_audio_file: what
the Releases library import reads out of the scan cache."""

from __future__ import annotations

from waves.library_index import JoinedLibrary, LibraryIndex


def _row(folder, album, artist, tracks=()):
    return (folder, album, artist, "2020", 10, 0.0, 0, "flac", 0, 16, 44100, None, None, None, 0, 10, -1, list(tracks))


def _track(folder, title, artist):
    return (folder, title, artist, "flac", 0, 16, 44100, 200, -1)


def _index(tmp_path, rows):
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"))
    idx._upsert([_row(*r) for r in rows])
    return idx


def test_one_row_per_artist_with_album_count_and_folders(tmp_path):
    idx = _index(
        tmp_path,
        [
            ("/m/Caribou/Suddenly", "Suddenly", "Caribou"),
            ("/m/Caribou/Our Love", "Our Love", "Caribou"),
            ("/m/Nils/Spaces", "Spaces", "Nils Frahm"),
        ],
    )
    rows = idx.distinct_artists()
    assert [r["name"] for r in rows] == ["Caribou", "Nils Frahm"]
    assert rows[0]["albums"] == 2
    assert rows[0]["folders"] == ["/m/Caribou/Our Love", "/m/Caribou/Suddenly"]


def test_spellings_of_one_artist_fold_to_the_most_used(tmp_path):
    idx = _index(
        tmp_path,
        [
            ("/m/a", "A", "The Beatles"),
            ("/m/b", "B", "Beatles"),
            ("/m/c", "C", "The Beatles"),
        ],
    )
    rows = idx.distinct_artists()
    assert [(r["name"], r["albums"]) for r in rows] == [("The Beatles", 3)]


def test_various_artists_and_untagged_folders_are_left_out(tmp_path):
    idx = _index(
        tmp_path,
        [
            ("/m/va", "Hits", "Various Artists"),
            ("/m/none", "Untitled", ""),
            ("/m/x", "X", "Caribou"),
        ],
    )
    assert [r["name"] for r in idx.distinct_artists()] == ["Caribou"]


def test_at_most_three_folders_are_kept(tmp_path):
    idx = _index(tmp_path, [(f"/m/{i}", f"A{i}", "Caribou") for i in range(6)])
    (row,) = idx.distinct_artists()
    assert row["albums"] == 6 and len(row["folders"]) == 3


def test_first_audio_file_picks_by_name_and_skips_the_rest(tmp_path):
    folder = tmp_path / "album"
    folder.mkdir()
    for name in ("cover.jpg", "02 - b.flac", "01 - a.flac", "notes.txt"):
        (folder / name).write_bytes(b"")
    (folder / "00.flac").mkdir()  # a directory with an audio name is not a file
    assert LibraryIndex.first_audio_file(str(folder)) == str(folder / "01 - a.flac")


def test_first_audio_file_answers_empty_for_nothing_to_read(tmp_path):
    (tmp_path / "art").mkdir()
    (tmp_path / "art" / "cover.jpg").write_bytes(b"")
    assert LibraryIndex.first_audio_file(str(tmp_path / "art")) == ""
    assert LibraryIndex.first_audio_file(str(tmp_path / "gone")) == ""


def test_track_counts_and_the_artist_key_ride_along(tmp_path):
    idx = _index(
        tmp_path, [("/m/Caribou/Suddenly", "Suddenly", "Caribou"), ("/m/Caribou/Our Love", "Our Love", "Caribou")]
    )
    (row,) = idx.distinct_artists()
    assert row["tracks"] == 20 and row["key"] == "caribou"


def test_artist_titles_lists_what_the_library_holds_under_the_key(tmp_path):
    songs = [_track("/m/Caribou/Suddenly", t, "Caribou") for t in ("Home", "Never Come Back", "Home")]
    idx = _index(
        tmp_path,
        [
            ("/m/Caribou/Suddenly", "Suddenly", "Caribou", songs),
            ("/m/Caribou/Our Love", "Our Love", "Caribou"),
            ("/m/Nils/Spaces", "Spaces", "Nils Frahm"),
        ],
    )
    got = idx.artist_titles("caribou")
    assert sorted(got["albums"]) == ["Our Love", "Suddenly"]
    assert sorted(got["tracks"]) == ["Home", "Never Come Back"]
    assert idx.artist_titles("") == {"albums": [], "tracks": []}
    assert idx.artist_titles("nobody") == {"albums": [], "tracks": []}


def test_artist_albums_lists_each_keys_albums_newest_first(tmp_path):
    idx = LibraryIndex(str(tmp_path / "library.sqlite3"))
    rows = [
        _row("/m/c/a", "Andorra", "Caribou"),
        _row("/m/c/s", "Suddenly", "Caribou"),
        _row("/m/n/x", "Spaces", "Nils"),
    ]
    rows[1] = (*rows[1][:3], "2020-02-21", *rows[1][4:])
    rows[0] = (*rows[0][:3], "2007", *rows[0][4:])
    idx._upsert(rows)
    keys = {r["name"]: r["key"] for r in idx.distinct_artists()}
    got = idx.artist_albums([keys["Caribou"], "nobody"])
    assert list(got) == [keys["Caribou"]]
    assert [(a["title"], a["year"], a["folder"]) for a in got[keys["Caribou"]]] == [
        ("Suddenly", "2020", "/m/c/s"),
        ("Andorra", "2007", "/m/c/a"),
    ]
    first = got[keys["Caribou"]][0]
    assert (first["tracks"], first["codec"], first["bits"], first["rate"]) == (10, "flac", 16, 44100)
    assert idx.artist_albums([]) == {}


def _file_tags(idx, folder, name, tags):
    import json

    with idx._lock:
        idx._conn.execute(
            "INSERT OR REPLACE INTO file_tags (folder_path, name, tags) VALUES (?, ?, ?)",
            (folder, name, json.dumps(tags)),
        )
        idx._conn.commit()


def test_artist_tidal_ids_reads_the_id_waves_wrote_in_every_format(tmp_path):
    idx = _index(
        tmp_path,
        [
            ("/m/Adept/Sleepless", "Sleepless", "Adept"),
            ("/m/Adept/Death Dealers", "Death Dealers", "Adept"),
            ("/m/Air/Moon Safari", "Moon Safari", "Air"),
        ],
    )
    # FLAC keeps the tag under its own name, an MP3 as a user text frame.
    _file_tags(idx, "/m/Adept/Sleepless", "1.flac", {"easy": {"waves_tidal_album_artist_id": ["34868882"]}})
    _file_tags(
        idx, "/m/Adept/Death Dealers", "1.mp3", {"easy": {}, "raw": {"TXXX:WAVES_TIDAL_ALBUM_ARTIST_ID": ["34868882"]}}
    )
    _file_tags(
        idx,
        "/m/Air/Moon Safari",
        "1.m4a",
        {"easy": {}, "raw": {"----:com.apple.iTunes:WAVES_TIDAL_ALBUM_ARTIST_ID": ["123"]}},
    )
    assert idx.artist_tidal_ids() == {"adept": "34868882", "air": "123"}


def test_two_tidal_ids_under_one_name_settle_to_none(tmp_path):
    idx = _index(
        tmp_path,
        [("/m/Adept/Sleepless", "Sleepless", "Adept"), ("/m/Adept/Techno", "Techno", "Adept")],
    )
    _file_tags(idx, "/m/Adept/Sleepless", "1.flac", {"easy": {"waves_tidal_album_artist_id": ["34868882"]}})
    _file_tags(idx, "/m/Adept/Techno", "1.flac", {"easy": {"waves_tidal_album_artist_id": ["3581093"]}})
    assert idx.artist_tidal_ids() == {}
    assert idx.artist_tidal_id_sets() == {"adept": {"34868882", "3581093"}}


def test_a_file_naming_two_tidal_artists_says_nothing(tmp_path):
    idx = _index(tmp_path, [("/m/Adept/Duets", "Duets", "Adept")])
    _file_tags(idx, "/m/Adept/Duets", "1.flac", {"easy": {"waves_tidal_album_artist_id": ["1", "2"]}})
    assert idx.artist_tidal_ids() == {}
    _file_tags(idx, "/m/Adept/Duets", "1.flac", {"easy": {"waves_tidal_album_artist_id": ["not-an-id"]}})
    assert idx.artist_tidal_ids() == {}


def test_artist_upcs_reads_every_upc_tag_name_and_view(tmp_path):
    idx = _index(
        tmp_path,
        [
            ("/m/Band/One", "One", "Band"),
            ("/m/Band/Two", "Two", "Band"),
            ("/m/Band/Three", "Three", "Band"),
            ("/m/Air/Moon Safari", "Moon Safari", "Air"),
        ],
    )
    _file_tags(idx, "/m/Band/One", "1.flac", {"easy": {"upc": ["00881453348211"]}})
    _file_tags(idx, "/m/Band/Two", "1.mp3", {"easy": {}, "raw": {"TXXX:BARCODE": ["12345"]}})
    _file_tags(idx, "/m/Band/Three", "1.m4a", {"easy": {}, "raw": {"----:com.apple.iTunes:EAN": ["0000", "x9"]}})
    _file_tags(idx, "/m/Air/Moon Safari", "1.flac", {"easy": {"upc": ["777"]}})
    assert idx.artist_upcs("band") == {"881453348211", "12345"}
    assert idx.artist_upcs("") == set()
    assert JoinedLibrary(idx).artist_upcs("air") == {"777"}
