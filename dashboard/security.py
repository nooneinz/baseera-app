import os
import uuid
from functools import wraps
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils.html import escape
from django.core import signing
from django.contrib.auth.models import User

ALLOWED_UPLOAD_EXTENSIONS = {
    ".csv",
    ".xlsx",
    ".xls",
    ".pdf",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
}

DEFAULT_MAX_UPLOAD_SIZE = 20 * 1024 * 1024


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def build_safe_filename(original_name, prefix="upload"):
    raw_name = (original_name or f"{prefix}.bin").strip()
    clean_name = raw_name.replace("\\", "/").split("/")[-1]
    safe_name = "".join(ch for ch in clean_name if ch.isalnum() or ch in {"-", "_", "."})
    if not safe_name or safe_name in {".", ".."}:
        safe_name = f"{prefix}.bin"

    name, ext = os.path.splitext(safe_name)
    if not ext:
        ext = ".bin"
    clean_ext = ext.lower()
    if clean_ext not in ALLOWED_UPLOAD_EXTENSIONS and clean_ext not in {".bin"}:
        clean_ext = ".bin"

    return f"{uuid.uuid4().hex}{clean_ext}"


def validate_uploaded_file(uploaded_file, max_size_bytes=DEFAULT_MAX_UPLOAD_SIZE, allowed_extensions=None):
    if uploaded_file is None:
        raise ValueError("No file was provided.")

    allowed = set(allowed_extensions or ALLOWED_UPLOAD_EXTENSIONS)
    file_name = getattr(uploaded_file, "name", "") or "upload.bin"
    file_ext = os.path.splitext(file_name)[1].lower()

    if file_ext not in allowed:
        raise ValueError("Unsupported file type.")

    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    disallowed_mimes = {"application/x-dosexec", "application/x-msdos-program", "application/x-executable", "text/html", "application/javascript", "text/javascript", "application/x-sh", "application/x-python"}
    if content_type in disallowed_mimes:
        raise ValueError("نوع الملف غير مسموح به لأسباب أمنية / Insecure file MIME type.")

    file_size = getattr(uploaded_file, "size", 0) or 0
    if file_size <= 0:
        raise ValueError("الملف المرفوع فارغ / Empty file upload is not allowed.")
    if file_size > max_size_bytes:
        raise ValueError("حجم الملف يتجاوز الحد الأقصى المسموح / File exceeds size limit.")

    return True


def rate_limit(requests_per_minute=60, key_prefix="api"):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if getattr(settings, "DISABLE_RATE_LIMIT", False):
                return view_func(request, *args, **kwargs)

            client_ip = get_client_ip(request)
            cache_key = f"rate_limit:{key_prefix}:{client_ip}"
            current_count = cache.get(cache_key, 0)
            if current_count >= requests_per_minute:
                return JsonResponse({"status": "error", "message": "Too many requests. Please retry later."}, status=429)
            cache.set(cache_key, current_count + 1, timeout=60)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def validate_ssrf_url(url, allowed_hosts=None, allowed_schemes=None):
    allowed_hosts = set((allowed_hosts or {"docs.google.com", "spreadsheets.google.com", "localhost", "127.0.0.1"}))
    allowed_schemes = set((allowed_schemes or {"http", "https"}))

    if not url:
        raise ValueError("URL is required.")

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError("The URL scheme is not allowed.")
    if host not in allowed_hosts:
        raise ValueError("The requested host is not in the allowlist.")
    if not parsed.netloc:
        raise ValueError("The URL is malformed.")
    return True


def sanitize_for_output(value):
    if value is None:
        return ""
    return escape(str(value))


# The exact character sequences the platform's own agent response parser
# (dashboard/services/ai_service.py) treats as live control tags --
# [[ACTION:...]] tool calls, and the <internal_simulation>/<agent_state>/
# <file_proposal>/<approval_checkpoint> block tags. A cell value or a raw
# file-context string is never supposed to legitimately contain these; if
# one does, it's either a coincidence or an attempt to smuggle a fake
# instruction into the model's context via uploaded data (indirect prompt
# injection). Task 5 hardening: neutralize them before any of that text is
# embedded in an LLM prompt.
def sanitize_cell_for_prompt(value, max_len=300):
    """
    Defuses indirect prompt-injection payloads that can hide inside a
    single uploaded spreadsheet cell (a product name, a transaction
    description, a raw file-context blob, ...) before that text reaches an
    LLM prompt. Also caps length so one oversized cell can't blow out the
    prompt budget. Ordinary business text is left readable.
    """
    if value is None:
        return value
    text = str(value)
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    # Break the exact bracket/tag sequences our own parsers look for,
    # without deleting the surrounding text.
    text = text.replace("[[", "[ ").replace("]]", " ]")
    text = text.replace("<", "‹").replace(">", "›")
    return text


def sanitize_for_prompt(value, max_len=300):
    """Recursively applies sanitize_cell_for_prompt across dicts/lists/strings."""
    if isinstance(value, dict):
        return {k: sanitize_for_prompt(v, max_len=max_len) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_prompt(v, max_len=max_len) for v in value]
    if isinstance(value, str):
        return sanitize_cell_for_prompt(value, max_len=max_len)
    return value


def safe_error_message(message, fallback="An internal error has occurred."):
    if not message:
        return fallback
    cleaned = sanitize_for_output(message)
    return cleaned if len(cleaned) < 200 else fallback


def issue_access_token(user):
    pwd_hash = user.password[-10:] if user.password else "nopwd"
    return signing.dumps({"user_id": user.pk, "pwd_hash": pwd_hash}, salt="baseera-mobile-auth")


def token_required(view_func):
    """
    Dual-mode auth for the mobile API surface: a real Bearer token, or (see
    below) a fallback to an existing web session.

    CSRF note for every dashboard.api_views view wrapped in this decorator:
    they all still carry @csrf_exempt, and that split IS the documented,
    technically justified exception -- a stateless Bearer-token client (the
    mobile app) never holds the ambient browser cookies CSRF attacks rely
    on, and it has no CSRF-token machinery to send one even if asked
    (Django's CSRF middleware would otherwise reject every legitimate
    mobile request outright, since no csrftoken cookie exists for it to
    validate against). Removing @csrf_exempt from these views to fix the
    session-fallback gap below would break the real mobile app in
    production; that is not an oversight, it is why this exemption stays.

    Known, tracked residual gap: the session-cookie fallback one line down
    means a user who is ALSO logged into the web session is not CSRF-
    protected on these same endpoints while authenticated that way. Closing
    that properly needs splitting this into two decorators (a strict
    Bearer-only one for the mobile views, session auth kept CSRF-protected
    separately) rather than a blanket exemption removal -- a real
    architectural change, intentionally out of scope for this pass rather
    than risking mobile app breakage to rush it.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        # Allow already authenticated web users (Session/Cookie)
        if hasattr(request, 'user') and request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
            
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return JsonResponse({"status": "error", "message": "Authentication required"}, status=401)
        try:
            payload = signing.loads(
                authorization[7:].strip(),
                salt="baseera-mobile-auth",
                max_age=60 * 60 * 12,
            )
            user = User.objects.get(pk=payload["user_id"], is_active=True)
            expected_hash = user.password[-10:] if user.password else "nopwd"
            if payload.get("pwd_hash") != expected_hash:
                raise ValueError("Password changed, token invalidated")
            request.user = user
        except (signing.BadSignature, signing.SignatureExpired, KeyError, User.DoesNotExist, TypeError, ValueError):
            return JsonResponse({"status": "error", "message": "Invalid or expired token"}, status=401)
        return view_func(request, *args, **kwargs)
    return _wrapped


def require_owner_or_admin(model_name=None, field_name="user"):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({"status": "error", "message": "Authentication required"}, status=401)
            if request.user.is_staff or request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            model = model_name
            if model is None:
                return view_func(request, *args, **kwargs)

            obj = model.objects.filter(pk=kwargs.get("pk") or kwargs.get("alert_id") or kwargs.get("notif_id") or kwargs.get("id")).first()
            if obj is None:
                return JsonResponse({"status": "error", "message": "Resource not found"}, status=404)
            if getattr(obj, field_name) != request.user:
                return JsonResponse({"status": "error", "message": "Forbidden"}, status=403)
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
