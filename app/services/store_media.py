"""Store welcome image: validated, resized and saved outside public static assets.

Only the authenticated owner can replace it; Telegram and the Mini App read a
fixed public URL. Files live on the persistent ./data bind mount.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
from tempfile import NamedTemporaryFile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "branding"
WELCOME_IMAGE = DATA_DIR / "welcome.jpg"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000


def image_path(kind: str) -> Path | None:
    path = WELCOME_IMAGE if kind == "welcome" else DATA_DIR / "logo.jpg"
    return path if path.is_file() and not path.is_symlink() else None


def welcome_image_path() -> Path | None:
    return image_path("welcome")


def logo_image_url() -> str | None:
    path = image_path("logo")
    return f"/api/v1/store-profile/logo-image?v={path.stat().st_mtime_ns}" if path else None


def welcome_image_url() -> str | None:
    path = welcome_image_path()
    return f"/api/v1/store-profile/welcome-image?v={path.stat().st_mtime_ns}" if path else None


def save_welcome_image(data_base64: str) -> str:
    _save_image(data_base64, WELCOME_IMAGE, (200, 120), (1600, 1200))
    return welcome_image_url() or "/api/v1/store-profile/welcome-image"


def save_logo_image(data_base64: str) -> str:
    _save_image(data_base64, DATA_DIR / "logo.jpg", (64, 64), (512, 512))
    return logo_image_url() or "/api/v1/store-profile/logo-image"


def _save_image(data_base64: str, destination: Path, minimum: tuple[int, int], maximum: tuple[int, int]) -> None:
    if not isinstance(data_base64, str):
        raise ValueError("Неверный формат изображения")
    raw = data_base64.partition(",")[2] if data_base64.startswith("data:image/") else data_base64
    if len(raw) > (MAX_UPLOAD_BYTES * 4 // 3 + 8):
        raise ValueError("Размер фотографии не должен превышать 5 МБ")
    try:
        payload = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Невозможно прочитать изображение") from exc
    if not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("Размер фотографии не должен превышать 5 МБ")
    try:
        with Image.open(io.BytesIO(payload)) as original:
            if original.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError("Поддерживаются JPG, PNG и WebP")
            if original.width < minimum[0] or original.height < minimum[1] or original.width * original.height > MAX_PIXELS:
                raise ValueError(f"Минимальный размер — {minimum[0]}×{minimum[1]}, максимум — 16 мегапикселей")
            image = ImageOps.exif_transpose(original)
            image.thumbnail(maximum, Image.Resampling.LANCZOS)
            if image.mode != "RGB":
                canvas = Image.new("RGB", image.size, (18, 18, 18))
                if "A" in image.getbands():
                    canvas.paste(image, mask=image.getchannel("A"))
                else:
                    canvas.paste(image.convert("RGB"))
                image = canvas
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=86, optimize=True, progressive=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Повреждённое или неподдерживаемое изображение") from exc
    if DATA_DIR.is_symlink():
        raise ValueError("Некорректное расположение изображения")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=DATA_DIR, prefix=".branding-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(output.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def delete_welcome_image() -> None:
    if welcome_image_path():
        WELCOME_IMAGE.unlink()


def delete_logo_image() -> None:
    if path := image_path("logo"):
        path.unlink(missing_ok=True)
