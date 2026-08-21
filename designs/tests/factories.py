"""Shared test helpers."""

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from designs.models import Category, Season, Status


def make_image(colour=(40, 70, 140), size=(800, 1000), fmt="JPEG", name="photo.jpg"):
    """A real image file, because the upload pipeline validates the bytes."""
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def make_user(username="aarti", **kwargs):
    return get_user_model().objects.create_user(username=username, password="test-pass-1234", **kwargs)


def reference_data():
    season, _ = Season.objects.get_or_create(code="SS26", defaults={"label": "Spring/Summer 26"})
    category, _ = Category.objects.get_or_create(code="KURTA", defaults={"label": "Kurta"})
    return season, category


def status(code):
    return Status.objects.get(code=code)
