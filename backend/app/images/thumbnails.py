from __future__ import annotations

from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


THUMBNAIL_SIZE = (512, 512)
WEBP_QUALITY = 80


class InvalidImage(ValueError):
    """Raised when uploaded bytes cannot be safely decoded as an image."""


def write_thumbnail(content: bytes, target: Path) -> tuple[int, int]:
    """Validate image bytes and write a metadata-free, panel-safe WebP."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as source:
                source.load()
                image = ImageOps.exif_transpose(source)
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format="WEBP", quality=WEBP_QUALITY, method=6)
                size = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImage("The uploaded file is not a valid or safe image") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output.getvalue())
    return size
