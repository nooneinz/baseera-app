# بوت واتساب بصيرة — عبر n8n + WhatsApp Cloud API (Meta)

يتيح هذا التكامل لصاحب المنشأة أن **يرسل صورة كشف حساب / فاتورة / دفتر، أو ملف Excel/CSV،
إلى رقم واتساب بصيرة، فيصله رداً فورياً**: أكبر تسريب مالي في بياناته أو لقطة تدفّقه النقدي —
محسوبة من صفوفه الحقيقية، بلا تخمين.

## المعمارية (التقسيم)

```
مستخدم واتساب
    │ (رسالة / صورة)
    ▼
WhatsApp Cloud API (Meta)  ──webhook──►  n8n  ──HTTP──►  بصيرة
                                          │   POST /api/integrations/whatsapp/inbound/
                                          │        { phone, text?, media_base64?, media_mime? }
                                          │◄──── { status, reply }
                                          ▼
                            Meta /messages  ──►  يرسل reply للمستخدم
```

- **n8n** يملك النقل: التحقق من الـ webhook، تنزيل الوسائط من Meta، وإرسال الرد.
- **بصيرة** تملك الذكاء: تُعرّف المُرسِل برقمه، وتُمرّر ما أرسله على **نفس** محرّكات التطبيق
  (التحقق → المعالجة → كشف الهدر/التدفّق النقدي)، وترجع رداً واحداً جاهزاً للواتساب.

الأرقام كلها محسوبة حتمياً من صفوف المستخدم (`waste_analyzer` + `first_win_insights`) — لا اختلاق.

## 1) إعداد بصيرة (متغيّر بيئة واحد)

في Render → خدمة بصيرة → Environment، أضف:

| المتغيّر | القيمة |
|---|---|
| `WHATSAPP_WEBHOOK_SECRET` | سرّ عشوائي قوي (٣٢+ حرفاً). n8n يرسله في ترويسة كل طلب. |
| `BASEERA_PUBLIC_URL` (اختياري) | `https://baseera.it.com` (يُستخدم في روابط الرد). |

النقطة النهائية: `POST https://baseera.it.com/api/integrations/whatsapp/inbound/`
محميّة بترويسة `X-Baseera-Webhook-Secret: <WHATSAPP_WEBHOOK_SECRET>` (بدونها ترجع 401).

**العقد (ما يرسله n8n):**
```json
{ "phone": "9689xxxxxxx", "text": "اختياري",
  "media_base64": "اختياري (Base64 للملف/الصورة)", "media_mime": "image/jpeg | text/csv | application/pdf ..." }
```
**الرد:** `{ "status": "success|unregistered|text|invalid|error", "reply": "نص عربي جاهز للإرسال" }`

> المُرسِل يُطابَق بآخر ٨ أرقام من رقم واتساب مقابل `رقم الهاتف` في ملف المستخدم.
> فليُسجّل صاحب المنشأة بنفس رقم الواتساب.

## 2) إعداد WhatsApp Cloud API (Meta)

1. [developers.facebook.com](https://developers.facebook.com) → أنشئ تطبيقاً (Business) → أضف منتج **WhatsApp**.
2. من WhatsApp → API Setup: احصل على **Temporary/Permanent Access Token** و **Phone Number ID**، وأضف رقم اختبار.
3. Configuration → Webhook:
   - **Callback URL**: رابط الـ Webhook من n8n (الخطوة ٣).
   - **Verify Token**: أي نص تختاره (نفسه في n8n).
   - اشترك في حقل **messages**.

## 3) استيراد تدفّق n8n

1. n8n → Import from File → اختر [`n8n_workflow.json`](./n8n_workflow.json).
2. عدّل قيم البيئة/الاعتماد في العُقد:
   - `META_ACCESS_TOKEN` — رمز Meta.
   - `META_PHONE_NUMBER_ID` — معرّف رقمك.
   - `META_VERIFY_TOKEN` — نفس Verify Token أعلاه.
   - `BASEERA_WEBHOOK_SECRET` — نفس `WHATSAPP_WEBHOOK_SECRET`.
3. فعّل التدفّق، وانسخ رابط عقدة **Webhook (POST)** إلى Meta كـ Callback URL.

### عُقد التدفّق (خريطة)
| العقدة | الدور |
|---|---|
| **Webhook (GET) — Meta Verify** | يرد بـ `hub.challenge` لإتمام تحقّق Meta. |
| **Webhook (POST) — Inbound** | يستقبل أحداث الرسائل من Meta. |
| **Code — Extract** | يستخرج `phone`, `type`, `text`, `media_id` من `entry[0].changes[0].value.messages[0]`. |
| **IF — Is Image?** | يفرّع: صورة/مستند ↔ نص. |
| **HTTP — Get Media URL** | `GET graph.facebook.com/v20.0/{{media_id}}` (Bearer) ← يرجّع `url`. |
| **HTTP — Download Media** | ينزّل الملف من `url` (Bearer) كـ Binary. |
| **Extract From File → Base64** | يحوّل الـ Binary إلى Base64 في حقل `media_base64`. |
| **HTTP — Baseera Inbound** | `POST .../whatsapp/inbound/` بالترويسة السرّية + `{phone, text|media_base64, media_mime}`. |
| **HTTP — Send Reply** | `POST graph.facebook.com/v20.0/{{PHONE_NUMBER_ID}}/messages` بنص `reply`. |

## 4) تجربة سريعة (بدون n8n)

```bash
curl -X POST https://baseera.it.com/api/integrations/whatsapp/inbound/ \
  -H "Content-Type: application/json" \
  -H "X-Baseera-Webhook-Secret: $WHATSAPP_WEBHOOK_SECRET" \
  -d '{"phone":"9689xxxxxxx","text":"مرحبا"}'
```
يجب أن يرجع `{"status":"text","reply":"..."}`. جرّب رقماً غير مسجّل → `unregistered`.

## ملاحظات
- الصور/المستندات الممسوحة تمرّ على OCR المالي (يحتاج `GEMINI_API_KEY` مفعّلاً). الـ CSV/Excel لا يحتاجان.
- الحد الأقصى للملف ٢٠ ميجابايت. النقطة محدّدة المعدّل (٦٠ طلب/دقيقة).
- الرد يبقى قصيراً (سطر النتيجة + رابط اللوحة) ليناسب الواتساب.
