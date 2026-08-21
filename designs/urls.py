from django.urls import path

from . import views

urlpatterns = [
    path("", views.design_list, name="design-list"),
    path("designs/new/", views.design_create, name="design-create"),
    path("designs/<str:code>/", views.design_detail, name="design-detail"),
    path("designs/<str:code>/edit/", views.design_update, name="design-update"),
    path("designs/<str:code>/versions/", views.version_create, name="version-create"),
    path(
        "designs/<str:code>/versions/<int:number>/requirement/",
        views.requirement_set,
        name="requirement-set",
    ),
    path("designs/<str:code>/versions/<int:number>/comments/", views.comment_create, name="comment-create"),
    path("designs/<str:code>/status/", views.status_change, name="status-change"),
    path("designs/<str:code>/assets/", views.assets_edit, name="assets-edit"),
    path("designs/<str:code>/specs/", views.spec_set, name="spec-set"),

    # Settings. Everything django-admin used to do, in the app: reference data,
    # the specification grid, the external-tool cards, the workflow and the
    # team. Each section renders as a modal for htmx and as a page without.
    path("settings/", views.settings_home, name="settings"),
    path("settings/<slug:section>/", views.settings_section, name="settings-section"),
    path("settings/spec-fields/<slug:field_code>/options/", views.spec_options, name="spec-options-field"),

    path("settings/drops/add/", views.drop_add, name="drop-add"),
    path("settings/drops/<int:pk>/save/", views.drop_save, name="drop-save"),
    path("settings/drops/<int:pk>/toggle/", views.drop_toggle, name="drop-toggle"),

    path("settings/categories/add/", views.category_add, name="category-add"),
    path("settings/categories/<int:pk>/save/", views.category_save, name="category-save"),
    path("settings/categories/<int:pk>/toggle/", views.category_toggle, name="category-toggle"),

    path("settings/spec-fields/add/", views.spec_field_add, name="spec-field-add"),
    path("settings/spec-fields/<int:pk>/save/", views.spec_field_save, name="spec-field-save"),
    path("settings/spec-fields/<int:pk>/toggle/", views.spec_field_toggle, name="spec-field-toggle"),
    path("settings/spec-fields/<slug:field_code>/options/add/", views.spec_option_add, name="spec-option-add"),
    path("settings/spec-options/<int:pk>/retire/", views.spec_option_retire, name="spec-option-retire"),

    path("settings/guidance/save/", views.guidance_save, name="guidance-save"),
    path("settings/guidance/<int:pk>/save/", views.guidance_save, name="guidance-card-save"),
    path("settings/guidance/<int:pk>/toggle/", views.guidance_toggle, name="guidance-toggle"),

    path("settings/workflow/add/", views.status_add, name="status-add"),
    path("settings/workflow/<int:pk>/save/", views.status_save, name="status-save"),
    path("settings/workflow/<int:pk>/toggle/", views.status_toggle, name="status-toggle"),
    path("settings/workflow/<int:pk>/moves/", views.status_moves, name="status-moves"),

    path("settings/team/add/", views.teammate_add, name="teammate-add"),
    path("settings/team/<int:pk>/password/", views.teammate_password, name="teammate-password"),
    path("settings/team/<int:pk>/toggle/", views.teammate_toggle, name="teammate-toggle"),

    # The §10 instrumentation, no longer behind admin.
    path("insights/", views.insights, name="insights"),

    path("guidance/", views.guidance_modal, name="guidance-modal"),

    # Image access always goes through the application so the bucket can stay
    # private and links can expire.
    path("images/<uuid:pk>/", views.version_image, name="version-image"),
    path("images/<uuid:pk>/thumb/", views.version_thumbnail, name="version-thumbnail"),
]
