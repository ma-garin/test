from django.urls import path

from apps.projects import views

app_name = "projects"

urlpatterns = [
    path("", views.project_list, name="list"),
    path("tasks/new/", views.task_create, name="task_create"),
    path("tasks/<uuid:pk>/", views.task_detail, name="task_detail"),
    path("tasks/<uuid:pk>/edit/", views.task_edit, name="task_edit"),
    path("tasks/<uuid:pk>/archive/", views.task_archive, name="task_archive"),
    path("risks/new/", views.risk_create, name="risk_create"),
    path("risks/<uuid:pk>/edit/", views.risk_edit, name="risk_edit"),
    path("risks/<uuid:pk>/close/", views.risk_close, name="risk_close"),
    path("risks/<uuid:pk>/promote/", views.risk_promote, name="risk_promote"),
    path("issues/", views.issue_list, name="issue_list"),
    path("issues/new/", views.issue_create, name="issue_create"),
    path("issues/<uuid:pk>/edit/", views.issue_edit, name="issue_edit"),
    path("issues/<uuid:pk>/close/", views.issue_close, name="issue_close"),
    path("<uuid:pk>/", views.project_detail, name="detail"),
    path("changes/new/", views.change_create, name="change_create"),
    path("changes/<uuid:pk>/edit/", views.change_edit, name="change_edit"),
    path("changes/<uuid:pk>/decide/", views.change_decide, name="change_decide"),
    path("defects/", views.defect_list, name="defect_list"),
    path("defects/new/", views.defect_create, name="defect_create"),
    path("defects/<uuid:pk>/edit/", views.defect_edit, name="defect_edit"),
    path("defects/<uuid:pk>/close/", views.defect_close, name="defect_close"),
]
