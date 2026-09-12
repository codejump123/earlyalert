"""Create the three development accounts, one per role.

Idempotent: re-running resets the passwords and clears any lock, and never
duplicates a user. Development only; deployment creates accounts through the
Django admin.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import ROLE_ADMIN, ROLE_ADVISOR, ROLE_INSTRUCTOR, Profile

SEED_ACCOUNTS = [
    ("instructor1", ROLE_INSTRUCTOR, False),
    ("advisor1", ROLE_ADVISOR, False),
    ("admin1", ROLE_ADMIN, True),
]
DEFAULT_PASSWORD = "earlyalert-dev"


class Command(BaseCommand):
    help = "Create or reset the three development accounts (one per role)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help=f"Password for all seeded accounts (default {DEFAULT_PASSWORD!r}).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        user_model = get_user_model()
        password = options["password"]
        for username, role, is_staff in SEED_ACCOUNTS:
            user, created = user_model.objects.get_or_create(username=username)
            user.set_password(password)
            user.is_staff = is_staff
            user.is_superuser = is_staff
            user.is_active = True
            user.save()
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.role = role
            profile.is_locked = False
            profile.failed_attempts = 0
            profile.save()
            verb = "created" if created else "reset"
            self.stdout.write(f"{verb}: {username} ({role})")
        self.stdout.write(
            self.style.SUCCESS(f"Three accounts ready. Password: {password}")
        )
