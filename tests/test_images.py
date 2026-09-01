from io import BytesIO

import pytest
from PIL import Image

from app.images.thumbnails import InvalidImage, write_thumbnail


def image_bytes(format: str, size: tuple[int, int], mode: str = "RGB") -> bytes:
    output = BytesIO()
    color = (30, 120, 160, 180) if mode == "RGBA" else (30, 120, 160)
    Image.new(mode, size, color).save(output, format=format)
    return output.getvalue()


def test_upload_is_resized_and_converted_to_webp(tmp_path):
    target = tmp_path / "product.webp"
    dimensions = write_thumbnail(image_bytes("JPEG", (2400, 1200)), target)

    assert dimensions == (512, 256)
    with Image.open(target) as result:
        assert result.format == "WEBP"
        assert result.size == (512, 256)


def test_thumbnail_preserves_transparency_and_aspect_ratio(tmp_path):
    target = tmp_path / "transparent.webp"
    dimensions = write_thumbnail(image_bytes("PNG", (200, 800), "RGBA"), target)

    assert dimensions == (128, 512)
    with Image.open(target) as result:
        assert result.mode == "RGBA"


def test_invalid_image_is_rejected_without_writing_file(tmp_path):
    target = tmp_path / "invalid.webp"

    with pytest.raises(InvalidImage):
        write_thumbnail(b"not an image", target)

    assert not target.exists()
