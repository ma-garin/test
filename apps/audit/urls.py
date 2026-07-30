from django.urls import path

from apps.audit import views

app_name = "audit"

urlpatterns = [
    path("operations/", views.operation_list, name="operation_list"),
    path("feedback/", views.feedback_list, name="feedback_list"),
]
