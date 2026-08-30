from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("tenant/", views.select_tenant, name="select_tenant"),
    # ログイン直後の一度だけの導線。以後の切替は select_tenant を使う。
    path("welcome/tenant/", views.onboarding_tenant, name="onboarding_tenant"),
]
