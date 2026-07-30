from django.urls import path

from apps.integrations import views

app_name = "integrations"

urlpatterns = [
    path("", views.connection_list, name="list"),
    path("new/", views.connection_create, name="create"),
    path("<uuid:pk>/edit/", views.connection_edit, name="edit"),
    path("<uuid:pk>/check/", views.connection_check, name="check"),
    path("<uuid:pk>/sync/", views.connection_sync, name="sync"),
    path("pipeline/", views.pipeline, name="pipeline"),
    path("jobs/", views.job_list, name="job_list"),
]
