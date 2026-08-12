from django.urls import path

from apps.pmo_automation import views

app_name = "pmo_automation"

urlpatterns = [
    path("approval-center/", views.approval_center, name="approval_center"),
    path("work-items/<uuid:pk>/", views.work_item_detail, name="work_item_detail"),
]
