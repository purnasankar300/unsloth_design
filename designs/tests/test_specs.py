"""The design specification.

Which attributes a garment is described by is a merchandising decision. The
fields and their values are therefore rows, and — as with statuses — nothing in
the application names one.
"""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from designs import services
from designs.models import DesignSpec, SpecField, SpecOption

from .factories import make_image, make_user, reference_data

MEMORY_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=MEMORY_STORAGE)
class SpecValueTests(TestCase):
    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Block-print Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )
        self.fabric = SpecField.objects.get(code="fabric")
        self.fit = SpecField.objects.get(code="fit")

    def test_spec_value_is_set_through_services(self):
        option = self.fabric.options.get(label="Slub Cotton")
        services.set_spec(design=self.design, field=self.fabric, option=option, actor=self.user)

        spec = DesignSpec.objects.get(design=self.design, field=self.fabric)
        self.assertEqual(spec.option, option)
        self.assertEqual(spec.history.count(), 1)
        self.assertEqual(spec.history.first().history_user, self.user)

    def test_setting_the_same_field_twice_replaces_the_value(self):
        first = self.fabric.options.get(label="Viscose")
        second = self.fabric.options.get(label="Modal")
        services.set_spec(design=self.design, field=self.fabric, option=first, actor=self.user)
        services.set_spec(design=self.design, field=self.fabric, option=second, actor=self.user)

        self.assertEqual(DesignSpec.objects.filter(design=self.design, field=self.fabric).count(), 1)
        self.assertEqual(DesignSpec.objects.get(design=self.design, field=self.fabric).option, second)

    def test_option_from_another_field_is_refused(self):
        stray = self.fit.options.first()
        with self.assertRaises(ValidationError):
            services.set_spec(design=self.design, field=self.fabric, option=stray, actor=self.user)
        self.assertFalse(DesignSpec.objects.filter(design=self.design).exists())

    def test_a_retired_option_cannot_be_newly_chosen(self):
        option = self.fabric.options.get(label="Bamboo")
        services.retire_spec_option(option=option, actor=self.user)
        option.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.set_spec(design=self.design, field=self.fabric, option=option, actor=self.user)


@override_settings(STORAGES=MEMORY_STORAGE)
class OptionListTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.fabric = SpecField.objects.get(code="fabric")

    def test_added_option_is_offered(self):
        option = services.add_spec_option(field=self.fabric, label="Hemp Blend", actor=self.user)
        self.assertIn(option, list(self.fabric.options.filter(is_active=True)))

    def test_duplicate_option_label_is_refused(self):
        with self.assertRaises(ValidationError):
            services.add_spec_option(field=self.fabric, label="viscose", actor=self.user)

    def test_empty_option_label_is_refused(self):
        with self.assertRaises(ValidationError):
            services.add_spec_option(field=self.fabric, label="   ", actor=self.user)

    def test_retiring_an_option_never_deletes_it(self):
        option = self.fabric.options.get(label="Linen Blend")
        services.retire_spec_option(option=option, actor=self.user)
        option.refresh_from_db()
        self.assertFalse(option.is_active)
        self.assertTrue(SpecOption.objects.filter(pk=option.pk).exists())


@override_settings(STORAGES=MEMORY_STORAGE)
class RetiredValueTests(TestCase):
    """Removing a value must not disturb the designs already carrying it."""

    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Block-print Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )
        self.fabric = SpecField.objects.get(code="fabric")
        self.option = self.fabric.options.get(label="Modal")
        services.set_spec(design=self.design, field=self.fabric, option=self.option, actor=self.user)
        services.retire_spec_option(option=self.option, actor=self.user)

    def test_existing_design_keeps_the_value(self):
        self.assertEqual(DesignSpec.objects.get(design=self.design, field=self.fabric).option, self.option)

    def test_the_design_still_offers_its_own_retired_value(self):
        rows = {field.code: options for field, options, _ in services.spec_choices(self.design)}
        self.assertIn(self.option, rows["fabric"])

    def test_a_fresh_design_is_not_offered_the_retired_value(self):
        rows = {field.code: options for field, options, _ in services.spec_choices(None)}
        self.assertNotIn(self.option, rows["fabric"])


class SpecFieldNamingTests(TestCase):
    def test_no_spec_field_name_is_hardcoded(self):
        """The seeded field labels are placeholders; the team renames them in admin.

        The same guarantee as ``test_no_status_name_is_hardcoded``: if a label
        appeared in the application, renaming it would break the app silently.
        The seed migration is the one place they are allowed to live.
        """
        root = Path(__file__).resolve().parent.parent
        sources = [p for p in root.rglob("*.py") if "migrations" not in p.parts and "tests" not in p.parts]
        sources += list(root.rglob("*.html"))

        labels = list(SpecField.objects.values_list("label", flat=True))
        self.assertTrue(labels, "the spec fields should be seeded")

        for path in sources:
            text = path.read_text()
            for label in labels:
                self.assertNotIn(label, text, f"{path.name} names the spec field {label!r}")
