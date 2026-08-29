from django.urls import path

from apps.performance import views

app_name = "performance"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("orgs/", views.org_list, name="org_list"),
    path("orgs/new/", views.org_form, name="org_create"),
    path("orgs/<uuid:pk>/edit/", views.org_form, name="org_edit"),
    path("orgs/<uuid:pk>/", views.org_detail, name="org_detail"),
    path("members/new/", views.member_form, name="member_create"),
    path("members/<uuid:pk>/edit/", views.member_form, name="member_edit"),
    path("members/<uuid:pk>/", views.member_detail, name="member_detail"),
    path("years/new/", views.fiscal_year_form, name="year_create"),
    path("years/<uuid:pk>/edit/", views.fiscal_year_form, name="year_edit"),
    path("plans/", views.plan_list, name="plan_list"),
    path("plans/new/", views.plan_create, name="plan_create"),
    path("plans/<uuid:pk>/edit/", views.plan_edit, name="plan_edit"),
    path("plans/<uuid:pk>/activate/", views.plan_activate, name="plan_activate"),
    path("entry/", views.figure_entry, name="figure_entry"),
    path("kpi/", views.kpi_list, name="kpi_list"),
    path("kpi/new/", views.kpi_create, name="kpi_create"),
    path("kpi/<uuid:pk>/edit/", views.kpi_edit, name="kpi_edit"),
    path("kpi/entry/", views.kpi_entry, name="kpi_entry"),
    path("import/", views.import_view, name="import"),
    path("import/<uuid:pk>/", views.import_detail, name="import_detail"),
    path("csv/template/<str:kind>/", views.csv_template, name="csv_template"),
    path("csv/export/<str:kind>/", views.csv_export, name="csv_export"),
]
