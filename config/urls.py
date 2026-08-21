from django.contrib.auth import views as auth_views
from django.urls import include, path

# There is no django-admin. Everything a non-developer has to change lives in
# the application itself, under /settings/ — see docs/application.md.
urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("designs.urls")),
]
