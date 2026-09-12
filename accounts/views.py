"""Login and logout (UC01).

Failed logins are counted per account; five consecutive failures lock it. A
locked account is refused regardless of password and only an administrator
clears the lock. The rejection message is identical for a wrong password and
an unknown username, so the form never reveals which usernames exist.
"""

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from audit.services import record

CREDENTIALS_REJECTED = "Username or password not recognized."
ACCOUNT_LOCKED = "This account is locked. Contact an administrator."


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("presentation_selector")
    error = None
    username = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user_model = get_user_model()
        account = user_model.objects.filter(username=username).first()
        profile = getattr(account, "profile", None) if account else None

        if profile is not None and profile.is_locked:
            # Refused before any password check.
            record(None, "login_locked", f"user:{username}")
            error = ACCOUNT_LOCKED
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                if profile is not None:
                    locked = profile.register_failure()
                    if locked:
                        record(None, "login_locked", f"user:{username}")
                        error = ACCOUNT_LOCKED
                if error is None:
                    record(None, "login_failed", f"user:{username}")
                    error = CREDENTIALS_REJECTED
            elif not user.is_active:
                record(None, "login_failed", f"user:{username}")
                error = CREDENTIALS_REJECTED
            else:
                if profile is not None:
                    profile.register_success()
                login(request, user)
                record(user, "login", f"user:{user.username}")
                return redirect("presentation_selector")
    return render(
        request,
        "accounts/login.html",
        {"error": error, "username": username},
    )


@require_http_methods(["GET", "POST"])
def logout_view(request):
    if request.user.is_authenticated:
        logout(request)
    return redirect("login")
