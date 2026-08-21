"""Object storage keys and signed URLs.

Keys are opaque and immutable: a UUID path, never a design name and never the
filename the user happened to upload. Names change and leak information; keys
must not.

The bucket is private. Nothing in the application ever emits a bucket URL — the
only way to see an image is a short-lived signed URL generated on demand, so
access to an unreleased design is revocable.
"""

from django.conf import settings
from django.core.files.storage import default_storage

IMAGE_KEY = "designs/{design_id}/versions/{version_id}{ext}"
THUMBNAIL_KEY = "designs/{design_id}/versions/{version_id}-thumb.jpg"


def image_key(design_id, version_id, ext):
    return IMAGE_KEY.format(design_id=design_id, version_id=version_id, ext=ext)


def thumbnail_key(design_id, version_id):
    return THUMBNAIL_KEY.format(design_id=design_id, version_id=version_id)


def signed_url(key, expire=None):
    """A time-limited URL for one object.

    ``django-storages`` signs by default because ``querystring_auth`` is on;
    the expiry is passed explicitly so the caller can shorten it.
    """
    return default_storage.url(key, expire=expire or settings.SIGNED_URL_EXPIRY)


def save_bytes(key, data, content_type):
    """Write one object. Never overwrites: keys are unique per version."""
    from django.core.files.base import ContentFile

    file = ContentFile(data)
    file.content_type = content_type
    return default_storage.save(key, file)
