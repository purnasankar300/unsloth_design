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
    # The option lists behind the gear button, and the external-tool guidance
    # behind the sparkle. Both render as a modal for htmx and as a page without.
    path("spec-options/", views.spec_options, name="spec-options"),
    path("spec-options/<slug:field_code>/", views.spec_options, name="spec-options-field"),
    path("spec-options/<slug:field_code>/add/", views.spec_option_add, name="spec-option-add"),
    path("spec-option/<int:pk>/retire/", views.spec_option_retire, name="spec-option-retire"),
    path("guidance/", views.guidance_modal, name="guidance-modal"),
    # Image access always goes through the application so the bucket can stay
    # private and links can expire.
    path("images/<uuid:pk>/", views.version_image, name="version-image"),
    path("images/<uuid:pk>/thumb/", views.version_thumbnail, name="version-thumbnail"),
]
