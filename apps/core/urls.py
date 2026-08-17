from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("settings/", views.settings_page, name="settings"),
    path("guide/", views.user_guide, name="user_guide"),
    path("guide/<slug:key>/", views.user_guide_category, name="user_guide_category"),
    path("ollama-models/", views.ollama_models, name="ollama_models"),
    path("screen-map/", views.screen_map, name="screen_map"),
    path("not-implemented/", views.not_implemented, name="not_implemented"),
]
