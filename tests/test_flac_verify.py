"""waves.flac_verify: the FLAC signature check and its one write.

The three guarantees the module makes are pinned here byte for byte: a scan
never writes; a fix writes exactly the 34 STREAMINFO bytes of a file whose
signature was unset (the rest of the file, and its size, compared before and
after); a set signature is never rewritten, a changed file is never touched,
and an unsupported depth is never written.
"""

from __future__ import annotations

import csv
import hashlib
import os
import pathlib
import shutil
import subprocess
import time
from dataclasses import replace

import pytest

from waves import flac_verify as fv

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")


# ---- hand-built headers (no ffmpeg) ----------------------------------------


def _packed(rate: int, channels: int, bps: int, total: int) -> bytes:
    return ((rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total).to_bytes(8, "big")


def _streaminfo(*, rate=44100, channels=2, bps=16, total=0, md5=bytes(16), blocks=(4096, 4096)) -> bytes:
    sizes = blocks[0].to_bytes(2, "big") + blocks[1].to_bytes(2, "big") + (100).to_bytes(3, "big")
    sizes += (9000).to_bytes(3, "big")
    return sizes + _packed(rate, channels, bps, total) + md5


def _flac_bytes(info: bytes, *, id3: bytes = b"", audio: bytes = b"\xff\xf8" + b"\x55" * 200) -> bytes:
    return id3 + b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + info + audio


def _id3v2(payload_len: int = 30, footer: bool = False) -> bytes:
    size = bytes(
        [(payload_len >> 21) & 0x7F, (payload_len >> 14) & 0x7F, (payload_len >> 7) & 0x7F, payload_len & 0x7F]
    )
    return (
        b"ID3\x04\x00"
        + bytes([0x10 if footer else 0])
        + size
        + b"\x00" * payload_len
        + (b"3DI" + b"\x00" * 7 if footer else b"")
    )


def test_read_streaminfo_parses_fields_and_offset(tmp_path):
    md5 = bytes(range(16))
    info = _streaminfo(rate=96000, channels=2, bps=24, total=123456789, md5=md5)
    path = tmp_path / "a.flac"
    path.write_bytes(_flac_bytes(info))

    si = fv.read_streaminfo(path)

    assert (si.sample_rate, si.channels, si.bits_per_sample, si.total_samples) == (96000, 2, 24, 123456789)
    assert si.md5 == md5
    assert si.offset == 8
    assert si.raw == info
    assert not si.md5_unset


def test_read_streaminfo_steps_over_an_id3v2_tag(tmp_path):
    info = _streaminfo()
    for footer in (False, True):
        tag = _id3v2(30, footer=footer)
        path = tmp_path / f"id3-{int(footer)}.flac"
        path.write_bytes(_flac_bytes(info, id3=tag))
        si = fv.read_streaminfo(path)
        assert si.offset == len(tag) + 8
        assert si.md5_unset


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"OggS" + b"\x00" * 60, "Ogg FLAC"),
        (b"fLaC" + bytes([0x84]) + (40).to_bytes(3, "big") + b"\x00" * 40, "VORBIS_COMMENT first"),
        (b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + b"\x00" * 10, "truncated block"),
        (b"fLaC", "no block header"),
        (b"", "empty"),
    ],
)
def test_read_streaminfo_rejects_what_it_cannot_trust(tmp_path, data, reason):
    path = tmp_path / "x.flac"
    path.write_bytes(data)
    with pytest.raises(fv.FlacHeaderError):
        fv.read_streaminfo(path)


def test_sealed_bytes_changes_only_the_signature_and_the_count(tmp_path):
    info = _streaminfo(rate=48000, channels=2, bps=24, total=0)
    path = tmp_path / "a.flac"
    path.write_bytes(_flac_bytes(info))
    si = fv.read_streaminfo(path)
    md5 = hashlib.md5(b"audio").digest()  # noqa: S324

    out = fv.sealed_bytes(si, md5, 777)

    assert len(out) == 34
    assert out[:10] == info[:10]
    assert out[18:] == md5
    packed = int.from_bytes(out[10:18], "big")
    assert (packed >> 44, ((packed >> 41) & 7) + 1, ((packed >> 36) & 0x1F) + 1, packed & ((1 << 36) - 1)) == (
        48000,
        2,
        24,
        777,
    )
    # A count the 36-bit field cannot hold keeps the header's own.
    assert fv.sealed_bytes(si, md5, 1 << 36)[10:18] == info[10:18]
    with pytest.raises(ValueError, match="16 bytes"):
        fv.sealed_bytes(si, b"short", 1)


def test_write_streaminfo_touches_exactly_the_34_block_bytes(tmp_path):
    tag = _id3v2(12)
    info = _streaminfo(total=0)
    audio = bytes(range(256)) * 40
    path = tmp_path / "a.flac"
    before = _flac_bytes(info, id3=tag, audio=audio)
    path.write_bytes(before)
    si = fv.read_streaminfo(path)
    md5 = hashlib.md5(audio).digest()  # noqa: S324
    new = fv.sealed_bytes(si, md5, 5000)

    fv.write_streaminfo(path, si, new)

    after = path.read_bytes()
    assert len(after) == len(before)
    assert after[: si.offset] == before[: si.offset]
    assert after[si.offset : si.offset + 34] == new
    assert after[si.offset + 34 :] == before[si.offset + 34 :]
    got = fv.read_streaminfo(path)
    assert got.md5 == md5
    assert got.total_samples == 5000


def test_write_streaminfo_refuses_a_header_that_changed_underneath(tmp_path):
    info = _streaminfo()
    path = tmp_path / "a.flac"
    path.write_bytes(_flac_bytes(info))
    si = fv.read_streaminfo(path)
    # Something else rewrote the file between the scan and the fix.
    rewritten = _flac_bytes(_streaminfo(md5=b"\x01" * 16))
    path.write_bytes(rewritten)

    with pytest.raises(fv.HeaderChanged):
        fv.write_streaminfo(path, si, fv.sealed_bytes(si, b"\x02" * 16, 1))

    assert path.read_bytes() == rewritten


def test_write_streaminfo_never_rewrites_the_shape_fields(tmp_path):
    info = _streaminfo()
    path = tmp_path / "a.flac"
    before = _flac_bytes(info)
    path.write_bytes(before)
    si = fv.read_streaminfo(path)
    tampered = b"\x00" * 10 + info[10:]

    with pytest.raises(ValueError, match="never rewritten"):
        fv.write_streaminfo(path, si, tampered)
    with pytest.raises(ValueError, match="34 bytes"):
        fv.write_streaminfo(path, si, info + b"\x00")
    assert path.read_bytes() == before


def test_the_frame_checksums_are_the_formats_own():
    """CRC-8 (poly 0x07) and CRC-16 (poly 0x8005), both from 0 with no final
    xor: the published check values for "123456789"."""
    assert fv._crc8(b"123456789") == 0xF4
    assert fv._crc16(b"123456789") == 0xFEE8


def _frame(number: int, payload: bytes) -> bytes:
    """A frame the way the end check reads one: a header (sync, 192 samples,
    44.1 kHz, stereo, 16-bit, frame number) closed by its CRC-8, a body, and
    the CRC-16 of all of it."""
    head = bytes([0xFF, 0xF8, 0x19, 0x18, number])
    body = head + bytes([fv._crc8(head)]) + payload
    return body + fv._crc16(body).to_bytes(2, "big")


def test_ends_on_whole_frame_reads_the_end_of_the_file(tmp_path):
    path = tmp_path / "t.flac"
    frames = _frame(0, b"\x11" * 300) + _frame(1, b"\x22" * 120)
    path.write_bytes(_flac_bytes(_streaminfo(), audio=frames))
    info = fv.read_streaminfo(path)
    assert fv.ends_on_whole_frame(path, info)

    for tail, why in (
        (frames + b"\xff\xf8\x19", "the start of another frame's header"),
        (frames + b"\x01", "one byte after the last frame"),
        (frames[:-1], "the last frame cut short by a byte"),
        (frames[:-60], "the last frame cut in the middle"),
        (frames[:-3] + b"\x00" + frames[-2:], "a byte of the last frame changed"),
    ):
        path.write_bytes(_flac_bytes(_streaminfo(), audio=tail))
        assert not fv.ends_on_whole_frame(path, info), why

    # Zero bytes after the last whole frame leave its CRC at 0 and pass:
    # every frame is whole, so the audio is too (see the docstring).
    path.write_bytes(_flac_bytes(_streaminfo(), audio=frames + bytes(2)))
    assert fv.ends_on_whole_frame(path, info)
    # A cut on a frame boundary is a shorter whole stream: nothing in the
    # file can tell (the header's sample count is what catches that one).
    path.write_bytes(_flac_bytes(_streaminfo(), audio=_frame(0, b"\x11" * 300)))
    assert fv.ends_on_whole_frame(path, info)


def _sized_frame(number: bytes, block: int, payload: bytes, *, variable: bool = False) -> bytes:
    """A frame whose header states its block size (8-bit, code 6) and carries
    ``number`` already coded (the frame number, UTF-8 style)."""
    head = bytes([0xFF, 0xF9 if variable else 0xF8, 0x69, 0x18]) + number + bytes([block - 1])
    body = head + bytes([fv._crc8(head)]) + payload
    return body + fv._crc16(body).to_bytes(2, "big")


def test_the_last_frame_proves_the_length_only_when_it_is_the_short_one(tmp_path):
    """A fixed-blocksize stream: every frame holds the block size except the
    stream's own last, which is shorter. Ending on that short frame proves
    the length (its number times the block size plus its own); ending on a
    full one is what a cut on a frame boundary leaves, and proves nothing."""
    path = tmp_path / "t.flac"
    info = _streaminfo(blocks=(192, 192))
    whole = _frame(0, b"\x11" * 300) + _frame(1, b"\x22" * 300)
    # Frame 300, coded in two bytes: 110 00100, 10 101100.
    for number, value in ((bytes([2]), 2), (bytes([0xC4, 0xAC]), 300)):
        path.write_bytes(_flac_bytes(info, audio=whole + _sized_frame(number, 100, b"\x33" * 80)))
        assert fv.proven_total_samples(path, fv.read_streaminfo(path)) == value * 192 + 100
    for audio, why in (
        (whole, "a full-size last frame (a cut on a boundary, or a whole number of blocks)"),
        (whole + _sized_frame(bytes([2]), 192, b"\x33" * 80), "a stated size equal to the block size"),
        (whole + _sized_frame(bytes([2]), 100, b"\x33" * 80, variable=True), "a variable-blocksize stream"),
        (whole + _sized_frame(bytes([2]), 100, b"\x33" * 80)[:-1], "not ending on a whole frame"),
    ):
        path.write_bytes(_flac_bytes(info, audio=audio))
        assert fv.proven_total_samples(path, fv.read_streaminfo(path)) is None, why
    # A header whose least and largest block disagree does not declare a
    # fixed blocksize: the same short frame proves nothing.
    path.write_bytes(_flac_bytes(_streaminfo(blocks=(16, 192)), audio=whole + _sized_frame(bytes([2]), 100, b"\x33")))
    assert fv.proven_total_samples(path, fv.read_streaminfo(path)) is None
    assert fv.proven_total_samples(tmp_path / "gone.flac", fv.read_streaminfo(path)) is None


def test_unsupported_depth_is_reported_and_never_written(tmp_path):
    """A 20-bit file: libFLAC hashes 3-byte samples as stored, ffmpeg would
    hand back shifted 24-bit ones, so the hash cannot be reproduced. The
    checker must say so before it spawns anything, and write nothing."""
    info = _streaminfo(bps=20, total=0)
    path = tmp_path / "a.flac"
    before = _flac_bytes(info)
    path.write_bytes(before)

    row = fv.seal_file(path, "/nonexistent/ffmpeg")

    assert row.status == fv.UNSUPPORTED
    assert "20-bit" in row.detail
    assert path.read_bytes() == before


def test_check_file_header_only_never_needs_a_decoder(tmp_path):
    unset = tmp_path / "u.flac"
    unset.write_bytes(_flac_bytes(_streaminfo()))
    sealed = tmp_path / "s.flac"
    sealed.write_bytes(_flac_bytes(_streaminfo(md5=b"\x07" * 16)))
    junk = tmp_path / "j.flac"
    junk.write_bytes(b"not a flac at all")

    assert fv.check_file(str(unset)).status == fv.UNSET
    assert fv.check_file(str(sealed)).status == fv.SET
    assert fv.check_file(str(junk)).status == fv.UNREADABLE
    # Deep without a decoder degrades to the header answer, never an error.
    assert fv.check_file(str(unset), None, deep=True).status == fv.UNSET
    assert fv.check_file(str(tmp_path / "missing.flac")).status == fv.UNREADABLE


# ---- the walk ------------------------------------------------------------------


def test_effective_roots_drops_blanks_duplicates_and_nested_folders(tmp_path):
    lib = str(tmp_path / "Music")
    dl = str(tmp_path / "Music" / "Downloads")
    other = str(tmp_path / "Other")
    assert fv.effective_roots([dl, lib]) == [lib]
    assert fv.effective_roots([lib, dl]) == [lib]
    assert fv.effective_roots(["", " ", lib, lib, other]) == [lib, other]
    assert fv.effective_roots([]) == []


def test_iter_flac_files_scope(tmp_path):
    root = tmp_path / "root"
    (root / "Artist" / "Album").mkdir(parents=True)
    (root / ".hidden").mkdir()
    (root / "Artist" / "Album" / "01 Song.FLAC").write_bytes(b"x")
    (root / "Artist" / "Album" / "02 Song.flac").write_bytes(b"x")
    (root / "Artist" / "Album" / "._02 Song.flac").write_bytes(b"x")  # AppleDouble sidecar
    (root / "Artist" / "Album" / "cover.jpg").write_bytes(b"x")
    (root / "Artist" / "Album" / "03 Song.m4a").write_bytes(b"x")
    (root / ".hidden" / "04 Song.flac").write_bytes(b"x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "05 Song.flac").write_bytes(b"x")
    # A folder link out of the root is not followed; a file link out of the
    # root is skipped; a file link that stays inside the root is kept, but
    # only when the walk finds no other name for its file (counted once).
    os.symlink(outside, root / "Linked folder")
    os.symlink(outside / "05 Song.flac", root / "Artist" / "06 Linked out.flac")
    os.symlink(root / "Artist" / "Album" / "02 Song.flac", root / "Artist" / "07 Linked in.flac")
    os.symlink(root / "Artist" / "07 Linked in.flac", root / "Artist" / "08 Link to a link.flac")
    os.symlink(root / ".hidden" / "04 Song.flac", root / "Artist" / "09 Linked hidden.flac")
    os.symlink(root / ".hidden" / "04 Song.flac", root / "Artist" / "10 Linked hidden again.flac")
    unreadable: list[str] = []

    found = [os.path.relpath(p, root) for p in fv.iter_flac_files([str(root), str(root / "Artist")], None, unreadable)]

    assert found == [
        os.path.join("Artist", "Album", "01 Song.FLAC"),
        os.path.join("Artist", "Album", "02 Song.flac"),
        os.path.join("Artist", "09 Linked hidden.flac"),
    ]
    assert unreadable == []


def test_iter_flac_files_reports_every_folder_it_could_not_read(tmp_path):
    """A missing root, a root that is a file, and a subfolder the system will
    not list are named, never taken for empty; what could be read is still
    walked."""
    root = tmp_path / "root"
    (root / "Open").mkdir(parents=True)
    (root / "Open" / "a.flac").write_bytes(b"x")
    locked = root / "Locked"
    locked.mkdir()
    (locked / "b.flac").write_bytes(b"x")
    not_a_folder = tmp_path / "file.flac"
    not_a_folder.write_bytes(b"x")
    missing = tmp_path / "Not mounted"
    os.chmod(locked, 0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("permissions are not enforced for this user")
        unreadable: list[str] = []
        found = list(fv.iter_flac_files([str(missing), str(root), str(not_a_folder)], None, unreadable))
    finally:
        os.chmod(locked, 0o700)

    assert found == [str(root / "Open" / "a.flac")]
    assert sorted(unreadable) == sorted([str(missing), str(locked), str(not_a_folder)])
    # Without a list to fill the walk is the same, and still never raises.
    assert list(fv.iter_flac_files([str(missing)])) == []


def test_iter_flac_files_lists_ahead_in_the_one_at_a_time_order(tmp_path, monkeypatch):
    """Folders listed several at a time still come out in the order a
    one-at-a-time walk gives, with the same unreadable folders in order."""
    root = tmp_path / "lib"
    for a in "abcde":
        for b in "xyz":
            for n in (1, 2):
                _fake = root / a / b / f"{n}.flac"
                _fake.parent.mkdir(parents=True, exist_ok=True)
                _fake.write_bytes(b"x")
    (root / "c" / "locked").mkdir()
    real_list = fv._list_folder

    def list_folder(folder, real_roots, unreadable):
        if folder.endswith("locked") or folder.endswith(os.path.join("a", "y")):
            unreadable.append(folder)
            return [], [], []
        return real_list(folder, real_roots, unreadable)

    monkeypatch.setattr(fv, "_list_folder", list_folder)
    walked: list[str] = []
    found = list(fv.iter_flac_files([str(root)], None, walked))
    monkeypatch.setattr(fv, "_WALK_THREADS", 1)
    monkeypatch.setattr(fv, "_WALK_AHEAD", 1)
    one: list[str] = []
    assert list(fv.iter_flac_files([str(root)], None, one)) == found
    assert walked == one == [str(root / "a" / "y"), str(root / "c" / "locked")]
    assert [os.path.relpath(p, root) for p in found[:4]] == [
        os.path.join("a", "x", "1.flac"),
        os.path.join("a", "x", "2.flac"),
        os.path.join("a", "z", "1.flac"),
        os.path.join("a", "z", "2.flac"),
    ]


def test_iter_flac_files_stops_when_asked(tmp_path):
    for i in range(5):
        d = tmp_path / f"d{i}"
        d.mkdir()
        (d / "a.flac").write_bytes(b"x")
    calls = iter([True, True, False, False, False, False])
    found = list(fv.iter_flac_files([str(tmp_path)], should_continue=lambda: next(calls)))
    assert len(found) < 5


# ---- with a real decoder -------------------------------------------------------


def _run(*args: str) -> bytes:
    # argv is ffmpeg from PATH plus literal test arguments.
    return subprocess.run([FFMPEG, "-v", "error", *args], capture_output=True, check=True).stdout  # noqa: S603


def _make_flac(path: pathlib.Path, *, bps: int = 24, seconds: float = 0.5) -> tuple[bytes, int]:
    """A real FLAC file from ffmpeg's encoder (16- or 24-bit, the depths it
    writes); returns the libFLAC-style MD5 of its PCM and the sample count."""
    fmt = {16: ("s16", "16"), 24: ("s32", "24")}[bps]
    sine = f"sine=frequency=440:duration={seconds}"
    _run(
        "-y",
        "-f",
        "lavfi",
        "-i",
        sine,
        "-ac",
        "2",
        "-ar",
        "48000",
        "-sample_fmt",
        fmt[0],
        "-bits_per_raw_sample",
        fmt[1],
        "-c:a",
        "flac",
        str(path),
    )
    assert fv.read_streaminfo(path).bits_per_sample == bps
    pcm = _run("-i", str(path), "-f", {16: "s16le", 24: "s24le"}[bps], "-")
    return hashlib.md5(pcm).digest(), len(pcm) // (2 * (bps // 8))  # noqa: S324


def _unset(path: pathlib.Path) -> None:
    """Zero the signature and the count in place, the way old packaging shipped them."""
    si = fv.read_streaminfo(path)
    fv.write_streaminfo(path, si, fv.sealed_bytes(si, bytes(16), 0))
    assert fv.read_streaminfo(path).md5_unset


def _set_md5(path: pathlib.Path, md5: bytes) -> None:
    si = fv.read_streaminfo(path)
    fv.write_streaminfo(path, si, si.raw[:18] + md5)


def _without_block(data: bytes, si: fv.StreamInfo) -> bytes:
    return data[: si.offset] + data[si.offset + 34 :]


@needs_ffmpeg
def test_seal_file_fills_an_unset_signature_and_leaves_every_other_byte(tmp_path):
    path = tmp_path / "t.flac"
    md5, samples = _make_flac(path)
    _unset(path)
    before = path.read_bytes()
    si = fv.read_streaminfo(path)

    row = fv.seal_file(path, FFMPEG)

    assert row.status == fv.SEALED
    assert row.detail == "signature and sample count written"
    after = path.read_bytes()
    assert len(after) == len(before)
    assert _without_block(after, si) == _without_block(before, si)
    got = fv.read_streaminfo(path)
    assert got.md5 == md5
    assert got.total_samples == samples
    assert (got.sample_rate, got.channels, got.bits_per_sample) == (48000, 2, 24)
    # The file now verifies, and a second pass changes nothing.
    assert fv.check_file(str(path), FFMPEG, deep=True).status == fv.OK
    assert fv.seal_file(path, FFMPEG).status == fv.OK
    assert path.read_bytes() == after


@needs_ffmpeg
def test_seal_file_hashes_16_bit_the_libflac_way(tmp_path):
    bps = 16
    path = tmp_path / f"t{bps}.flac"
    md5, samples = _make_flac(path, bps=bps)
    # ffmpeg's own encoder wrote a sound signature: the deep check agrees.
    assert fv.check_file(str(path), FFMPEG, deep=True).status == fv.OK
    _unset(path)
    assert fv.seal_file(path, FFMPEG).status == fv.SEALED
    got = fv.read_streaminfo(path)
    assert (got.md5, got.total_samples, got.bits_per_sample) == (md5, samples, bps)


@needs_ffmpeg
def test_seal_file_reports_a_mismatch_and_writes_nothing(tmp_path):
    path = tmp_path / "t.flac"
    md5, _ = _make_flac(path)
    wrong = bytes(b ^ 0x01 for b in md5)
    _set_md5(path, wrong)
    before = path.read_bytes()

    row = fv.seal_file(path, FFMPEG)

    assert row.status == fv.MISMATCH
    assert row.md5_audio == md5
    assert path.read_bytes() == before
    assert fv.check_file(str(path), FFMPEG, deep=True).status == fv.MISMATCH
    assert path.read_bytes() == before


@needs_ffmpeg
def test_seal_file_fill_sample_count_is_the_download_engines_knob(tmp_path):
    path = tmp_path / "t.flac"
    md5, samples = _make_flac(path)
    si = fv.read_streaminfo(path)
    fv.write_streaminfo(path, si, fv.sealed_bytes(si, md5, 0))  # signature kept, count unknown
    before = path.read_bytes()

    assert fv.seal_file(path, FFMPEG).status == fv.OK
    assert path.read_bytes() == before, "the library tool leaves a verified file alone"

    row = fv.seal_file(path, FFMPEG, fill_sample_count=True)

    assert (row.status, row.detail) == (fv.SEALED, "sample count written")
    got = fv.read_streaminfo(path)
    assert (got.md5, got.total_samples) == (md5, samples)


@needs_ffmpeg
def test_seal_file_leaves_a_file_whose_header_moved_since_the_scan(tmp_path):
    path = tmp_path / "t.flac"
    _make_flac(path)
    _unset(path)
    scanned = fv.read_streaminfo(path)
    _set_md5(path, b"\x09" * 16)  # someone else sealed (or re-downloaded) it meanwhile
    before = path.read_bytes()

    row = fv.seal_file(path, FFMPEG, expected=scanned)

    assert row.status == fv.CHANGED
    assert path.read_bytes() == before


@needs_ffmpeg
def test_seal_file_leaves_a_file_modified_while_it_was_decoding(tmp_path, monkeypatch):
    path = tmp_path / "t.flac"
    _make_flac(path)
    _unset(path)
    before = path.read_bytes()
    real_decode = fv.decode_md5

    def decode_then_touch(*args, **kwargs):
        out = real_decode(*args, **kwargs)
        os.utime(path, ns=(1, 1))  # the file's mtime moved under the decode
        return out

    monkeypatch.setattr(fv, "decode_md5", decode_then_touch)

    row = fv.seal_file(path, FFMPEG)

    assert row.status == fv.CHANGED
    assert "while it was being decoded" in row.detail
    assert path.read_bytes() == before


@needs_ffmpeg
def test_seal_file_reports_a_decode_failure_and_writes_nothing(tmp_path):
    path = tmp_path / "t.flac"
    _make_flac(path)
    _unset(path)
    si = fv.read_streaminfo(path)
    # Keep the header, destroy the audio.
    data = path.read_bytes()
    path.write_bytes(data[: si.offset + 34 + 64] + b"\x00" * 16)
    before = path.read_bytes()

    row = fv.seal_file(path, FFMPEG)

    assert row.status == fv.DECODE_FAILED, row
    assert path.read_bytes() == before


@needs_ffmpeg
@pytest.mark.parametrize("damage", ["cut", "corrupt", "trailing"])
def test_seal_file_never_signs_a_file_that_did_not_decode_cleanly(tmp_path, damage):
    """ffmpeg left to itself skips a bad frame, stops at a cut-off tail and
    exits 0, and the hash of what came out would then be written as the
    signature of a damaged file, with the header's sample count (the one
    field that showed the damage) replaced by the shorter decode. Every
    such file is reported and left alone, byte for byte."""
    path = tmp_path / "t.flac"
    _make_flac(path, bps=16, seconds=3.0)
    si = fv.read_streaminfo(path)
    data = bytearray(path.read_bytes())
    if damage == "cut":
        data = data[: len(data) * 2 // 3]
    elif damage == "corrupt":
        mid = len(data) // 2
        for i in range(mid, mid + 200):
            data[i] ^= 0x5A
    else:
        data += b"TAG" + b"x" * 125  # an ID3v1 trailer: bytes after the last frame
    path.write_bytes(bytes(data))
    si = fv.read_streaminfo(path)
    fv.write_streaminfo(path, si, fv.sealed_bytes(si, bytes(16), si.total_samples))  # unset, count kept
    before = path.read_bytes()

    row = fv.seal_file(path, FFMPEG)

    assert row.status in _REFUSED, row
    assert path.read_bytes() == before
    assert fv.read_streaminfo(path).total_samples == si.total_samples
    assert fv.check_file(str(path), FFMPEG, deep=True).status not in (fv.OK, fv.SEALED)


# How a refusal is worded depends on the ffmpeg build: newer ones stop on the
# bad frame (DECODE_FAILED), older ones (6.1, Ubuntu 24.04) drop it and come up
# short (LENGTH_DIFFERS). Either way the file must be left alone, byte for byte.
_REFUSED = (fv.DECODE_FAILED, fv.LENGTH_DIFFERS)


def _frame_spans(path: pathlib.Path) -> list[tuple[int, int]]:
    """(start, size) of every audio frame, from ffmpeg's own packet sizes
    (an oracle independent of the module's frame reader)."""
    data = path.read_bytes()
    pos = 4
    while True:  # step over the metadata blocks
        last, length = data[pos] & 0x80, int.from_bytes(data[pos + 1 : pos + 4], "big")
        pos += 4 + length
        if last:
            break
    out = _run("-i", str(path), "-c:a", "copy", "-f", "framecrc", "-").decode()
    spans = []
    for line in out.splitlines():
        if line and not line.startswith("#"):
            size = int(line.split(",")[4])
            spans.append((pos, size))
            pos += size
    assert pos == len(data), "the frames account for every byte after the metadata"
    return spans


@needs_ffmpeg
@pytest.mark.parametrize("count", ["kept", "zero"])
@pytest.mark.parametrize("damage", ["sample", "checksum"])
def test_seal_file_never_signs_a_file_with_one_flipped_bit(tmp_path, damage, count):
    """One flipped bit in a frame's first stored sample changes the audio and
    leaves the frame's structure intact: only the frame's CRC-16 shows it.
    With ``-err_detect crccheck`` alone ffmpeg logged the mismatch, decoded
    the damaged samples and exited 0, the length came out right, and the
    damaged audio was signed (``flac -t`` then fails the file). A flipped
    bit in the checksum itself is the same check from the other side."""
    path = tmp_path / "t.flac"
    _make_flac(path, bps=16, seconds=3.0)
    spans = _frame_spans(path)
    start, size = spans[len(spans) // 2]  # the encoder's block size varies by ffmpeg version
    data = bytearray(path.read_bytes())
    if damage == "sample":
        data[start + fv._frame_header_len(bytes(data), start) + 2] ^= 0x01
    else:
        data[start + size - 1] ^= 0x01
    path.write_bytes(bytes(data))
    si = fv.read_streaminfo(path)
    fv.write_streaminfo(path, si, fv.sealed_bytes(si, bytes(16), si.total_samples if count == "kept" else 0))
    before = path.read_bytes()

    row = fv.seal_file(path, FFMPEG)

    assert row.status in _REFUSED, row
    assert path.read_bytes() == before
    assert fv.check_file(str(path), FFMPEG, deep=True).status not in (fv.OK, fv.SEALED)


@needs_ffmpeg
@pytest.mark.parametrize("extra", [2, 5, 8])
def test_seal_file_never_signs_a_file_with_stray_bytes_after_its_last_frame(tmp_path, extra):
    """A cut a few bytes into a frame leaves only the start of its header
    behind, which ffmpeg's parser drops without a word: the decode is clean
    and short. With a sample count of 0 in the header nothing else can tell,
    so the end of the file is checked: it must close a whole frame."""
    path = tmp_path / "t.flac"
    _make_flac(path, bps=16, seconds=3.0)
    _unset(path)
    start, _size = _frame_spans(path)[-1]
    data = path.read_bytes()
    path.write_bytes(data + data[start : start + extra])
    assert fv.read_streaminfo(path).total_samples == 0
    before = path.read_bytes()

    row = fv.seal_file(path, FFMPEG)

    assert (row.status, row.detail) == (
        fv.DECODE_FAILED,
        "the file ends partway through an audio frame (cut off, or bytes after the last frame)",
    )
    assert path.read_bytes() == before
    # Without the stray bytes the same file is whole, and is signed.
    path.write_bytes(data)
    assert fv.seal_file(path, FFMPEG).status == fv.SEALED


@needs_ffmpeg
def test_seal_file_leaves_a_file_whose_audio_is_another_length_than_its_header(tmp_path, monkeypatch):
    """The second guard, for a decoder that does not stop at a bad frame:
    an unset signature is only written when the decode has the length the
    header states. A count of 0 means unknown and is filled in."""
    path = tmp_path / "t.flac"
    md5, samples = _make_flac(path)
    si = fv.read_streaminfo(path)
    fv.write_streaminfo(path, si, fv.sealed_bytes(si, bytes(16), samples))
    before = path.read_bytes()
    monkeypatch.setattr(fv, "decode_md5", lambda *a, **k: (md5, samples - 4096))

    row = fv.seal_file(path, FFMPEG)

    assert row.status == fv.LENGTH_DIFFERS
    assert row.detail == f"decoded {samples - 4096} samples, the header says {samples}"
    assert path.read_bytes() == before
    assert fv.fix_rows(fv.scan([str(tmp_path)]), FFMPEG, settle_seconds=0.0)[0].status == fv.LENGTH_DIFFERS
    assert path.read_bytes() == before
    # The download engine's knob changes nothing here: the signature is unset.
    assert fv.seal_file(path, FFMPEG, fill_sample_count=True).status == fv.LENGTH_DIFFERS
    assert path.read_bytes() == before

    # Unknown count: the end of the file proves the length, and the short
    # decode does not reach it.
    si = fv.read_streaminfo(path)
    fv.write_streaminfo(path, si, fv.sealed_bytes(si, bytes(16), 0))
    before = path.read_bytes()
    row = fv.seal_file(path, FFMPEG)
    assert (row.status, row.detail) == (
        fv.LENGTH_DIFFERS,
        f"decoded {samples - 4096} samples, the last frame ends at {samples}",
    )
    assert path.read_bytes() == before
    # The real decode does, and the proven length is what is written.
    monkeypatch.undo()
    row = fv.seal_file(path, FFMPEG)
    assert (row.status, row.detail) == (fv.SEALED, "signature and sample count written")
    assert fv.read_streaminfo(path).total_samples == samples


@needs_ffmpeg
@pytest.mark.parametrize("frames_kept", [3, 20, -1])
def test_seal_file_never_signs_a_file_cut_on_a_frame_boundary_with_no_count(tmp_path, frames_kept):
    """Cut exactly between two frames, the file is a shorter stream of whole
    frames: every checksum holds, it ends on a whole frame and decodes
    cleanly. With no sample count in the header nothing but the end of the
    file can show the cut, so a file that does not end on the stream's own
    short last frame is reported and left alone, header count included."""
    path = tmp_path / "t.flac"
    _make_flac(path, bps=16, seconds=3.0)
    _unset(path)
    spans = _frame_spans(path)
    start, _size = spans[frames_kept]  # the frame that now starts the cut-off part
    data = path.read_bytes()
    path.write_bytes(data[:start])
    before = path.read_bytes()
    assert fv.ends_on_whole_frame(path, fv.read_streaminfo(path))

    row = fv.seal_file(path, FFMPEG)

    assert row.status == fv.LENGTH_DIFFERS, row
    assert "does not prove the audio is complete" in row.detail
    assert path.read_bytes() == before
    assert fv.fix_rows(fv.scan([str(tmp_path)]), FFMPEG, settle_seconds=0.0)[0].status == fv.LENGTH_DIFFERS
    assert path.read_bytes() == before


@needs_ffmpeg
def test_a_whole_number_of_blocks_with_no_count_is_left_alone_and_with_one_is_signed(tmp_path):
    """The one complete file the end cannot vouch for: its length is an exact
    multiple of the block size, so its last frame is full-size like a cut's.
    With no count it fails closed; with the header's count it is signed."""
    path = tmp_path / "t.flac"
    # 36864 samples: a whole number of every block size an encoder picks at
    # 48 kHz (192, 576, 1024, 1152, 2304, 4096, 4608).
    total = 36864
    sine = "sine=frequency=440:sample_rate=48000:duration=2"
    _run(
        "-y",
        "-f",
        "lavfi",
        "-i",
        sine,
        "-af",
        f"atrim=end_sample={total}",
        "-ac",
        "2",
        "-sample_fmt",
        "s16",
        "-c:a",
        "flac",
        str(path),
    )
    si = fv.read_streaminfo(path)
    assert si.total_samples == total
    assert total % int.from_bytes(si.raw[2:4], "big") == 0
    md5 = si.md5
    fv.write_streaminfo(path, si, fv.sealed_bytes(si, bytes(16), si.total_samples))
    unset_with_count = path.read_bytes()
    _unset(path)
    before = path.read_bytes()

    assert fv.seal_file(path, FFMPEG).status == fv.LENGTH_DIFFERS
    assert path.read_bytes() == before

    path.write_bytes(unset_with_count)
    row = fv.seal_file(path, FFMPEG)
    assert (row.status, row.detail) == (fv.SEALED, "signature written")
    assert fv.read_streaminfo(path).md5 == md5


def _stalled_decoder(tmp_path: pathlib.Path) -> str:
    """A stand-in for ffmpeg that never writes a byte, as a decode stuck on
    a share that stopped answering looks from here."""
    script = tmp_path / "stalled-ffmpeg"
    script.write_text("#!/bin/sh\nexec sleep 30\n")
    script.chmod(0o755)
    return str(script)


def _whole_frames_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "t.flac"
    path.write_bytes(_flac_bytes(_streaminfo(), audio=_frame(0, b"\x11" * 300) + _frame(1, b"\x22" * 120)))
    return path


def _watchdogs() -> list:
    import threading

    return [t for t in threading.enumerate() if t.name == "flac-decode-watchdog"]


@pytest.mark.skipif(os.name == "nt", reason="the stand-in decoder is a shell script")
def test_a_decode_that_stops_making_progress_is_stopped_by_its_time_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "DECODE_MIN_SECONDS", 1.0)
    path = _whole_frames_file(tmp_path)
    before = path.read_bytes()
    t0 = time.monotonic()

    row = fv.check_file(str(path), _stalled_decoder(tmp_path), deep=True)

    assert time.monotonic() - t0 < 10, "the stalled read did not hold the call"
    assert (row.status, row.detail) == (fv.DECODE_FAILED, "the decoder did not finish within 1 seconds")
    assert path.read_bytes() == before
    assert _watchdogs() == [], "the watchdog ends with the call"


@pytest.mark.skipif(os.name == "nt", reason="the stand-in decoder is a shell script")
def test_a_decode_sharing_the_link_gets_that_many_times_as_long(tmp_path, monkeypatch):
    """Several decodes at once split a slow share between them: a limit
    sized for one would fail healthy files as "did not decode cleanly"."""
    monkeypatch.setattr(fv, "DECODE_MIN_SECONDS", 1.0)
    path = _whole_frames_file(tmp_path)
    t0 = time.monotonic()

    row = fv.check_file(str(path), _stalled_decoder(tmp_path), deep=True, shared=2)

    assert time.monotonic() - t0 >= 1.5
    assert (row.status, row.detail) == (fv.DECODE_FAILED, "the decoder did not finish within 2 seconds")


@pytest.mark.skipif(os.name == "nt", reason="the stand-in decoder is a shell script")
def test_cancel_reaches_a_decode_that_is_not_producing_output(tmp_path):
    """The read loop only asks between chunks; a decoder stuck before its
    first chunk is stopped by the watchdog, and the row says canceled."""
    path = _whole_frames_file(tmp_path)
    before = path.read_bytes()
    deadline = time.monotonic() + 0.5
    t0 = time.monotonic()

    row = fv.seal_file(path, _stalled_decoder(tmp_path), should_continue=lambda: time.monotonic() < deadline)

    assert time.monotonic() - t0 < 10
    assert row.status == fv.CANCELLED
    assert path.read_bytes() == before
    assert _watchdogs() == []


def test_the_decode_time_limit_scales_with_the_file():
    """Generous: a floor, plus the file read at a crawl, so only a stalled
    read ever reaches it."""
    assert fv.decode_time_limit(0) == round(fv.DECODE_MIN_SECONDS)
    mb100 = 100 * 1024 * 1024
    assert fv.decode_time_limit(mb100) == round(fv.DECODE_MIN_SECONDS + mb100 / fv.DECODE_MIN_BYTES_PER_SECOND)
    assert fv.decode_time_limit(mb100) > 10 * 60, "a 100 MB file gets well over ten minutes"


@needs_ffmpeg
def test_seal_file_can_be_cancelled_mid_decode(tmp_path):
    path = tmp_path / "t.flac"
    _make_flac(path, seconds=3)
    _unset(path)
    before = path.read_bytes()

    row = fv.seal_file(path, FFMPEG, should_continue=lambda: False)

    assert row.status == fv.CANCELLED
    assert path.read_bytes() == before


@needs_ffmpeg
def test_fix_rows_only_touches_unset_rows(tmp_path, monkeypatch):
    unset = tmp_path / "u.flac"
    _make_flac(unset)
    _unset(unset)
    kept = tmp_path / "k.flac"
    _make_flac(kept)
    busy = tmp_path / "b.flac"
    _make_flac(busy)
    _unset(busy)
    changed = tmp_path / "c.flac"
    _make_flac(changed)
    _unset(changed)
    junk = tmp_path / "j.flac"
    junk.write_bytes(b"nope")
    rows = fv.scan([str(tmp_path)])
    assert {r.status for r in rows} == {fv.UNSET, fv.SET, fv.UNREADABLE}
    # Old enough to be trusted complete, except the one written just now.
    old = time.time() - 3600
    for p in (unset, kept, changed):
        os.utime(p, (old, old))
    rows = fv.scan([str(tmp_path)])
    before = {p: p.read_bytes() for p in (unset, kept, busy, changed, junk)}
    # Rewritten after the scan: a different mtime, same shape.
    os.utime(changed, (old + 5, old + 5))
    seen: list[fv.Row] = []

    out = fv.fix_rows(rows, FFMPEG, on_row=seen.append, settle_seconds=60.0)

    by_name = {os.path.basename(r.path): r for r in out}
    assert by_name["u.flac"].status == fv.SEALED
    assert by_name["k.flac"].status == fv.SET
    assert by_name["j.flac"].status == fv.UNREADABLE
    assert by_name["b.flac"].status == fv.BUSY
    assert by_name["c.flac"].status == fv.CHANGED
    assert [os.path.basename(r.path) for r in seen] == ["b.flac", "c.flac", "u.flac"]
    for p in (kept, busy, changed, junk):
        assert p.read_bytes() == before[p], p.name
    assert fv.read_streaminfo(unset).md5 != bytes(16)
    # The next run (the busy file has settled by then) writes it too.
    rows = fv.scan([str(tmp_path)])
    out = fv.fix_rows(rows, FFMPEG, settle_seconds=0.0)
    assert {os.path.basename(r.path): r.status for r in out}["b.flac"] == fv.SEALED


@needs_ffmpeg
def test_fix_rows_cancel_leaves_the_rest_unset_for_next_time(tmp_path):
    paths = []
    for i in range(3):
        p = tmp_path / f"{i}.flac"
        _make_flac(p)
        _unset(p)
        paths.append(p)
    rows = fv.scan([str(tmp_path)])
    before = [p.read_bytes() for p in paths]
    # One True for fix_rows's own check before the first file, then the
    # decoder's first chunk asks and is told to stop.
    budget = iter([True, False])

    out = fv.fix_rows(rows, FFMPEG, should_continue=lambda: next(budget, False), settle_seconds=0.0)

    # The first file's decode was interrupted (the budget ran out inside it),
    # the rest were never reached: all stay UNSET, so a later fix sees them.
    assert [r.status for r in out] == [fv.UNSET] * 3
    assert {r.detail for r in out} == {"not reached, the run was canceled"}
    for b, p in zip(before, paths, strict=True):
        assert p.read_bytes() == b
    assert [r.status for r in fv.fix_rows(out, FFMPEG, settle_seconds=0.0)] == [fv.SEALED] * 3


@needs_ffmpeg
def test_scan_deep_reports_every_status_and_writes_nothing(tmp_path):
    ok = tmp_path / "ok.flac"
    md5, _ = _make_flac(ok)
    bad = tmp_path / "bad.flac"
    _make_flac(bad)
    _set_md5(bad, bytes(b ^ 0xFF for b in md5))
    unset = tmp_path / "unset.flac"
    _make_flac(unset)
    _unset(unset)
    before = {p: p.read_bytes() for p in (ok, bad, unset)}

    rows = fv.scan([str(tmp_path)], FFMPEG, deep=True)

    assert {os.path.basename(r.path): r.status for r in rows} == {
        "ok.flac": fv.OK,
        "bad.flac": fv.MISMATCH,
        "unset.flac": fv.UNSET,
    }
    for p, b in before.items():
        assert p.read_bytes() == b
    assert fv.summarize(rows) == {"files": 3, fv.OK: 1, fv.MISMATCH: 1, fv.UNSET: 1}


# ---- the report ----------------------------------------------------------------


def test_write_report_is_a_spreadsheet_friendly_csv(tmp_path):
    path = tmp_path / "Ångström" / "u.flac"
    path.parent.mkdir()
    path.write_bytes(_flac_bytes(_streaminfo(rate=44100, channels=2, bps=16, total=10)))
    rows = fv.scan([str(tmp_path)])
    rows.append(fv.Row(str(tmp_path / "gone.flac"), fv.UNREADABLE, "No such file"))
    rows.append(
        replace(rows[0], status=fv.SEALED, detail="signature written", md5_audio=b"\xab" * 16, samples_decoded=10)
    )
    out = tmp_path / "reports" / fv.report_name("scan", 0)

    written = fv.write_report(out, rows)

    assert written == str(out)
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "a byte-order mark so a spreadsheet reads UTF-8 names"
    with out.open(encoding="utf-8-sig", newline="") as f:
        table = list(csv.DictReader(f))
    assert list(table[0].keys()) == list(fv.REPORT_COLUMNS)
    assert table[0]["status"] == fv.UNSET
    assert table[0]["meaning"] == "no signature"
    assert table[0]["file"] == str(path)
    assert (table[0]["bit_depth"], table[0]["sample_rate"], table[0]["channels"], table[0]["samples_in_header"]) == (
        "16",
        "44100",
        "2",
        "10",
    )
    assert table[0]["md5_in_header"] == ""
    assert table[1]["meaning"] == "not a readable FLAC file"
    assert table[1]["detail"] == "No such file"
    assert table[2]["md5_of_audio"] == "ab" * 16
    assert table[2]["samples_decoded"] == "10"
    assert fv.report_name("fix", 0).startswith("flac-check-fix-")


def test_the_report_lists_the_folders_it_could_not_read(tmp_path):
    out = tmp_path / "r.csv"
    fv.write_report(out, [fv.Row(str(tmp_path / "a.flac"), fv.UNSET)], [str(tmp_path / "Share")])
    with out.open(encoding="utf-8-sig", newline="") as f:
        table = list(csv.DictReader(f))
    assert [(r["status"], r["file"]) for r in table] == [
        (fv.UNSET, str(tmp_path / "a.flac")),
        (fv.FOLDER_UNREADABLE, str(tmp_path / "Share")),
    ]
    assert table[1]["meaning"] == "folder could not be opened, its files were not checked"
    assert fv.FOLDER_UNREADABLE not in fv.summarize([fv.Row("x", fv.UNSET)]), "never a file's status"


def test_a_report_never_replaces_another_from_the_same_second(tmp_path):
    names = [fv.report_name("scan", 0), fv.report_name("scan", 0, 2), fv.report_name("scan", 0, 3)]
    assert names[1] == names[0][: -len(".csv")] + "-2.csv"
    written = [
        os.path.basename(fv.write_new_report(tmp_path, "scan", [fv.Row(f"{i}.flac", fv.SET)], when=0)) for i in range(3)
    ]
    assert written == names
    for i, name in enumerate(names):
        assert f"{i}.flac" in (tmp_path / name).read_text(encoding="utf-8-sig")
    with pytest.raises(FileExistsError):
        fv.write_report(tmp_path / names[0], [], exclusive=True)
