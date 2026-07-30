from django.urls import path

from apps.agents import views

app_name = "agents"

urlpatterns = [
    path("", views.run_list, name="run_list"),
    path("<uuid:pk>/", views.run_detail, name="run_detail"),
]
