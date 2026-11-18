# Rockbox-compatible album artwork

Waves normally saves exactly the cover image bytes received from TIDAL's image CDN. Some images are progressive JPEGs. They are valid JPEG files, but Rockbox and other legacy/low-memory players may be unable to decode them.

## Reproduction

A user-provided `cover.jpg` saved by Waves produced these ImageMagick fields:

```text
Geometry: 320x320+0+0
Colorspace: sRGB
Interlace: JPEG
Filesize: 22616B
```

In ImageMagick, `Interlace: JPEG` indicates progressive JPEG. The player failed to display artwork when preferring either folder art or embedded FLAC artwork.

## Using the opt-in fix

In **Settings → Tags & Artwork**, turn on **Rockbox-compatible artwork** before downloading new music. Waves decodes the cover, flattens transparency, makes an 8-bit RGB image no larger than 320×320, and writes a baseline (non-progressive) JPEG.

The conversion applies both to the embedded FLAC image and `cover.jpg`. It is off by default and does not modify audio data.

Only new downloads are affected: existing `cover.jpg` files and FLAC tags in the library are not rewritten. To repair existing external cover art, use ImageMagick separately on the files you explicitly select.

If artwork is corrupt or unsupported, compatibility mode skips that artwork rather than incorrectly writing its bytes as `image/jpeg`. Normal downloads continue as usual when the mode is disabled.
