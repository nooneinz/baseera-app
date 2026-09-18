# ARCHITECTURE-ESSENTIALS — بصيرة (للتضمين مع كل Prompt)

> ملخّص قصير يُلصق في بداية أي أمر للوكيل، ليعطيه السياق دون استهلاك نافذة كبيرة.
> التفصيل الكامل في `ARCHITECTURE.md` و`PRD.md`.

**ما هو:** منصة ذكاء مالي لمنشآت الخليج الصغيرة. يرفع صاحب المنشأة ملفاً مالياً فتكشف له بصيرة أين يخسر فلوسه. **مبدأ حاكم: الأرقام تُحسب حتمياً في بايثون؛ الذكاء يشرح فقط ولا يخترع.**

**التقنيات (Stack):** Django (Python) · PostgreSQL(+GIN) · Redis · MinIO · Gemini (OCR+شرح) · Flutter (جوال) · n8n + Meta (واتساب) · ASP.NET Core (خلفية B2B منفصلة).

**النماذج (Models):** `Profile` (phone_number يربط واتساب) · `ProjectFile` · `DynamicRecord` (row_data JSON) · `Notification` · `AgentMemory` · `WeeklyDigest` · `BoardroomSession` · `CustomAgent` · `SystemLog`. **كل استعلام يُفلتر بـ `user`.**

**الوحدات (Modules) في `dashboard/services/`:**
- الوكيل: `orchestrator` · `retrieval_service`(RAG) · `ai_service` · `agent_tools`(ReAct) · `agent_escalation_chain` · `agent_actions`.
- الحساب الحتمي: `waste_analyzer` · `first_win_insights` · `runway` · `sector_benchmark` · `weekly_pulse` · `anomaly_detector`.
- الإدخال/الأمان: `validation_service` · `vision_ocr_service` · `privacy`.
- الواتساب: `whatsapp_service`.

**الحدود (Boundaries):**
1. الحساب في بايثون فقط — الذكاء لا يحسب.
2. بيانات المستخدم لا تخرج خاماً — تُجهَّل عبر `privacy` قبل Gemini.
3. n8n يملك نقل الواتساب؛ بصيرة تملك الذكاء (نقطة استقبال واحدة محايدة).
4. عزل صارم: كل استعلام/نقطة API تُفلتر بالمالك.

**مسار البيانات:** رفع → تحقّق → (OCR للصور) → DynamicRecord → حساب حتمي → تجهيل → شرح → عرض/رد.

**أوامر مفيدة:** `python manage.py test` · `python manage.py check` · النشر تلقائي من `main` (Render).
