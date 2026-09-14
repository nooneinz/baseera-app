# النبض الأسبوعي التلقائي (Business Pulse)

كل أسبوع، تولّد بصيرة **ملخص «نبض الأعمال»** لكل مستخدم لديه بيانات (نفس بطاقة الداشبورد)،
واختيارياً **ترسله على واتساب** المستخدم.

## التشغيل

**متغيّرات البيئة (Render):**
| المتغيّر | الغرض |
|---|---|
| `CRON_SECRET` | سرّ يحمي نقطة الجدولة (ترويسة `X-Baseera-Cron-Secret`). |
| `WHATSAPP_OUTBOUND_WEBHOOK_URL` (اختياري) | رابط n8n يرسل الرسالة عبر مزوّد الواتساب. بدونه، يتحدّث الملخص داخل التطبيق فقط. |

**النقطة:** `POST https://baseera.it.com/api/cron/weekly-pulse/` بترويسة `X-Baseera-Cron-Secret: <CRON_SECRET>`.

## الجدولة عبر n8n (مجاني — نفس حسابك)
1. Workflow جديد → عقدة **Schedule Trigger** (مثلاً كل أحد الساعة ٩ صباحاً).
2. عقدة **HTTP Request**: `POST` إلى نقطة النبض أعلاه، مع ترويسة `X-Baseera-Cron-Secret`.
3. فعّلي الـ Workflow. خلاص — يشتغل أسبوعياً تلقائياً.

**أو** عبر سطر الأوامر / Render Cron:
```bash
python manage.py send_weekly_pulse           # يولّد ويرسل (إن وُجد الويبهوك)
python manage.py send_weekly_pulse --no-push # يحدّث الملخص داخل التطبيق فقط
```

## الإرسال على واتساب (لاحقاً)
عند ربط بوت الواتساب، أنشئي في n8n **ويبهوك إرسال** يستقبل `{ phone, message }` ويرسلها عبر المزوّد،
وضعي رابطه في `WHATSAPP_OUTBOUND_WEBHOOK_URL`. حينها يصل النبض تلقائياً لكل صاحب محل على واتسابه.
