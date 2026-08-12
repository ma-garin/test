from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("settings/", views.settings_page, name="settings"),
    path("screen-map/", views.screen_map, name="screen_map"),
]
