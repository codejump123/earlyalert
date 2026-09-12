from django.contrib import admin

from .models import AuditEntry


@admin.register(AuditEntry)
class AuditEntryAdmin(admin.ModelAdmin):
    """Read-only: the log is append-only, with no update or delete path."""

    list_display = ("timestamp", "user", "role", "action", "target")
    list_filter = ("action", "role")
    search_fields = ("target",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
