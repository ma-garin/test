from django.urls import path

from apps.projects import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("<uuid:pk>/", views.project_detail, name="detail"),
]
