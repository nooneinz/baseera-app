# بوت واتساب بصيرة — عبر Twilio Sandbox (للتجربة السريعة)

مسار سريع للتجربة **بدون** حساب Meta Business ولا انتظار. **كود بصيرة ونقطة الاستقبال نفسها بلا أي تغيير** — يتغيّر فقط تدفّق n8n.

## 1) حساب Twilio (٥ دقائق)
1. سجّلي في [twilio.com/try-twilio](https://www.twilio.com/try-twilio) (مجاني، يعطيك رصيد تجربة).
2. من Console احفظي: **Account SID** و **Auth Token** (تحت Account Info).
3. اذهبي إلى **Messaging → Try it out → Send a WhatsApp message** (WhatsApp Sandbox).
4. من **واتسابك**، أرسلي كلمة الانضمام المعروضة (مثل `join xxxx-yyyy`) إلى رقم الـ Sandbox (`+1 415 523 8886`). بيوصلك تأكيد الانضمام. احفظي رقم الـ Sandbox.

## 2) بصيرة (نفس المرحلة ١ السابقة)
- تأكدي أن `WHATSAPP_WEBHOOK_SECRET` و `GEMINI_API_KEY` مضبوطان في Render، وأن PR #52 مدموج ومنشور.

## 3) n8n
1. Import → [`n8n_workflow_twilio.json`](./n8n_workflow_twilio.json).
2. أنشئي **اعتماد Basic Auth** واحد في n8n:
   - Credentials → New → **Basic Auth** → الاسم: `Twilio Basic Auth`.
   - **User** = Account SID، **Password** = Auth Token.
   - اربطيه بعقدتَي **Download Media (Twilio)** و **Send Reply (Twilio)**.
3. عدّلي القيم:
   - في **Send Reply**: استبدلي `TWILIO_ACCOUNT_SID` في الرابط بـ Account SID، و `whatsapp:+14155238886` برقم الـ Sandbox حقك (لو مختلف).
   - في عقدتَي **Baseera Inbound**: استبدلي `BASEERA_WEBHOOK_SECRET` بالسرّ نفسه في Render.
4. **فعّلي (Activate)** التدفّق، وانسخي رابط **Production URL** من عقدة **Twilio Inbound (POST)**.

## 4) اربطي Twilio بـ n8n
- في Twilio Sandbox → **Sandbox settings** → خانة **"When a message comes in"**:
  - الصقي رابط n8n (Production URL)، الطريقة **POST**. احفظي.

## 5) جرّبي 🎉
من واتسابك (المنضمّ للـ Sandbox)، أرسلي لرقم الـ Sandbox:
- **نص**: «كم ربحت هذا الشهر؟» → يرد الوكيل (لو رقمك مسجّل في بصيرة وعندك بيانات).
- **صورة** كشف حساب/فاتورة → يرجّع لك التحليل (أكبر هدر/تدفّق نقدي).

> مهم: **سجّلي في بصيرة بنفس رقم واتسابك** (الرقم الذي انضممت به للـ Sandbox) حتى يُطابقك النظام.

## ملاحظات
- Sandbox للتجربة فقط: كل مستخدم لازم ينضم بكلمة `join ...` مرة وحدة. للإنتاج الحقيقي → مسار Meta (`README.md`).
- تنزيل وسائط Twilio يتطلب Basic Auth (SID/Token) — لذلك الاعتماد مربوط بعقدة التنزيل أيضاً.
