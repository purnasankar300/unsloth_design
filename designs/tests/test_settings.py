"""The settings screens — everything django-admin used to do.

There is no admin any more, so these cover the invariants it used to give for
free: one initial status, retiring instead of deleting, and never locking the
last person out.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from designs import services
from designs.models import (
    AllowedTransition, Category, GuidanceCard, Season, SpecField, SpecOption, Status,
)

from .factories import make_image, make_user, reference_data

MEMORY_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

SECTIONS = ["drops", "categories", "spec-fields", "guidance", "workflow", "team"]


class AdminIsGoneTests(TestCase):
    def test_the_admin_app_is_not_installed(self):
        from django.conf import settings

        self.assertNotIn("django.contrib.admin", settings.INSTALLED_APPS)

    def test_the_admin_url_does_not_exist(self):
        make_user()
        self.client.force_login(get_user_model().objects.get())
        self.assertEqual(self.client.get("/admin/").status_code, 404)


class SettingsRenderTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_every_section_renders_as_a_modal_and_as_a_page(self):
        for section in SECTIONS:
            url = reverse("settings-section", args=[section])

            partial = self.client.get(url, headers={"HX-Request": "true"}).content.decode()
            self.assertIn("modal-inner", partial, section)
            self.assertNotIn("<!doctype", partial.lower(), section)

            page = self.client.get(url).content.decode()
            self.assertIn("<!doctype", page.lower(), section)

    def test_an_unknown_section_is_a_404(self):
        self.assertEqual(self.client.get("/settings/nonsense/").status_code, 404)

    def test_settings_are_open_to_any_signed_in_user(self):
        """No roles, spec §3 — a plain account reaches every screen."""
        self.client.force_login(make_user("nikhil"))
        for section in SECTIONS:
            response = self.client.get(reverse("settings-section", args=[section]))
            self.assertEqual(response.status_code, 200, section)


class ReferenceDataTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_a_drop_and_a_category_make_the_create_form_usable(self):
        warning = "Add at least one drop and one category"
        self.assertIn(warning, self.client.get(reverse("design-create")).content.decode())

        self.client.post(reverse("drop-add"), {"code": "SS26", "label": "Spring/Summer 26"})
        self.client.post(reverse("category-add"), {"code": "KURTA", "label": "Kurta"})

        self.assertNotIn(warning, self.client.get(reverse("design-create")).content.decode())

    def test_a_code_is_uppercased_and_must_be_unique(self):
        services.add_drop(code="ss26", label="", actor=self.user)
        self.assertEqual(Season.objects.get().code, "SS26")
        self.assertEqual(Season.objects.get().label, "SS26")
        with self.assertRaises(ValidationError):
            services.add_drop(code="SS26", label="Again", actor=self.user)

    def test_renaming_a_drop_leaves_its_code_alone(self):
        drop = services.add_drop(code="SS26", label="Spring/Summer 26", actor=self.user)
        services.update_drop(drop=drop, label="Summer 26", actor=self.user)
        drop.refresh_from_db()
        self.assertEqual((drop.code, drop.label), ("SS26", "Summer 26"))

    @override_settings(STORAGES=MEMORY_STORAGE)
    def test_retiring_a_drop_keeps_it_on_designs_that_use_it(self):
        season, category = reference_data()
        design = services.create_design(
            name="", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )
        services.retire_drop(drop=season, actor=self.user)

        design.refresh_from_db()
        self.assertEqual(design.season, season)          # kept
        self.assertFalse(Season.objects.get(pk=season.pk).is_active)
        self.assertEqual(Season.objects.count(), 1)      # never deleted

    def test_a_retired_category_is_not_offered_to_new_designs(self):
        category = services.add_category(code="KURTA", label="Kurta", actor=self.user)
        services.retire_category(category=category, actor=self.user)

        from designs.forms import NewDesignForm

        self.assertNotIn(category, NewDesignForm().fields["category"].queryset)


class SpecFieldTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_a_new_attribute_gets_a_slug_for_a_code(self):
        field = services.add_spec_field(label="Placket style", show_on_card=True, actor=self.user)
        self.assertEqual(field.code, "placket-style")
        self.assertTrue(field.show_on_card)

    def test_a_clashing_label_is_refused(self):
        services.add_spec_field(label="Placket", show_on_card=False, actor=self.user)
        with self.assertRaises(ValidationError):
            services.add_spec_field(label="placket", show_on_card=False, actor=self.user)

    def test_retiring_an_attribute_keeps_its_values(self):
        field = services.add_spec_field(label="Placket", show_on_card=False, actor=self.user)
        services.add_spec_option(field=field, label="Half", actor=self.user)

        services.retire_spec_field(field=field, actor=self.user)

        field.refresh_from_db()
        self.assertFalse(field.is_active)
        self.assertEqual(SpecOption.objects.filter(field=field).count(), 1)
        self.assertEqual(SpecField.objects.filter(pk=field.pk).count(), 1)


class GuidanceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_steps_are_replaced_wholesale(self):
        card = services.save_guidance_card(
            card=None, name="Some tool", url="https://example.com", summary="",
            steps="one\ntwo\nthree", actor=self.user,
        )
        self.assertEqual([s.text for s in card.steps.all()], ["one", "two", "three"])

        services.save_guidance_card(
            card=card, name="Some tool", url="https://example.com", summary="",
            steps="only this", actor=self.user,
        )
        self.assertEqual([s.text for s in card.steps.all()], ["only this"])

    def test_a_hidden_card_is_not_shown_to_designers(self):
        card = GuidanceCard.objects.first()
        services.retire_guidance_card(card=card, actor=self.user)
        html = self.client.get(reverse("guidance-modal")).content.decode()
        self.assertNotIn(card.name, html)


class WorkflowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_only_one_status_can_be_the_initial_one(self):
        """A database constraint enforces it; the service has to keep it true."""
        first = Status.objects.get(is_initial=True)
        other = Status.objects.filter(is_initial=False).first()

        services.update_status(
            status=other, label=other.label, tone=other.tone, order=other.order,
            is_initial=True, is_approval=other.is_approval, is_terminal=other.is_terminal,
            actor=self.user,
        )

        first.refresh_from_db()
        self.assertFalse(first.is_initial)
        self.assertEqual(Status.objects.filter(is_initial=True).count(), 1)

    def test_renaming_a_stage_changes_no_behaviour(self):
        status = Status.objects.get(is_initial=True)
        services.update_status(
            status=status, label="Sketching", tone="neutral", order=status.order,
            is_initial=True, is_approval=False, is_terminal=False, actor=self.user,
        )
        status.refresh_from_db()
        self.assertEqual(status.label, "Sketching")
        self.assertTrue(status.is_initial)

    def test_setting_moves_rewrites_only_that_stage(self):
        status = Status.objects.get(is_initial=True)
        targets = list(Status.objects.exclude(pk=status.pk)[:2])
        others_before = AllowedTransition.objects.exclude(from_status=status).count()

        services.set_transitions(status=status, targets=targets, actor=self.user)

        self.assertEqual(
            set(status.transitions_out.values_list("to_status_id", flat=True)),
            {t.pk for t in targets},
        )
        self.assertEqual(AllowedTransition.objects.exclude(from_status=status).count(), others_before)

    def test_a_stage_cannot_move_to_itself(self):
        status = Status.objects.get(is_initial=True)
        services.set_transitions(status=status, targets=[status], actor=self.user)
        self.assertEqual(status.transitions_out.count(), 0)

    def test_retiring_a_stage_never_deletes_it(self):
        status = Status.objects.filter(is_initial=False).first()
        services.retire_status(status=status, actor=self.user)
        self.assertFalse(Status.objects.get(pk=status.pk).is_active)


class TeamTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_a_new_member_can_sign_in(self):
        services.add_teammate(
            username="devika", full_name="Devika R", password="test-pass-9876", actor=self.user)
        self.client.logout()
        self.assertTrue(self.client.login(username="devika", password="test-pass-9876"))

    def test_a_removed_member_cannot_sign_in_but_keeps_their_record(self):
        other = make_user("devika")
        services.deactivate_teammate(user=other, actor=self.user)

        self.client.logout()
        self.assertFalse(self.client.login(username="devika", password="test-pass-1234"))
        self.assertEqual(get_user_model().objects.filter(username="devika").count(), 1)

    def test_the_last_active_account_cannot_be_removed(self):
        """With no admin to recover from, this would lock everybody out."""
        with self.assertRaises(ValidationError):
            services.deactivate_teammate(user=self.user, actor=self.user)
        self.assertTrue(get_user_model().objects.get(pk=self.user.pk).is_active)

    def test_a_password_can_be_reset(self):
        other = make_user("devika")
        services.set_teammate_password(user=other, password="brand-new-pass-1", actor=self.user)
        self.client.logout()
        self.assertTrue(self.client.login(username="devika", password="brand-new-pass-1"))

    def test_a_duplicate_username_is_refused(self):
        with self.assertRaises(ValidationError):
            services.add_teammate(username="AARTI", full_name="", password="x-pass-1234", actor=self.user)
