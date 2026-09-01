import base64
from enum import StrEnum

from tidalapi import Quality

CTX_TIDAL: str = "tidal"
REQUESTS_TIMEOUT_SEC: int = 45
EXTENSION_LYRICS: str = ".lrc"
UNIQUIFY_THRESHOLD: int = 99
FILENAME_SANITIZE_PLACEHOLDER: str = "_"
# What a path segment that is nothing but "." or ".." is written as. Neither
# survives being a folder name: "." is what every platform calls "this folder",
# so the segment evaporates in the join that builds the destination and the
# album's tracks land loose in the artist folder (issue #29), and ".." walks up
# out of the download folder. Fullwidth full stop for the same reason "?" takes
# "？": the folder still reads as the release's own name.
DOT_SEGMENT_STANDIN: str = "．"
COVER_NAME: str = "cover.jpg"
BLOCK_SIZE: int = 4096
BLOCKS: int = 1024
CHUNK_SIZE: int = BLOCK_SIZE * BLOCKS
# The file is written UTF-8, which is what the 8 promises (and what the UI has
# always said it writes). Libraries built before the rename keep their .m3u
# name: the writer prefers an existing legacy file over minting a sibling.
PLAYLIST_EXTENSION: str = ".m3u8"
PLAYLIST_EXTENSION_LEGACY: str = ".m3u"
PLAYLIST_PREFIX: str = "_"
FILENAME_LENGTH_MAX: int = 255
FORMAT_TEMPLATE_EXPLICIT: str = " (Explicit)"
METADATA_EXPLICIT: str = " 🅴"

# The stand-ins recommended for the rejected characters that actually turn up in
# release titles, keyed by character (the full rejected set lives in
# helper.path.ILLEGAL_FILENAME_CHARS; a key outside it is dropped on load).
# Removing these five reads wrong: a colon separates a subtitle, a slash sits
# inside a name ("AC/DC"), and a title that is nothing but punctuation ("?")
# sanitizes away to nothing at all and loses its folder. The four left unnamed
# (* < > |) are rare in music and read fine simply removed, so they keep
# following the single general stand-in.
#
# Not the dataclass default: an existing library was built under whatever
# spelling it already uses, so these are offered on the settings page rather
# than applied (see WavesBridge._migrate_illegal_map_offer), and are the
# factory value for a brand-new install only.
DEFAULT_ILLEGAL_MAP: dict[str, str] = {
    "/": "-",  # AC/DC -> AC-DC
    "\\": "-",
    ":": " · ",  # Mercury: Act 1 -> Mercury · Act 1
    # Fullwidth question mark: the one lookalike here, because every ASCII
    # stand-in reads wrong where a question mark almost always sits (the end of
    # a title), and removing it is what leaves the album "?" folderless.
    "?": "？",
    '"': "'",  # "Heroes" -> 'Heroes'
}

# Dolby Atmos client id (obfuscated). This is a public, first-party TIDAL app
# id shared across the whole third-party ecosystem (streamrip, tiddl, OrpheusDL
# and others carry the same value); it is NOT derived from any user account.
# TIDAL only serves Atmos manifests to a client it still honours, and the
# previous id here was revoked (every playback request answered 401/subStatus
# 4005), which is why Atmos-only tracks silently landed as the stereo AAC 320
# fallback. This id still delivers real Atmos (verified end to end: eac3, 5.1,
# 768 kbps) and needs NO secret: TIDAL accepts the refresh with an empty
# secret, so the only value shipped is this public id. Expect TIDAL to rotate
# it eventually; a fresh working id surfaces in the streamrip / OrpheusDL /
# tiddl issue trackers when it does.
ATMOS_ID_B64 = "NE4zbj" + "ZRMXg5" + "NUxMNU" + "s3cA=="

ATMOS_CLIENT_ID = base64.b64decode(ATMOS_ID_B64).decode("utf-8")
# Empty on purpose: this client authenticates without a private secret (tidalapi
# then sends the public id in the secret's place). Nothing sensitive is shipped.
ATMOS_CLIENT_SECRET = ""
ATMOS_REQUEST_QUALITY = Quality.low_320k


class QualityVideo(StrEnum):
    P360 = "360"
    P480 = "480"
    P720 = "720"
    P1080 = "1080"


class MediaType(StrEnum):
    TRACK = "track"
    VIDEO = "video"
    PLAYLIST = "playlist"
    ALBUM = "album"
    MIX = "mix"
    ARTIST = "artist"


class CoverDimensions(StrEnum):
    Px80 = "80"
    Px160 = "160"
    Px320 = "320"
    Px640 = "640"
    Px1280 = "1280"
    PxORIGIN = "origin"


class TidalLists(StrEnum):
    Playlists = "Playlists"
    Favorites = "Favorites"
    Mixes = "Mixes"


class QueueDownloadStatus(StrEnum):
    Waiting = "⏳️"
    Downloading = "▶️"
    Finished = "✅"
    Failed = "❌"
    Skipped = "↪️"


FAVORITES: dict[str, dict[str, str]] = {
    "fav_videos": {"name": "Videos", "function_name": "videos"},
    "fav_tracks": {"name": "Tracks", "function_name": "tracks_paginated"},
    "fav_mixes": {"name": "Mixes & Radio", "function_name": "mixes"},
    "fav_artists": {"name": "Artists", "function_name": "artists_paginated"},
    "fav_albums": {"name": "Albums", "function_name": "albums_paginated"},
}


class AudioExtensionsValid(StrEnum):
    FLAC = ".flac"
    M4A = ".m4a"
    MP4 = ".mp4"
    MP3 = ".mp3"
    OGG = ".ogg"
    ALAC = ".alac"


class MetadataTargetUPC(StrEnum):
    UPC = "UPC"
    BARCODE = "BARCODE"
    EAN = "EAN"


METADATA_LOOKUP_UPC: dict[str, dict[str, str]] = {
    "UPC": {"MP3": "UPC", "MP4": "UPC", "FLAC": "UPC"},
    "BARCODE": {"MP3": "BARCODE", "MP4": "BARCODE", "FLAC": "BARCODE"},
    "EAN": {"MP3": "EAN", "MP4": "EAN", "FLAC": "EAN"},
}


class InitialKey(StrEnum):
    ALPHANUMERIC = "alphanumeric"
    CLASSIC = "classic"


class DownsampleTarget(StrEnum):
    BIT16_48 = "16_48"
    BIT24_48 = "24_48"

    @property
    def sample_rate(self) -> int:
        return 48000

    @property
    def bit_depth(self) -> int:
        return 16 if self is DownsampleTarget.BIT16_48 else 24
