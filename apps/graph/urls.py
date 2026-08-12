from django.urls import path

from apps.graph import views

app_name = "graph"

urlpatterns = [
    path("quality/", views.graph_quality, name="quality"),
    path("impact/<uuid:pk>/", views.impact_view, name="impact"),
]
