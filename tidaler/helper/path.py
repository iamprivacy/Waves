import contextlib
import math
import os
import pathlib
import posixpath
import re
import shutil
import sys
import threading
import unicodedata
from collections.abc import Collection
from copy import deepcopy
from urllib.parse import unquote, urlsplit

from pathvalidate import sanitize_filename, sanitize_filepath
from pathvalidate.error import ErrorReason, ValidationError
from tidalapi import Album, Mix, Playlist, Track, UserPlaylist, Video
from tidalapi.media import AudioExtensions

from tidaler import __config_dirname__
from tidaler.constants import (
    FILENAME_LENGTH_MAX,
    FILENAME_SANITIZE_PLACEHOLDER,
    FORMAT_TEMPLATE_EXPLICIT,
    UNIQUIFY_THRESHOLD,
    MediaType,
)
from tidaler.helper.tidal import name_builder_album_artist, name_builder_artist, name_builder_title


def path_home() -> str:
    """Get the home directory path.

    Returns:
        str: The home directory path.
    """
    if "XDG_CONFIG_HOME" in os.environ:
        return os.environ["XDG_CONFIG_HOME"]
    elif "HOME" in os.environ:
        return os.environ["HOME"]
    elif "HOMEDRIVE" in os.environ and "HOMEPATH" in os.environ:
        return os.path.join(os.environ["HOMEDRIVE"], os.environ["HOMEPATH"])
    else:
        return os.path.abspath("./")


# The platform's cap on a WHOLE path, one under the documented maximum so the
# terminating NUL it includes is never the difference (MAX_PATH 260 on Windows,
# PATH_MAX 1024 on macOS; Linux allows 4096 but nothing here needs the
# headroom). pathvalidate cannot be trusted with this number: it strips the
# drive or UNC prefix before measuring and allows the remainder up to 260, so
# a Windows path 3 characters over the real limit (15 with a \\server\share
# base) passed its check and failed at the final move, after the download had
# finished. Every whole-path measurement in this module uses this cap and
# counts the full spelling, prefix included.
PATH_LENGTH_MAX: int = 259 if sys.platform == "win32" else 1023


def _path_length(path: pathlib.Path) -> int:
    """How long a path is, the way its platform will measure it.

    Windows measures UTF-16 units against MAX_PATH; Python's len counts code
    points, which only differs on astral characters (each costs two UTF-16
    units), so those are counted at their real weight. POSIX measures bytes.
    """
    text = str(path)
    if sys.platform == "win32":
        return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)
    return len(os.fsencode(text))


def _exceeds_path_cap(path: pathlib.Path) -> bool:
    """Whether a whole path is over the platform cap, prefix included."""
    return _path_length(path) > PATH_LENGTH_MAX


# One-shot legacy-config migration bookkeeping (see path_config_base):
# "" = nothing to do / not attempted, "moved" = migrated, "failed" = the move
# raised and the legacy folder is still in use. The app logs this at startup.
CONFIG_MIGRATION: str = ""
_migrate_lock = threading.Lock()


def _path_home_plain() -> str:
    """The user's home directory, ignoring the XDG_CONFIG_HOME override.

    Used to build the platform-native config location, which must anchor on
    the real home even when XDG_CONFIG_HOME points elsewhere.
    """
    if "HOME" in os.environ:
        return os.environ["HOME"]
    if "HOMEDRIVE" in os.environ and "HOMEPATH" in os.environ:
        return os.path.join(os.environ["HOMEDRIVE"], os.environ["HOMEPATH"])
    return os.path.abspath("./")


def _path_config_native() -> str:
    """The platform's conventional per-user config folder, or "" when the
    platform has no convention beyond XDG (Linux and everything else).

    macOS:   ~/Library/Application Support/<app>
    Windows: %APPDATA%\\<app> (roaming; "" if APPDATA is unset)
    """
    if sys.platform == "darwin":
        return os.path.join(_path_home_plain(), "Library", "Application Support", __config_dirname__)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return os.path.join(appdata, __config_dirname__) if appdata else ""
    return ""


def _migrate_legacy_config(legacy: str, native: str) -> None:
    """Move the legacy dotfolder to the native location, exactly once.

    Whole-directory rename when the native folder does not exist yet;
    otherwise a merge that never overwrites (an installer may have created
    the native folder first, e.g. the Homebrew cask writing its
    install-channel sentinel there before the app's first launch). A failure
    is recorded so path_config_base keeps answering with the still-working
    legacy folder instead of silently splitting the user's settings.
    """
    global CONFIG_MIGRATION
    try:
        if not os.path.isdir(native):
            os.makedirs(os.path.dirname(native), exist_ok=True)
            shutil.move(legacy, native)
        else:
            for name in os.listdir(legacy):
                target = os.path.join(native, name)
                if not os.path.exists(target):
                    shutil.move(os.path.join(legacy, name), target)
            # Only removable once everything moved; a leftover (collision)
            # keeps the folder, which is fine: native already wins.
            with contextlib.suppress(OSError):
                os.rmdir(legacy)
        CONFIG_MIGRATION = "moved"
    except OSError:
        CONFIG_MIGRATION = "failed"


def path_config_base() -> str:
    """Get the base configuration path.

    Platform-native so users can actually find it: Application Support on
    macOS, %APPDATA% on Windows, XDG ~/.config elsewhere. An explicit
    XDG_CONFIG_HOME still wins on every platform (power-user override, and
    the upstream behavior). The first call that finds config only at the
    legacy ~/.config location migrates it over; if that move fails the
    legacy folder stays authoritative, so settings never silently vanish.

    Returns:
        str: The base configuration path.
    """
    # https://wiki.archlinux.org/title/XDG_Base_Directory
    # X11 workaround: If user specified config path is set, do not point to "~/.config"
    path_user_custom: str = os.environ.get("XDG_CONFIG_HOME", "")
    if path_user_custom:
        return os.path.join(path_user_custom, __config_dirname__)

    legacy: str = os.path.join(path_home(), ".config", __config_dirname__)
    native: str = _path_config_native()
    if not native or native == legacy:
        return legacy

    if CONFIG_MIGRATION == "failed":
        return legacy
    # Migrate when the legacy folder exists and the native one is not yet a
    # real config home (missing, or created empty/sentinel-only by an
    # installer). Both having settings.json means native is already live:
    # leave the stale legacy folder alone.
    if os.path.isdir(legacy) and not os.path.isfile(os.path.join(native, "settings.json")):
        with _migrate_lock:
            if os.path.isdir(legacy) and not os.path.isfile(os.path.join(native, "settings.json")):
                _migrate_legacy_config(legacy, native)
        if CONFIG_MIGRATION == "failed":
            return legacy
    return native


def path_file_log() -> str:
    """Get the path to the log file.

    Returns:
        str: The log file path.
    """
    return os.path.join(path_config_base(), "app.log")


def path_file_token() -> str:
    """Get the path to the token file.

    Returns:
        str: The token file path.
    """
    return os.path.join(path_config_base(), "token.json")


def path_file_settings() -> str:
    """Get the path to the settings file.

    Returns:
        str: The settings file path.
    """
    return os.path.join(path_config_base(), "settings.json")


# Tokens whose value dresses itself with a trailing separator space when
# present (and disappears entirely when not); format_path_media preserves
# that one space through sanitization.
_SELF_DRESSING_TOKENS = {"video_year_optional"}


# A replacement is decoration, not content: a couple of characters at most,
# so a pasted paragraph cannot become part of every file name.
_REPLACEMENT_MAX_LEN = 3


# The characters a file name cannot hold, in the order the Settings page lists
# them. pathvalidate rejects every one of them on every platform (it applies
# the union of the Windows and POSIX rules), which is exactly what makes them
# the set a per-character stand-in can be given: anything else is never
# removed in the first place, so a stand-in for it would do nothing.
ILLEGAL_FILENAME_CHARS = ("/", "\\", ":", "*", "?", '"', "<", ">", "|")


def safe_filename_replacement(value: str) -> str:
    """A user-chosen illegal-character replacement, reduced to what is safe.

    The Settings box accepts anything, so the value is laundered here, at the
    point of use, rather than trusted from disk: only characters that are
    themselves legal in a file name survive (probed one at a time between
    letters, so edge-trimming rules cannot hide an illegal one), and the
    result is capped at a few characters. Anything else, including a non
    string smuggled in by hand-editing the config file, collapses toward "",
    which is the shipped behavior of simply removing the character.
    """
    if not isinstance(value, str) or not value:
        return ""
    kept = []
    for ch in value[:_REPLACEMENT_MAX_LEN]:
        probe = f"a{ch}a"
        if ch == " " or sanitize_filename(probe) == probe:
            kept.append(ch)
    return "".join(kept)


def safe_filename_replacement_map(value) -> dict[str, str]:
    """The user's per-character stand-ins, reduced to what is safe.

    One stand-in for every rejected character reads badly on the characters
    that carry meaning: a colon is a subtitle ("Rarities Edition: Live"), and
    "-" there is not what the title said. The map names a stand-in per
    character, so ":" can become " · " while "?" becomes "-" and "/" is simply
    removed (issue #16).

    Laundered here, at the point of use, exactly like the general stand-in:
    only the characters a file name genuinely cannot hold can be given one
    (anything else is never removed, so a stand-in for it would silently do
    nothing), and each stand-in goes through safe_filename_replacement, so no
    entry can put a rejected character back into a name. A key or value of any
    other shape, which only a hand-edited config file can produce, is dropped.
    """
    if not isinstance(value, dict):
        return {}
    return {
        char: safe_filename_replacement(replacement)
        for char, replacement in value.items()
        if isinstance(char, str) and char in ILLEGAL_FILENAME_CHARS
    }


def _apply_replacement_map(value: str, replacement_map: dict[str, str]) -> str:
    """Write each mapped character's own stand-in in its place.

    Runs before sanitize_filename, so a mapped character is already gone by
    the time the general stand-in applies and only the characters left unnamed
    fall back to it.
    """
    if not replacement_map:
        return value
    return "".join(replacement_map.get(char, char) for char in value)


def _tidy_spacing(value: str) -> str:
    """Collapse the whitespace a stripped illegal character leaves behind.

    ``pathvalidate`` deletes characters a filesystem rejects but keeps what
    surrounded them, so an album called ``The Better Life / Dead Love`` came
    out as ``The Better Life  Dead Love``, with a double space where the
    slash used to be (reported in issue #15). Runs of whitespace collapse to
    a single space and the edges are trimmed, which also tidies names whose
    illegal character sat at the start or end.

    Applied to a token's value only, never to the assembled template, so a
    separator the template itself spells out is untouched.
    """
    return re.sub(r"\s+", " ", value).strip()


def sanitize_name_component(
    value: str,
    illegal_replacement: str = "",
    illegal_map: dict[str, str] | None = None,
    tidy_spacing: bool = True,
) -> str:
    """One path segment written the way the library spells names.

    The per-character stand-ins first, then pathvalidate for whatever is left
    (with the general stand-in), then the spacing tidy. Sanitizing runs against
    pathvalidate's universal rules, not the running platform's, so a name
    written on a Mac is one a Windows machine reading the same share can open
    too.

    Every name that becomes a folder or a file in the library goes through here,
    so a name is spelled identically wherever it is built: a playlist's m3u used
    to skip the stand-ins entirely, and a playlist called "?" therefore lost its
    name where an album called "?" kept one.

    Args:
        value (str): The raw name.
        illegal_replacement (str, optional): Text written where a rejected character
            is removed. Defaults to "", plain removal.
        illegal_map (dict[str, str] | None, optional): Per-character stand-ins applied
            before the general one. Defaults to None.
        tidy_spacing (bool, optional): Collapse the whitespace a removed character
            leaves behind. Defaults to True.

    Returns:
        str: The sanitized name, possibly empty when nothing survives.
    """
    result = _apply_replacement_map(value, illegal_map or {})
    result = sanitize_filename(result, replacement_text=illegal_replacement)

    return _tidy_spacing(result) if tidy_spacing else result


def format_path_media(
    fmt_template: str,
    media: Track | Album | Playlist | UserPlaylist | Video | Mix,
    album_track_num_pad_min: int = 0,
    list_pos: int = 0,
    list_total: int = 0,
    delimiter_artist: str = ", ",
    delimiter_album_artist: str = ", ",
    use_primary_album_artist: bool = False,
    tidy_spacing: bool = True,
    illegal_replacement: str = "",
    illegal_map: dict[str, str] | None = None,
) -> str:
    """Formats a media path string using a template and media attributes.

    Replaces placeholders in the format template with sanitized media attribute values to generate a valid file path.

    Args:
        fmt_template (str): The format template string containing placeholders.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract values from.
        album_track_num_pad_min (int, optional): Minimum padding for track numbers. Defaults to 0.
        list_pos (int, optional): Position in a list. Defaults to 0.
        list_total (int, optional): Total items in a list. Defaults to 0.
        delimiter_artist (str, optional): Delimiter for artist names. Defaults to ", ".
        delimiter_album_artist (str, optional): Delimiter for album artist names. Defaults to ", ".
        use_primary_album_artist (bool, optional): If True, uses first album artist for folder paths. Defaults to False.
        tidy_spacing (bool, optional): Collapse the whitespace a stripped illegal
            character leaves behind. Defaults to True. Pass False to reproduce the
            names releases before 0.1.17 produced, which is how the download engine
            recognises a library built under the old spelling and keeps writing
            into it instead of renaming anything.
        illegal_replacement (str, optional): Text written where an illegal
            character is removed ("AC/DC" with "-" becomes "AC-DC"). Defaults to
            "", plain removal. Callers pass values through
            safe_filename_replacement first; the engine always passes "" when
            reproducing an older spelling, since the setting postdates them.
        illegal_map (dict[str, str], optional): Per-character stand-ins, applied
            before the general one, so ":" can become " · " while everything
            else follows illegal_replacement. Defaults to None, no overrides.
            Callers pass values through safe_filename_replacement_map first.

    Returns:
        str: The formatted and sanitized media path string.
    """
    result = fmt_template

    # Search track format template for placeholder.
    regex = r"\{(.+?)\}"
    matches = re.finditer(regex, fmt_template, re.MULTILINE)

    for _matchNum, match in enumerate(matches, start=1):
        template_str = match.group()
        result_fmt = format_str_media(
            match.group(1),
            media,
            album_track_num_pad_min,
            list_pos,
            list_total,
            delimiter_artist=delimiter_artist,
            delimiter_album_artist=delimiter_album_artist,
            use_primary_album_artist=use_primary_album_artist,
        )

        if result_fmt != match.group(1):
            # Sanitize here, in case of the filename has slashes or something, which will be recognized later as a directory separator.
            # Do not sanitize if value is the FORMAT_TEMPLATE_EXPLICIT placeholder, since it has a leading whitespace which otherwise gets removed.
            if result_fmt == FORMAT_TEMPLATE_EXPLICIT:
                value = FORMAT_TEMPLATE_EXPLICIT
            else:
                value = sanitize_name_component(result_fmt, illegal_replacement, illegal_map, tidy_spacing)
            # Self-dressing tokens carry their own separator space
            # ("[2026] "); sanitize_filename trims edge whitespace, which
            # would weld the year prefix straight onto the title. Scoped to
            # the known tokens so an ordinary value that happens to end in a
            # space keeps today's trimmed behavior.
            if match.group(1) in _SELF_DRESSING_TOKENS and result_fmt.endswith(" ") and value:
                value += " "
            result = result.replace(template_str, value)

    return _drop_empty_segments(result)


def _drop_empty_segments(path_relative: str) -> str:
    """Collapse empty components out of a formatted relative media path.

    A token whose value sanitizes to ``""`` is substituted blind, and both
    default templates open with ``{artist_name}``. An artist name that empties
    out under pathvalidate (``?``, ``*``, ``<>``, ``|``, ``"``, or a name of
    only dots) therefore made the relative path start with a separator, and
    ``Path(path_base) / file_name_relative`` DISCARDS the base when the
    right-hand operand is absolute. On Windows ``PureWindowsPath`` keeps the
    drive, so the track landed outside the download folder (at ``C:\\<album>``)
    and the queue still reported done; on POSIX the write failed at the volume
    root with an unexplained errno 30. ``_no_traversal`` covers ``..`` escaping
    the base and does not address this shape.

    Dropping empty components keeps the path relative and inside the base, and
    also tidies the doubled separator an emptied mid-template token leaves.
    """
    return "/".join(part for part in re.split(r"[\\/]+", path_relative) if part)


def format_str_media(
    name: str,
    media: Track | Album | Playlist | UserPlaylist | Video | Mix,
    album_track_num_pad_min: int = 0,
    list_pos: int = 0,
    list_total: int = 0,
    delimiter_artist: str = ", ",
    delimiter_album_artist: str = ", ",
    use_primary_album_artist: bool = False,
) -> str:
    """Formats a string for media attributes based on the provided name.

    Attempts to format the given name using a sequence of formatter functions, returning the first successful result.

    Args:
        name (str): The format string name to process.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract values from.
        album_track_num_pad_min (int, optional): Minimum padding for track numbers. Defaults to 0.
        list_pos (int, optional): Position in a list. Defaults to 0.
        list_total (int, optional): Total items in a list. Defaults to 0.
        delimiter_artist (str, optional): Delimiter for artist names. Defaults to ", ".
        delimiter_album_artist (str, optional): Delimiter for album artist names. Defaults to ", ".
        use_primary_album_artist (bool, optional): If True, uses first album artist for folder paths. Defaults to False.

    Returns:
        str: The formatted string for the media attribute, or the original name if no formatter matches.
    """
    try:
        # Try each formatter function in sequence
        for formatter in (
            _format_names,
            _format_numbers,
            _format_ids,
            _format_durations,
            _format_dates,
            _format_video_dates,
            _format_metadata,
            _format_volumes,
        ):
            result = formatter(
                name,
                media,
                album_track_num_pad_min,
                list_pos,
                list_total,
                delimiter_artist=delimiter_artist,
                delimiter_album_artist=delimiter_album_artist,
                use_primary_album_artist=use_primary_album_artist,
            )
            if result is not None:
                return result
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        print(f"Error formatting path for media attribute '{name}': {e}")

    return name


def _format_artist_names(
    name: str,
    media: Track | Album | Playlist | UserPlaylist | Video | Mix,
    delimiter_artist: str = ", ",
    delimiter_album_artist: str = ", ",
    *_args,
    use_primary_album_artist: bool = False,
    **kwargs,
) -> str | None:
    """Handle artist name-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract artist information from.
        delimiter_artist (str, optional): Delimiter for artist names. Defaults to ", ".
        delimiter_album_artist (str, optional): Delimiter for album artist names. Defaults to ", ".
        use_primary_album_artist (bool, optional): If True, uses first album artist for folder paths. Defaults to False.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted artist name or None if the format string is not artist-related.
    """
    if name == "artist_name" and isinstance(media, Track | Video):
        # For folder paths, use album artist if setting is enabled
        if use_primary_album_artist and hasattr(media, "album") and media.album and media.album.artists:
            return media.album.artists[0].name
        # Otherwise use track artists as before
        if hasattr(media, "artists"):
            return name_builder_artist(media, delimiter=delimiter_artist)
        elif hasattr(media, "artist"):
            return media.artist.name
    elif name == "artist_name_primary" and isinstance(media, Track | Video):
        # Only the first credited artist: keeps one folder per artist where
        # {artist_name} would mint a new "A, B, C" folder for every collab.
        # Videos have no usable album artist (their album is a placeholder),
        # so this is their only route to a stable per-artist folder.
        primary = getattr(media, "artist", None)
        if primary is not None and getattr(primary, "name", ""):
            return primary.name
        artists = getattr(media, "artists", None) or []
        return artists[0].name if artists else ""
    elif name == "album_artist":
        return name_builder_album_artist(media, first_only=True)
    elif name == "album_artists":
        return name_builder_album_artist(media, delimiter=delimiter_album_artist)
    return None


def _format_titles(
    name: str, media: Track | Album | Playlist | UserPlaylist | Video | Mix, *_args, **kwargs
) -> str | None:
    """Handle title-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract title information from.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted title or None if the format string is not title-related.
    """
    if name == "track_title" and isinstance(media, Track | Video):
        return name_builder_title(media)
    elif name == "mix_name" and isinstance(media, Mix):
        return media.title
    elif name == "playlist_name" and isinstance(media, Playlist | UserPlaylist):
        return media.name
    elif name == "album_title":
        if isinstance(media, Album):
            return media.name
        elif isinstance(media, Track):
            return media.album.name
    return None


def _format_names(
    name: str,
    media: Track | Album | Playlist | UserPlaylist | Video | Mix,
    *args,
    delimiter_artist: str = ", ",
    delimiter_album_artist: str = ", ",
    use_primary_album_artist: bool = False,
    **kwargs,
) -> str | None:
    """Handles name-related format strings for media.

    Tries to format the provided name as an artist or title, returning the first matching result.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract name information from.
        *args: Additional arguments (not used).
        delimiter_artist (str, optional): Delimiter for artist names. Defaults to ", ".
        delimiter_album_artist (str, optional): Delimiter for album artist names. Defaults to ", ".
        use_primary_album_artist (bool, optional): If True, uses first album artist for folder paths. Defaults to False.

    Returns:
        str | None: The formatted name or None if the format string is not name-related.
    """
    # First try artist name formats
    result = _format_artist_names(
        name,
        media,
        delimiter_artist=delimiter_artist,
        delimiter_album_artist=delimiter_album_artist,
        use_primary_album_artist=use_primary_album_artist,
    )
    if result is not None:
        return result

    # Then try title formats
    return _format_titles(name, media)


def _format_numbers(
    name: str,
    media: Track | Album | Playlist | UserPlaylist | Video | Mix,
    album_track_num_pad_min: int,
    list_pos: int,
    list_total: int,
    *_args,
    **kwargs,
) -> str | None:
    """Handle number-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract number information from.
        album_track_num_pad_min (int): Minimum padding for track numbers.
        list_pos (int): Position in a list.
        list_total (int): Total items in a list.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted number or None if the format string is not number-related.
    """
    if name == "album_track_num" and isinstance(media, Track | Video):
        return calculate_number_padding(
            album_track_num_pad_min,
            media.track_num,
            media.album.num_tracks if hasattr(media, "album") else 1,
        )
    elif name == "album_num_tracks" and isinstance(media, Track | Video):
        return str(media.album.num_tracks if hasattr(media, "album") else 1)
    elif name == "list_pos" and isinstance(media, Track | Video):
        # TODO: Rename `album_track_num_pad_min` globally.
        return calculate_number_padding(album_track_num_pad_min, list_pos, list_total)
    return None


def _format_ids(
    name: str, media: Track | Album | Playlist | UserPlaylist | Video | Mix, *_args, **kwargs
) -> str | None:
    """Handle ID-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract ID information from.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted ID or None if the format string is not ID-related.
    """
    # Handle track and playlist IDs
    if (
        (name == "track_id" and isinstance(media, Track))
        or (name == "playlist_id" and isinstance(media, Playlist))
        or (name == "video_id" and isinstance(media, Video))
    ):
        return str(media.id)
    # Handle album IDs
    elif name == "album_id":
        if isinstance(media, Album):
            return str(media.id)
        elif isinstance(media, Track):
            return str(media.album.id)
    # Handle ISRC
    elif name == "isrc" and isinstance(media, Track):
        # "" (my token, no value) rather than None ("not my token"): None
        # leaves a literal {isrc} in the folder name, "" is substituted and
        # the empty segment is collapsed away.
        return media.isrc or ""
    elif name == "album_artist_id" and isinstance(media, Album):
        return str(media.artist.id)
    elif name == "track_artist_id" and isinstance(media, Track):
        return str(media.album.artist.id)
    return None


def _format_durations(
    name: str, media: Track | Album | Playlist | UserPlaylist | Video | Mix, *_args, **kwargs
) -> str | None:
    """Handle duration-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract duration information from.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted duration or None if the format string is not duration-related.
    """
    # Format track durations
    if name == "track_duration_seconds" and isinstance(media, Track | Video):
        return str(media.duration)
    elif name == "track_duration_minutes" and isinstance(media, Track | Video):
        m, s = divmod(media.duration, 60)
        return f"{m:01d}:{s:02d}"

    # Format album durations
    elif name == "album_duration_seconds" and isinstance(media, Album):
        return str(media.duration)
    elif name == "album_duration_minutes" and isinstance(media, Album):
        m, s = divmod(media.duration, 60)
        return f"{m:01d}:{s:02d}"

    # Format playlist durations
    elif name == "playlist_duration_seconds" and isinstance(media, Album):
        return str(media.duration)
    elif name == "playlist_duration_minutes" and isinstance(media, Album):
        m, s = divmod(media.duration, 60)
        return f"{m:01d}:{s:02d}"

    return None


def _format_dates(
    name: str, media: Track | Album | Playlist | UserPlaylist | Video | Mix, *_args, **kwargs
) -> str | None:
    """Handle date-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract date information from.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted date or None if the format string is not date-related.
    """
    if name == "album_year":
        if isinstance(media, Album):
            return str(media.year)
        elif isinstance(media, Track):
            return str(media.album.year)
    elif name == "album_date":
        # "" (my token, no value) rather than None ("not my token"): None
        # leaves a literal {album_date} in the folder name (back-catalogue
        # albums often have a year but no full date), "" is substituted and
        # the empty segment is collapsed away.
        if isinstance(media, Album):
            return media.release_date.strftime("%Y-%m-%d") if media.release_date else ""
        elif isinstance(media, Track):
            return media.album.release_date.strftime("%Y-%m-%d") if media.album.release_date else ""

    return None


def _format_video_dates(
    name: str, media: Track | Album | Playlist | UserPlaylist | Video | Mix, *_args, **kwargs
) -> str | None:
    """Handle the video release-date format strings.

    A video carries its own release_date (no album to borrow one from).
    All three return "" rather than None on a missing date, the same
    rationale as album_date: "" substitutes and collapses away, None would
    leave the literal token in the path.
    """
    if not isinstance(media, Video):
        return None
    if name == "video_year":
        return str(media.release_date.year) if media.release_date else ""
    elif name == "video_date":
        return media.release_date.strftime("%Y-%m-%d") if media.release_date else ""
    elif name == "video_year_optional":
        # Self-dressed like track_volume_num_optional: the default video
        # template's bracketed year prefix ("[2026] Song"), or nothing at
        # all when TIDAL has no release date, so a dateless video gets a
        # clean bare title instead of "[] Song".
        return f"[{media.release_date.year}] " if media.release_date else ""

    return None


def _format_metadata(
    name: str, media: Track | Album | Playlist | UserPlaylist | Video | Mix, *_args, **kwargs
) -> str | None:
    """Handle metadata-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract metadata information from.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted metadata or None if the format string is not metadata-related.
    """
    if name == "video_quality" and isinstance(media, Video):
        return media.video_quality
    elif name == "track_quality" and isinstance(media, Track):
        return ", ".join(tag for tag in media.media_metadata_tags if tag is not None)
    elif name == "track_explicit" and isinstance(media, Track | Video):
        return FORMAT_TEMPLATE_EXPLICIT if media.explicit else ""
    elif name == "album_explicit":
        # Paths are always formatted with the Track / Video being written, so
        # the album marker must resolve through media.album too, not just for
        # a bare Album object.
        if isinstance(media, Album):
            return FORMAT_TEMPLATE_EXPLICIT if media.explicit else ""
        if isinstance(media, Track | Video) and getattr(media, "album", None):
            return FORMAT_TEMPLATE_EXPLICIT if media.album.explicit else ""
        return ""
    elif name == "media_type":
        if isinstance(media, Album):
            return media.type
        elif isinstance(media, Track):
            return media.album.type
    return None


def _format_volumes(
    name: str, media: Track | Album | Playlist | UserPlaylist | Video | Mix, *_args, **kwargs
) -> str | None:
    """Handle volume-related format strings.

    Args:
        name (str): The format string name to check.
        media (Track | Album | Playlist | UserPlaylist | Video | Mix): The media object to extract volume information from.
        *_args (Any): Additional arguments (not used).

    Returns:
        str | None: The formatted volume information or None if the format string is not volume-related.
    """
    if name == "album_num_volumes" and isinstance(media, Album):
        return str(media.num_volumes)
    elif name == "track_volume_num" and isinstance(media, Track | Video):
        return str(media.volume_num)
    elif name == "track_volume_num_optional" and isinstance(media, Track | Video):
        num_volumes: int = media.album.num_volumes if hasattr(media, "album") else 1
        # Disc-number prefix in Plex's documented "2-01 - Track" style: the
        # dash keeps disc 2 track 13 readable as 2-13 instead of 213.
        return "" if num_volumes == 1 else f"{media.volume_num!s}-"
    elif name == "track_volume_num_optional_CD" and isinstance(media, Track | Video):
        num_volumes: int = media.album.num_volumes if hasattr(media, "album") else 1
        return "" if num_volumes == 1 else f"CD{media.volume_num!s}"
    return None


def calculate_number_padding(padding_minimum: int, item_position: int, items_max: int) -> str:
    """Calculate the padded number string for an item.

    Args:
        padding_minimum (int): Minimum number of digits for padding.
        item_position (int): The position of the item.
        items_max (int): The maximum number of items.

    Returns:
        str: The padded number string.
    """
    result: str

    if items_max > 0:
        count_digits = max(int(math.log10(items_max)) + 1, padding_minimum)
        result = str(item_position).zfill(count_digits)
    else:
        result = str(item_position)

    return result


def get_format_template(
    media: Track | Album | Playlist | UserPlaylist | Video | Mix | MediaType, settings
) -> str | bool:
    """Get the format template for a given media type.

    Args:
        media (Track | Album | Playlist | UserPlaylist | Video | Mix | MediaType): The media object or type.
        settings: The settings object containing format templates.

    Returns:
        str | bool: The format template string or False if not found.
    """
    result = False

    if isinstance(media, Track) or media == MediaType.TRACK:
        result = settings.data.format_track
    elif isinstance(media, Album) or media == MediaType.ALBUM or media == MediaType.ARTIST:
        result = settings.data.format_album
    elif isinstance(media, Playlist | UserPlaylist) or media == MediaType.PLAYLIST:
        result = settings.data.format_playlist
    elif isinstance(media, Mix) or media == MediaType.MIX:
        result = settings.data.format_mix
    elif isinstance(media, Video) or media == MediaType.VIDEO:
        result = settings.data.format_video

    return result


def _no_traversal(part: str) -> str:
    """Neutralize a bare current/parent-directory path component.

    ``pathvalidate``'s ``sanitize_filename`` strips illegal characters but
    deliberately leaves ``.`` and ``..`` untouched, so a remote-controlled media
    name (artist/album/track title from the API) that renders to exactly ``..``
    would survive as a live traversal segment and escape the download directory.
    Mapping it to the standard replacement char makes that impossible regardless
    of the user's path template.
    """
    return "_" if part in (".", "..") else part


def _shorten_to_valid_length(path: pathlib.Path, sanitize) -> pathlib.Path:
    """Shrink a too-long directory path until the platform accepts it.

    Deterministic (the same input always shortens the same way, so every track
    of an album still lands in one folder): halve the deepest component's name
    repeatedly, and once it is down to a single character drop it and move one
    level up. The shallow components (the user's download base) go last, so
    the result stays inside the library for any realistic base path.

    Args:
        path (pathlib.Path): The over-long directory path.
        sanitize: Callable that returns the path if valid and raises
            ``ValidationError`` (PV1101) when it is still too long.

    Returns:
        pathlib.Path: The shortened, valid path.
    """
    parts = list(path.parts)
    while len(parts) > 1:
        candidate = pathlib.Path(*parts)
        try:
            return sanitize(candidate)
        except ValidationError as e:
            if not str(e).startswith("[PV1101]"):
                raise
        name = parts[-1]
        if len(name) > 1:
            parts[-1] = name[: max(1, len(name) // 2)]
        else:
            parts.pop()
    return sanitize(pathlib.Path(*parts))


def _fit_name_within_path(directory: pathlib.Path, name: str, sanitize) -> pathlib.Path:
    """Shrink an over-long full path, taking it out of the file name first.

    The name belongs to this one file, so trimming it costs nothing else;
    shortening the directory instead would respell a folder every track of the
    album shares, and a respelled folder is exactly how issue #16 orphaned an
    album. The extension is kept whatever happens, or the file stops reading as
    audio. Only once the stem is down to a single character does the directory
    have to give, and then through the same deterministic shortener the
    directory-only case uses, so all of an album's tracks still land together.

    The trim is measured, not guessed: the stem gives up exactly the overage
    (byte-counted, so it is never short on POSIX and at worst generous on
    Windows) and the halving loop below is only the backstop for whatever the
    arithmetic cannot see. Halving alone cost a title half its length for a
    one-character overflow, and two long titles differing only in their back
    halves collapsed onto one name.

    Args:
        directory (pathlib.Path): The already-sanitized parent directory.
        name (str): The already-sanitized file name.
        sanitize: Callable that returns its argument or raises ValidationError
            (PV1101) when the path is still too long.

    Returns:
        pathlib.Path: A path that fits.
    """
    suffix: str = pathlib.PurePath(name).suffix
    stem: str = name[: len(name) - len(suffix)] if suffix else name

    overage: int = _path_length(directory / (stem + suffix)) - PATH_LENGTH_MAX
    if overage > 0:
        measured = truncate_to_byte_limit(stem, max(1, len(os.fsencode(stem)) - overage))
        if measured:
            candidate = directory / (measured + suffix)
            try:
                sanitize(candidate)
            except ValidationError as e:
                if not str(e).startswith("[PV1101]"):
                    raise
            else:
                return candidate

    while len(stem) > 1:
        stem = stem[: max(1, len(stem) // 2)]
        candidate = directory / (stem + suffix)

        try:
            sanitize(candidate)
        except ValidationError as e:
            if not str(e).startswith("[PV1101]"):
                raise

            continue

        return candidate

    def _sanitize_with_name(candidate_dir: pathlib.Path) -> pathlib.Path:
        sanitize(candidate_dir / (stem + suffix))

        return candidate_dir

    return _shorten_to_valid_length(directory, _sanitize_with_name) / (stem + suffix)


def path_file_sanitize(path_file: pathlib.Path, adapt: bool = False) -> pathlib.Path:
    """Sanitize a file path to ensure it is valid.

    Making the name unique is a separate step (path_file_uniquify): the caller
    has to hold its claim lock across picking the name and recording it, and it
    alone knows whether a file already on disk blocks the name or is the very
    copy this download replaces.

    Args:
        path_file (pathlib.Path): The file path to sanitize.
        adapt (bool, optional): Whether to adapt the path in case of errors. Defaults to False.

    Returns:
        pathlib.Path: The sanitized file path.
    """
    sanitized_filename = _no_traversal(
        sanitize_filename(path_file.name, replacement_text="_", validate_after_sanitize=True, platform="auto")
    )

    if not sanitized_filename.endswith(path_file.suffix):
        sanitized_filename = (
            sanitized_filename[: -len(path_file.suffix) - len(FILENAME_SANITIZE_PLACEHOLDER)]
            + FILENAME_SANITIZE_PLACEHOLDER
            + path_file.suffix
        )

    sanitized_path = pathlib.Path(
        *[
            (
                _no_traversal(
                    sanitize_filename(part, replacement_text="_", validate_after_sanitize=True, platform="auto")
                )
                if part not in path_file.anchor
                else part
            )
            for part in path_file.parent.parts
        ]
    )

    def _sanitize(p: pathlib.Path) -> pathlib.Path:
        result = sanitize_filepath(p, replacement_text="_", validate_after_sanitize=True, platform="auto")
        # pathvalidate's own length check is not enough: it strips the drive or
        # UNC prefix before measuring, so a path over the real Windows limit by
        # up to the prefix's length (plus its off-by-one against the NUL) came
        # back approved and failed at the final move. Measure the whole
        # spelling here, and speak PV1101 so the adapt machinery above and
        # below handles both length failures through one code path.
        if _exceeds_path_cap(result):
            raise ValidationError(
                description=f"path exceeds the platform cap of {PATH_LENGTH_MAX}",
                reason=ErrorReason.INVALID_LENGTH,
            )
        return result

    try:
        sanitized_path = _sanitize(sanitized_path)
    except ValidationError as e:
        if adapt and str(e).startswith("[PV1101]"):
            # The whole path exceeds the platform length cap (realistically
            # only Windows' 260). The old fallback substituted Path.home(),
            # silently relocating the track OUTSIDE the download base into the
            # user's home folder. Shorten the deepest components instead so
            # the file stays under the library base.
            sanitized_path = _shorten_to_valid_length(sanitized_path, _sanitize)
        else:
            raise

    result = sanitized_path / sanitized_filename

    # The joined path is what actually gets created, and only it can exceed the
    # platform's PATH cap (260 on Windows, 1024 elsewhere): the directory was
    # measured on its own and the name on its own, and both fit while the two
    # together do not. That was never re-checked, so an over-long path went out
    # to the move and failed there, with the download already finished.
    try:
        _sanitize(result)
    except ValidationError as e:
        if not (adapt and str(e).startswith("[PV1101]")):
            raise

        result = _fit_name_within_path(sanitized_path, sanitized_filename, _sanitize)

    return result


def truncate_to_byte_limit(value: str, limit_bytes: int) -> str:
    """Cut a name down to a byte budget, never mid-character.

    FILENAME_LENGTH_MAX is a BYTE limit on every real filesystem (255 on ext4,
    APFS, NTFS and the SMB dialects in between). Both places that trimmed a name
    to fit counted characters instead, so a title in CJK, Cyrillic or emoji
    measured well inside the cap at three or four bytes per character and blew
    ENAMETOOLONG when the move finally tried to create it.

    Args:
        value (str): The name to fit.
        limit_bytes (int): The budget, in encoded bytes.

    Returns:
        str: The longest prefix of value that fits, possibly empty.
    """
    if limit_bytes <= 0:
        return ""

    # A character is at least one byte, so the character-count slice is a safe
    # starting point and usually the answer already (any ASCII name).
    result: str = value[:limit_bytes]

    while result and len(os.fsencode(result)) > limit_bytes:
        result = result[:-1]

    return result


def name_comparison_key(value: str) -> str:
    """How a path has to be compared to answer "is this the same file?".

    A filesystem is not a string comparison. APFS and NTFS fold case, so
    "Intro.flac" and "intro.flac" are one file; and a name typed as NFC (what
    the API sends) is the same file as the NFD spelling a tool carried over
    from HFS+ wrote, because both filesystems compare normalization-insensitively.
    The in-flight claim set compared exact strings, so two tracks differing only
    that way each read the other's name as free, both claimed it, and the second
    finished download was refused at the move.

    Folding here is deliberately unconditional rather than probed per
    filesystem: on a case-sensitive Linux volume a genuine case-twin pair gets
    an unnecessary "_01", which is harmless and vanishingly rare, while probing
    would have to be redone per destination and would still race.

    FOR COMPARISON ONLY. What is written to disk stays exactly what the template
    produced: writing a folded or renormalized name would spell a library one
    way and look it up another, which is precisely how issue #16 lost a folder.

    Args:
        value (str): A path or name, as a string.

    Returns:
        str: The key two spellings of one file share.
    """
    return unicodedata.normalize("NFC", value).casefold()


def path_file_uniquify(
    path_file: pathlib.Path, *, names_taken: Collection[str] | None = None, check_disk: bool = True
) -> pathlib.Path | None:
    """Ensure a file path is unique by appending a suffix if necessary.

    Args:
        path_file (pathlib.Path): The file path to uniquify.
        names_taken (Collection[str] | None, optional): Paths (as strings) claimed by a
            download still in flight, treated as occupied. Defaults to None.
        check_disk (bool, optional): Whether a file already on disk counts as occupied.
            False for a download that is meant to replace what is there (skip-existing
            off, or a quality upgrade), which still has to step around the names its
            concurrent siblings hold. Defaults to True.

    Returns:
        pathlib.Path | None: The unique file path, or None when the name and all of its
            numbered variants are taken. The caller has to fail the download then: the
            old answer, the last occupied candidate, only moved the loss one step on.
    """
    unique_suffix: str | None = file_unique_suffix(path_file, names_taken=names_taken, check_disk=check_disk)

    if unique_suffix is None:
        return None

    if unique_suffix:
        path_file = _path_with_unique_suffix(path_file, unique_suffix)

    return path_file


def _path_with_unique_suffix(path_file: pathlib.Path, unique_suffix: str) -> pathlib.Path:
    """The path a unique suffix produces, stem trimmed to the filename cap.

    Only the FILENAME is bounded by the 255 limit. The old check measured the
    WHOLE path against it, so a short name at a deep path took the truncation
    branch, and that branch chopped a fixed slice off the stem's end (an
    8-character stem was annihilated to "_01.flac"). Measure the name alone and
    keep as much stem as fits.

    The limit is bytes, not characters: a stem in CJK or emoji fits 255
    characters easily and blows 255 bytes long before that, and the move then
    failed with ENAMETOOLONG after the download had finished. Only the stem is
    ever trimmed, never the suffix that makes the name unique.

    The WHOLE path is budgeted too, not only the name. A destination the
    sanitizer had just fitted to the platform's path cap sat within three
    characters of it, so inserting "_01" pushed the full path back over the
    limit nothing re-measured, and the move failed after the download had
    finished, identically on every retry.

    Args:
        path_file (pathlib.Path): The base file path.
        unique_suffix (str): The suffix to insert before the extension.

    Returns:
        pathlib.Path: The suffixed path.
    """
    file_suffix = unique_suffix + path_file.suffix
    stem = str(path_file.stem)

    budget: int = min(
        FILENAME_LENGTH_MAX - len(os.fsencode(file_suffix)),
        PATH_LENGTH_MAX - _path_length(path_file.parent) - 1 - len(os.fsencode(file_suffix)),
    )

    if len(os.fsencode(stem)) > budget:
        stem = truncate_to_byte_limit(stem, budget) or stem[:1]

    return path_file.parent / (stem + file_suffix)


def unique_variant_name(path_file: pathlib.Path, unique_suffix: str) -> str:
    """The NAME a unique suffix would produce for this path, trimming included.

    The scan that looks for a track's numbered copies has to spell each
    candidate exactly the way the writer spells it. Building the name by raw
    concatenation missed every copy of a stem at the 255-byte cap (the writer
    gives up stem bytes to the suffix, the concatenation does not), so the
    copies were re-downloaded, and re-numbered, on every run.
    """
    return _path_with_unique_suffix(path_file, unique_suffix).name


def file_unique_suffix(
    path_file: pathlib.Path,
    separator: str = "_",
    *,
    names_taken: Collection[str] | None = None,
    check_disk: bool = True,
) -> str | None:
    """Generate a unique suffix for a file path.

    Candidates are probed through _path_with_unique_suffix, the same trimming
    the caller applies, so the name checked here is the name actually returned
    (a stem near the 255-character cap used to be probed untrimmed and could
    hand back a trimmed name that already existed).

    Args:
        path_file (pathlib.Path): The file path to check for uniqueness.
        separator (str, optional): The separator to use for the suffix. Defaults to "_".
        names_taken (Collection[str] | None, optional): Paths (as strings) a concurrent
            download has claimed but not yet moved into place. They are nowhere on disk,
            so only this set keeps two colliding tracks from choosing one name.
            Defaults to None.
        check_disk (bool, optional): Whether a file already on disk counts as occupied.
            A download meant to replace what is there passes False: the file it lands
            on is its own older copy. Claims are honored either way, since a name a
            sibling is holding belongs to a download nothing may overwrite.
            Defaults to True.

    Returns:
        str | None: The unique suffix, an empty string if not needed, or None when the
            name and every one of its numbered variants is occupied. That used to come
            back as the last candidate tried, which is itself taken, so the caller
            moved onto an occupied name and the finished download was refused there
            with a collision error that named the wrong cause.
    """
    threshold_zfill: int = len(str(UNIQUIFY_THRESHOLD))
    # Claims are matched the way a filesystem matches (see name_comparison_key),
    # not as exact strings: a case twin or the other unicode normalization of a
    # claimed name is the same file, and handing it out again loses a download.
    taken: set[str] = {name_comparison_key(name) for name in names_taken or ()}
    count: int = 0
    path_file_tmp: pathlib.Path = deepcopy(path_file)
    unique_suffix: str = ""

    def _occupied(candidate: pathlib.Path) -> bool:
        return (check_disk and check_file_exists(candidate)) or name_comparison_key(str(candidate)) in taken

    while _occupied(path_file_tmp):
        if count >= UNIQUIFY_THRESHOLD:
            return None

        count += 1
        unique_suffix = separator + str(count).zfill(threshold_zfill)
        path_file_tmp = _path_with_unique_suffix(path_file, unique_suffix)

    return unique_suffix


def strip_apple_double(path_file: pathlib.Path) -> None:
    """Best-effort removal of macOS AppleDouble litter for one file.

    On network mounts that cannot store extended attributes in-band (WebDAV is
    the classic case), macOS materialises every xattr it wants to attach (for
    example com.apple.provenance on newly created files) as a hidden 4 KB
    sibling named ``._<name>``. Users browsing the share from another OS see
    these as ghost files next to every track and cover. Removing the xattrs
    from the file makes macOS's own WebDAV client retire the server-side
    companion. Deliberately prevention-only: this function never deletes or
    unlinks any file itself, it only edits attribute metadata on the one file
    Waves just wrote.

    macOS only, and every step is best-effort: metadata cleanup must never
    fail a download that already landed.

    Args:
        path_file (pathlib.Path): The final destination file to clean up.
    """
    if sys.platform != "darwin":
        return

    for name_xattr in _xattr_names(path_file):
        _xattr_remove(path_file, name_xattr)


def _xattr_names(path_file: pathlib.Path) -> list[str]:
    """List extended attribute names of a file via libc (macOS).

    Python's os.listxattr exists only on Linux, so macOS goes through ctypes.
    Best-effort: any failure returns an empty list.

    Args:
        path_file (pathlib.Path): The file to inspect.

    Returns:
        list[str]: Extended attribute names, empty on any failure.
    """
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        path_bytes: bytes = os.fsencode(str(path_file))
        size: int = libc.listxattr(path_bytes, None, 0, 0)
        if size <= 0:
            return []
        buffer = ctypes.create_string_buffer(size)
        size = libc.listxattr(path_bytes, buffer, size, 0)
        if size <= 0:
            return []
        return [name.decode("utf-8", errors="replace") for name in buffer.raw[:size].split(b"\x00") if name]
    except Exception:
        return []


def _xattr_remove(path_file: pathlib.Path, name_xattr: str) -> None:
    """Remove one extended attribute via libc (macOS), best-effort.

    Args:
        path_file (pathlib.Path): The file to modify.
        name_xattr (str): The attribute name to remove.
    """
    with contextlib.suppress(Exception):
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.removexattr(os.fsencode(str(path_file)), name_xattr.encode("utf-8"), 0)


def check_file_exists(path_file: pathlib.Path, extension_ignore: bool = False) -> bool:
    """Check if a file exists.

    Args:
        path_file (pathlib.Path): The file path to check.
        extension_ignore (bool, optional): Whether to ignore the file extension. Defaults to False.

    Returns:
        bool: True if the file exists, False otherwise.
    """
    if extension_ignore:
        path_file_stem: str = pathlib.Path(path_file).stem
        path_parent: pathlib.Path = pathlib.Path(path_file).parent
        path_files: list[str] = []

        path_files.extend(str(path_parent.joinpath(path_file_stem + extension)) for extension in AudioExtensions)
    else:
        path_files: list[str] = [str(path_file)]

    def _nonempty(candidate: str) -> bool:
        # A zero-byte file under a final name is a truncation artifact (a
        # crash between create and write), never a finished download; treating
        # it as existing would trust it forever and skip the re-download.
        try:
            return os.path.isfile(candidate) and os.path.getsize(candidate) > 0
        except OSError:
            return False

    return any(_nonempty(_file) for _file in path_files)


def resource_path(relative_path: str) -> str:
    """Get the absolute path to a resource.

    Args:
        relative_path: The relative path to the resource.

    Returns:
        str: The absolute path to the resource.
    """
    # PyInstaller creates a temp folder and stores path in _MEIPASS
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))

    return os.path.join(base_path, relative_path)


def url_to_filename(url: str) -> str:
    """Convert a URL to a valid filename.

    Args:
        url (str): The URL to convert.

    Returns:
        str: The corresponding filename.

    Raises:
        ValueError: If the URL contains invalid characters for a filename.
    """
    urlpath: str = urlsplit(url).path
    basename: str = posixpath.basename(unquote(urlpath))

    if os.path.basename(basename) != basename or unquote(posixpath.basename(urlpath)) != basename:
        raise ValueError  # reject '%2f' or 'dir%5Cbasename.ext' on Windows

    return basename
