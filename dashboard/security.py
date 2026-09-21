import os
import uuid
import socket
import ipaddress
import urllib.request
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


def rate_limit(requests_per_minute=60, key_prefix="api", methods=None, per_user=False):
    """
    `methods`: when given (e.g. ("POST",)), only requests using one of these
    HTTP methods are counted/limited at all -- every other method passes
    straight through, uncounted. Defaults to None (every method is limited,
    the original behavior) so every existing call site is unaffected.

    This exists because login/register were rate-limited on every request
    including a plain GET to render the page: a handful of page loads (a
    few browser tabs, a couple of retries, several people behind the same
    office/mobile-carrier NAT -- a routine case in Oman) was enough to burn
    through the whole per-IP budget and get the entire shared IP locked out
    of the page itself, not just repeated login attempts, for a full
    minute. Scoping the limiter to POST there keeps the real protection
    (brute-forcing the login/register form) while a page view is never
    counted against it at all.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if getattr(settings, "DISABLE_RATE_LIMIT", False):
                return view_func(request, *args, **kwargs)
            if methods is not None and request.method not in methods:
                return view_func(request, *args, **kwargs)

            # per_user=True keys the limit on the authenticated user id when
            # available (F-10: caps expensive per-user LLM spend even behind
            # a shared NAT/IP), falling back to client IP for anonymous
            # callers. Place such a decorator BELOW an auth decorator (e.g.
            # token_required) so request.user is already resolved here.
            if per_user and getattr(getattr(request, "user", None), "is_authenticated", False):
                scope = f"user:{request.user.id}"
            else:
                scope = f"ip:{get_client_ip(request)}"
            cache_key = f"rate_limit:{key_prefix}:{scope}"
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


def _host_resolves_public(host):
    """True only if every A/AAAA record for `host` is a public address.
    Blocks loopback/private/link-local/reserved/multicast targets -- the
    ranges an SSRF payload aims for (127.0.0.1, 169.254.169.254, 10.x, ...)."""
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return False
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            return False
    return True


class _NoPrivateRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validates every redirect hop (F-07): a Google Sheets export URL
    legitimately 307-redirects, but the target must still be an http(s)
    public host -- never an internal/metadata address."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Blocked redirect to a non-http(s) scheme.")
        if not _host_resolves_public(parsed.hostname or ""):
            raise ValueError("Blocked redirect to a non-public host.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url_ssrf_safe(url, allowed_hosts=None, allowed_schemes=None,
                        max_bytes=20 * 1024 * 1024, timeout=15):
    """SSRF-hardened fetch (F-07): validates the initial URL against the host
    allow-list, requires the initial host to resolve to a public IP, follows
    redirects only to public http(s) hosts, and caps the response size.
    Returns the response bytes."""
    validate_ssrf_url(url, allowed_hosts=allowed_hosts, allowed_schemes=allowed_schemes)
    parsed = urlparse(url)
    if not _host_resolves_public(parsed.hostname or ""):
        raise ValueError("The requested host does not resolve to a public address.")
    opener = urllib.request.build_opener(_NoPrivateRedirectHandler)
    with opener.open(url, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Remote file exceeds the maximum allowed size.")
    return data


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


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def _request_origin_is_trusted(request):
    """
    CSRF defense for the session-cookie auth path (F-05). Returns True if the
    request's Origin (or, failing that, Referer) is same-origin with the host
    or listed in settings.CSRF_TRUSTED_ORIGINS. Bearer-token requests never
    reach this -- they carry no ambient cookies, so they are not a CSRF
    vector. If neither header is present we return True (conservative: a
    cross-site browser attack always sends Origin on an unsafe fetch/form
    POST, so this still blocks the real vector without breaking non-browser
    or header-stripped same-origin callers).
    """
    origin = request.headers.get("Origin")
    if not origin:
        referer = request.headers.get("Referer")
        if not referer:
            return True
        parsed_ref = urlparse(referer)
        if not parsed_ref.scheme or not parsed_ref.netloc:
            return True
        origin = f"{parsed_ref.scheme}://{parsed_ref.netloc}"

    try:
        host = request.get_host()
    except Exception:
        host = ""
    scheme = "https" if request.is_secure() else "http"
    allowed = {f"https://{host}", f"http://{host}", f"{scheme}://{host}"}
    for trusted in getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or []:
        allowed.add(trusted.strip().rstrip("/"))

    return origin.rstrip("/") in allowed


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

    F-05: the session-cookie fallback path is now CSRF-defended by an
    Origin/Referer same-origin check on state-changing methods (see
    _request_origin_is_trusted). Bearer-token callers are not a CSRF vector
    (no ambient cookies) and skip that check. SameSite=Lax on the session
    cookie already blocks the cookie on most cross-site POSTs; the Origin
    check is defense-in-depth on top of it.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        # Allow already authenticated web users (Session/Cookie)
        if hasattr(request, 'user') and request.user.is_authenticated:
            # F-05: these views are @csrf_exempt, so for the cookie-auth
            # path we re-impose a CSRF defense via an Origin/Referer check
            # on state-changing methods. Bearer callers skip this branch
            # entirely (handled below) since they are not CSRF-exposed.
            if request.method not in _CSRF_SAFE_METHODS and not _request_origin_is_trusted(request):
                return JsonResponse({"status": "error", "message": "Cross-origin request blocked"}, status=403)
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


class _RejectingCsrfMiddleware:
    """Lazily-built CsrfViewMiddleware whose _reject returns the reason string
    (instead of an HttpResponseForbidden) so a caller can decide how to respond.
    Built lazily to avoid importing Django's middleware at module import time."""
    _cls = None

    @classmethod
    def check(cls, request):
        """Run Django's real CSRF check for `request`. Returns None if it
        passes, or a truthy rejection reason if it fails."""
        if cls._cls is None:
            from django.middleware.csrf import CsrfViewMiddleware

            class _M(CsrfViewMiddleware):
                def _reject(self, request, reason):
                    return reason

            cls._cls = _M
        middleware = cls._cls(lambda r: None)
        return middleware.process_view(request, None, (), {})


def session_csrf_protect(view_func):
    """
    For DUAL-MODE views (Bearer token OR web session, i.e. those also wrapped
    in @token_required and @csrf_exempt): enforce Django's real CSRF token
    check, but ONLY on the session-cookie path.

    A Bearer-token caller (the mobile app) is still anonymous when this runs
    -- token_required resolves the token further in -- so it skips the check;
    it carries no ambient cookies and is not a CSRF vector. A request already
    authenticated by session cookie is a browser request, so we require a valid
    CSRF token on state-changing methods. This is stricter than the Origin/
    Referer defense in token_required and is applied to the two LLM endpoints
    (chat, boardroom) whose web callers already send the token.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if (getattr(request, "user", None) is not None
                and request.user.is_authenticated
                and request.method not in _CSRF_SAFE_METHODS):
            reason = _RejectingCsrfMiddleware.check(request)
            if reason:
                return JsonResponse(
                    {"status": "error", "message": "CSRF verification failed"},
                    status=403,
                )
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
