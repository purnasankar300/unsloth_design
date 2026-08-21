"""Tests for the rules the spec makes binding."""

from concurrent.futures import ThreadPoolExecutor

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings

from designs import codes, services
from designs.models import Comment, Design, Version

from .factories import make_image, make_user, reference_data, status

MEMORY_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=MEMORY_STORAGE)
class DesignCreationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.season, self.category = reference_data()

    def create(self, name="Block-print Kurta"):
        return services.create_design(
            name=name,
            season=self.season,
            category=self.category,
            actor=self.user,
            reference_upload=make_image(),
            requirement="Reference from the Jaipur trip.",
        )

    def test_code_is_generated_and_sequential(self):
        first, second = self.create(), self.create("Another Kurta")
        self.assertEqual(first.code, "SS26-KURTA-001")
        self.assertEqual(second.code, "SS26-KURTA-002")

    def test_name_is_not_the_identity(self):
        """Two designs may share a name; their codes must still differ."""
        first, second = self.create("Same Name"), self.create("Same Name")
        self.assertNotEqual(first.code, second.code)

    def test_reference_becomes_version_one(self):
        design = self.create()
        reference = design.reference_version
        self.assertEqual(reference.number, 1)
        self.assertIsNone(reference.parent)
        self.assertTrue(reference.is_reference)

    def test_creation_is_recorded_in_the_trail(self):
        design = self.create()
        entry = design.transitions.get()
        self.assertIsNone(entry.from_status)
        self.assertEqual(entry.actor, self.user)

    def test_second_reference_is_refused(self):
        design = self.create()
        with self.assertRaises(ValidationError):
            services.add_version(
                design=design, parent=None, upload=make_image(), requirement="", actor=self.user
            )


@override_settings(STORAGES=MEMORY_STORAGE)
class UploadPipelineTests(TestCase):
    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(size=(1200, 1500)), requirement="",
        )
        self.reference = self.design.reference_version

    def test_metadata_is_computed_on_upload(self):
        version = self.reference
        self.assertEqual((version.width, version.height), (1200, 1500))
        self.assertGreater(version.file_size, 0)
        self.assertEqual(len(version.content_hash), 64)

    def test_thumbnail_is_generated(self):
        from django.core.files.storage import default_storage

        self.assertTrue(default_storage.exists(self.reference.thumbnail_key))

    def test_keys_are_opaque(self):
        """No design name and no uploaded filename may appear in a storage key."""
        version = self.reference
        for key in (version.image_key, version.thumbnail_key):
            self.assertNotIn("photo", key)
            self.assertNotIn("Kurta", key)
            self.assertIn(str(self.design.id), key)
            self.assertIn(str(version.id), key)

    def test_non_image_is_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        with self.assertRaises(ValidationError):
            services.add_version(
                design=self.design,
                parent=self.reference,
                upload=SimpleUploadedFile("notes.txt", b"this is not an image", content_type="text/plain"),
                requirement="",
                actor=self.user,
            )

    def test_duplicate_bytes_are_detected(self):
        """A manual round-trip makes re-uploading the same file likely."""
        same = make_image()
        first = services.add_version(
            design=self.design, parent=self.reference, upload=same, requirement="", actor=self.user
        )
        second = services.add_version(
            design=self.design, parent=self.reference, upload=make_image(), requirement="", actor=self.user
        )
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(services.duplicate_of(second), first)

    def test_oversized_upload_is_rejected(self):
        with override_settings(MAX_UPLOAD_BYTES=10):
            with self.assertRaises(ValidationError):
                services.add_version(
                    design=self.design, parent=self.reference, upload=make_image(), requirement="", actor=self.user
                )


@override_settings(STORAGES=MEMORY_STORAGE)
class VersionTreeTests(TestCase):
    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )
        self.reference = self.design.reference_version

    def branch(self, parent):
        return services.add_version(
            design=self.design, parent=parent, upload=make_image(), requirement="", actor=self.user
        )

    def test_versions_form_a_tree_not_a_chain(self):
        chain = self.reference
        for _ in range(3):
            chain = self.branch(chain)
        rebranch = self.branch(self.reference)

        self.assertEqual(chain.depth_from_reference, 3)
        self.assertEqual(rebranch.depth_from_reference, 1)
        self.assertEqual(rebranch.parent, self.reference)

    def test_tree_structure_is_built_from_parents(self):
        child = self.branch(self.reference)
        grandchild = self.branch(child)
        tree = services.build_tree(self.design)

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree[0]["version"], self.reference)
        self.assertEqual(tree[0]["children"][0]["version"], child)
        self.assertEqual(tree[0]["children"][0]["children"][0]["version"], grandchild)

    def test_parent_from_another_design_is_refused(self):
        season, category = reference_data()
        other = services.create_design(
            name="Other", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )
        with self.assertRaises(ValidationError):
            services.add_version(
                design=self.design, parent=other.reference_version, upload=make_image(),
                requirement="", actor=self.user,
            )

    def test_version_numbers_are_unique_per_design(self):
        self.branch(self.reference)
        numbers = list(self.design.versions.values_list("number", flat=True))
        self.assertEqual(numbers, sorted(set(numbers)))


@override_settings(STORAGES=MEMORY_STORAGE)
class StatusTests(TestCase):
    def setUp(self):
        self.creator = make_user("aarti")
        self.other = make_user("devika")
        season, category = reference_data()
        self.design = services.create_design(
            name="Kurta", season=season, category=category, actor=self.creator,
            reference_upload=make_image(), requirement="",
        )

    def review(self):
        services.change_status(design=self.design, to_status=status("in-review"), actor=self.other)
        self.design.refresh_from_db()

    def test_illegal_transition_is_refused(self):
        with self.assertRaises(ValidationError):
            services.change_status(design=self.design, to_status=status("approved"), actor=self.other)

    def test_legal_transition_is_recorded(self):
        self.review()
        entry = self.design.transitions.first()
        self.assertEqual(entry.to_status.code, "in-review")
        self.assertEqual(entry.actor, self.other)

    def test_approval_marks_exactly_one_version(self):
        self.review()
        version = self.design.reference_version
        services.change_status(
            design=self.design, to_status=status("approved"), actor=self.other, version=version
        )
        self.design.refresh_from_db()
        self.assertEqual(self.design.versions.filter(is_approved=True).count(), 1)
        self.assertIsNotNone(self.design.approved_at)

    def test_approval_requires_a_version(self):
        self.review()
        with self.assertRaises(ValidationError):
            services.change_status(design=self.design, to_status=status("approved"), actor=self.other)

    def test_self_approval_is_permitted_but_flagged(self):
        """Spec §3: allowed for a team this size, but it must be plainly visible."""
        self.review()
        entry = services.change_status(
            design=self.design,
            to_status=status("approved"),
            actor=self.creator,
            version=self.design.reference_version,
        )
        self.assertTrue(entry.is_self_approval)

    def test_approval_by_someone_else_is_not_flagged(self):
        self.review()
        entry = services.change_status(
            design=self.design, to_status=status("approved"), actor=self.other,
            version=self.design.reference_version,
        )
        self.assertFalse(entry.is_self_approval)

    def test_leaving_approval_retires_the_final_version(self):
        self.review()
        services.change_status(
            design=self.design, to_status=status("approved"), actor=self.other,
            version=self.design.reference_version,
        )
        services.change_status(
            design=self.design, to_status=status("revision-requested"), actor=self.other
        )
        self.design.refresh_from_db()
        self.assertFalse(self.design.versions.filter(is_approved=True).exists())
        self.assertIsNone(self.design.approved_at)

    def test_no_status_name_is_hardcoded(self):
        """Renaming a status in the admin must not change behaviour."""
        approved = status("approved")
        approved.label = "Signed off"
        approved.code = "signed-off"
        approved.save()

        self.review()
        entry = services.change_status(
            design=self.design, to_status=approved, actor=self.creator,
            version=self.design.reference_version,
        )
        self.design.refresh_from_db()
        self.assertTrue(entry.is_self_approval)
        self.assertIsNotNone(self.design.approved_at)


@override_settings(STORAGES=MEMORY_STORAGE)
class CommentTests(TestCase):
    def setUp(self):
        self.user = make_user()
        season, category = reference_data()
        self.design = services.create_design(
            name="Kurta", season=season, category=category, actor=self.user,
            reference_upload=make_image(), requirement="",
        )

    def test_comments_belong_to_a_version_not_a_design(self):
        version = self.design.reference_version
        comment = services.add_comment(version=version, author=self.user, body="Neckline sits right.")
        self.assertEqual(comment.version, version)
        self.assertFalse(hasattr(Comment, "design"))

    def test_empty_comment_is_refused(self):
        with self.assertRaises(ValidationError):
            services.add_comment(version=self.design.reference_version, author=self.user, body="   ")

    def test_comment_edits_keep_history(self):
        comment = services.add_comment(
            version=self.design.reference_version, author=self.user, body="First take."
        )
        comment.body = "Second take."
        comment.save()
        self.assertEqual(comment.history.count(), 2)


class CodeAllocationConcurrencyTests(TransactionTestCase):
    """Two people creating a design at the same moment must not collide."""

    reset_sequences = True

    def test_parallel_allocation_produces_unique_codes(self):
        season, category = reference_data()

        def allocate():
            try:
                return codes.allocate_code(season, category)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: allocate(), range(5)))

        self.assertEqual(len(set(results)), 5, f"duplicate codes allocated: {results}")
