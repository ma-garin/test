from django.urls import path

from apps.pmo import views

app_name = "pmo"

urlpatterns = [
    path("consultation/", views.consultation, name="consultation"),
    path("planning/", views.planning, name="planning"),
    path("deliverables/", views.deliverables, name="deliverables"),
    path("approvals/", views.approvals, name="approvals"),
    path("prompts/", views.prompt_library, name="prompt_library"),
    path("education/", views.education, name="education"),
]
