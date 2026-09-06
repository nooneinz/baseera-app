#!/usr/bin/env bash
#
# run_local.sh — تشغيل بصيرة محليًا للتجربة (خادم Django + معاينة صفحات الجوال)
#
# الاستخدام:
#   ./run_local.sh          # يشغّل الخادمين معًا
#   Ctrl+C                  # يوقف الاثنين
#
# بعد التشغيل افتح المتصفح على:  http://127.0.0.1:8081/welcome.html
# (فعّل محاكاة الجوال في أدوات المطور لتجربة 320 / 375 / 768 بكسل)

set -euo pipefail

# جذر المشروع = مجلد هذا السكربت
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WWW="$ROOT/baseera_mobile_app/assets/www"

# اختر أمر بايثون المتاح
PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
  echo "خطأ: لم يتم العثور على python3 / python في النظام." >&2
  exit 1
fi

# متغيرات بيئة التطوير:
#  - DEBUG=True                     لعرض الأخطاء أثناء التطوير فقط (لا تستخدمها في الإنتاج)
#  - DISABLE_RATE_LIMIT=True        حتى لا يعيق تحديد المعدل تجربتك المحلية
#  - EMAIL_BACKEND=console          يطبع رسائل OTP/البريد في الطرفية بدل SMTP
export DEBUG="${DEBUG:-True}"
export DISABLE_RATE_LIMIT="${DISABLE_RATE_LIMIT:-True}"
export EMAIL_BACKEND="${EMAIL_BACKEND:-django.core.mail.backends.console.EmailBackend}"

BACKEND_PORT="${BACKEND_PORT:-8000}"
WEB_PORT="${WEB_PORT:-8081}"

echo "==> تهيئة قاعدة البيانات (migrate)…"
( cd "$ROOT" && "$PY" manage.py migrate --noinput )

# أوقف الخادمين عند الخروج (Ctrl+C)
PIDS=()
cleanup() {
  echo ""
  echo "==> إيقاف الخوادم…"
  for pid in "${PIDS[@]:-}"; do
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "==> تشغيل الواجهة الخلفية (Django) على http://127.0.0.1:${BACKEND_PORT}"
( cd "$ROOT" && exec "$PY" manage.py runserver "127.0.0.1:${BACKEND_PORT}" ) &
PIDS+=("$!")

echo "==> تشغيل معاينة صفحات الجوال على http://127.0.0.1:${WEB_PORT}"
( cd "$WWW" && exec "$PY" -m http.server "${WEB_PORT}" --bind 127.0.0.1 ) &
PIDS+=("$!")

sleep 2
echo ""
echo "─────────────────────────────────────────────────────"
echo "  ✅ جاهز! افتح المتصفح على:"
echo "     http://127.0.0.1:${WEB_PORT}/welcome.html"
echo ""
echo "  الواجهة الخلفية:  http://127.0.0.1:${BACKEND_PORT}"
echo "  للإيقاف: اضغط Ctrl+C"
echo "─────────────────────────────────────────────────────"

# ابقَ حيًا حتى يخرج أي خادم
wait
