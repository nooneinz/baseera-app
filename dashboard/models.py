from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

def validate_strategic_priorities(value):
    allowed_priorities = {"cash_preservation", "growth", "profitability", "cost_reduction", "long_term_stability"}
    if not isinstance(value, list):
        raise ValidationError("Strategic priorities must be a list.")
    if len(value) != 5:
        raise ValidationError("You must provide exactly 5 priorities.")
    if set(value) != allowed_priorities:
        raise ValidationError("Invalid or duplicate priorities provided.")

class CompanyStrategicProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='company_profile')
    
    company_name = models.CharField(max_length=255)
    sector = models.CharField(max_length=100, choices=[
        ('retail', 'تجزئة'),
        ('restaurant', 'مطاعم'),
        ('manufacturing', 'تصنيع'),
        ('services', 'خدمات'),
        ('ecommerce', 'تجارة إلكترونية'),
        ('other', 'أخرى')
    ])
    size = models.CharField(max_length=50, choices=[
        ('micro', 'متناهية الصغر (1-5)'),
        ('small', 'صغيرة (6-49)'),
        ('medium', 'متوسطة (50-249)'),
        ('large', 'كبيرة (250+)')
    ])

    growth_stage = models.CharField(max_length=50, choices=[
        ('startup', 'تأسيس / بداية'),
        ('growth', 'نمو وتوسع'),
        ('mature', 'استقرار وتشغيل'),
        ('turnaround', 'إعادة هيكلة / تخفيض تكاليف')
    ])

    strategic_priorities_ranking = models.JSONField(default=list, validators=[validate_strategic_priorities])

    risk_tolerance = models.CharField(max_length=20, choices=[
        ('conservative', 'محافظ (Conservative)'),
        ('balanced', 'متوازن (Balanced)'),
        ('aggressive', 'جريء (Aggressive)')
    ])

    max_investment_limit = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    cash_reserve_floor = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)

    active_agents = models.JSONField(default=list)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Strategic Profile for {self.company_name}"


class Profile(models.Model):
    PROJECT_TYPES = [
        ('pharmacy', 'صيدلية (Pharmacy)'),
        ('real_estate', 'عقارات (Real Estate)'),
        ('retail', 'تجارة التجزئة (Retail)'),
        ('fnb', 'مطاعم ومقاهي (F&B)'),
        ('other', 'أخرى (Other)'),
    ]

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, verbose_name="المستخدم / User"
    )
    phone_number = models.CharField(
        max_length=20, verbose_name="رقم الهاتف العُماني / Omani Phone"
    )
    commercial_register = models.CharField(
        max_length=50, verbose_name="رقم السجل التجاري (CR) / Commercial Register"
    )
    company_name = models.CharField(
        max_length=150,
        default="مؤسسة عُمانية ناشئة",
        verbose_name="اسم الشركة / SME Name",
    )
    project_type = models.CharField(
        max_length=20,
        choices=PROJECT_TYPES,
        default='other',
        verbose_name="نوع المشروع / Project Type",
    )

    trial_start_date = models.DateTimeField(
        null=True, blank=True, verbose_name="تاريخ بدء الفترة التجريبية / Trial Start Date"
    )
    subscription_plan = models.CharField(
        max_length=50,
        default='trial',
        choices=[
            ('trial', 'فترة تجريبية (Trial)'),
            ('starter', 'باقة الانطلاق (Starter)'),
            ('growth', 'باقة النمو (Growth)'),
            ('enterprise', 'باقة المؤسسات (Enterprise)'),
        ],
        verbose_name="باقة الاشتراك / Subscription Plan"
    )
    is_subscribed = models.BooleanField(
        default=False, verbose_name="هل الاشتراك نشط؟ / Is Subscribed"
    )

    def __str__(self):
        return f"{self.user.username} - {self.company_name}"

    def get_trial_info(self):
        """
        Calculates trial days countdown & status.
        Default trial duration is 14 days starting from user.date_joined or trial_start_date.
        """
        if self.is_subscribed:
            return {
                'days_remaining': 999,
                'days_passed': 0,
                'percentage_remaining': 100,
                'percentage_used': 0,
                'is_expired': False,
                'is_subscribed': True,
                'plan': self.get_subscription_plan_display(),
            }

        start_date = self.trial_start_date or self.user.date_joined
        from django.utils import timezone
        now = timezone.now()

        if not start_date:
            days_passed = 0
        else:
            days_passed = max(0, (now.date() - start_date.date()).days)

        days_remaining = max(0, 14 - days_passed)
        percentage_remaining = min(100, max(0, int((days_remaining / 14.0) * 100)))
        percentage_used = 100 - percentage_remaining
        is_expired = days_passed >= 14 or days_remaining <= 0

        return {
            'days_remaining': days_remaining,
            'days_passed': days_passed,
            'percentage_remaining': percentage_remaining,
            'percentage_used': percentage_used,
            'is_expired': is_expired,
            'is_subscribed': False,
            'plan': 'trial',
        }


class ProjectFile(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, verbose_name="المستخدم / User"
    )
    excel_file = models.FileField(
        upload_to="excel_files/",
        verbose_name="ملف البيانات المحاسبية / Accounting File",
    )
    uploaded_at = models.DateTimeField(
        auto_now_add=True, verbose_name="تاريخ الرفع / Uploaded At"
    )

    def __str__(self):
        return f"{self.user.username} - {self.excel_file.name}"


class UserFeedback(models.Model):
    FEEDBACK_TYPES = [
        ("rating", "تقييم المنصة"),
        ("suggestion", "اقتراح ميزة"),
        ("complaint", "تقديم شكوى"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    feedback_type = models.CharField(max_length=20, choices=FEEDBACK_TYPES, default="rating")
    from django.core.validators import MinValueValidator, MaxValueValidator
    rating = models.IntegerField(default=5, validators=[MinValueValidator(1), MaxValueValidator(5)])
    email = models.EmailField()
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


    def __str__(self):
        return f"[{self.get_feedback_type_display()}] - {self.email} ({self.rating}★)"


class DynamicRecord(models.Model):
    """
    Cumulative data table for storing parsed Excel/CSV data.
    Allows time-series analysis and tracking across multiple uploaded files of ANY sector.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="dynamic_records")
    project_file = models.ForeignKey(ProjectFile, on_delete=models.SET_NULL, null=True, blank=True)
    
    schema_hash = models.CharField(max_length=64, help_text="Hash of the column names to isolate sectors")
    row_data = models.JSONField(help_text="The actual row data mapped by column name")
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Record for {self.user.username} (File: {self.project_file.id if self.project_file else 'None'})"

class SystemLog(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="المستخدم"
    )
    action_type = models.CharField(max_length=50, verbose_name="نوع العملية")
    details = models.TextField(verbose_name="التفاصيل", null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="وقت العملية")

    def __str__(self):
        return f"{self.action_type} - {self.timestamp}"

class Invoice(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم")
    plan_name = models.CharField(max_length=50, verbose_name="الباقة")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="المبلغ")
    currency = models.CharField(max_length=10, default="OMR", verbose_name="العملة")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الدفع")

    def __str__(self):
        return f"{self.user.username} - {self.plan_name} - {self.amount} {self.currency}"


class PaymentIdempotency(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payment_requests")
    key = models.CharField(max_length=128)
    status = models.CharField(max_length=20, default="processing")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "key"), name="unique_payment_idempotency_key"),
        ]

    def __str__(self):
        return f"Payment request {self.user_id}:{self.key} ({self.status})"

class Announcement(models.Model):
    TYPES = [
        ('info', 'معلومة (Info)'),
        ('warning', 'تحذير (Warning)'),
        ('success', 'نجاح (Success)'),
    ]
    title = models.CharField(max_length=200, verbose_name="العنوان")
    message = models.TextField(verbose_name="الرسالة")
    type = models.CharField(max_length=20, choices=TYPES, default='info', verbose_name="نوع الإعلان")
    is_active = models.BooleanField(default=True, verbose_name="نشط")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class AIUsageLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم")
    query = models.TextField(verbose_name="السؤال", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - AI Request at {self.created_at}"

class Notification(models.Model):
    TYPES = [
        ('success', 'نجاح'),
        ('warning', 'تحذير'),
        ('info', 'معلومة'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم")
    title = models.CharField(max_length=200, verbose_name="العنوان")
    message = models.TextField(verbose_name="الرسالة")
    type = models.CharField(max_length=20, choices=TYPES, default='info', verbose_name="نوع الإشعار")
    is_read = models.BooleanField(default=False, verbose_name="مقروء")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت الإشعار")

    def __str__(self):
        return f"{self.user.username} - {self.title}"


class AgentMemory(models.Model):
    """Stores long-term vector memory for the AI Agent."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم")
    content = models.TextField(verbose_name="الذكرى")
    embedding = models.JSONField(verbose_name="متجه البحث (Embedding)")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Memory for {self.user.username} at {self.created_at}"


class SalesGoal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم", related_name="sales_goals")
    target_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=5000.0, verbose_name="مستهدف الإيراد")
    target_profit = models.DecimalField(max_digits=12, decimal_places=2, default=1500.0, verbose_name="مستهدف صافي الربح")
    month = models.CharField(max_length=7, verbose_name="الشهر المستهدف", help_text="YYYY-MM")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'month'], name='unique_user_month_goal')
        ]

    def __str__(self):
        return f"{self.user.username} - Goal for {self.month}: {self.target_revenue}"


class AnomalyAlert(models.Model):
    SEVERITIES = [
        ('critical', 'حرج / عالي الخطورة'),
        ('warning', 'تحذير / متوسط'),
        ('info', 'ملحوظة / منخفض'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم", related_name="anomaly_alerts")
    project_file = models.ForeignKey(ProjectFile, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=250, verbose_name="عنوان الشذوذ")
    description = models.TextField(verbose_name="التفاصيل والتأثير")
    recommendation = models.TextField(verbose_name="توصية بصيرة المقترحة", null=True, blank=True)
    severity = models.CharField(max_length=20, choices=SEVERITIES, default='warning')
    metric_name = models.CharField(max_length=100, default="sales", verbose_name="المؤشر المتأثر")
    change_percent = models.FloatField(default=0.0, verbose_name="نسبة التغير")
    is_dismissed = models.BooleanField(default=False, verbose_name="تم التجاهل")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.severity}] {self.title} - {self.user.username}"


class WeeklyDigest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم", related_name="weekly_digests")
    week_label = models.CharField(max_length=50, verbose_name="الأسبوع")
    summary_text = models.TextField(verbose_name="الملخص التنفيذي")
    top_risks = models.JSONField(default=list, verbose_name="أهم المخاطر")
    top_opportunities = models.JSONField(default=list, verbose_name="أهم الفرص")
    action_plan = models.JSONField(default=list, verbose_name="خطة العمل المقترحة")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Weekly Digest ({self.week_label}) - {self.user.username}"


class CustomAgent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم", related_name="custom_agents")
    name = models.CharField(max_length=150, verbose_name="اسم الوكيل")
    role_title = models.CharField(max_length=200, verbose_name="المسمى الوظيفي / التخصص")
    department = models.CharField(max_length=100, default="general", verbose_name="القسم / المجال")
    icon = models.CharField(max_length=50, default="bot", verbose_name="أيقونة الوكيل (Lucide Icon)")
    color = models.CharField(max_length=50, default="indigo", verbose_name="لون الثيم")
    system_prompt = models.TextField(verbose_name="توجيهات النظام ونبرة الحديث")
    knowledge_notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات معرفية ومستندات")
    is_active = models.BooleanField(default=True, verbose_name="مفعل")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.role_title}) - {self.user.username}"


class BoardroomSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم", related_name="boardroom_sessions")
    topic = models.CharField(max_length=300, verbose_name="موضوع أو قرار الجلسة")
    debate_history = models.JSONField(default=list, verbose_name="سجل حوار ومداخلات الوكلاء")
    final_resolution = models.TextField(blank=True, null=True, verbose_name="القرار النهائي المعتمد")
    action_items = models.JSONField(default=list, verbose_name="خطة العمل التنفيذية")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Boardroom Session: {self.topic[:40]} - {self.user.username}"


class CommitteeThread(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم", related_name="committee_threads")
    title = models.CharField(max_length=250, default="محادثة جماعية جديدة", verbose_name="عنوان الجلسة")
    selected_agents = models.JSONField(default=list, verbose_name="الوكلاء المختارون")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="وقت الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")

    def __str__(self):
        return f"{self.title} - {self.user.username}"


class CommitteeMessage(models.Model):
    thread = models.ForeignKey(CommitteeThread, on_delete=models.CASCADE, related_name="messages", verbose_name="جلسة المحادثة")
    role = models.CharField(max_length=20, default="assistant", choices=[("user", "مستخدم"), ("assistant", "وكيل ذكي"), ("system", "نظام")])
    agent_id = models.CharField(max_length=50, default="general", verbose_name="معرف الوكيل")
    agent_name = models.CharField(max_length=150, default="بصيرة", verbose_name="اسم الوكيل")
    agent_avatar = models.CharField(max_length=50, default="sparkles", verbose_name="أيقونة الوكيل")
    agent_color = models.CharField(max_length=50, default="indigo", verbose_name="لون الوكيل")
    content = models.TextField(verbose_name="نص الرسالة")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإرسال")

    def __str__(self):
        return f"{self.agent_name} ({self.role}) in {self.thread_id}"



# --- Decision Platform Models ---

class DecisionMetric(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="decision_metrics")
    name = models.CharField(max_length=255, verbose_name="اسم المؤشر")
    current_value = models.FloatField(default=0.0, verbose_name="القيمة الحالية")
    target_value = models.FloatField(default=0.0, verbose_name="القيمة المستهدفة")
    status = models.CharField(max_length=50, default="stable", choices=[
        ("stable", "مستقر"),
        ("improving", "يتحسن"),
        ("declining", "يتراجع"),
    ])
    last_modified_by_agent = models.CharField(max_length=100, null=True, blank=True)
    last_modified_timestamp = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.user.username}"


class RiskAlert(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="risk_alerts")
    title = models.CharField(max_length=255, verbose_name="عنوان الخطر")
    description = models.TextField(verbose_name="التفاصيل")
    severity = models.CharField(max_length=50, default="high", choices=[
        ("low", "منخفض"),
        ("medium", "متوسط"),
        ("high", "مرتفع"),
        ("critical", "حرج"),
    ])
    status = models.CharField(max_length=50, default="active", choices=[
        ("active", "نشط"),
        ("mitigated", "تم التخفيف"),
        ("resolved", "تم الحل"),
    ])
    resolution_summary = models.TextField(null=True, blank=True)
    last_modified_by_agent = models.CharField(max_length=100, null=True, blank=True)
    last_modified_timestamp = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.status}"


class FinancialLeak(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="financial_leaks")
    source = models.CharField(max_length=255, verbose_name="مصدر الهدر")
    estimated_cost = models.FloatField(default=0.0, verbose_name="التكلفة التقديرية")
    status = models.CharField(max_length=50, default="identified", choices=[
        ("identified", "مكتشف"),
        ("action_taken", "تم اتخاذ إجراء"),
        ("resolved", "تم الإيقاف"),
    ])
    action_taken = models.TextField(null=True, blank=True)
    last_modified_by_agent = models.CharField(max_length=100, null=True, blank=True)
    last_modified_timestamp = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.source} ({self.status})"


class ApprovedPlan(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="المستخدم / User")
    file_name = models.CharField(max_length=255, verbose_name="اسم الملف / File Name")
    file_path = models.CharField(max_length=500, verbose_name="مسار الملف / File Path", default="")
    justification = models.TextField(blank=True, null=True, verbose_name="سبب الاعتماد / Justification")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الاعتماد / Approved At")

    def __str__(self):
        return f"{self.file_name} - {self.user.username}"
