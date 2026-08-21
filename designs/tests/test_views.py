"""Tests for access control and how images reach the browser."""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from designs import services

from .factories import make_image, make_user, reference_data

MEMORY_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=MEMORY_STORAGE)
class AccessTests(TestCase):
    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Block-print Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )

    def test_anonymous_users_are_sent_to_login(self):
        for url in [
            reverse("design-list"),
            self.design.get_absolute_url(),
            reverse("version-image", args=[self.design.reference_version.pk]),
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse("login"), response.url, url)

    def test_any_user_may_act(self):
        """There are no roles: a second user has the same powers as the creator."""
        stranger = make_user("nikhil")
        self.client.force_login(stranger)
        response = self.client.post(
            reverse("status-change", args=[self.design.code]), {"to_status": "in-review"}
        )
        self.assertEqual(response.status_code, 302)
        self.design.refresh_from_db()
        self.assertEqual(self.design.status.code, "in-review")


@override_settings(STORAGES=MEMORY_STORAGE)
class ImageDeliveryTests(TestCase):
    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )
        self.version = self.design.reference_version
        self.client.force_login(self.user)

    @patch("designs.views.storage.signed_url")
    def test_image_view_redirects_to_a_signed_url(self, signed_url):
        signed_url.return_value = "https://bucket.example/key?X-Amz-Signature=abc&X-Amz-Expires=600"
        response = self.client.get(reverse("version-image", args=[self.version.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("X-Amz-Signature", response.url)
        signed_url.assert_called_once_with(self.version.image_key)

    @patch("designs.views.storage.signed_url")
    def test_thumbnail_view_serves_the_thumbnail_key(self, signed_url):
        signed_url.return_value = "https://bucket.example/thumb"
        self.client.get(reverse("version-thumbnail", args=[self.version.pk]))
        signed_url.assert_called_once_with(self.version.thumbnail_key)

    def test_no_bucket_url_appears_in_rendered_html(self):
        """Templates must link to the application, never to storage."""
        for url in [reverse("design-list"), self.design.get_absolute_url()]:
            html = self.client.get(url).content.decode()
            self.assertNotIn("r2.cloudflarestorage.com", html)
            self.assertNotIn("X-Amz-Signature", html)
            self.assertNotIn(":9000", html)

    def test_gallery_requests_thumbnails_only(self):
        html = self.client.get(reverse("design-list")).content.decode()
        self.assertIn(reverse("version-thumbnail", args=[self.version.pk]), html)
        self.assertNotIn(f'src="{reverse("version-image", args=[self.version.pk])}"', html)


@override_settings(STORAGES=MEMORY_STORAGE)
class LoopTests(TestCase):
    """The core loop of section 5, driven through the views."""

    def setUp(self):
        self.creator = make_user("aarti")
        self.season, self.category = reference_data()
        self.client.force_login(self.creator)

    def test_create_upload_comment_rebranch_approve(self):
        response = self.client.post(
            reverse("design-create"),
            {
                "name": "Block-print Kurta",
                "season": self.season.pk,
                "category": self.category.pk,
                "reference_image": make_image(),
                "requirement": "Reference from Jaipur.",
            },
        )
        self.assertEqual(response.status_code, 302)

        from designs.models import Design

        design = Design.objects.get()
        reference = design.reference_version

        self.client.post(
            reverse("version-create", args=[design.code]),
            {"parent": reference.number, "image": make_image(colour=(200, 40, 40)), "requirement": "Mandarin collar."},
        )
        self.assertEqual(design.versions.count(), 2)

        version_two = design.versions.get(number=2)
        self.client.post(
            reverse("comment-create", args=[design.code, version_two.number]),
            {"body": "Collar works."},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(version_two.comments.count(), 1)

        # Re-branch from the reference rather than continuing the chain.
        self.client.post(
            reverse("version-create", args=[design.code]),
            {"parent": reference.number, "image": make_image(colour=(20, 120, 90)), "requirement": "Fresh start."},
        )
        version_three = design.versions.get(number=3)
        self.assertEqual(version_three.parent, reference)
        self.assertEqual(version_three.depth_from_reference, 1)

        self.client.post(reverse("status-change", args=[design.code]), {"to_status": "in-review"})
        self.client.post(
            reverse("status-change", args=[design.code]),
            {"to_status": "approved", "version": version_three.number, "comment": "This is the one."},
        )

        design.refresh_from_db()
        self.assertEqual(design.status.code, "approved")
        self.assertTrue(design.approved_version.pk == version_three.pk)
        self.assertTrue(design.transitions.filter(is_self_approval=True).exists())

    def test_duplicate_upload_warns_but_saves(self):
        self.client.post(
            reverse("design-create"),
            {
                "name": "Kurta", "season": self.season.pk, "category": self.category.pk,
                "reference_image": make_image(), "requirement": "",
            },
        )
        from designs.models import Design

        design = Design.objects.get()
        response = self.client.post(
            reverse("version-create", args=[design.code]),
            {"parent": design.reference_version.number, "image": make_image(), "requirement": "same file"},
            follow=True,
        )
        self.assertEqual(design.versions.count(), 2)
        self.assertTrue(
            any("identical" in str(m).lower() for m in response.context["messages"]),
            "expected a duplicate-upload warning",
        )


@override_settings(STORAGES=MEMORY_STORAGE)
class BoardTests(TestCase):
    """The board, the drawer, and the labels the team actually reads."""

    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Block-print Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="From Jaipur.",
        )
        self.client.force_login(self.user)

    def test_status_filter_comes_from_the_status_table(self):
        """The dropdown is whatever the table holds — code and label both."""
        from designs.models import Status

        html = self.client.get(reverse("design-list")).content.decode()
        for status in Status.objects.filter(is_active=True):
            self.assertIn(f'value="{status.code}"', html)
            self.assertIn(status.label, html)

    def test_category_filter_comes_from_the_category_table(self):
        from designs.models import Category

        html = self.client.get(reverse("design-list")).content.decode()
        for category in Category.objects.filter(is_active=True):
            self.assertIn(f'value="{category.code}"', html)
            self.assertIn(category.label, html)

    def test_the_three_filters_compose(self):
        response = self.client.get(
            reverse("design-list"),
            {"status": self.design.status.code, "category": self.design.category.code, "q": "Block-print"},
        )
        self.assertContains(response, self.design.code)

        response = self.client.get(
            reverse("design-list"),
            {"status": self.design.status.code, "category": self.design.category.code, "q": "nothing-matches-this"},
        )
        self.assertNotContains(response, self.design.code)

    def test_reference_is_labelled_ref_not_v1(self):
        reference = self.design.reference_version
        self.assertEqual(reference.display_label, "REF")
        html = self.client.get(self.design.get_absolute_url()).content.decode()
        self.assertIn("REF", html)

    def test_later_versions_count_from_one(self):
        second = services.add_version(
            design=self.design, parent=self.design.reference_version,
            upload=make_image(colour=(9, 9, 9)), requirement="Collar.", actor=self.user,
        )
        self.assertEqual(second.number, 2)
        self.assertEqual(second.display_label, "v1")

    def test_htmx_gets_the_drawer_partial_and_a_browser_gets_the_page(self):
        partial = self.client.get(self.design.get_absolute_url(), headers={"HX-Request": "true"})
        self.assertNotIn(b"<!doctype html>", partial.content.lower())
        self.assertIn(b"drawer-inner", partial.content)

        page = self.client.get(self.design.get_absolute_url())
        self.assertIn(b"<!doctype html>", page.content.lower())
        self.assertIn(b"drawer-inner", page.content)

    def test_board_search_matches_a_specification_value(self):
        from designs.models import SpecField

        field = SpecField.objects.get(code="fabric")
        option = field.options.get(label="Slub Cotton")
        services.set_spec(design=self.design, field=field, option=option, actor=self.user)

        found = self.client.get(reverse("design-list"), {"q": "slub"}).context["cards"]
        self.assertEqual([card["design"].pk for card in found], [self.design.pk])

        missing = self.client.get(reverse("design-list"), {"q": "herringbone"}).context["cards"]
        self.assertEqual(missing, [])

    def test_setting_a_spec_from_the_drawer_persists_it(self):
        from designs.models import DesignSpec, SpecField

        field = SpecField.objects.get(code="gsm")
        option = field.options.get(label="180")
        response = self.client.post(
            reverse("spec-set", args=[self.design.code]),
            {"field": field.code, "option": option.pk},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("toast", response.headers.get("HX-Trigger", ""))
        self.assertEqual(DesignSpec.objects.get(design=self.design, field=field).option, option)

    def test_option_lists_and_guidance_render_without_htmx(self):
        for url in [reverse("spec-options"), reverse("guidance-modal")]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"<!doctype html>", response.content.lower())
