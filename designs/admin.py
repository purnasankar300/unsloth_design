"""Admin.

Two jobs beyond the usual: it is where a non-developer edits the workflow
statuses and the external-tool guidance cards, and it is where the day-one
instrumentation is read (see ``insights.py``).

Nothing here can delete a design, a version or a comment. The record is the
product; storage is cheap and a lost approved design is not.
"""

from django.contrib import admin
from django.db.models import Count
from django.urls import path
from django.utils.html import format_html

from .insights import insights_view
from .models import (
    AllowedTransition,
    Category,
    Comment,
    Design,
    DesignCodeSequence,
    DesignSpec,
    GuidanceCard,
    GuidanceStep,
    Measurement,
    Season,
    SpecField,
    SpecOption,
    Status,
    StatusTransition,
    Version,
)


class NoDeleteMixin:
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["code", "label", "is_active"]
    list_editable = ["is_active"]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["code", "label", "is_active"]
    list_editable = ["is_active"]


class TransitionOutInline(admin.TabularInline):
    model = AllowedTransition
    fk_name = "from_status"
    extra = 1
    verbose_name = "permitted move out of this status"
    verbose_name_plural = "permitted moves out of this status"


@admin.register(Status)
class StatusAdmin(admin.ModelAdmin):
    """Where the §9 open question gets answered, without touching code."""

    list_display = ["label", "code", "order", "tone", "is_initial", "is_approval", "is_terminal", "is_active"]
    list_editable = ["order", "tone", "is_active"]
    prepopulated_fields = {"code": ("label",)}
    inlines = [TransitionOutInline]
    fieldsets = [
        (None, {"fields": ["label", "code", "order", "tone", "is_active"]}),
        (
            "Meaning",
            {
                "fields": ["is_initial", "is_approval", "is_terminal"],
                "description": (
                    "The application reads these flags, never the status name. "
                    "Rename a status freely; change a flag only if its meaning really changed."
                ),
            },
        ),
    ]


class SpecOptionInline(admin.TabularInline):
    model = SpecOption
    extra = 3
    fields = ["label", "swatch_hex", "order", "is_active"]


@admin.register(SpecField)
class SpecFieldAdmin(NoDeleteMixin, admin.ModelAdmin):
    """Where the team decides what a garment is described by.

    Retire a value with ``is_active`` rather than removing it — designs already
    carrying it keep it, and the option is PROTECTed by them in any case.
    """

    list_display = ["label", "code", "order", "show_on_card", "option_count", "is_active"]
    list_editable = ["order", "show_on_card", "is_active"]
    prepopulated_fields = {"code": ("label",)}
    inlines = [SpecOptionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_options=Count("options"))

    @admin.display(description="values", ordering="_options")
    def option_count(self, obj):
        return obj._options


class DesignSpecInline(admin.TabularInline):
    model = DesignSpec
    extra = 0
    can_delete = False
    fields = ["field", "option"]


@admin.register(AllowedTransition)
class AllowedTransitionAdmin(admin.ModelAdmin):
    list_display = ["from_status", "to_status"]
    list_filter = ["from_status", "to_status"]


class MeasurementInline(admin.TabularInline):
    model = Measurement
    extra = 1
    can_delete = False


class VersionInline(admin.TabularInline):
    model = Version
    extra = 0
    can_delete = False
    fields = ["number", "parent", "created_by", "created_at", "is_approved", "content_hash"]
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Design)
class DesignAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ["code", "name", "category", "season", "status", "version_count", "lead_time_display", "created_by"]
    list_filter = ["status", "category", "season"]
    search_fields = ["code", "name"]
    readonly_fields = ["code", "created_by", "created_at", "approved_at"]
    inlines = [DesignSpecInline, MeasurementInline, VersionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_versions=Count("versions"))

    @admin.display(description="versions", ordering="_versions")
    def version_count(self, obj):
        return obj._versions

    @admin.display(description="creation → approval")
    def lead_time_display(self, obj):
        delta = obj.lead_time
        if delta is None:
            return "—"
        days = delta.days
        return f"{days}d {delta.seconds // 3600}h" if days else f"{delta.seconds // 3600}h"

    def get_urls(self):
        return [path("insights/", self.admin_site.admin_view(insights_view), name="designs-insights")] + super().get_urls()


@admin.register(Version)
class VersionAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ["__str__", "parent", "created_by", "created_at", "is_approved", "size_display"]
    list_filter = ["is_approved", "origin", "created_by"]
    search_fields = ["design__code", "content_hash"]
    readonly_fields = [f.name for f in Version._meta.fields if not f.editable] + ["design", "parent", "created_by"]

    @admin.display(description="size")
    def size_display(self, obj):
        return f"{obj.file_size / 1024 / 1024:.1f} MB"

    def has_add_permission(self, request):
        return False


@admin.register(Comment)
class CommentAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ["version", "author", "created_at", "preview"]
    search_fields = ["body", "version__design__code"]
    readonly_fields = ["version", "author", "created_at"]

    @admin.display(description="comment")
    def preview(self, obj):
        return obj.body[:90] + ("…" if len(obj.body) > 90 else "")


@admin.register(StatusTransition)
class StatusTransitionAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ["design", "from_status", "to_status", "actor", "created_at", "self_approval_flag"]
    list_filter = ["to_status", "is_self_approval", "actor"]
    search_fields = ["design__code"]
    readonly_fields = [f.name for f in StatusTransition._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.display(description="self-approved", boolean=False)
    def self_approval_flag(self, obj):
        if not obj.is_self_approval:
            return "—"
        return format_html('<b style="color:#A9701A">creator approved own design</b>')


class GuidanceStepInline(admin.TabularInline):
    model = GuidanceStep
    extra = 2


@admin.register(GuidanceCard)
class GuidanceCardAdmin(admin.ModelAdmin):
    """Tools and instructions change; this is how they get updated."""

    list_display = ["name", "url", "order", "is_active"]
    list_editable = ["order", "is_active"]
    inlines = [GuidanceStepInline]


admin.site.register(DesignCodeSequence)

admin.site.site_header = "Selvedge administration"
admin.site.site_title = "Selvedge"
admin.site.index_title = "Garment design tracker"
