from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="pages/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("tenant/", views.select_tenant, name="select_tenant"),
]
