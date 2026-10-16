from django.contrib import admin

from studio.models import AudioAsset, ExportJob, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "owner", "updated_at", "created_at")
    search_fields = ("title", "id", "owner__username")
    list_filter = ("created_at",)


@admin.register(AudioAsset)
class AudioAssetAdmin(admin.ModelAdmin):
    list_display = ("id", "original_name", "project", "status", "duration", "created_at")
    search_fields = ("original_name", "id")
    list_filter = ("status",)


@admin.register(ExportJob)
class ExportJobAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "status", "progress", "created_at", "completed_at")
    list_filter = ("status",)
    search_fields = ("id",)
