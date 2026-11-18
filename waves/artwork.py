"""Optional compatibility conversion of downloaded album artwork.

TIDAL's image CDN can return progressive JPEGs.  Some older players (including
Rockbox configurations) cannot decode these.  Do not change artwork unless the
user explicitly opts in; audio bytes are never touched.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError


def baseline_jpeg(data: bytes, max_size: int = 320) -> bytes | None:
    """Return an 8-bit RGB, non-progressive JPEG or None for invalid art.

    Image.load() fully decodes the input before any output is produced.
    preserve_aspect_ratio; never upscale a smaller source image.
    """
    if not data:
        return None
    try:
        with Image.open(BytesIO(data)) as source:
            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            with BytesIO() as output:
                image.save(output, format="JPEG", progressive=False, optimize=False, quality=90)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
