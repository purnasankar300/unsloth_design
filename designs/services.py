"""Write operations that touch more than one table.

Everything here records who acted and when: attribution is mandatory (spec §3),
and there are no roles, so the audit trail is the only accountability the
application has.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from . import codes, imaging
from .models import (
    AllowedTransition,
    Comment,
    Design,
    DesignSpec,
    SpecOption,
    Status,
    StatusTransition,
    Version,
)


class TransitionNotAllowed(ValidationError):
    """The requested status change is not a legal move from the current status."""


@transaction.atomic
def create_design(*, name, season, category, actor, reference_upload, requirement="", **fields):
    """Create a design with its reference image as version 1.

    A blank name falls back to the allocated code. The name is only a label
    (spec §6); the code is the identity, so an unnamed design is still legible.
    """
    initial = Status.objects.filter(is_initial=True).first() or Status.objects.order_by("order").first()
    if initial is None:
        raise ValidationError("No statuses are configured. Add them in the admin before creating designs.")

    code = codes.allocate_code(season, category)
    design = Design.objects.create(
        code=code,
        name=name or code,
        season=season,
        category=category,
        status=initial,
        created_by=actor,
        **fields,
    )

    add_version(design=design, parent=None, upload=reference_upload, requirement=requirement, actor=actor)

    StatusTransition.objects.create(design=design, from_status=None, to_status=initial, actor=actor)
    return design


@transaction.atomic
def add_version(*, design, parent, upload, requirement, actor):
    """Add one image to a design's tree.

    ``parent=None`` means this is the reference. Any other parent is a normal
    branch — including branching straight back to the reference when a chain of
    edits has degraded the image.
    """
    if parent is not None and parent.design_id != design.id:
        raise ValidationError("That parent version belongs to a different design.")

    locked = Design.objects.select_for_update().get(pk=design.pk)
    if parent is None and locked.versions.exists():
        raise ValidationError("This design already has a reference image.")

    next_number = (locked.versions.aggregate(top=Max("number"))["top"] or 0) + 1

    version = Version(
        design=locked,
        parent=parent,
        number=next_number,
        requirement=requirement,
        created_by=actor,
    )
    version.__dict__.update(imaging.store(locked.id, version.id, upload))
    version.save()
    return version


def duplicate_of(version):
    """An earlier version of the same design with identical bytes, if any.

    A manual round-trip through external tools makes re-uploading the same file
    genuinely likely, so this is surfaced as a warning rather than an error.
    """
    return (
        Version.objects.filter(design_id=version.design_id, content_hash=version.content_hash)
        .exclude(pk=version.pk)
        .order_by("number")
        .first()
    )


@transaction.atomic
def add_comment(*, version, author, body):
    body = (body or "").strip()
    if not body:
        raise ValidationError("Write something before posting.")
    return Comment.objects.create(version=version, author=author, body=body)


@transaction.atomic
def set_spec(*, design, field, option, actor):
    """Record one specification value for a design.

    The option must belong to the field it is being filed under, and must still
    be offered — otherwise a stale form could quietly write a retired value.
    """
    if option.field_id != field.id:
        raise ValidationError(f"{option.label} is not a {field.label} value.")
    if not option.is_active:
        raise ValidationError(f"{option.label} is no longer offered for {field.label}.")

    spec = DesignSpec.objects.filter(design=design, field=field).first() or DesignSpec(design=design, field=field)
    spec.option = option
    # simple_history reads this off the instance, so the row is attributable even
    # when the change did not come through a request.
    spec._history_user = actor
    spec.save()

    design._history_user = actor
    design.save(update_fields=["updated_at"])
    return spec


@transaction.atomic
def add_spec_option(*, field, label, actor):
    """Add a value to a field's option list."""
    label = (label or "").strip()
    if not label:
        raise ValidationError("Type a value first.")
    if field.options.filter(label__iexact=label).exists():
        raise ValidationError(f"{label} is already in {field.label}.")

    last = field.options.order_by("-order").first()
    return SpecOption.objects.create(field=field, label=label, order=(last.order + 10) if last else 10)


@transaction.atomic
def retire_spec_option(*, option, actor):
    """Stop offering a value. Designs already carrying it keep it.

    Never a delete: nothing in this application is destroyed, and the option is
    PROTECTed by every design that uses it anyway.
    """
    if option.is_active:
        option.is_active = False
        option.save(update_fields=["is_active"])
    return option


def spec_choices(design=None):
    """The spec grid: every active field with the options to offer for it.

    Returns ``[(field, options, current_option_or_None), …]``. A design holding a
    since-retired value keeps that value in its own list, so opening the drawer
    never silently drops what someone chose.
    """
    from .models import SpecField

    chosen = {}
    if design is not None:
        chosen = {s.field_id: s.option for s in design.specs.select_related("field", "option")}

    rows = []
    fields = SpecField.objects.filter(is_active=True).prefetch_related("options")
    for field in fields:
        current = chosen.get(field.id)
        options = [o for o in field.options.all() if o.is_active]
        if current is not None and not current.is_active:
            options.append(current)
        rows.append((field, options, current))
    return rows


def allowed_targets(status):
    """Statuses that can legally be reached from ``status``."""
    return Status.objects.filter(transitions_in__from_status=status, is_active=True).order_by("order")


@transaction.atomic
def change_status(*, design, to_status, actor, comment="", version=None):
    """Move a design to another status, recording the move.

    No status name appears here. Whether a version must be chosen, and whether
    the design counts as approved, comes from the ``is_approval`` flag on the
    target row — so renaming the pipeline in the admin cannot break this.
    """
    design = Design.objects.select_for_update().get(pk=design.pk)

    if to_status == design.status:
        raise TransitionNotAllowed(f"This design is already {to_status.label}.")

    if not AllowedTransition.objects.filter(from_status=design.status, to_status=to_status).exists():
        raise TransitionNotAllowed(
            f"{design.status.label} → {to_status.label} is not a permitted move. "
            "Permitted moves are configured in the admin."
        )

    if to_status.is_approval:
        if version is None:
            raise ValidationError("Choose which version is the approved one.")
        if version.design_id != design.id:
            raise ValidationError("That version belongs to a different design.")

    previous = design.status
    design.status = to_status

    if to_status.is_approval:
        design.versions.filter(is_approved=True).update(is_approved=False)
        Version.objects.filter(pk=version.pk).update(is_approved=True)
        design.approved_at = timezone.now()
    elif previous.is_approval:
        # Moving back out of approval retires the final version rather than
        # leaving a stale "approved" marker on the tree.
        design.versions.filter(is_approved=True).update(is_approved=False)
        design.approved_at = None

    design.save(update_fields=["status", "approved_at", "updated_at"])

    return StatusTransition.objects.create(
        design=design,
        from_status=previous,
        to_status=to_status,
        version=version if to_status.is_approval else None,
        actor=actor,
        comment=comment.strip(),
        # Permitted, but it must be plainly visible in the trail.
        is_self_approval=bool(to_status.is_approval and actor == design.created_by),
    )


@transaction.atomic
def update_design(*, design, name, season, category, actor):
    """Rename or re-file a design.

    The code is deliberately *not* recalculated. It was allocated once and it is
    the identity (spec §6); a design that changed its code would break every
    reference to it. Thin on purpose — simple-history records the row — but the
    write still goes through here, so there is one place that knows the code
    stays put.
    """
    design.name = (name or "").strip() or design.code
    design.season = season
    design.category = category
    design._history_user = actor
    design.save(update_fields=["name", "season", "category", "updated_at"])
    return design


@transaction.atomic
def set_requirement(*, version, text, actor):
    """Correct what a version says it was for.

    The image is immutable; the sentence describing it is not — people upload
    first and explain afterwards. The previous wording stays in the version's
    history.
    """
    text = (text or "").strip()
    if text == version.requirement:
        return version
    version.requirement = text
    version._history_user = actor
    version.save(update_fields=["requirement"])
    return version


def build_tree(design):
    """Return the version tree as nested ``{"version": v, "children": [...]}``.

    One query for the whole design; children are assembled in Python.
    """
    versions = list(design.versions.select_related("created_by").order_by("number"))
    nodes = {v.id: {"version": v, "children": []} for v in versions}
    roots = []
    for version in versions:
        node = nodes[version.id]
        parent = nodes.get(version.parent_id)
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


def activity(design):
    """Every recorded event on a design, oldest first.

    Three tables, one timeline: status moves, version uploads and comments. The
    drawer shows them as a single stream, because "who did what, in what order"
    is the record this application exists to keep.

    Three queries regardless of how many versions or comments a design has —
    each one pulls the related rows the template reads.
    """
    from .models import Comment

    events = []

    moves = design.transitions.select_related("from_status", "to_status", "actor", "version")
    for move in moves:
        events.append({"kind": "move", "when": move.created_at, "actor": move.actor, "obj": move})

    for version in design.versions.select_related("created_by"):
        events.append(
            {"kind": "upload", "when": version.created_at, "actor": version.created_by, "obj": version}
        )

    comments = (
        Comment.objects.filter(version__design=design)
        .select_related("author", "version")
        .order_by("created_at")
    )
    for comment in comments:
        events.append(
            {"kind": "comment", "when": comment.created_at, "actor": comment.author, "obj": comment}
        )

    events.sort(key=lambda event: event["when"])
    return events


# --------------------------------------------------------------------------
# Configuration. There is no django-admin: every one of these is reachable
# from the settings screens, and spec §3 means any signed-in user may use them.
# Retiring is always ``is_active = False`` — nothing here is ever destroyed.
# --------------------------------------------------------------------------


def _unique_slug(model, label, *, field="code"):
    """A slug from a label, suffixed until it is free."""
    from django.utils.text import slugify

    base = slugify(label)[:40] or "item"
    candidate, n = base, 2
    while model.objects.filter(**{field: candidate}).exists():
        suffix = f"-{n}"
        candidate = base[: 40 - len(suffix)] + suffix
        n += 1
    return candidate


def _next_order(queryset):
    last = queryset.order_by("-order").first()
    return (last.order + 10) if last else 10


@transaction.atomic
def add_drop(*, code, label, actor):
    """A selling season. Its code becomes the first segment of a design code.

    The code is typed rather than derived: it is structural, it appears in every
    design code forever, and no label reliably shortens to `SS26`.
    """
    from .models import Season

    code = (code or "").strip().upper()
    label = (label or "").strip() or code
    if not code:
        raise ValidationError("A drop needs a code.")
    if Season.objects.filter(code=code).exists():
        raise ValidationError(f"{code} already exists.")

    drop = Season(code=code, label=label)
    drop.full_clean()
    drop.save()
    return drop


@transaction.atomic
def update_drop(*, drop, label, actor):
    """Rename a drop. The code is never touched — design codes carry it."""
    drop.label = (label or "").strip() or drop.code
    drop.save(update_fields=["label"])
    return drop


@transaction.atomic
def retire_drop(*, drop, actor):
    """Stop offering a drop. Designs already filed under it keep it."""
    if drop.is_active:
        drop.is_active = False
        drop.save(update_fields=["is_active"])
    return drop


@transaction.atomic
def restore_drop(*, drop, actor):
    if not drop.is_active:
        drop.is_active = True
        drop.save(update_fields=["is_active"])
    return drop


@transaction.atomic
def add_category(*, code, label, actor):
    """A garment category. Its code is the second segment of a design code."""
    from .models import Category

    code = (code or "").strip().upper()
    label = (label or "").strip() or code
    if not code:
        raise ValidationError("A category needs a code.")
    if Category.objects.filter(code=code).exists():
        raise ValidationError(f"{code} already exists.")

    category = Category(code=code, label=label)
    category.full_clean()
    category.save()
    return category


@transaction.atomic
def update_category(*, category, label, actor):
    category.label = (label or "").strip() or category.code
    category.save(update_fields=["label"])
    return category


@transaction.atomic
def retire_category(*, category, actor):
    if category.is_active:
        category.is_active = False
        category.save(update_fields=["is_active"])
    return category


@transaction.atomic
def restore_category(*, category, actor):
    if not category.is_active:
        category.is_active = True
        category.save(update_fields=["is_active"])
    return category


@transaction.atomic
def add_spec_field(*, label, show_on_card, actor):
    """Add an attribute a garment is described by.

    The code is slugified from the label and then frozen: designs reference the
    field by row, but the code is what the URLs and the option lists key off.
    """
    from .models import SpecField

    label = (label or "").strip()
    if not label:
        raise ValidationError("A field needs a name.")
    if SpecField.objects.filter(label__iexact=label).exists():
        raise ValidationError(f"{label} already exists.")

    return SpecField.objects.create(
        code=_unique_slug(SpecField, label),
        label=label,
        show_on_card=bool(show_on_card),
        order=_next_order(SpecField.objects),
    )


@transaction.atomic
def update_spec_field(*, field, label, order, show_on_card, actor):
    """Rename or reorder a field. The code stays as issued."""
    field.label = (label or "").strip() or field.label
    field.order = order if order is not None else field.order
    field.show_on_card = bool(show_on_card)
    field.save(update_fields=["label", "order", "show_on_card"])
    return field


@transaction.atomic
def retire_spec_field(*, field, actor):
    """Stop describing garments by this attribute. Existing values are kept."""
    if field.is_active:
        field.is_active = False
        field.save(update_fields=["is_active"])
    return field


@transaction.atomic
def restore_spec_field(*, field, actor):
    if not field.is_active:
        field.is_active = True
        field.save(update_fields=["is_active"])
    return field


@transaction.atomic
def save_guidance_card(*, card=None, name, url, summary, steps, actor):
    """Create or rewrite one external-tool card.

    Steps are replaced wholesale rather than edited row by row — the list is
    short, and a textarea is a better editor for it than a formset.
    """
    from .models import GuidanceCard, GuidanceStep

    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        raise ValidationError("A card needs a name and a link.")

    if card is None:
        card = GuidanceCard(order=_next_order(GuidanceCard.objects))
    card.name = name
    card.url = url
    card.summary = (summary or "").strip()
    card.full_clean()
    card.save()

    card.steps.all().delete()
    lines = [line.strip() for line in (steps or "").splitlines() if line.strip()]
    GuidanceStep.objects.bulk_create(
        [GuidanceStep(card=card, order=(i + 1) * 10, text=line[:300]) for i, line in enumerate(lines)]
    )
    return card


@transaction.atomic
def retire_guidance_card(*, card, actor):
    if card.is_active:
        card.is_active = False
        card.save(update_fields=["is_active"])
    return card


@transaction.atomic
def restore_guidance_card(*, card, actor):
    if not card.is_active:
        card.is_active = True
        card.save(update_fields=["is_active"])
    return card


def _apply_status_flags(status, *, is_initial, is_approval, is_terminal):
    """Set the flags, keeping the one-initial-status invariant.

    ``only_one_initial_status`` is a database constraint, so a second initial
    status has to clear the first inside the same transaction or the write
    fails.
    """
    if is_initial and not status.is_initial:
        Status.objects.filter(is_initial=True).update(is_initial=False)
    status.is_initial = bool(is_initial)
    status.is_approval = bool(is_approval)
    status.is_terminal = bool(is_terminal)


@transaction.atomic
def add_status(*, label, tone, is_initial=False, is_approval=False, is_terminal=False, actor):
    """Add a workflow stage. Spec §9 is answered here, not in code."""
    label = (label or "").strip()
    if not label:
        raise ValidationError("A status needs a name.")
    if Status.objects.filter(label__iexact=label).exists():
        raise ValidationError(f"{label} already exists.")

    status = Status(
        code=_unique_slug(Status, label),
        label=label,
        tone=tone or "neutral",
        order=_next_order(Status.objects),
    )
    _apply_status_flags(status, is_initial=is_initial, is_approval=is_approval, is_terminal=is_terminal)
    status.full_clean()
    status.save()
    return status


@transaction.atomic
def update_status(*, status, label, tone, order, is_initial=False, is_approval=False,
                  is_terminal=False, actor):
    """Rename, recolour, reorder or re-mean a stage.

    The application reads the flags and never the name, so renaming is free.
    Changing a flag changes behaviour — that is the whole point of them.
    """
    status.label = (label or "").strip() or status.label
    status.tone = tone or status.tone
    status.order = order if order is not None else status.order
    _apply_status_flags(status, is_initial=is_initial, is_approval=is_approval, is_terminal=is_terminal)
    status.save(update_fields=["label", "tone", "order", "is_initial", "is_approval", "is_terminal"])
    return status


@transaction.atomic
def retire_status(*, status, actor):
    """Stop offering a stage. Designs sitting in it stay where they are."""
    if status.is_active:
        status.is_active = False
        status.save(update_fields=["is_active"])
    return status


@transaction.atomic
def restore_status(*, status, actor):
    if not status.is_active:
        status.is_active = True
        status.save(update_fields=["is_active"])
    return status


@transaction.atomic
def set_transitions(*, status, targets, actor):
    """Rewrite which stages this one may move to.

    The only place in the application that really deletes rows, because an
    `AllowedTransition` has no active flag: the absence of the row *is* the
    meaning. No design data is touched.
    """
    wanted = {t.pk for t in targets if t.pk != status.pk}
    status.transitions_out.exclude(to_status_id__in=wanted).delete()
    existing = set(status.transitions_out.values_list("to_status_id", flat=True))
    AllowedTransition.objects.bulk_create(
        [AllowedTransition(from_status=status, to_status_id=pk) for pk in wanted - existing]
    )
    return status


@transaction.atomic
def add_teammate(*, username, full_name, password, actor):
    """Add one of the team. There are no roles — everyone can do everything."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    username = (username or "").strip()
    if not username:
        raise ValidationError("A username is required.")
    if User.objects.filter(username__iexact=username).exists():
        raise ValidationError(f"{username} is taken.")
    if not password:
        raise ValidationError("Set a password so they can sign in.")

    first, _, last = (full_name or "").strip().partition(" ")
    user = User(username=username, first_name=first[:150], last_name=last[:150])
    user.set_password(password)
    user.save()
    return user


@transaction.atomic
def set_teammate_password(*, user, password, actor):
    if not password:
        raise ValidationError("Type the new password first.")
    user.set_password(password)
    user.save(update_fields=["password"])
    return user


@transaction.atomic
def deactivate_teammate(*, user, actor):
    """Take someone out of the app without removing what they did.

    Never a delete — every design, version and comment PROTECTs its author. And
    never the last one standing: with no admin to recover from, that would lock
    everybody out.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    if user.is_active and User.objects.filter(is_active=True).count() <= 1:
        raise ValidationError("This is the only active account — add another before removing it.")
    if user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])
    return user


@transaction.atomic
def reactivate_teammate(*, user, actor):
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    return user
