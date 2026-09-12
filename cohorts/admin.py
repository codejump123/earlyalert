from django.contrib import admin

from .models import DataUpload, Presentation, Student


@admin.register(Presentation)
class PresentationAdmin(admin.ModelAdmin):
    list_display = ("code_module", "code_presentation", "start_date", "length_days")
    list_filter = ("code_module",)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("id_student", "presentation", "final_result", "date_unregistration")
    list_filter = ("presentation", "final_result", "imd_band", "disability")
    search_fields = ("id_student",)


@admin.register(DataUpload)
class DataUploadAdmin(admin.ModelAdmin):
    list_display = ("filename", "row_count", "uploaded_at", "batch_id", "superseded")
    list_filter = ("superseded", "filename")
    readonly_fields = ("checksum", "row_count", "uploaded_at", "batch_id")
