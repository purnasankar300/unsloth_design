"""Views.

Two rules run through all of them:

* Images are never linked directly. ``version_image`` and ``version_thumbnail``
  redirect to a short-lived signed URL, so the bucket stays private and access
  is revocable.
* Lists request thumbnails only. Serving full-size images in a gallery is both
  slow and expensive in egress.
"""

import json

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import services, storage
from .forms import (
    AssetsForm,
    CommentForm,
    DesignHeaderForm,
    MeasurementFormSet,
    NewDesignForm,
    NewVersionForm,
)
from .models import (
    Category,
    Design,
    GuidanceCard,
    Season,
    SpecField,
    SpecOption,
    Status,
    Version,
)


def _design_qs():
    return Design.objects.select_related("season", "category", "status", "created_by")


def design_list(request):
    designs = (
        _design_qs()
        .prefetch_related("specs__field", "specs__option")
        .annotate(
            version_count=Count("versions", distinct=True),
            comment_count=Count("versions__comments", distinct=True),
        )
    )

    category = request.GET.get("category", "")
    status = request.GET.get("status", "")
    query = request.GET.get("q", "").strip()

    if category:
        designs = designs.filter(category__code=category)
    if status:
        designs = designs.filter(status__code=status)
    if query:
        # The board's search box offers "name, code, fabric…", so it reaches into
        # the specification values as well as the design's own identity.
        designs = designs.filter(
            Q(code__icontains=query)
            | Q(name__icontains=query)
            | Q(specs__option__label__icontains=query)
        ).distinct()

    # Two queries for every cover image on the board, rather than one per card.
    approved = {v.design_id: v for v in Version.objects.filter(design__in=designs, is_approved=True)}
    latest = {}
    for version in Version.objects.filter(design__in=designs).order_by("design_id", "number"):
        latest[version.design_id] = version

    cards = [
        {
            "design": design,
            # The approved image if there is one, otherwise the newest — that is
            # what the team wants to see at a glance.
            "cover": approved.get(design.id) or latest.get(design.id),
            "version_count": design.version_count,
            "comment_count": design.comment_count,
            "chips": [spec for spec in design.specs.all() if spec.field.show_on_card],
        }
        for design in designs
    ]

    return render(
        request,
        "designs/list.html",
        {
            "cards": cards,
            "total": Design.objects.count(),
            "categories": Category.objects.filter(is_active=True),
            "statuses": Status.objects.filter(is_active=True),
            "selected_category": category,
            "selected_status": status,
            "query": query,
        },
    )


def _detail_context(request, design, *, selected_number=None, compare=None):
    """Everything the drawer needs, whether it is rendered as a page or swapped in."""
    if selected_number is None:
        selected_number = request.GET.get("v")
    if compare is None:
        compare = request.GET.get("compare") == "1"

    selected = None
    if selected_number:
        selected = design.versions.filter(number=selected_number).first()
    if selected is None:
        selected = design.approved_version or design.versions.order_by("-number").first()

    # Which inline editor, if any, the drawer is showing. Held in the URL so a
    # deep link and a no-JS click open the same thing the htmx swap does.
    editing = request.GET.get("edit", "")

    reference = design.reference_version
    versions = list(design.versions.select_related("created_by").order_by("number"))

    return {
        "design": design,
        "editing": editing,
        "header_form": DesignHeaderForm(instance=design) if editing == "head" else None,
        "versions": versions,
        "tree": services.build_tree(design),
        "selected": selected,
        "reference": reference,
        # Comparing the reference against itself shows nothing, so the mode is
        # only honoured once there is an edit on screen.
        "compare": bool(compare and reference and selected and selected.pk != reference.pk),
        "edit_count": max(len(versions) - 1, 0),
        "duplicate": services.duplicate_of(selected) if selected else None,
        # One stream for the whole design: uploads, status moves and comments.
        "activity": services.activity(design),
        "comment_form": CommentForm(),
        "version_form": NewVersionForm(),
        "spec_rows": services.spec_choices(design),
        "guidance_cards": GuidanceCard.objects.filter(is_active=True).prefetch_related("steps"),
        "transitions": services.allowed_targets(design.status),
        "statuses_all": Status.objects.filter(is_active=True),
        "signed_url_minutes": settings.SIGNED_URL_EXPIRY // 60,
        "version_count": len(versions),
    }


def design_detail(request, code):
    """The drawer.

    htmx gets the drawer body alone; anything else — a deep link, a bookmark, a
    click with JavaScript off — gets the same partial wrapped in a full page, so
    the two can never drift apart.
    """
    design = get_object_or_404(_design_qs(), code=code)
    context = _detail_context(request, design)
    template = "designs/partials/_drawer.html" if request.headers.get("HX-Request") else "designs/detail.html"
    return render(request, template, context)


def _drawer_or_redirect(request, design, *, selected_number=None, toast=""):
    """Answer a POST the way the caller asked the question.

    An htmx caller gets the re-rendered drawer plus a toast; a plain form post
    gets the redirect it expects, with the message in the message framework.
    """
    if not request.headers.get("HX-Request"):
        target = design.get_absolute_url()
        return redirect(f"{target}?v={selected_number}" if selected_number else target)

    context = _detail_context(request, design, selected_number=selected_number)
    response = render(request, "designs/partials/_drawer.html", context)
    if toast:
        response["HX-Trigger"] = json.dumps({"toast": toast})
    return response


def design_create(request):
    form = NewDesignForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            # The design and the specification values chosen alongside it are one
            # write: a half-filled grid must never become a half-saved design.
            with transaction.atomic():
                design = services.create_design(
                    name=form.cleaned_data["name"],
                    season=form.cleaned_data["season"],
                    category=form.cleaned_data["category"],
                    actor=request.user,
                    reference_upload=form.cleaned_data["reference_image"],
                    requirement=form.cleaned_data["requirement"],
                )
                for field, option_pk in form.chosen_specs():
                    services.set_spec(
                        design=design,
                        field=field,
                        option=get_object_or_404(SpecOption, pk=option_pk),
                        actor=request.user,
                    )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, f"{design.code} created. The reference image is locked as REF.")
            return redirect(design)

    if not Season.objects.filter(is_active=True).exists() or not Category.objects.filter(is_active=True).exists():
        messages.warning(
            request,
            "Add at least one drop and one category in the admin first — they make up the design code.",
        )

    return render(request, "designs/create.html", {"form": form})


@require_POST
def version_create(request, code):
    design = get_object_or_404(Design, code=code)
    form = NewVersionForm(request.POST, request.FILES)

    parent = None
    parent_number = request.POST.get("parent")
    if parent_number:
        parent = get_object_or_404(Version, design=design, number=parent_number)
    else:
        parent = design.approved_version or design.versions.order_by("-number").first()

    if not form.is_valid():
        messages.error(request, "; ".join(f"{f}: {e[0]}" for f, e in form.errors.items()))
        return redirect(design)

    try:
        version = services.add_version(
            design=design,
            parent=parent,
            upload=form.cleaned_data["image"],
            requirement=form.cleaned_data["requirement"],
            actor=request.user,
        )
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
        return redirect(design)

    duplicate = services.duplicate_of(version)
    if duplicate:
        note = (
            f"{version.display_label} is byte-for-byte identical to {duplicate.display_label}. "
            "It has been saved, but you may have uploaded the wrong file."
        )
        messages.warning(request, note)
    else:
        branched = "from the reference" if parent is None or parent.is_reference else f"from {parent.display_label}"
        note = f"{version.display_label} uploaded, branched {branched}."
        messages.success(request, note)

    return _drawer_or_redirect(request, design, selected_number=version.number, toast=note)


@require_POST
def comment_create(request, code, number):
    design = get_object_or_404(Design, code=code)
    version = get_object_or_404(Version, design=design, number=number)
    form = CommentForm(request.POST)

    if form.is_valid():
        services.add_comment(version=version, author=request.user, body=form.cleaned_data["body"])
        toast = f"Comment posted on {version.display_label}."
    else:
        toast = "Write something before posting."
        messages.error(request, toast)

    return _drawer_or_redirect(request, design, selected_number=version.number, toast=toast)


@require_POST
def status_change(request, code):
    design = get_object_or_404(_design_qs(), code=code)
    to_status = get_object_or_404(Status, code=request.POST.get("to_status", ""))

    version = None
    if request.POST.get("version"):
        version = design.versions.filter(number=request.POST["version"]).first()
    if to_status.is_approval and version is None:
        version = design.versions.order_by("-number").first()

    try:
        transition = services.change_status(
            design=design,
            to_status=to_status,
            actor=request.user,
            comment=request.POST.get("comment", ""),
            version=version,
        )
    except ValidationError as error:
        note = "; ".join(error.messages)
        messages.error(request, note)
    else:
        if transition.is_self_approval:
            note = (
                f"{design.code} approved. You created this design, "
                "so the approval is recorded as self-approval."
            )
        else:
            note = f"{design.code} moved to {to_status.label}."
        messages.success(request, note)

    design.refresh_from_db()
    return _drawer_or_redirect(
        request, design, selected_number=version.number if version else None, toast=note
    )


def assets_edit(request, code):
    """The authoritative assets, as a modal over the drawer or as a page.

    Same dual render as ``spec_options``: an htmx caller edits without leaving
    the design, a plain browser gets the standalone page and a redirect.
    """
    design = get_object_or_404(_design_qs(), code=code)
    form = AssetsForm(request.POST or None, request.FILES or None, instance=design)
    formset = MeasurementFormSet(request.POST or None, instance=design)
    htmx = bool(request.headers.get("HX-Request"))

    if request.method == "POST" and form.is_valid() and formset.is_valid():
        form.save()
        formset.save()
        note = "Authoritative assets updated."
        messages.success(request, note)
        if not htmx:
            return redirect(design)
        # Hand back the drawer, so the panel behind the modal is not stale, and
        # ask the overlay to close.
        response = render(request, "designs/partials/_drawer.html", _detail_context(request, design))
        response["HX-Trigger"] = json.dumps({"toast": note, "overlay-close": "modal"})
        return response

    context = {"design": design, "form": form, "formset": formset}
    if htmx:
        response = render(request, "designs/partials/_assets_modal.html", context)
        response["HX-Retarget"] = "#modal-body"
        return response
    return render(request, "designs/assets.html", context)


@require_POST
def design_update(request, code):
    """Rename or re-file a design. The code is never touched."""
    design = get_object_or_404(_design_qs(), code=code)
    form = DesignHeaderForm(request.POST, instance=design)

    if form.is_valid():
        # The form validates; the service writes. Never form.save().
        services.update_design(actor=request.user, design=design, **form.cleaned_data)
        note = f"{design.code} updated. The code is unchanged."
        messages.success(request, note)
    else:
        note = "; ".join(m for errors in form.errors.values() for m in errors)
        messages.error(request, note)

    design.refresh_from_db()
    return _drawer_or_redirect(
        request, design, selected_number=request.POST.get("v") or None, toast=note
    )


@require_POST
def requirement_set(request, code, number):
    """Correct the description a version was uploaded with."""
    design = get_object_or_404(_design_qs(), code=code)
    version = get_object_or_404(design.versions, number=number)

    services.set_requirement(version=version, text=request.POST.get("requirement", ""), actor=request.user)
    note = f"{version.display_label} description updated."
    messages.success(request, note)

    return _drawer_or_redirect(request, design, selected_number=version.number, toast=note)


@require_POST
def spec_set(request, code):
    """Record one specification value.

    One field per request: the drawer's dropdowns each post on change, so a
    half-filled grid is never a half-saved design.
    """
    design = get_object_or_404(_design_qs(), code=code)
    field = get_object_or_404(SpecField, code=request.POST.get("field", ""))
    option = get_object_or_404(SpecOption, pk=request.POST.get("option", 0))

    try:
        services.set_spec(design=design, field=field, option=option, actor=request.user)
    except ValidationError as error:
        note = "; ".join(error.messages)
        messages.error(request, note)
    else:
        note = f"{field.label} → {option.label}"
        messages.success(request, note)

    return _drawer_or_redirect(
        request, design, selected_number=request.POST.get("v") or None, toast=note
    )


def _options_context(field=None):
    fields = SpecField.objects.filter(is_active=True).prefetch_related("options")
    selected = field or fields.first()
    return {
        "fields": fields,
        "field": selected,
        "options": selected.options.all() if selected else [],
    }


def spec_options(request, field_code=None):
    """The option-list editor behind the gear button."""
    field = get_object_or_404(SpecField, code=field_code) if field_code else None
    context = _options_context(field)
    template = (
        "designs/partials/_settings_modal.html"
        if request.headers.get("HX-Request")
        else "designs/options.html"
    )
    return render(request, template, context)


@require_POST
def spec_option_add(request, field_code):
    field = get_object_or_404(SpecField, code=field_code)
    try:
        option = services.add_spec_option(field=field, label=request.POST.get("label", ""), actor=request.user)
    except ValidationError as error:
        note = "; ".join(error.messages)
        messages.error(request, note)
    else:
        note = f"Added {option.label} to {field.label}."
        messages.success(request, note)
    return _options_response(request, field, note)


@require_POST
def spec_option_retire(request, pk):
    option = get_object_or_404(SpecOption.objects.select_related("field"), pk=pk)
    services.retire_spec_option(option=option, actor=request.user)
    note = f"Removed {option.label} from {option.field.label}. Designs already using it keep it."
    messages.success(request, note)
    return _options_response(request, option.field, note)


def _options_response(request, field, toast):
    if not request.headers.get("HX-Request"):
        return redirect("spec-options-field", field_code=field.code)
    response = render(request, "designs/partials/_settings_modal.html", _options_context(field))
    response["HX-Trigger"] = json.dumps({"toast": toast})
    return response


def guidance_modal(request):
    """The external-tool guidance cards, as a modal or as a page."""
    context = {"guidance_cards": GuidanceCard.objects.filter(is_active=True).prefetch_related("steps")}
    template = (
        "designs/partials/_guidance_modal.html"
        if request.headers.get("HX-Request")
        else "designs/guidance.html"
    )
    return render(request, template, context)


def _redirect_to_signed(key):
    """Hand the browser a URL that works for ten minutes and then stops."""
    return HttpResponseRedirect(storage.signed_url(key))


def version_image(request, pk):
    version = get_object_or_404(Version, pk=pk)
    return _redirect_to_signed(version.image_key)


def version_thumbnail(request, pk):
    version = get_object_or_404(Version, pk=pk)
    return _redirect_to_signed(version.thumbnail_key)
