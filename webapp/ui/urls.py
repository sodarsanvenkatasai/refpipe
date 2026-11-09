from django.urls import path

from . import views

app_name = "ui"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("runs/", views.run_list, name="run_list"),
    path("runs/new/", views.run_new, name="run_new"),
    path("runs/sample/", views.run_sample, name="run_sample"),
    path("runs/<str:run_id>/", views.run_detail, name="run_detail"),
    path("runs/<str:run_id>/rerun/", views.run_rerun, name="run_rerun"),
    path("runs/<str:run_id>/status.json", views.run_status, name="run_status"),
    path("runs/<str:run_id>/download/<str:filename>", views.run_download, name="run_download"),
    path("samples/download/<str:filename>/", views.sample_download, name="sample_download"),
    path("compare/", views.compare_view, name="compare"),
    path("reference/", views.reference_list, name="reference_list"),
    path("reference/load/", views.reference_load, name="reference_load"),
    path("reference/<str:version>/download/", views.reference_download, name="reference_download"),
    path("reference/<str:version>/", views.reference_detail, name="reference_detail"),
    path("entities/", views.entities_view, name="entities"),
    path("data/validated/", views.validated_view, name="validated"),
    path("data/quarantine/", views.quarantine_view, name="quarantine"),
]