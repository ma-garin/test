from django.urls import path

from apps.dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.control, name="control"),
    path("tasks/", views.tasks, name="tasks"),
    path("progress/", views.progress, name="progress"),
    path("quality/", views.quality, name="quality"),
    path("risk/", views.risk, name="risk"),
    path("change/", views.change, name="change"),
    path("intervention/", views.intervention, name="intervention"),
    path(
        "intervention/<uuid:pk>/decide/",
        views.intervention_decide,
        name="intervention_decide",
    ),
    path("kpi/", views.kpi, name="kpi"),
]
