from django.contrib.auth import views as auth_views
from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("account/", views.account, name="account"),
    path("tenant/", views.select_tenant, name="select_tenant"),
    path("project/", views.select_project, name="select_project"),
    # ログイン直後の一度だけの導線。以後の切替は select_tenant / select_project を使う。
    path("welcome/tenant/", views.onboarding_tenant, name="onboarding_tenant"),
    path("welcome/project/", views.onboarding_project, name="onboarding_project"),
]
