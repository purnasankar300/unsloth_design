"""Domain model for the garment design tracker.

Two constraints from the spec shape everything here:

* Versions form a *tree*, not a chain. A design deep in revision must be able to
  branch back to the original reference, because externally edited images
  degrade after several successive edit rounds.
* Workflow status names are not settled. Statuses and their legal transitions
  are therefore rows, not code. Nothing in this module (or anywhere else in the
  application) branches on a status *name* — behaviour keys off the boolean
  flags ``is_approval`` and ``is_terminal``.
"""

import uuid

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from simple_history.models import HistoricalRecords

CODE_SEGMENT = RegexValidator(
    r"^[A-Z0-9]{2,8}$",
    "Use 2–8 capital letters or digits — this becomes part of the design code.",
)


class Season(models.Model):
    """A selling season, e.g. SS26. Feeds the first segment of a design code.

    The team calls it a drop, so that is the label everywhere in the UI; the
    model, the field name and the code segment stay ``season``.
    """

    code = models.CharField(max_length=8, unique=True, validators=[CODE_SEGMENT])
    label = models.CharField(max_length=80)
    is_active = models.BooleanField(
        default=True, help_text="Inactive seasons stay on old designs but are not offered for new ones."
    )

    class Meta:
        ordering = ["-code"]

    def __str__(self):
        return self.code


class Category(models.Model):
    """A garment category, e.g. KURTA. Feeds the second segment of a design code."""

    code = models.CharField(max_length=8, unique=True, validators=[CODE_SEGMENT])
    label = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["label"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.label


class SpecField(models.Model):
    """One row of the design specification.

    Which attributes a garment is described by — what it is made of, how it is
    knitted, how it is cut — is a merchandising decision, not an engineering
    one, so the fields are rows like ``Status`` is. Nothing in Python or in a
    template names one: the grid iterates whatever is active, and the seed
    migration is the only place the starting labels appear.

    Season and Category are deliberately *not* spec fields. They feed the design
    code and are therefore structural, not descriptive.
    """

    code = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=60)
    order = models.PositiveSmallIntegerField(default=0)
    show_on_card = models.BooleanField(
        default=False, help_text="Show this field as a chip on the board card. Four or so is the useful maximum."
    )
    is_active = models.BooleanField(
        default=True, help_text="Inactive fields stay on existing designs but are not offered on new ones."
    )

    class Meta:
        ordering = ["order", "label"]

    def __str__(self):
        return self.label


class SpecOption(models.Model):
    """A permitted value for one spec field.

    Retired with ``is_active=False``, never deleted: removing a value must leave
    the designs already using it untouched, and nothing in this application is
    ever destroyed.
    """

    field = models.ForeignKey(SpecField, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=80)
    swatch_hex = models.CharField(
        max_length=7, blank=True, help_text="Optional #RRGGBB, for the colour chip only. Not a Pantone match."
    )
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "label"]
        constraints = [
            models.UniqueConstraint(fields=["field", "label"], name="unique_option_label_per_field")
        ]

    def __str__(self):
        return f"{self.field.label}: {self.label}"


class Status(models.Model):
    """A workflow stage.

    Names and ordering are data so the pipeline can be settled later without a
    migration. The flags carry the meaning code depends on.
    """

    TONES = [
        ("neutral", "Neutral"),
        ("progress", "In progress"),
        ("attention", "Needs attention"),
        ("good", "Good"),
        ("stopped", "Stopped"),
    ]

    code = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=60)
    order = models.PositiveSmallIntegerField(default=0)
    tone = models.CharField(
        max_length=12, choices=TONES, default="neutral", help_text="Controls the badge colour only."
    )
    is_initial = models.BooleanField(
        default=False, help_text="The status a newly created design starts in. Exactly one status should have this."
    )
    is_approval = models.BooleanField(
        default=False,
        help_text="Reaching this status means the design is approved and requires a version to be marked final.",
    )
    is_terminal = models.BooleanField(
        default=False, help_text="No further work is expected. Shown separately from the main pipeline."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "label"]
        verbose_name_plural = "statuses"
        constraints = [
            models.UniqueConstraint(
                fields=["is_initial"],
                condition=Q(is_initial=True),
                name="only_one_initial_status",
            )
        ]

    def __str__(self):
        return self.label


class AllowedTransition(models.Model):
    """A legal move between two statuses. Absence of a row means the move is illegal."""

    from_status = models.ForeignKey(Status, on_delete=models.CASCADE, related_name="transitions_out")
    to_status = models.ForeignKey(Status, on_delete=models.CASCADE, related_name="transitions_in")

    class Meta:
        ordering = ["from_status__order", "to_status__order"]
        constraints = [
            models.UniqueConstraint(fields=["from_status", "to_status"], name="unique_transition_pair"),
            models.CheckConstraint(
                condition=~Q(from_status=models.F("to_status")), name="transition_changes_status"
            ),
        ]

    def __str__(self):
        return f"{self.from_status} → {self.to_status}"


class DesignCodeSequence(models.Model):
    """Per season+category counter behind the auto-generated design code.

    Locked with ``select_for_update`` during allocation so two people creating a
    design at the same moment cannot receive the same code.
    """

    season = models.ForeignKey(Season, on_delete=models.PROTECT)
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["season", "category"], name="unique_sequence_per_bucket")]

    def __str__(self):
        return f"{self.season}-{self.category} @ {self.next_number}"


class Design(models.Model):
    """A garment idea, tracked from reference photo to approval.

    ``code`` is the identity. ``name`` is a label on top of it: names get
    renamed and duplicated, codes must not.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=32, unique=True, editable=False)
    name = models.CharField(max_length=140)
    season = models.ForeignKey(Season, on_delete=models.PROTECT, related_name="designs", verbose_name="Drop")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="designs")
    status = models.ForeignKey(Status, on_delete=models.PROTECT, related_name="designs")

    # Authoritative real-world assets. The edited images are a visualisation
    # aid; these fields are what a garment is actually made from.
    logo_file = models.FileField(upload_to="assets/logos/", blank=True)
    colour_code = models.CharField(
        max_length=60, blank=True, help_text="Pantone or equivalent, e.g. 19-4324 TCX. Authoritative — not the image."
    )
    colour_hex = models.CharField(max_length=7, blank=True, help_text="Optional #RRGGBB, for the swatch chip only.")
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="designs_created")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} {self.name}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("design-detail", args=[self.code])

    @property
    def reference_version(self):
        """The root of the version tree — the original uploaded reference."""
        return self.versions.filter(parent__isnull=True).first()

    @property
    def approved_version(self):
        return self.versions.filter(is_approved=True).first()

    @property
    def lead_time(self):
        """Creation to approval. ``None`` while the design is unapproved."""
        if self.approved_at:
            return self.approved_at - self.created_at
        return None


class Measurement(models.Model):
    """An authoritative real-world measurement.

    A child table rather than fixed chest/length/sleeve columns, because which
    measurements matter differs by category.
    """

    design = models.ForeignKey(Design, on_delete=models.PROTECT, related_name="measurements")
    name = models.CharField(max_length=60)
    value_cm = models.DecimalField(max_digits=6, decimal_places=1, validators=[MinValueValidator(0)])
    order = models.PositiveSmallIntegerField(default=0)

    history = HistoricalRecords()

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return f"{self.name}: {self.value_cm} cm"


class DesignSpec(models.Model):
    """One field of one design's specification.

    A row per value rather than columns on ``Design``, so adding an attribute is
    an admin action and not a migration. Options are PROTECTed: a value in use
    cannot be destroyed out from under the design that carries it.
    """

    design = models.ForeignKey(Design, on_delete=models.PROTECT, related_name="specs")
    field = models.ForeignKey(SpecField, on_delete=models.PROTECT, related_name="values")
    option = models.ForeignKey(SpecOption, on_delete=models.PROTECT, related_name="values")

    history = HistoricalRecords()

    class Meta:
        ordering = ["field__order", "field__label"]
        constraints = [
            models.UniqueConstraint(fields=["design", "field"], name="one_value_per_field_per_design")
        ]

    def __str__(self):
        return f"{self.design.code} {self.field.label}: {self.option.label}"


class Version(models.Model):
    """One image in a design's tree.

    The original reference is version 1 with no parent, so there is a single
    image table and a single upload path, and "branch from the original" is an
    ordinary parent assignment.
    """

    MANUAL_UPLOAD = "manual_upload"
    ORIGINS = [
        (MANUAL_UPLOAD, "Manual upload"),
        # Reserved. Automated image editing is deferred, not rejected; adding a
        # generated source later must be additive, so origin is recorded from
        # the start rather than assumed.
        ("api_generated", "API generated"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    design = models.ForeignKey(Design, on_delete=models.PROTECT, related_name="versions")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    number = models.PositiveIntegerField(editable=False)
    requirement = models.TextField(
        blank=True, help_text="What this version was meant to change, in the uploader's own words."
    )

    image_key = models.CharField(max_length=255, unique=True, editable=False)
    thumbnail_key = models.CharField(max_length=255, unique=True, editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)
    file_size = models.PositiveBigIntegerField(editable=False)
    content_hash = models.CharField(
        max_length=64, editable=False, db_index=True, help_text="SHA-256 of the uploaded bytes. Catches re-uploads."
    )
    origin = models.CharField(max_length=20, choices=ORIGINS, default=MANUAL_UPLOAD, editable=False)

    is_approved = models.BooleanField(default=False, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="versions_created")
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["design", "number"], name="unique_version_number_per_design"),
            # Exactly one version per design may be the approved one.
            models.UniqueConstraint(
                fields=["design"], condition=Q(is_approved=True), name="one_approved_version_per_design"
            ),
            models.CheckConstraint(condition=~Q(parent=models.F("id")), name="version_is_not_its_own_parent"),
        ]

    def __str__(self):
        return f"{self.design.code} v{self.number}"

    @property
    def label(self):
        return f"v{self.number}"

    @property
    def is_reference(self):
        return self.parent_id is None

    @property
    def display_label(self):
        """What the UI calls this image: REF for the reference, then v1, v2…

        ``number`` stays 1-based in the database — the audit trail, the admin and
        every historical row keep it. This is the label only.
        """
        return "REF" if self.is_reference else f"v{self.number - 1}"

    @property
    def depth_from_reference(self):
        """How many successive edits away from the original this image is.

        Degradation accumulates with depth, which is the reason to re-branch.
        """
        depth, node, seen = 0, self, set()
        while node.parent_id and node.parent_id not in seen:
            seen.add(node.parent_id)
            node = node.parent
            depth += 1
        return depth


class Comment(models.Model):
    """Feedback on a specific image.

    Attached to a Version, not a Design — the whole point of a comment is which
    image it is about. Comments are never deleted; edits keep their history.
    """

    version = models.ForeignKey(Version, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="comments")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author} on {self.version}"


class StatusTransition(models.Model):
    """A first-class record of every status change, including creation.

    Self-approval is permitted (spec §3) but must be plainly visible, so it is
    stored as a flag rather than inferred when rendering.
    """

    design = models.ForeignKey(Design, on_delete=models.PROTECT, related_name="transitions")
    from_status = models.ForeignKey(
        Status, null=True, blank=True, on_delete=models.PROTECT, related_name="+", help_text="Empty on creation."
    )
    to_status = models.ForeignKey(Status, on_delete=models.PROTECT, related_name="+")
    version = models.ForeignKey(
        Version, null=True, blank=True, on_delete=models.PROTECT, related_name="transitions",
        help_text="The version marked final, when this transition approved the design.",
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transitions")
    comment = models.TextField(blank=True)
    is_self_approval = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        origin = self.from_status.label if self.from_status else "Created"
        return f"{self.design.code}: {origin} → {self.to_status.label}"


class GuidanceCard(models.Model):
    """An external editing tool and how to use it.

    Presentational only — the application tracks nothing about what happens in
    the tool. Editable in admin because tools, URLs and instructions will change
    and a non-developer must be able to update them.
    """

    name = models.CharField(max_length=80)
    url = models.URLField()
    summary = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class GuidanceStep(models.Model):
    card = models.ForeignKey(GuidanceCard, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField(default=0)
    text = models.CharField(max_length=300)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.text
