# ARCHITECTURE — منصة بصيرة (Baseera)

> المعمارية التقنية الكاملة. يقابلها `PRD.md` (ماذا/لِمن) و`ARCHITECTURE-ESSENTIALS.md` (ملخّص للتضمين مع كل Prompt).

## 1) التقنيات (The Stack)

| الطبقة | التقنية |
|---|---|
| التطبيق الأساسي | **Django (Python)** — `dashboard/` + `baseera_web/` |
| الواجهة | Django Templates + Tailwind (Play CDN) + نظام تصميم مخصّص (RTL، وضع داكن) |
| الجوال | **Flutter** (`baseera_mobile_app/`) عبر واجهات `api/mobile/*` |
| قناة الواتساب | **n8n** (نقل) + **WhatsApp Cloud API (Meta)** |
| قاعدة البيانات | **PostgreSQL** (+ فهرس GIN للبحث النصّي) |
| التخزين المؤقت / الحدود | **Redis** (cache + rate limiting) |
| تخزين الملفات | **MinIO** (متوافق S3) |
| الذكاء التوليدي | **Gemini** (`gemini-3.6-flash`) — للـ OCR والشرح فقط |
| الخلفية المؤسسية B2B | **ASP.NET Core (C#)** — خدمة منفصلة متعددة المستأجرين |
| الاستضافة/النشر | Render (نشر تلقائي من `main`) |

## 2) نماذج البيانات (Data Models — `dashboard/models.py`)

| النموذج | الحقول الرئيسية | الغرض |
|---|---|---|
| `Profile` | user, phone_number, project_type, company_name | ملف المنشأة؛ `phone_number` يربط واتساب بالمستخدم |
| `ProjectFile` | user, excel_file, document_type, uploaded_at | ملف مرفوع |
| `DynamicRecord` | user, project_file, row_data (JSON), schema_hash | صف واحد من البيانات (مخطّط ديناميكي) |
| `Notification` | user, title, message, type, is_read | التنبيهات (يكتبها الوكيل الاستباقي أيضاً) |
| `AgentMemory` | user, content | ذاكرة طويلة المدى للوكيل |
| `CustomAgent` | user, ... | وكيل مخصّص ينشئه المستخدم |
| `BoardroomSession` | user, topic, ... | جلسة نقاش مجلس الوكلاء |
| `WeeklyDigest` | user, summary_text, created_at | النبض الأسبوعي |
| `SystemLog` | user, action_type, details | سجل الأحداث (يشمل رسائل واتساب) |
| `AnomalyAlert`, `RiskAlert`, `SalesGoal`, `ApprovedPlan`, `CompanyStrategicProfile`, `UserFeedback` | — | نماذج مساندة للتحليل والقرار |

> **قاعدة العزل:** كل استعلام على بيانات المستخدم يُفلتر بـ `user=request.user`؛ كل نقاط الـ API ذات المعرّف تُفلتر بالمالك (لا IDOR).

## 3) الوحدات البرمجية (Modules — `dashboard/services/`)

**محرّك «لُبّ» (العقل الحتمي + الوكيل):**
- `orchestrator.py` — توجيه الطلب لأحد ٣ مسارات حسب النية.
- `retrieval_service.py` — استرجاع (RAG) هجين على مستندات المستخدم + فهرسة.
- `ai_service.py` — غلاف Gemini؛ `get_agent_meta` (شخصيات الوكلاء)، توليد الردود.
- `agent_tools.py` — أدوات ReAct الآمنة (`create_notification`, `save_memory`).
- `agent_escalation_chain.py` — تمرير القرار بين الأدوار حسب الخطورة.
- `agent_actions.py` — الوكيل الاستباقي (يكتشف → ينشئ تنبيهاً → يصيغ رسالة).

**المحرّكات الحتمية (الحساب، بلا LLM):**
- `waste_analyzer.py` — إشارات الهدر (سعر/تكلفة).
- `first_win_insights.py` — دخل/مصروف/صافي + أكبر البنود + المتكرر.
- `runway.py` — توقّع نفاد السيولة.
- `sector_benchmark.py` — مقارنة مجهّلة بوسيط القطاع.
- `weekly_pulse.py` — توليد ودفع النبض الأسبوعي.
- `anomaly_detector.py` — كشف القيم الشاذة.

**الإدخال والأمان:**
- `validation_service.py` — تحقّق نوع/محتوى الملف (python-magic + pandas + pdfplumber).
- `vision_ocr_service.py` — قراءة بصرية للـ PDF الممسوح والصور (Gemini vision).
- `privacy.py` — تجهيل المعرّفات قبل أي إرسال لـ Gemini.

**قناة الواتساب:**
- `whatsapp_service.py` — نقطة الاستقبال المحايدة للمزوّد (تحقّق → معالجة → رد قصير عُماني).

## 4) مسار البيانات (Data Flow)
```
رفع → تحقّق (validation) → [صورة/PDF ممسوح: OCR] → معالجة إلى DynamicRecord
     → حساب حتمي (بايثون) → تجهيل (privacy) → شرح (Gemini) → عرض/رد
```
الأرقام تمرّ عبر الحساب الحتمي دائماً؛ الذكاء يدخل فقط في الشرح وعلى بيانات مجهّلة.

## 5) الحدود البرمجية (Boundaries)
- **الحساب ↔ الشرح:** الأرقام في بايثون؛ الذكاء لا يحسب أبداً.
- **بصيرة ↔ n8n:** n8n يملك نقل الواتساب؛ بصيرة تملك الذكاء (نقطة استقبال واحدة محايدة للمزوّد).
- **الويب/الجوال ↔ B2B:** خدمة ASP.NET منفصلة تماماً (عزل متعدد المستأجرين).
- **بيانات المستخدم ↔ الخارج:** لا تخرج بيانات خام؛ فقط ملخّص مجهّل لـ Gemini.

## 6) نقاط التكامل الخارجية (Integrations)
| الخدمة | الغرض | الأمان |
|---|---|---|
| Gemini API | OCR + شرح | بيانات مجهّلة فقط |
| Meta WhatsApp Cloud API | إرسال/استقبال رسائل | عبر n8n + Facebook Graph credential |
| n8n | تنسيق نقل الواتساب | Webhook محمي بسرّ `WHATSAPP_WEBHOOK_SECRET` |

## 7) متغيّرات البيئة الأساسية (Env)
`GEMINI_API_KEY` · `WHATSAPP_WEBHOOK_SECRET` · `CRON_SECRET` · `BASEERA_PUBLIC_URL` · `DATABASE_URL` · `SECRET_KEY` · `WHATSAPP_OUTBOUND_WEBHOOK_URL` (اختياري).
