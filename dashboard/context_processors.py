def translation_processor(request):
    # استقبال طلب تغيير اللغة أو استدعاؤه من الجلسة
    lang = request.GET.get("lang")
    if lang in ["ar", "en"]:
        request.session["lang"] = lang
    else:
        lang = request.session.get("lang") or request.COOKIES.get("lang", "ar")

    translations = {
        "ar": {
            "dir": "rtl",
            "lang_code": "ar",
            "align": "right",
            "other_lang": "en",
            "other_lang_name": "ENG",
            "current_lang_name": "AR",
            # Navbar / Common
            "brand": "بصيرة",
            "workspace_label": "مكتبة",
            "datasets_label": "المستندات",
            "reports_label": "التقارير",
            "chat_history_label": "سجل المحادثات",
            "subbrand": "بوابة ذكاء قرارات الأعمال والمحاسبة للمؤسسات العُمانية",
            "home": "الرئيسية",
            "about": "من نحن",
            "agents_nav": "الوكلاء المعتمدون",
            "contact": "اتصل بنا",
            "dashboard_link": "لوحة التحكم",
            "logout_link": "خروج",
            "login_link": "دخول",
            "register_link": "تسجيل مؤسسة",
            "footer_text": "منصة بصيرة الذكية - التحول المالي الرقمي الشامل للمؤسسات",
            # Welcome Page
            "vision_badge": "ذكاء اصطناعي تفاعلي متوافق مع رؤية عمان 2040",
            "hero_title": "بصيرة تكشف لك أين تخسر فلوسك",
            "hero_title_sub": "بدون محاسب",
            "hero_desc": "منصة ذكاء مالي للمنشآت العُمانية: ارفع بياناتك أو صوّر دفترك، وخلال ثوانٍ ترى أكبر تسريب مالي في مشروعك — بالرقم والدليل، وخطوة واحدة لإيقافه. أرقام محسوبة من بياناتك، لا تخمين.",
            "register_today": "ابدأ مجاناً واكتشف أول تسريب",
            "learn_story": "تعرف على قصة المنصة",
            "agents_badge": "الوكلاء المعتمدون",
            "agents_title": "الوكلاء المعتمدون",
            "agents_subtitle": "اكتشف كيف يتعاون وكلاؤنا الأذكياء عبر خط تنسيق متكامل لتحويل بياناتك المالية إلى قرارات استراتيجية استثنائية.",
            "explore_agent_btn": "استكشف الوكيل ✨",
            "connected_agents_lbl": "خط التنسيق المباشر (Orchestration):",
            "key_capabilities_lbl": "القدرات والمهام الرئيسية:",
            "features_title": "ما الذي تقدمه لك منصة بصيرة؟",
            "feat_1_title": "الوكيل المستقل (AI Agentic)",
            "feat_1_desc": "يقوم بتنظيف وفهم بياناتك والتحقق الذاتي من صحة القيود والعمليات الحسابية.",
            "feat_2_title": "لوحة تحكم وتحليلات تفاعلية",
            "feat_2_desc": "خرائط تفاعلية توضح مستويات الأرباح والهدر ونسب المبيعات وتوفير استشارات تفاعلية.",
            "feat_3_title": "كشف الهدر والبنود الخفية",
            "feat_3_desc": "مراقبة التكاليف المنسية مثل المشتريات بدون حساب أو النقد غير الموثق وحماية هوامشك.",
            # Login Page
            "login_title": "تسجيل الدخول لبصيرة",
            "login_desc": "أدخل بيانات اعتمادك لمتابعة ذكاء أعمالك",
            "username_lbl": "اسم المستخدم (يُستخدم لتسجيل الدخول)",
            "username_placeholder": "أدخل اسم المستخدم الخاص بك (مثال: ahmed_2026)",
            "full_name_lbl": "الاسم الكامل لمالك الحساب",
            "full_name_placeholder": "الاسم الكامل (مثال: أحمد بن سعيد المعمري)",
            "password_lbl": "كلمة المرور",
            "login_btn": "دخول آمن",
            "no_account": "ليس لديك حساب بعد؟",
            "register_now": "سجل مؤسستك الآن",
            "reset_title": "إعادة تعيين كلمة المرور",
            "reset_desc": "أدخل اسم المستخدم والسجل التجاري ورقم الهاتف لتحديث كلمة المرور",
            "new_password_lbl": "كلمة المرور الجديدة",
            "reset_btn": "تحديث كلمة المرور",
            "back_login": "العودة لتسجيل الدخول",
            # Register Page
            "reg_title": "تسجيل مؤسسة جديدة",
            "reg_desc": "الربط والتحقق الذاتي برقم السجل التجاري العماني المعتمد",
            "owner_name": "الاسم الكامل لمالك الحساب",
            "company_name": "اسم الشركة / المؤسسة",
            "company_placeholder": "اسم المنشأة أو المشروع التجاري",
            "cr_number": "رقم السجل التجاري (CR)",
            "phone": "رقم الهاتف العُماني المعتمد",
            "email": "البريد الإلكتروني",
            "create_password": "إنشاء كلمة المرور",
            "terms_agree": "أوافق على اتفاقية شروط الاستخدام وحفظ أمن البيانات بالسلطنة.",
            "confirm_register": "تأكيد التسجيل والمتابعة",
            "already_registered": "مسجل بالفعل؟",
            "secure_login": "دخول آمن",

            # Dashboard Page
            "welcome_user": "مرحباً بك",
            "active_project": "مشروعك النشط",
            "cr_label": "السجل التجاري",
            "badge_status": "بصيرة ذكية ومستقرة - داعم المؤسسات العُمانية",
            "kpi_sales": "إجمالي المبيعات المقدرة",
            "kpi_sales_growth": "+12% مقارنة بالشهر السابق",
            "kpi_margin": "هوامش الأرباح الإجمالية",
            "kpi_margin_warn": "زيادة طفيفة بتكاليف الموردين",
            "kpi_warn": "تنبيهات الهدر والسرية",
            "kpi_warn_desc": "تكاليف نقدية غير موثقة (كاش)",
            "kpi_growth": "التنبؤ المالي للنمو",
            "kpi_growth_desc": "بناء على قراءة موسم الصيف بمسقط",
            "upload_title": "معالجة البيانات المحاسبية",
            "upload_desc": "ارفع ملف الإكسيل (Excel) أو جداول المبيعات اليومية لكي يقوم الوكيل بتنظيفها وعرض لوحة تحكم فورية ومكتملة لك.",
            "drag_drop": "اسحب ملف المبيعات هنا أو اضغط للتصفح",
            "supported_formats": "الصيغ المدعومة: .xlsx, .csv",
            "analyze_btn": "تحليل الملف بالذكاء الاصطناعي",
            "history_title": "تاريخ التحليلات المحاسبية",
            "download_doc": "تحميل المستند",
            "no_files": "لم تقم برفع أي ملف بيانات محاسبية حتى الآن.",
            # Contact Us
            "contact_title": "اتصل بنا",
            "contact_desc": "فريق الدعم الفني العُماني جاهز للإجابة عن استفساراتك وتدقيق ملفاتك",
            "channels_title": "قنوات الدعم المباشر بسلطنة عُمان:",
            "whatsapp_support": "دعم الواتساب الفوري",
            "office_location": "موقع المكتب المعتمد",
            "seeb_muscat": "الجامعة الوطنية، فرع الحيل الجنوبية",
            "why_contact_title": "لماذا تتصل بنا؟",
            "why_contact_desc": "إذا واجهت صعوبة في تهيئة جداول الإكسيل الخاصة بمطعمك أو مشروعك، تواصل معنا فوراً وسيقوم مهندسو ومستشارو المنصة بتعديلها وضمان مطابقتها.",
            "name_placeholder": "الاسم الكامل",
            "email_placeholder": "البريد الإلكتروني أو رقم الهاتف",
            "message_placeholder": "كيف يمكننا مساعدتك اليوم؟ اكتب استفسارك...",
            "submit_btn": "إرسال الطلب الفوري",
            # About Us
            "about_title": 'قصة منصة "بصيرة" ورسالتها الوطنية',
            "about_desc": "بصيرة ليست مجرد شات عادي للأسئلة والأجوبة، بل هي عالم محاسبي وبياني متكامل ومستقل. تهدف المنصة إلى معالجة صعوبة وتكاليف توظيف المحاسبين والخبراء الماليين في سلطنة عُمان وتوفير أدوات تمكن رائد الأعمال من حماية مشروعه وتنميته بشكل مستمر ومنع التعثر المالي.",
            "devices_title": " كفاءة العمل وتكامل الأدوار (التقسيم الرباعي الحقيقي للأجهزة)",
            "devices_desc": "تم بناء وبرمجة هذا الهيكل بالاعتماد الكامل على 4 كمبيوترات مخصصة تضمن استقرار وتأمين المنظومة:",
            "dev_1_title": "الجهاز 1: مهندس الذكاء الاصطناعي",
            "dev_1_desc": "صياغة الـ Prompts، معالجة خوارزميات الوكلاء الذاتيين باستخدام Jupyter Notebook والربط بـ n8n.",
            "dev_2_title": "الجهاز 2: مطور الخلفية والأمن",
            "dev_2_desc": "بناء الخادم الآمن بـ ASP.NET Core وإدارة قواعد البيانات لـ PostgreSQL وتأمين البيئة عبر Docker.",
            "dev_3_title": "الجهاز 3: مطور تطبيقات الهواتف",
            "dev_3_desc": "تطوير تطبيق الهواتف التفاعلي بـ Flutter وعرض لوحات البيانات التحليلية المتقدمة لحظياً.",
            "dev_4_title": "الجهاز 4: مصمم واجهات الاستخدام",
            "dev_4_desc": "تصميم الهوية البصرية وشاشات الـ Glassmorphism بدقة فائقة بـ Figma لضمان سهولة الفهم.",
            "vision_title": "التزامنا اللامحدود برؤية عُمان 2040",
            "vision_desc": "نسعى من خلال منصة بصيرة للمساهمة الفعالة في تسريع التحول الرقمي المالي وتقديم استشارات وبيانات حية مستندة إلى واقع السوق العمانية لحماية المؤسسات الصغيرة والمتوسطة من الفشل الإداري والمالي.",
            "lang_indicator_msg": "تم تحديد اللغة العربية بنجاح",
            # Agent & Committee Translations
            "committee_title": "تشكيل اللجنة الذكية",
            "confirm_committee": "تأكيد اختيار اللجنة / بدء محادثة جماعية",
            "ask_basira_title": "اسأل بصيرة",
            "decision_platform": "منصة القرارات",
            "general_agent": "المساعد العام",
            "financial_agent": "الوكيل المالي (CFO)",
            "supply_chain_agent": "سلاسل الإمداد (COO)",
            "pricing_agent": "أخصائي التسعير",
            "audit_agent": "التدقيق ومكافحة الهدر",
            "retention_agent": "استعادة العملاء والولاء",
        },
        "en": {
            "dir": "ltr",
            "lang_code": "en",
            "align": "left",
            "other_lang": "ar",
            "other_lang_name": "AR",
            "current_lang_name": "ENG",
            # Agent & Committee Translations
            "committee_title": "Form Smart Committee",
            "confirm_committee": "Confirm Committee / Start Group Chat",
            "ask_basira_title": "Ask Baseera",
            "decision_platform": "Decision Platform",
            "general_agent": "General Orchestrator",
            "financial_agent": "Financial Analyst (CFO)",
            "supply_chain_agent": "Supply Chain Agent (COO)",
            "pricing_agent": "Pricing Strategist",
            "audit_agent": "Forensic Auditor",
            "retention_agent": "Customer Retention Agent",

            # Navbar / Common
            "brand": "Baseera",
            "workspace_label": "Library",
            "datasets_label": "Documents",
            "reports_label": "Reports",
            "chat_history_label": "Chat History",
            "subbrand": "Omani SME Business Intelligence & Accounting Portal",
            "home": "Home",
            "about": "About Us",
            "agents_nav": "Our Specialized Agents",
            "contact": "Contact",
            "dashboard_link": "Dashboard",
            "logout_link": "Logout",
            "login_link": "Login",
            "register_link": "Register SME",
            "footer_text": "Baseera Smart Platform - Comprehensive SME Digital Financial Transformation",
            # Welcome Page
            "vision_badge": "Interactive AI Aligned with Oman Vision 2040",
            "hero_title": "Baseera shows you where you're losing money",
            "hero_title_sub": "without an accountant",
            "hero_desc": "A financial-intelligence platform for Omani SMEs: upload your data or snap a photo of your ledger, and in seconds see the biggest money leak in your business — with the number and the proof, and one step to stop it. Numbers computed from your data, never guessed.",
            "register_today": "Start free — find your first leak",
            "learn_story": "Discover Our Story",
            "agents_badge": "Our Specialized AI Agents",
            "agents_title": "Our Specialized AI Agents",
            "agents_subtitle": "Discover how our autonomous AI agents collaborate along an orchestration line to transform your financial data.",
            "explore_agent_btn": "Explore Agent ✨",
            "connected_agents_lbl": "Live Orchestration Pipeline:",
            "key_capabilities_lbl": "Core Capabilities & Tasks:",
            "features_title": "What Does Baseera Platform Deliver?",
            "feat_1_title": "Autonomous Agent (AI Agentic)",
            "feat_1_desc": "Cleans, structures, and self-verifies accounting entries and balances dynamically.",
            "feat_2_title": "Interactive Analytics Dashboard",
            "feat_2_desc": "Interactive layouts showing margins, leaks, and sales analytics with integrated consulting chatbot.",
            "feat_3_title": "Leak & Unrecorded Cost Detection",
            "feat_3_desc": "Track unrecorded cash purchases and undocumented worker expenses to protect your margins.",
            # Login Page
            "login_title": "Login to Baseera",
            "login_desc": "Enter credentials to access your business intelligence portal",
            "username_lbl": "Username (Used for Sign In)",
            "username_placeholder": "Enter your username (e.g., ahmed_2026)",
            "full_name_lbl": "Account Owner Full Name",
            "full_name_placeholder": "Full Name (e.g., Ahmed Al-Maamari)",
            "password_lbl": "Password",
            "login_btn": "Secure Login",
            "no_account": "Don't have an account?",
            "register_now": "Register SME Now",
            "reset_title": "Reset Password",
            "reset_desc": "Enter your username, CR, and phone to update your password",
            "new_password_lbl": "New Password",
            "reset_btn": "Update Password",
            "back_login": "Back to Login",
            # Register Page
            "reg_title": "Register New SME",
            "reg_desc": "Verified registration via Omani Commercial Register (CR)",
            "owner_name": "Account Owner Full Name",
            "company_name": "Company / Organization Name",
            "company_placeholder": "SME or Business Project Name",
            "cr_number": "Commercial Register (CR)",
            "phone": "Verified Omani Phone Number",
            "email": "Email Address",
            "create_password": "Create Secure Password",
            "terms_agree": "I agree to the terms of use and data privacy policies of the Sultanate.",
            "confirm_register": "Confirm & Launch Portal",
            "already_registered": "Already registered?",
            "secure_login": "Secure Login",

            # Dashboard Page
            "welcome_user": "Welcome",
            "active_project": "Active SME",
            "cr_label": "CR No.",
            "badge_status": "Stable AI Agent - Omani SME Support Engine",
            "kpi_sales": "Estimated Total Sales",
            "kpi_sales_growth": "+12% compared to last month",
            "kpi_margin": "Gross Profit Margins",
            "kpi_margin_warn": "Slight increase in supplier cost",
            "kpi_warn": "Leakage & Cost Alerts",
            "kpi_warn_desc": "Undocumented cash expenses",
            "kpi_growth": "Predicted Growth Trend",
            "kpi_growth_desc": "Based on Muscat summer analytics",
            "upload_title": "Accounting Data Processor",
            "upload_desc": "Upload your daily sales sheet or Excel file. Our AI Agent will clean it and prepare an interactive dashboard instantly.",
            "drag_drop": "Drag & drop sales file here or click to browse",
            "supported_formats": "Supported formats: .xlsx, .csv",
            "analyze_btn": "Analyze File with AI",
            "history_title": "Accounting Analysis History",
            "download_doc": "Download File",
            "no_files": "You have not uploaded any accounting data files yet.",
            # Contact Us
            "contact_title": "Contact Us",
            "contact_desc": "Our Omani technical support team is ready to answer questions and audit your sheets",
            "channels_title": "Direct Support Channels in Oman:",
            "whatsapp_support": "Instant WhatsApp Support",
            "office_location": "Headquarters Office",
            "seeb_muscat": "National University, South Hail Branch",
            "why_contact_title": "Why Contact Us?",
            "why_contact_desc": "If you face issues formatting your restaurant or business Excel spreadsheets, reach out. Our consultants will modify them for direct compatibility.",
            "name_placeholder": "Full Name",
            "email_placeholder": "Email Address or Phone",
            "message_placeholder": "How can we help you today? Write your message...",
            "submit_btn": "Send Instant Request",
            # About Us
            "about_title": 'The Story of "Baseera" & National Mission',
            "about_desc": "Baseera is not just a standard chatbot; it is a comprehensive, autonomous accounting environment. The platform is designed to overcome the high costs and hurdles of hiring full-time accountants in Oman, empowering founders to shield their startups from early failure.",
            "devices_title": " Operational Efficiency & Quad-Device Collaboration",
            "devices_desc": "This ecosystem was entirely engineered across 4 dedicated computing environments guaranteeing maximum robustness:",
            "dev_1_title": "Device 1: AI Architect",
            "dev_1_desc": "Prompt engineering, autonomous agent design via Jupyter Notebooks, and seamless n8n automation.",
            "dev_2_title": "Device 2: Backend & Security",
            "dev_2_desc": "Engineered with ASP.NET Core, managing PostgreSQL secure storage, isolated with Docker.",
            "dev_3_title": "Device 3: Mobile App Engineer",
            "dev_3_desc": "Developing native interactive screens using Flutter, delivering real-time dashboards.",
            "dev_4_title": "Device 4: UI/UX Specialist",
            "dev_4_desc": "Visual identity curation, Figma glassmorphism design with royal Omani color schemes.",
            "vision_title": "Unyielding Aligned to Oman Vision 2040",
            "vision_desc": "Through the Baseera platform, we actively participate in Oman's financial digital transformation, creating real-time market-conscious metrics to shield local SMEs.",
            "lang_indicator_msg": "Language switched to English",
        },
    }
    return {"trans": translations.get(lang, translations["ar"])}


from dashboard.models import Notification, AnomalyAlert

def notifications_processor(request):
    if request.user.is_authenticated:
        recent_notifications = Notification.objects.filter(user=request.user).order_by('-created_at')[:3]
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        try:
            anomaly_alerts = list(AnomalyAlert.objects.filter(user=request.user, is_dismissed=False).order_by('-created_at')[:5])
        except Exception:
            anomaly_alerts = []
        total_unread = unread_count + len(anomaly_alerts)
        return {
            'recent_notifications': recent_notifications,
            'unread_notifications_count': total_unread,
            'anomaly_alerts': anomaly_alerts,
        }
    return {
        'recent_notifications': [],
        'unread_notifications_count': 0,
        'anomaly_alerts': [],
    }


def trial_processor(request):
    if request.user.is_authenticated:
        # Super admin / staff bypass
        if request.user.is_staff or request.user.is_superuser or request.user.username == 'admin':
            return {
                'trial_info': {
                    'days_remaining': 999,
                    'days_passed': 0,
                    'percentage_remaining': 100,
                    'percentage_used': 0,
                    'is_expired': False,
                    'is_subscribed': True,
                    'plan': 'المسؤول الفائق (Super Admin)',
                }
            }

        try:
            from dashboard.models import Profile
            profile, _ = Profile.objects.get_or_create(user=request.user)
            trial_info = profile.get_trial_info()
        except Exception:
            trial_info = {
                'days_remaining': 14,
                'days_passed': 0,
                'percentage_remaining': 100,
                'percentage_used': 0,
                'is_expired': False,
                'is_subscribed': False,
                'plan': 'trial',
            }
        return {'trial_info': trial_info}

    return {
        'trial_info': {
            'days_remaining': 14,
            'days_passed': 0,
            'percentage_remaining': 100,
            'percentage_used': 0,
            'is_expired': False,
            'is_subscribed': False,
            'plan': 'trial',
        }
    }

