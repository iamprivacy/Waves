"""Real JPEG decode/encode regression checks for opt-in Rockbox artwork."""
from io import BytesIO

from PIL import Image

from waves.artwork import baseline_jpeg
from waves.model.cfg import Settings


def _jpeg(*, progressive: bool) -> bytes:
    image = Image.new("RGB", (640, 640), (40, 120, 180))
    out = BytesIO()
    image.save(out, "JPEG", progressive=progressive)
    return out.getvalue()


def test_progressive_jpeg_is_baseline_and_bounded():
    result = baseline_jpeg(_jpeg(progressive=True))
    assert result
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (320, 320)
        assert image.mode == "RGB"
        assert not image.info.get("progressive")
        assert not image.info.get("progression")


def test_transparent_png_is_flattened_to_baseline_jpeg():
    original = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    out = BytesIO()
    original.save(out, format="PNG")
    result = baseline_jpeg(out.getvalue())
    assert result
    with Image.open(BytesIO(result)) as image:
        assert image.size == (100, 50)
        assert image.mode == "RGB"
        assert not image.info.get("progressive")
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_bad_data_does_not_become_mislabeled_jpeg():
    assert baseline_jpeg(b"not a JPEG") is None
    assert baseline_jpeg(b"") is None


def test_compatibility_mode_is_opt_in():
    assert Settings().metadata_cover_rockbox_compatible is False
