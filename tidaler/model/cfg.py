from dataclasses import dataclass

from dataclasses_json import dataclass_json
from tidalapi import Quality

from tidaler.constants import CoverDimensions, DownsampleTarget, InitialKey, MetadataTargetUPC, QualityVideo


@dataclass_json
@dataclass
class Settings:
    skip_existing: bool = True
    lyrics_embed: bool = False
    lyrics_file: bool = False
    use_primary_album_artist: bool = (
        False  # When True, uses first album artist instead of track artists for folder paths
    )
    # TODO: Implement API KEY selection.
    # api_key_index: bool = 0
    # TODO: Implement album info download to separate file.
    # album_info_save: bool = False
    video_download: bool = True
    # TODO: Implement multi threading for downloads.
    # multi_thread: bool = False
    download_delay: bool = True
    # No default download folder: the user must choose one explicitly. A fresh
    # install starts blank and the first download is gated until a folder is set
    # (so nobody silently downloads into a folder they can't find). Existing
    # installs keep whatever they persisted, including the old "~/download".
    download_base_path: str = ""
    # One-time flag for the soft "you're still on the old default folder" nudge
    # shown to existing users who never changed "~/download". Set once the nudge
    # is shown or dismissed so it never nags again.
    download_folder_prompted: bool = False
    quality_audio: Quality = Quality.low_320k
    quality_video: QualityVideo = QualityVideo.P480
    download_dolby_atmos: bool = False
    # Artist > Album > Track, the shape a music library (and Plex) expects.
    # Playlists / mixes keep their own parent folder: they are platform
    # constructs a library manager can't model, but stay downloadable.
    format_album: str = (
        "{artist_name}/[{album_year}] {album_title}{album_explicit}/{track_volume_num_optional}"
        "{album_track_num}. {artist_name} - {track_title}{track_explicit}"
    )
    format_playlist: str = "Playlists/{playlist_name}/{list_pos}. {artist_name} - {track_title}"
    format_mix: str = "Mix/{mix_name}/{artist_name} - {track_title}"
    format_track: str = (
        "{artist_name}/[{album_year}] {album_title}{album_explicit}/{track_volume_num_optional}"
        "{album_track_num}. {artist_name} - {track_title}{track_explicit}"
    )
    format_video: str = "Videos/{artist_name} - {track_title}{track_explicit}"
    video_convert_mp4: bool = True
    path_binary_ffmpeg: str = ""
    # Read-only diagnostic, written by the app, never edited in the Settings UI:
    # which ffmpeg a download would actually use, as a CATEGORY only (never a
    # path). "custom" (user override), "managed" (bundled copy), "system" (found
    # on PATH) or "none". Lets a pasted config reveal the ffmpeg situation, since
    # path_binary_ffmpeg stays "" for both the managed and the absent cases.
    ffmpeg_source: str = "unknown"
    metadata_cover_dimension: CoverDimensions = CoverDimensions.Px320
    # Size of the separately-saved cover.jpg. The sentinel "follow" means "match
    # the embedded cover size above" (the historical behaviour); any other value
    # is a CoverDimensions member name (e.g. "Px640", "PxORIGIN") applied only to
    # the saved file, so the embedded art and the on-disk cover can differ.
    metadata_cover_file_dimension: str = "follow"
    metadata_cover_embed: bool = True
    mark_explicit: bool = False
    cover_album_file: bool = True
    # Also write cover.jpg when a single track is downloaded on its own (not just
    # as part of a full album). Off by default: the historical behaviour only
    # saved cover.jpg for album/collection downloads.
    cover_single_track_file: bool = False
    extract_flac: bool = True
    downsample_enabled: bool = False
    downsample_target: DownsampleTarget = DownsampleTarget.BIT16_48
    # Values above the shared HTTP pool size (10 connections) are clamped at
    # download time: extra workers can never hold a socket, they only cost
    # threads and memory.
    downloads_simultaneous_per_track_max: int = 10
    download_delay_sec_min: float = 3.0
    download_delay_sec_max: float = 5.0
    album_track_num_pad_min: int = 1
    downloads_concurrent_max: int = 3
    symlink_to_track: bool = False
    playlist_create: bool = False
    metadata_replay_gain: bool = False
    metadata_write_url: bool = True
    window_x: int = 50
    window_y: int = 50
    window_w: int = 1200
    window_h: int = 800
    filename_delimiter_artist: str = ", "
    filename_delimiter_album_artist: str = ", "
    metadata_target_upc: MetadataTargetUPC = MetadataTargetUPC.UPC
    # Rate limiting for API calls (tweaking variables)
    api_rate_limit_batch_size: int = 20  # Number of albums to process before applying rate limit delay
    api_rate_limit_delay_sec: float = 3.0  # Delay in seconds between batches to avoid rate limiting
    initial_key_format: InitialKey = InitialKey.ALPHANUMERIC


@dataclass_json
@dataclass
class HelpSettings:
    skip_existing: str = "Skip download if file already exists."
    album_cover_save: str = "Save cover to album folder."
    lyrics_embed: str = "Embed lyrics in audio file, if lyrics are available."
    use_primary_album_artist: str = "Use only the primary album artist for folder paths instead of track artists."
    lyrics_file: str = "Save lyrics to separate *.lrc file, if lyrics are available."
    api_key_index: str = "Set the device API KEY."
    album_info_save: str = "Save album info to track?"
    video_download: str = "Allow download of videos."
    multi_thread: str = "Download several tracks in parallel."
    download_delay: str = "Activate randomized download delay to mimic human behaviour."
    download_base_path: str = "Where to store the downloaded media."
    quality_audio: str = (
        'Desired audio download quality: "LOW" (96kbps), "HIGH" (320kbps), '
        '"LOSSLESS" (16 Bit, 44,1 kHz), '
        '"HI_RES_LOSSLESS" (up to 24 Bit, 192 kHz)'
    )
    quality_video: str = 'Desired video download quality: "360", "480", "720", "1080"'
    download_dolby_atmos: str = "Download Dolby Atmos audio streams if available."
    # TODO: Describe possible variables.
    format_album: str = "Where to download albums and how to name the items."
    format_playlist: str = "Where to download playlists and how to name the items."
    format_mix: str = "Where to download mixes and how to name the items."
    format_track: str = "Where to download tracks and how to name the items."
    format_video: str = "Where to download videos and how to name the items."
    video_convert_mp4: str = (
        "Videos are downloaded as MPEG Transport Stream (TS) files. With this option each video "
        "will be converted to MP4. FFmpeg must be installed."
    )
    path_binary_ffmpeg: str = (
        "Path to FFmpeg binary file (executable). Only necessary if FFmpeg is not set in $PATH. Mandatory for Windows: "
        "The directory of `ffmpeg.exe` must be set in %PATH%."
    )
    metadata_cover_dimension: str = (
        "The square dimensions of the cover image embedded into the track. Possible values: 80, 160, 320, 640, 1280, origin."
    )
    metadata_cover_file_dimension: str = (
        "Size of the saved 'cover.jpg'. 'Same as embedded' matches the embedded cover size; "
        "otherwise pick an independent size (80, 160, 320, 640, 1280, origin)."
    )
    metadata_cover_embed: str = "Embed album cover into file."
    mark_explicit: str = "Mark explicit tracks with '🅴' in track title (only applies to metadata)."
    cover_album_file: str = "Save cover to 'cover.jpg', if an album is downloaded."
    cover_single_track_file: str = "Also save cover.jpg when downloading a single track on its own."
    extract_flac: str = "Extract FLAC audio tracks from MP4 containers and save them as `*.flac` (uses FFmpeg)."
    downsample_enabled: str = (
        "Downsample FLAC files toward a fixed target rate/bit-depth using ffmpeg. "
        "Each dimension is reduced independently and never upsampled, a 24-bit/44.1 kHz "
        "source with a 16/48 target becomes 16-bit/44.1 kHz; a 16-bit/44.1 kHz source is "
        "left untouched. Useful for capping HI_RES_LOSSLESS downloads at a saner archive size."
    )
    downsample_target: str = (
        "Downsample target when downsample_enabled is true: " "'16_48' (16 bit / 48 kHz) or '24_48' (24 bit / 48 kHz)."
    )
    downloads_simultaneous_per_track_max: str = (
        "Maximum number of simultaneous chunk downloads per track (capped at 10, the connection pool size)."
    )
    download_delay_sec_min: str = "Lower boundary for the calculation of the download delay in seconds."
    download_delay_sec_max: str = "Upper boundary for the calculation of the download delay in seconds."
    album_track_num_pad_min: str = (
        "Minimum length of the album track count, will be padded with zeroes (0). To disable padding set this to 1."
    )
    downloads_concurrent_max: str = "Maximum concurrent number of downloads (threads)."
    symlink_to_track: str = (
        "If enabled the tracks of albums, playlists and mixes will be downloaded to the track directory but symlinked "
        "accordingly."
    )
    playlist_create: str = "Creates a '_playlist.m3u8' file for downloaded albums, playlists and mixes."
    metadata_replay_gain: str = "Replay gain information will be written to metadata."
    metadata_write_url: str = "URL of the media file will be written to metadata."
    window_x: str = "X-Coordinate of saved window location."
    window_y: str = "Y-Coordinate of saved window location."
    window_w: str = "Width of saved window size."
    window_h: str = "Height of saved window size."
    filename_delimiter_artist: str = "Filename delimiter for multiple artists. Default: ', '"
    filename_delimiter_album_artist: str = "Filename delimiter for multiple album artists. Default: ', '"
    metadata_target_upc: str = (
        "Select the target metadata tag ('UPC', 'BARCODE', 'EAN') where to write the UPC information to. Default: 'UPC'."
    )
    api_rate_limit_batch_size: str = "Number of albums to process before applying rate limit delay (tweaking variable)."
    api_rate_limit_delay_sec: str = "Delay in seconds between batches to avoid API rate limiting (tweaking variable)."
    initial_key_format: str = "Format for Initial Key metadata tag: 'alphanumeric' (default) or 'classic'."


@dataclass_json
@dataclass
class Token:
    token_type: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expiry_time: float = 0.0
