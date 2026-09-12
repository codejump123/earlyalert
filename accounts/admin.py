"""User and assignment management (admins work here, not in a custom view)."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    filter_horizontal = ("assignments",)
    readonly_fields = ("failed_attempts",)


class UserAdmin(BaseUserAdmin):
    inlines = [ProfileInline]
    list_display = ("username", "role", "is_locked", "failed_attempts", "is_active")
    actions = ["unlock_accounts"]

    @admin.display(description="role")
    def role(self, obj):
        return getattr(getattr(obj, "profile", None), "role", "")

    @admin.display(boolean=True, description="locked")
    def is_locked(self, obj):
        return bool(getattr(getattr(obj, "profile", None), "is_locked", False))

    @admin.display(description="failed")
    def failed_attempts(self, obj):
        return getattr(getattr(obj, "profile", None), "failed_attempts", 0)

    @admin.action(description="Unlock selected accounts")
    def unlock_accounts(self, request, queryset):
        unlocked = 0
        for user in queryset:
            profile = getattr(user, "profile", None)
            if profile is not None and profile.is_locked:
                profile.unlock()
                unlocked += 1
        self.message_user(request, f"Unlocked {unlocked} account(s).")


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
