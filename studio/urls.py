from django.urls import path

from studio import views

urlpatterns = [
    path("projects/", views.ProjectListCreate.as_view(), name="project-list"),
    path("projects/<uuid:project_id>/", views.ProjectDetail.as_view(), name="project-detail"),
    path(
        "projects/<uuid:project_id>/assets/",
        views.ProjectAssetUpload.as_view(),
        name="project-assets",
    ),
    path(
        "projects/<uuid:project_id>/exports/",
        views.ProjectExportCreate.as_view(),
        name="project-exports",
    ),
    path("assets/<uuid:asset_id>/", views.AssetDetail.as_view(), name="asset-detail"),
    path("assets/<uuid:asset_id>/audio/", views.AssetAudio.as_view(), name="asset-audio"),
    path("exports/<uuid:export_id>/", views.ExportDetail.as_view(), name="export-detail"),
    path(
        "exports/<uuid:export_id>/download/",
        views.ExportDownload.as_view(),
        name="export-download",
    ),
]
