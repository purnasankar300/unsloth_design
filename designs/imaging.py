"""The upload pipeline.

Every image entering the system goes through here, the original reference
included, so that hashing, sizing and thumbnailing are guaranteed rather than
remembered.
"""

import hashlib
import io

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

from . import storage

THUMBNAIL_SIZE = (400, 500)
THUMBNAIL_QUALITY = 82
CHUNK = 64 * 1024

# Pillow format -> file extension. Anything not listed is rejected: the app
# stores photographs, not arbitrary uploads.
ALLOWED_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "HEIF": ".heic",
    "TIFF": ".tif",
}


class UploadRejected(ValidationError):
    """The uploaded file is not an image this application will store."""


def inspect(upload):
    """Validate the upload and return ``(raw_bytes, format, width, height, sha256)``.

    Reads the file once into memory — uploads are single garment photographs
    capped by ``MAX_UPLOAD_BYTES``, not arbitrary streams.
    """
    if upload.size > settings.MAX_UPLOAD_BYTES:
        limit_mb = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise UploadRejected(f"That image is larger than {limit_mb} MB. Export it smaller and try again.")

    digest = hashlib.sha256()
    buffer = io.BytesIO()
    for chunk in upload.chunks(CHUNK):
        digest.update(chunk)
        buffer.write(chunk)
    raw = buffer.getvalue()

    if not raw:
        raise UploadRejected("That file is empty.")

    # verify() consumes the file object, so open twice: once to confirm the
    # bytes really are an image, once to read its dimensions.
    try:
        Image.open(io.BytesIO(raw)).verify()
        with Image.open(io.BytesIO(raw)) as image:
            image_format = image.format
            width, height = image.size
    except (UnidentifiedImageError, OSError):
        raise UploadRejected("That file is not an image, or the image is damaged.")

    if image_format not in ALLOWED_FORMATS:
        supported = ", ".join(sorted(ALLOWED_FORMATS))
        raise UploadRejected(f"{image_format or 'That format'} is not supported. Use one of: {supported}.")

    return raw, image_format, width, height, digest.hexdigest()


def build_thumbnail(raw):
    """A small JPEG for gallery and tree views.

    Lists never serve full-size images: it is both a performance and an
    egress-cost requirement.
    """
    with Image.open(io.BytesIO(raw)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        image.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True, progressive=True)
    return out.getvalue()


def store(design_id, version_id, upload):
    """Validate, hash, thumbnail and upload one image.

    Returns the metadata the ``Version`` row needs. Storage keys are derived
    from UUIDs only — the uploaded filename is deliberately discarded.
    """
    raw, image_format, width, height, content_hash = inspect(upload)
    thumbnail = build_thumbnail(raw)

    key = storage.image_key(design_id, version_id, ALLOWED_FORMATS[image_format])
    thumb_key = storage.thumbnail_key(design_id, version_id)

    storage.save_bytes(key, raw, f"image/{image_format.lower()}")
    storage.save_bytes(thumb_key, thumbnail, "image/jpeg")

    return {
        "image_key": key,
        "thumbnail_key": thumb_key,
        "width": width,
        "height": height,
        "file_size": len(raw),
        "content_hash": content_hash,
    }
