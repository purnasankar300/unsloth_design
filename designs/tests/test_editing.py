"""The UI pass: chrome, filters, optional fields, the activity feed, editing.

These cover the behaviour the drawer promises rather than its markup, except
where a class name is the contract between a view and the JavaScript.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from designs import services
from designs.models import Design, DesignSpec, Season, SpecField

from .factories import make_image, make_user, reference_data

MEMORY_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class LoginChromeTests(TestCase):
    def test_the_login_page_carries_no_application_chrome(self):
        """Signed out, there is nothing to search and nothing to open."""
        html = self.client.get(reverse("login")).content.decode()

        for fragment in ['class="topbar"', 'name="q"', 'id="drawer"', 'id="modal"', 'id="scrim"']:
            self.assertNotIn(fragment, html)
        self.assertIn('name="password"', html)


@override_settings(STORAGES=MEMORY_STORAGE)
class CreateTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.season, self.category = reference_data()
        self.client.force_login(self.user)

    def _post(self, **extra):
        payload = {
            "season": self.season.pk,
            "category": self.category.pk,
            "reference_image": make_image(),
            "requirement": "",
            "name": "",
        }
        payload.update(extra)
        return self.client.post(reverse("design-create"), payload)

    def test_a_design_needs_only_a_drop_a_category_and_a_photo(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Design.objects.count(), 1)

    def test_a_blank_name_falls_back_to_the_code(self):
        self._post()
        design = Design.objects.get()
        self.assertEqual(design.name, design.code)

    def test_the_form_offers_every_active_spec_field(self):
        html = self.client.get(reverse("design-create")).content.decode()
        for field in SpecField.objects.filter(is_active=True):
            self.assertIn(f'name="spec_{field.code}"', html)

    def test_specs_chosen_at_creation_are_recorded(self):
        field = SpecField.objects.filter(is_active=True).first()
        option = field.options.filter(is_active=True).first()

        self._post(**{f"spec_{field.code}": option.pk})

        spec = DesignSpec.objects.get()
        self.assertEqual(spec.option, option)
        # Attribution lives in the history row, as it does for every spec write.
        self.assertEqual(spec.history.first().history_user, self.user)

    def test_specs_are_optional(self):
        self._post()
        self.assertFalse(DesignSpec.objects.exists())


@override_settings(STORAGES=MEMORY_STORAGE)
class ActivityTests(TestCase):
    def setUp(self):
        self.creator = make_user()
        self.other = make_user("devika")
        season, category = reference_data()
        self.design = services.create_design(
            name="Block-print Kurta", season=season, category=category, actor=self.creator,
            reference_upload=make_image(), requirement="From Jaipur.",
        )
        self.reference = self.design.reference_version
        self.v1 = services.add_version(
            design=self.design, parent=self.reference, upload=make_image(),
            requirement="Flat embroidery.", actor=self.other,
        )
        services.add_comment(version=self.reference, author=self.other, body="Neckline too wide")
        services.add_comment(version=self.v1, author=self.creator, body="Much better")
        self.client.force_login(self.creator)

    def test_the_feed_holds_every_kind_of_event(self):
        kinds = {event["kind"] for event in services.activity(self.design)}
        self.assertEqual(kinds, {"move", "upload", "comment"})

    def test_the_feed_is_oldest_first(self):
        stamps = [event["when"] for event in services.activity(self.design)]
        self.assertEqual(stamps, sorted(stamps))

    def test_the_feed_costs_three_queries_however_many_versions(self):
        for _ in range(3):
            version = services.add_version(
                design=self.design, parent=self.reference, upload=make_image(),
                requirement="", actor=self.other,
            )
            services.add_comment(version=version, author=self.other, body="…")

        with self.assertNumQueries(3):
            for event in services.activity(self.design):
                # Touch what the template touches: nothing here may fan out.
                str(event["actor"])
                if event["kind"] == "comment":
                    str(event["obj"].version.display_label)
                if event["kind"] == "move":
                    str(event["obj"].to_status.label)

    def test_the_drawer_shows_comments_from_every_version_not_just_the_selected_one(self):
        html = self.client.get(self.design.get_absolute_url()).content.decode()
        self.assertIn("Neckline too wide", html)
        self.assertIn("Much better", html)

    def test_self_approval_stays_visible_in_the_feed(self):
        # Statuses are data, so walk the legal moves rather than naming one.
        target = services.allowed_targets(self.design.status)
        while target:
            step = next((s for s in target if s.is_approval), target[0])
            services.change_status(
                design=self.design, to_status=step, actor=self.creator,
                version=self.design.reference_version,
            )
            self.design.refresh_from_db()
            if step.is_approval:
                break
            target = services.allowed_targets(self.design.status)

        html = self.client.get(self.design.get_absolute_url()).content.decode()
        self.assertIn("selfflag", html)


@override_settings(STORAGES=MEMORY_STORAGE)
class DrawerEditingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.season, self.category = reference_data()
        self.design = services.create_design(
            name="Block-print Kurta", season=self.season, category=self.category, actor=self.user,
            reference_upload=make_image(), requirement="From Jaipur.",
        )
        self.client.force_login(self.user)

    def test_the_header_editor_renders_for_htmx_and_as_a_page(self):
        url = f"{self.design.get_absolute_url()}?edit=head"

        partial = self.client.get(url, headers={"HX-Request": "true"}).content.decode()
        self.assertIn("headedit", partial)
        self.assertNotIn("<!doctype", partial.lower())

        page = self.client.get(url).content.decode()
        self.assertIn("headedit", page)
        self.assertIn("<!doctype", page.lower())

    def test_renaming_and_refiling_never_changes_the_code(self):
        other_season = Season.objects.create(code="AW26", label="Autumn/Winter 26")
        code = self.design.code

        self.client.post(
            reverse("design-update", args=[self.design.code]),
            {"name": "Indigo Kurta", "season": other_season.pk, "category": self.category.pk},
        )

        self.design.refresh_from_db()
        self.assertEqual(self.design.code, code)
        self.assertEqual(self.design.name, "Indigo Kurta")
        self.assertEqual(self.design.season, other_season)

    def test_a_blank_name_on_update_falls_back_to_the_code(self):
        self.client.post(
            reverse("design-update", args=[self.design.code]),
            {"name": "  ", "season": self.season.pk, "category": self.category.pk},
        )
        self.design.refresh_from_db()
        self.assertEqual(self.design.name, self.design.code)

    def test_an_update_is_attributed_in_the_history(self):
        self.client.post(
            reverse("design-update", args=[self.design.code]),
            {"name": "Indigo Kurta", "season": self.season.pk, "category": self.category.pk},
        )
        latest = self.design.history.first()
        self.assertEqual(latest.name, "Indigo Kurta")
        self.assertEqual(latest.history_user, self.user)

    def test_the_requirement_can_be_corrected_after_upload(self):
        version = self.design.reference_version
        self.client.post(
            reverse("requirement-set", args=[self.design.code, version.number]),
            {"requirement": "From a Jaipur market stall."},
        )

        version.refresh_from_db()
        self.assertEqual(version.requirement, "From a Jaipur market stall.")
        self.assertEqual(version.history.count(), 2)
        self.assertEqual(version.history.first().history_user, self.user)

    def test_correcting_the_requirement_leaves_the_image_alone(self):
        version = self.design.reference_version
        key, number = version.image_key, version.number

        self.client.post(
            reverse("requirement-set", args=[self.design.code, version.number]),
            {"requirement": "Reworded."},
        )

        version.refresh_from_db()
        self.assertEqual((version.image_key, version.number), (key, number))

    def test_the_assets_editor_renders_as_a_modal_for_htmx_and_a_page_otherwise(self):
        url = reverse("assets-edit", args=[self.design.code])

        partial = self.client.get(url, headers={"HX-Request": "true"})
        self.assertIn("modal-inner", partial.content.decode())
        self.assertNotIn("<!doctype", partial.content.decode().lower())

        page = self.client.get(url).content.decode()
        self.assertIn("<!doctype", page.lower())

    def test_saving_assets_over_htmx_returns_the_drawer_and_closes_the_modal(self):
        response = self.client.post(
            reverse("assets-edit", args=[self.design.code]),
            {
                "colour_code": "19-4324 TCX", "colour_hex": "", "notes": "",
                "measurements-TOTAL_FORMS": "0", "measurements-INITIAL_FORMS": "0",
                "measurements-MIN_NUM_FORMS": "0", "measurements-MAX_NUM_FORMS": "1000",
            },
            headers={"HX-Request": "true"},
        )

        self.assertIn("drawer-inner", response.content.decode())
        self.assertIn("overlay-close", response["HX-Trigger"])
        self.design.refresh_from_db()
        self.assertEqual(self.design.colour_code, "19-4324 TCX")
