import os
import json
import logging
import pandas as pd
from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from .security import build_safe_filename, validate_uploaded_file, rate_limit, safe_error_message, validate_ssrf_url, sanitize_cell_for_prompt
from .models import Profile, ProjectFile, SystemLog, Invoice, Announcement, AIUsageLog, SalesGoal, AnomalyAlert, WeeklyDigest, CustomAgent, BoardroomSession

logger = logging.getLogger(__name__)


def welcome(request):
    if request.user.is_authenticated:
        lang = request.session.get('lang') or request.COOKIES.get('lang', 'ar')
        if lang == 'ar':
            messages.success(request, f"أهلاً بك من جديد يا {request.user.username}! حلّل بياناتك وتأكد منها، فهذا أهم شيء.")
        else:
            messages.success(request, f"Welcome back, {request.user.username}! Analyze and verify your data, that's the most important thing.")
        if request.user.is_staff or request.user.is_superuser or request.user.username == "admin":
            return redirect("admin_dashboard")
        return redirect("dashboard")
        
    return render(request, "dashboard/welcome.html")


from django.db import IntegrityError


@rate_limit(requests_per_minute=10, key_prefix="web_register")
@rate_limit(requests_per_minute=5, key_prefix="register")
def user_register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
        
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        cr_number = request.POST.get("cr_number", "").strip()
        company_name = request.POST.get("company_name", "").strip()
        project_type = request.POST.get("project_type", "other")
        password = request.POST.get("password", "")

        # --- Validation ---
        if not username or not password:
            messages.error(
                request,
                "اسم المستخدم (Login ID) وكلمة المرور مطلوبان / Username and password are required.",
            )
            return redirect("register")

        if User.objects.filter(username=username).exists():
            messages.error(
                request,
                f"اسم المستخدم '{username}' مسجل مسبقاً. جرب اسماً آخر / Username '{username}' is already taken.",
            )
            return redirect("register")

        if email and User.objects.filter(email=email).exists():
            messages.error(
                request, "البريد الإلكتروني مسجل مسبقاً / Email is already in use."
            )
            return redirect("register")

        # --- Create ---
        try:
            from django.db import transaction
            from django.utils import timezone
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=full_name
                )
                Profile.objects.create(
                    user=user,
                    phone_number=phone,
                    commercial_register=cr_number,
                    company_name=company_name,
                    project_type=project_type,
                    trial_start_date=timezone.now(),
                )
            login(request, user)
            
            SystemLog.objects.create(
                user=user,
                action_type="تسجيل حساب جديد / Register",
                details=f"سجل المستخدم {username} حساباً جديداً كقطاع {project_type}."
            )
            
            messages.success(
                request,
                f"مرحباً بك {username}! 🎉 تم تسجيل مؤسستك بنجاح. اختر مصدر بياناتك للبدء."
            )
            return redirect("portal")

        except IntegrityError:
            messages.error(
                request,
                "حدث خطأ أثناء التسجيل، اسم المستخدم أو البريد مستخدم مسبقاً / Registration failed: duplicate username or email.",
            )
            return redirect("register")

    return render(request, "dashboard/register.html")


@rate_limit(requests_per_minute=10, key_prefix="web_login")
@rate_limit(requests_per_minute=5, key_prefix="login")
def user_login(request):
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser or request.user.username == "admin":
            return redirect("admin_dashboard")
        return redirect("dashboard")
        
    if request.method == "POST":
        login_input = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=login_input, password=password)
        
        if user is None and '@' in login_input:
            user_obj = User.objects.filter(email=login_input).first()
            if user_obj:
                user = authenticate(request, username=user_obj.username, password=password)

        if user is not None:
            login(request, user)
            
            SystemLog.objects.create(
                user=user,
                action_type="تسجيل دخول / Login",
                details=f"قام المستخدم {user.username} بتسجيل الدخول بنجاح."
            )
            
            messages.success(request, f"مرحباً بعودتك / Welcome back, {user.username}!")
            if user.is_staff or user.is_superuser or user.username == "admin":
                return redirect("admin_dashboard")
            return redirect("dashboard")
        else:
            messages.error(request, "خطأ في البيانات تأكد من اسم المستخدم وكلمة المرور / Invalid credentials.")

    return render(request, "dashboard/login.html")


def admin_login(request):
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser or request.user.username == "admin":
            return redirect("admin_dashboard")
        messages.warning(request, "أنت مسجل كحساب مستخدم عادي. يرجى تسجيل الدخول بحساب مسؤول للوصول إلى لوحة الإدارة.")
        return redirect("dashboard")

    if request.method == "POST":
        login_input = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=login_input, password=password)
        if user is None and "@" in login_input:
            user_obj = User.objects.filter(email=login_input).first()
            if user_obj:
                user = authenticate(request, username=user_obj.username, password=password)

        if user is not None:
            if user.is_staff or user.is_superuser or user.username == "admin":
                login(request, user)
                SystemLog.objects.create(
                    user=user,
                    action_type="تسجيل دخول الإدارة / Admin Login",
                    details=f"قام مدير النظام {user.username} بتسجيل الدخول إلى بوابة المشرف."
                )
                messages.success(request, f"مرحباً بك يا مدير النظام {user.username}! تم تفعيل لوحة الإشراف.")
                return redirect("admin_dashboard")
            else:
                messages.error(request, "عذراً، هذا الحساب ليس لديه صلاحيات الإدارة (Super Admin). يرجى الدخول من بوابة المستخدمين والشركات.")
        else:
            messages.error(request, "خطأ في بيانات الاعتماد. يرجى التأكد من اسم مستخدم الإدارة وكلمة المرور.")

    return render(request, "dashboard/admin_login.html")

def social_login_dummy(request, provider):
    if request.user.is_authenticated:
        return redirect("dashboard")
        
    username = f"{provider.lower()}_user"
    email = f"{username}@example.com"
    
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        user = User.objects.create_user(username=username, email=email, password="social_password")
        Profile.objects.create(
            user=user,
            company_name=f"{provider.capitalize()} User",
            project_type="other"
        )
        SystemLog.objects.create(
            user=user,
            action_type="دخول اجتماعي / Social Login",
            details=f"سجل المستخدم عبر {provider}."
        )
        
    login(request, user)
    messages.success(request, f"تم الدخول بنجاح عبر {provider.capitalize()} / Logged in via {provider.capitalize()}!")
    return redirect("dashboard")

def user_logout(request):
    logout(request)
    messages.info(request, "تم تسجيل خروجك بنجاح / Logged out successfully.")
    return redirect("welcome")


@login_required
def portal(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    if request.method == "POST" and request.FILES.get("excel_file"):
        excel = request.FILES.get("excel_file")
        try:
            from dashboard.services.validation_service import validate_financial_file
            validation = validate_financial_file(excel)
            is_valid, status, msg = validation["is_valid"], validation["status"], validation["message"]
            accepted_sheets = validation["accepted_sheets"]
            if not is_valid:
                messages.error(request, msg)
                return redirect("portal")
            elif status == "warning":
                messages.warning(request, msg)
        except Exception as exc:
            messages.error(request, f"حدث خطأ أثناء فحص الملف: {str(exc)}")
            return redirect("portal")

        excel.seek(0)

        excel.name = build_safe_filename(excel.name)
        project_file = ProjectFile.objects.create(user=request.user, excel_file=excel)
        success, error_msg = process_excel_to_db(
            project_file, request.user, accepted_sheets,
            extracted_rows=validation.get("extracted_rows"),
        )
        if success:
            request.session['active_file_id'] = project_file.id
            try:
                from dashboard.services.retrieval_service import index_accepted_sheets
                index_accepted_sheets(project_file, accepted_sheets)
            except Exception as idx_err:
                print(f"Retrieval indexing error: {idx_err}")

            SystemLog.objects.create(
                user=request.user,
                action_type="رفع ملف / Upload",
                details=f"تم رفع وتحليل ملف جديد برقم {project_file.id}."
            )
            
            messages.success(
                request,
                "تم رفع وتحليل المستند بنجاح! / File uploaded and analyzed successfully!",
            )
            return redirect("dashboard")
        else:
            project_file.delete()
            messages.error(
                request,
                f"فشل في قراءة الملف. يرجى التأكد من صحة البيانات. التفاصيل: {error_msg}",
            )
            return redirect("portal")

    context = {
        "profile": profile,
    }
    return render(request, "dashboard/portal.html", context)


import json
import pandas as pd
import os
import datetime
from .models import Profile, ProjectFile, DynamicRecord, CommitteeThread, CommitteeMessage

import hashlib
import json

def parse_pdf_to_df(file_path):
    import importlib
    try:
        pypdf = importlib.import_module("pypdf")
    except Exception:
        pypdf = None
    import re
    import pandas as pd

    if not os.path.exists(file_path) or pypdf is None:
        return None

    try:
        reader = pypdf.PdfReader(file_path)
        all_text_lines = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                for line in text.split('\n'):
                    cleaned = line.strip()
                    if cleaned:
                        all_text_lines.append(cleaned)

        if not all_text_lines:
            return None

        # Pattern 1: Date Description Amount [Type]
        parsed_rows = []
        pattern = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+(.*?)\s+([\d\.,]+)\s*([A-Za-z]+)?$')

        for line in all_text_lines:
            match = pattern.match(line)
            if match:
                date_str, desc, amount_str, type_str = match.groups()
                try:
                    amount_val = float(amount_str.replace(',', ''))
                except ValueError:
                    amount_val = amount_str
                parsed_rows.append({
                    "التاريخ": date_str,
                    "البيان / الوصف": desc,
                    "المبلغ": amount_val,
                    "النوع": type_str or ("إيراد" if any(w in line for w in ["Income", "Deposit", "Sales"]) else "مصروف")
                })

        if parsed_rows:
            return pd.DataFrame(parsed_rows)

        # Pattern 2: Tabular split by 2+ spaces or tabs
        tabular_rows = []
        for line in all_text_lines:
            parts = re.split(r'\s{2,}|\t', line)
            if len(parts) >= 2:
                tabular_rows.append(parts)

        if len(tabular_rows) > 1:
            header = tabular_rows[0]
            data = tabular_rows[1:]
            cleaned_data = []
            for r in data:
                row_dict = {}
                for idx, val in enumerate(r):
                    col_name = header[idx] if idx < len(header) else f"Column_{idx+1}"
                    try:
                        val_num = float(val.replace(',', ''))
                        row_dict[col_name] = val_num
                    except (ValueError, AttributeError):
                        row_dict[col_name] = val
                cleaned_data.append(row_dict)
            if cleaned_data:
                return pd.DataFrame(cleaned_data)

        # Fallback 3: Generic text line extraction
        generic_rows = []
        for idx, line in enumerate(all_text_lines):
            numbers = re.findall(r'[-+]?\d*\.\d+|\d+', line)
            val = float(numbers[0]) if numbers else None
            generic_rows.append({
                "رقم السطر": idx + 1,
                "المحتوى": line,
                "القيمة المالية": val
            })
        return pd.DataFrame(generic_rows)
    except Exception as e:
        print(f"Error parsing PDF file: {e}")
        return None

def process_excel_to_db(project_file, user, accepted_sheets=None, extracted_rows=None):
    try:
        file_path = project_file.excel_file.path
        if not os.path.exists(file_path):
            return False, "الملف غير موجود."

        ext = os.path.splitext(file_path)[1].lower()
        allowed_exts = ['.csv', '.xlsx', '.xls', '.pdf', '.txt', '.png', '.jpg', '.jpeg']
        if ext not in allowed_exts:
            return False, "صيغة الملف غير مدعومة. يرجى رفع ملف بصيغة (CSV, Excel, PDF, Images)."

        # File size check: 20 MB limit
        max_bytes = 20 * 1024 * 1024
        if os.path.getsize(file_path) > max_bytes:
            return False, "حجم الملف يتجاوز الحد الأقصى (20 ميجابايت). يرجى اختيار ملف أصغر."

        df = None
        if ext == '.csv':
            for enc in ['utf-8', 'utf-8-sig', 'latin1', 'cp1256', 'iso-8859-1']:
                try:
                    import pandas as pd
                    df = pd.read_csv(file_path, encoding=enc)
                    break
                except Exception:
                    continue
        elif ext in ['.xlsx', '.xls']:
            try:
                import pandas as pd
                if accepted_sheets and len(accepted_sheets) > 0:
                    dfs = []
                    xls = pd.ExcelFile(file_path)
                    for sheet in accepted_sheets:
                        dfs.append(xls.parse(sheet))
                    df = pd.concat(dfs, ignore_index=True)
                else:
                    df = pd.read_excel(file_path)
            except Exception as ex_err:
                print(f"Excel read error: {ex_err}")
                df = read_file_to_df(file_path)
        elif ext == '.pdf':
            # 1. Native PDF parser
            df = parse_pdf_to_df(file_path)
            # 2. AI Extractor fallback
            if df is None or df.empty:
                try:
                    from dashboard.services.ai_service import GeminiAIService
                    import pandas as pd
                    ai_service = GeminiAIService()
                    extracted_data = ai_service.extract_structured_data_from_file(file_path)
                    if extracted_data and isinstance(extracted_data, list) and len(extracted_data) > 0:
                        df = pd.DataFrame(extracted_data)
                except Exception as ai_pdf_err:
                    print(f"AI PDF extract error: {ai_pdf_err}")
        elif ext == '.txt':
            # 1. Text table parser
            df = read_file_to_df(file_path)
            # 2. AI Text extractor
            if df is None or df.empty:
                try:
                    from dashboard.services.ai_service import GeminiAIService
                    import pandas as pd
                    ai_service = GeminiAIService()
                    extracted_data = ai_service.extract_structured_data_from_file(file_path)
                    if extracted_data and isinstance(extracted_data, list) and len(extracted_data) > 0:
                        df = pd.DataFrame(extracted_data)
                except Exception as ai_txt_err:
                    print(f"AI TXT extract error: {ai_txt_err}")
        elif ext in ['.png', '.jpg', '.jpeg']:
            import pandas as pd
            # The Validation Layer (validate_financial_file) already ran the
            # financial vision OCR pass before this file was ever saved --
            # reuse those rows instead of paying for a second AI call, and
            # never fall back to a placeholder row with no real numbers in
            # it (a real image only reaches here once validation confirmed
            # it had genuine extractable financial content).
            if extracted_rows:
                df = pd.DataFrame(extracted_rows)
            else:
                try:
                    from dashboard.services.ai_service import GeminiAIService
                    ai_service = GeminiAIService()
                    extracted_data = ai_service.extract_structured_data_from_file(file_path)
                    if extracted_data and isinstance(extracted_data, list) and len(extracted_data) > 0:
                        df = pd.DataFrame(extracted_data)
                except Exception as img_err:
                    print(f"Image extract error: {img_err}")
        else:
            df = read_file_to_df(file_path)

        if df is None or df.empty:
            return False, "الملف فارغ أو يتعذر استخراج البيانات منه. يرجى التأكد من محتوى الملف."

        # Clean dataframe columns and create a schema hash
        df.columns = df.columns.astype(str).str.strip()
        columns_tuple = tuple(df.columns.tolist())
        schema_hash = hashlib.md5(",".join(columns_tuple).encode('utf-8')).hexdigest()
        
        # Use pandas to_json to handle all datetime/NaN conversions safely
        records_json = json.loads(df.to_json(orient='records', date_format='iso'))
        
        records_to_create = []
        for row_dict in records_json:
            records_to_create.append(DynamicRecord(
                user=user,
                project_file=project_file,
                schema_hash=schema_hash,
                row_data=row_dict
            ))

        if records_to_create:
            DynamicRecord.objects.bulk_create(records_to_create)
            try:
                from .services.anomaly_detector import AnomalyDetector
                AnomalyDetector.detect_anomalies(records_json, user, project_file)
            except Exception as an_err:
                print(f"Anomaly detection error: {an_err}")
            try:
                from .services.ai_service import GeminiAIService
                sample_str = json.dumps(records_json[:100], ensure_ascii=False)
                GeminiAIService().generate_weekly_digest_for_user(sample_str, user)
            except Exception as digest_err:
                print(f"Digest generation error: {digest_err}")
        return True, None
    except Exception as e:
        error_details = str(e)
        print(f"Failed to process file to DB: {error_details}")
        if "openpyxl" in error_details:
            error_details = "مكتبة قراءة الإكسل غير متوفرة (openpyxl)."
        return False, error_details

def read_file_to_df(file_path):
    if not os.path.exists(file_path):
        return None

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return parse_pdf_to_df(file_path)

    if ext in (".xlsx", ".xls"):
        try:
            return pd.read_excel(file_path)
        except Exception:
            pass

    try:
        return pd.read_csv(file_path)
    except Exception:
        pass

    try:
        return pd.read_csv(file_path, encoding="utf-8-sig")
    except Exception:
        pass

    try:
        return pd.read_csv(file_path, encoding="latin1")
    except Exception:
        pass

    return None


def all_agents_gallery(request):
    return render(request, "dashboard/all_agents.html")

@login_required
def agents_hub(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    custom_agents = CustomAgent.objects.filter(user=request.user, is_active=True).order_by("-created_at")
    context = {
        "profile": profile,
        "custom_agents": custom_agents,
    }
    return render(request, "dashboard/agents_hub.html", context)

@login_required
def dashboard(request):
    view_mode = request.GET.get("view")
    if view_mode == "user":
        request.session["admin_view_mode"] = "user"
    elif view_mode == "admin":
        request.session.pop("admin_view_mode", None)
        return redirect("admin_dashboard")

    if (request.user.is_staff or request.user.is_superuser or request.user.username == "admin") and not request.session.get("impersonated_from") and request.session.get("admin_view_mode") != "user":
        return redirect("admin_dashboard")

    profile, _ = Profile.objects.get_or_create(user=request.user)
    files = ProjectFile.objects.filter(user=request.user).order_by("-uploaded_at")

    latest_file_json = None
    if files.exists():
        file_id = request.GET.get("file_id")
        
        # If no file_id is provided, default to workspace summary ("all")
        if not file_id:
            file_id = "all"
        
        # Optional: Keep session updated if they did provide a specific file_id
        if file_id != "all":
            request.session["active_file_id"] = file_id
        else:
            if "active_file_id" in request.session:
                del request.session["active_file_id"]

        if file_id == "all" or str(file_id) == "all":
            latest = None
        else:
            if file_id and files.filter(id=file_id).exists():
                latest = files.get(id=file_id)
            else:
                latest = None

        try:
            if latest:
                # File specific dashboard
                all_records = DynamicRecord.objects.filter(user=request.user, project_file=latest)
                if all_records.exists():
                    records_list = list(all_records.values_list('row_data', flat=True)[:10000])
                    df = pd.DataFrame(records_list)
                    df = df.where(pd.notnull(df), None)
                    latest_file_json = {
                        "fileName": latest.excel_file.name.split('/')[-1] if latest.excel_file else "ملف",
                        "sizeKB": len(records_list),
                        "columns": df.columns.tolist(),
                        "rows": records_list,
                        "csvUrl": "/api/user-data.csv",
                        "uploadedAt": latest.uploaded_at.isoformat() if latest.uploaded_at else datetime.datetime.now().isoformat(),
                    }
            else:
                # All files dashboard - load all actual records
                all_records = DynamicRecord.objects.filter(user=request.user)
                if all_records.exists():
                    records_list = list(all_records.values_list('row_data', flat=True)[:10000])
                    df = pd.DataFrame(records_list)
                    df = df.where(pd.notnull(df), None)
                    first_file = files.first()
                    display_name = first_file.excel_file.name.split('/')[-1] if (first_file and first_file.excel_file) else "البيانات المسجلة"
                    if files.count() > 1:
                        display_name = f"تجميع البيانات ({files.count()} ملفات)"
                    latest_file_json = {
                        "fileName": display_name,
                        "sizeKB": len(records_list),
                        "columns": df.columns.tolist(),
                        "rows": records_list,
                        "csvUrl": "/api/user-data.csv",
                        "uploadedAt": first_file.uploaded_at.isoformat() if (first_file and first_file.uploaded_at) else datetime.datetime.now().isoformat(),
                    }
        except Exception as e:
            print(f"Error parsing data: {e}")

    dynamic_count = files.count()
    if dynamic_count > 0:
        kpis = {
            "total_sales_ar": "يتم الحساب...",
            "total_sales_en": "Calculating...",
            "profit_margin": "...",
            "warnings_count_ar": "0 تنبيهات",
            "warnings_count_en": "0 Alerts",
            "predicted_growth_ar": "يتم الحساب...",
            "predicted_growth_en": "Calculating...",
        }
    else:
        # Fallback to zero KPIs if no data
        kpis = {
            "total_sales_ar": "0 ر.ع.",
            "total_sales_en": "0 OMR",
            "profit_margin": "0%",
            "warnings_count_ar": "0 تنبيهات",
            "warnings_count_en": "0 Alerts",
            "predicted_growth_ar": "0%",
            "predicted_growth_en": "0%",
        }

    active_announcements = Announcement.objects.filter(is_active=True).order_by("-created_at")

    from .models import AgentMemory, Notification, SalesGoal, AnomalyAlert, WeeklyDigest
    from .services.anomaly_detector import AnomalyDetector

    now = datetime.datetime.now()
    current_month_str = now.strftime("%Y-%m")
    sales_goal, _ = SalesGoal.objects.get_or_create(
        user=request.user,
        month=current_month_str,
        defaults={"target_revenue": 5000.0, "target_profit": 1500.0}
    )

    # Fetch active anomalies
    anomaly_alerts = AnomalyAlert.objects.filter(user=request.user, is_dismissed=False).order_by('-created_at')[:4]
    
    # If no anomalies recorded yet, run detector
    if not anomaly_alerts.exists():
        all_records = DynamicRecord.objects.filter(user=request.user)
        if all_records.exists():
            records_list = list(all_records.values_list('row_data', flat=True)[:10000])
            AnomalyDetector.detect_anomalies(records_list, request.user)
            anomaly_alerts = AnomalyAlert.objects.filter(user=request.user, is_dismissed=False).order_by('-created_at')[:4]

    # Weekly Digest (Business Pulse Report)
    weekly_digest = WeeklyDigest.objects.filter(user=request.user).order_by('-created_at').first()
    if not weekly_digest and files.exists():
        all_records = DynamicRecord.objects.filter(user=request.user)
        if all_records.exists():
            records_list = list(all_records.values_list('row_data', flat=True)[:100])
            sample_str = json.dumps(records_list, ensure_ascii=False)
            try:
                from .services.ai_service import GeminiAIService
                ai_service = GeminiAIService()
                weekly_digest = ai_service.generate_weekly_digest_for_user(sample_str, request.user)
            except Exception as digest_err:
                print(f"Error auto-generating weekly digest: {digest_err}")

    agent_memories = AgentMemory.objects.filter(user=request.user).order_by('-created_at')[:3]
    agent_notifs = Notification.objects.filter(user=request.user).order_by('-created_at')[:3]
    
    agent_activity = []
    for m in agent_memories:
        agent_activity.append({'type': 'memory', 'text': m.content, 'date': m.created_at})
    for n in agent_notifs:
        agent_activity.append({'type': 'notification', 'text': n.message, 'date': n.created_at})
        
    agent_activity = sorted(agent_activity, key=lambda x: x['date'], reverse=True)[:5]

    from .models import ApprovedPlan
    approved_plans = ApprovedPlan.objects.filter(user=request.user).order_by('-created_at')[:5]

    context = {
        "profile": profile,
        "files": files,
        "kpis": kpis,
        "announcements": active_announcements,
        "latest_file_json": json.dumps(latest_file_json) if latest_file_json else None,
        "agent_activity": agent_activity,
        "sales_goal": sales_goal,
        "anomaly_alerts": anomaly_alerts,
        "weekly_digest": weekly_digest,
        "approved_plans": approved_plans,
    }
    return render(request, "dashboard/dashboard.html", context)


def contact(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        email = request.POST.get("contact_info", request.POST.get("email", "")).strip()
        message = request.POST.get("message", "").strip()
        feedback_type = request.POST.get("feedback_type", "suggestion")
        
        from .models import UserFeedback
        UserFeedback.objects.create(
            user=request.user if request.user.is_authenticated else None,
            feedback_type=feedback_type,
            email=email if email else "no-email@baseera.om",
            message=f"[{name}] {message}"
        )
        
        messages.success(
            request,
            f"شكراً {name}! تم استلام رسالتك وملاحظتك بنجاح وسيتم مراجعتها من قبل الإدارة. / Thank you {name}! Received successfully.",
        )
        return redirect("contact")

    return render(request, "dashboard/contact.html")


@login_required
@rate_limit(requests_per_minute=30, key_prefix="sales_goal")
def api_update_sales_goal(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            month = data.get("month", datetime.datetime.now().strftime("%Y-%m"))
            target_revenue = float(data.get("target_revenue", 5000))
            target_profit = float(data.get("target_profit", 1500))

            from .models import SalesGoal
            goal, created = SalesGoal.objects.update_or_create(
                user=request.user,
                month=month,
                defaults={
                    "target_revenue": target_revenue,
                    "target_profit": target_profit,
                }
            )
            return JsonResponse({"status": "success", "target_revenue": float(goal.target_revenue), "target_profit": float(goal.target_profit)})
        except Exception:
            return JsonResponse({"status": "error", "message": "Invalid sales goal payload"}, status=400)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


@login_required
@rate_limit(requests_per_minute=15, key_prefix="analyze_waste")
def api_analyze_waste(request):
    """
    Real AI-driven waste detection (spec: 'الذكاء الاصطناعي يحلل الملف بناءً
    عليه يكشف الهدر'). Only called from the dashboard when the uploaded file
    has no literal waste/loss column -- the deterministic pre-pass in
    waste_analyzer infers leakage from price/cost/quantity patterns instead,
    and the LLM only interprets those verified numbers, never invents its own.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        data = {}

    file_id = data.get("file_id")

    from .models import DynamicRecord, CompanyStrategicProfile
    from .services.waste_analyzer import analyze_waste

    records_qs = DynamicRecord.objects.filter(user=request.user)
    if file_id and str(file_id) != "all":
        records_qs = records_qs.filter(project_file_id=file_id)
    ROW_CAP = 10000
    total_rows = records_qs.count()
    rows = list(records_qs.values_list("row_data", flat=True)[:ROW_CAP])

    lang = request.session.get("lang") or request.COOKIES.get("lang", "ar")

    ai_service = None
    try:
        from .services.ai_service import GeminiAIService
        ai_service = GeminiAIService()
    except Exception as e:
        print(f"Waste analysis: AI service unavailable, using deterministic-only mode: {e}")

    company_profile = CompanyStrategicProfile.objects.filter(user=request.user).first()

    try:
        result = analyze_waste(rows, ai_service=ai_service, lang=lang, company_profile=company_profile)
        result["status"] = "success"
        # Task 5 (UI transparency): tell the frontend outright when the
        # analysis only covered a prefix of the data, instead of silently
        # presenting a partial result as if it covered everything.
        result["analyzed_rows"] = len(rows)
        result["total_rows"] = total_rows
        result["truncated"] = total_rows > len(rows)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)


@login_required
@rate_limit(requests_per_minute=15, key_prefix="escalation_chain")
def api_escalation_chain(request):
    """
    Proactive, conditional multi-agent escalation chain (architecture spec's
    collaboration-chain example): Audit -> Financial -> (conditional) Supply
    Chain -> (conditional) Pricing, each stage grounded in real deterministic
    findings from the user's own uploaded data. See
    dashboard/services/agent_escalation_chain.py for the full contract.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        data = {}

    file_id = data.get("file_id")

    from .models import DynamicRecord
    from .services.agent_escalation_chain import run_escalation_chain

    records_qs = DynamicRecord.objects.filter(user=request.user)
    if file_id and str(file_id) != "all":
        records_qs = records_qs.filter(project_file_id=file_id)
    ROW_CAP = 10000
    total_rows = records_qs.count()
    rows = list(records_qs.values_list("row_data", flat=True)[:ROW_CAP])

    lang = request.session.get("lang") or request.COOKIES.get("lang", "ar")

    ai_service = None
    try:
        from .services.ai_service import GeminiAIService
        ai_service = GeminiAIService()
    except Exception as e:
        print(f"Escalation chain: AI service unavailable, using deterministic-only mode: {e}")

    try:
        result = run_escalation_chain(rows, ai_service=ai_service, lang=lang)
        result["status"] = "success"
        # Task 5 (UI transparency): tell the frontend outright when the
        # analysis only covered a prefix of the data, instead of silently
        # presenting a partial result as if it covered everything.
        result["analyzed_rows"] = len(rows)
        result["total_rows"] = total_rows
        result["truncated"] = total_rows > len(rows)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)


@login_required
@rate_limit(requests_per_minute=30, key_prefix="dismiss_anomaly")
def api_dismiss_anomaly(request, alert_id):
    from .models import AnomalyAlert
    try:
        alert = AnomalyAlert.objects.get(id=alert_id, user=request.user)
        alert.is_dismissed = True
        alert.save()
        return JsonResponse({"status": "success"})
    except AnomalyAlert.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Alert not found"}, status=404)
    except Exception:
        return JsonResponse({"status": "error", "message": "Unable to dismiss alert"}, status=400)


@login_required
@rate_limit(requests_per_minute=30, key_prefix="delete_notification")
def api_delete_notification(request, notif_id):
    from .models import Notification
    try:
        notif = Notification.objects.get(id=notif_id, user=request.user)
        notif.delete()
        return JsonResponse({"status": "success"})
    except Notification.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Notification not found"}, status=404)
    except Exception:
        return JsonResponse({"status": "error", "message": "Unable to delete notification"}, status=400)


@login_required
@rate_limit(requests_per_minute=30, key_prefix="mark_notification_read")
def api_mark_notifications_read(request):
    from .models import Notification
    try:
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return JsonResponse({"status": "success"})
    except Exception:
        return JsonResponse({"status": "error", "message": "Unable to update notifications"}, status=400)


@login_required
def api_get_weekly_digest(request):
    from .models import WeeklyDigest
    from .utils import translate_digest_item
    digest = WeeklyDigest.objects.filter(user=request.user).order_by('-created_at').first()
    lang = request.session.get("lang") or request.COOKIES.get("lang", "ar")
    if digest:
        risks = [translate_digest_item(r) if lang == 'en' else r for r in digest.top_risks]
        actions = [translate_digest_item(a) if lang == 'en' else a for a in digest.action_plan]
        summary = translate_digest_item(digest.summary_text) if lang == 'en' else digest.summary_text
        return JsonResponse({
            "status": "success",
            "week_label": digest.week_label,
            "summary_text": summary,
            "top_risks": risks,
            "top_opportunities": digest.top_opportunities,
            "action_plan": actions,
        })
    return JsonResponse({"status": "error", "message": "No digest available"}, status=404)


def about(request):
    return render(request, "dashboard/about.html")


@login_required
def ask_basira(request):
    agent_id = request.GET.get("agent_id", "general")
    custom_agent_info = None
    custom_agents_list = []
    custom_agents_data = []
    if request.user.is_authenticated:
        custom_agents_list = list(CustomAgent.objects.filter(user=request.user, is_active=True))
        for ca in custom_agents_list:
            custom_agents_data.append({
                "id": f"custom_{ca.id}",
                "name": ca.name,
                "icon": getattr(ca, "icon", "bot") or "bot",
                "color": getattr(ca, "color", "indigo") or "indigo"
            })
    if str(agent_id).startswith("custom_"):
        try:
            cid = int(str(agent_id).replace("custom_", ""))
            custom_agent_info = CustomAgent.objects.get(id=cid, user=request.user)
        except Exception:
            pass
    return render(request, "dashboard/ask_basira.html", {
        "agent_id": agent_id,
        "custom_agent": custom_agent_info,
        "custom_agents_list": custom_agents_list,
        "custom_agents_json": json.dumps(custom_agents_data, ensure_ascii=False)
    })




@login_required
def boardroom_view(request):
    """
    Multi-Agent Executive Boardroom View (غرفة اجتماعات مجلس الإدارة متعدد الوكلاء)
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    recent_sessions = BoardroomSession.objects.filter(user=request.user).order_by("-created_at")[:10]
    return render(request, "dashboard/boardroom.html", {
        "profile": profile,
        "recent_sessions": recent_sessions
    })


def api_boardroom_debate(request):
    """
    API to simulate a live multi-agent debate on a business decision.
    API to simulate a live multi-agent debate on a business decision.
    """
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode('utf-8'))
            topic = data.get("topic", "").strip()
            file_context = data.get("file_context", "")

            # Build a comprehensive workspace summary
            from django.contrib.auth.models import User
            user = request.user if request.user.is_authenticated else User.objects.first()
            
            workspace_context = f"The user has the following data files in their workspace:\n"
            if user:
                files = ProjectFile.objects.filter(user=user).order_by('-uploaded_at')[:5]
                for f in files:
                    rcount = DynamicRecord.objects.filter(project_file=f).count()
                    workspace_context += f"- File: {f.excel_file.name.split('/')[-1] if f.excel_file else f.id}, Records: {rcount}, Uploaded: {f.uploaded_at.strftime('%Y-%m-%d %H:%M') if f.uploaded_at else 'Unknown'}\n"
                
                # Fetch a small sample from the latest file to give agents concrete data
                latest_file = files.first()
                if latest_file:
                    sample_records = list(DynamicRecord.objects.filter(project_file=latest_file).values_list('row_data', flat=True)[:3])
                    workspace_context += f"\nSample data from latest file ({latest_file.excel_file.name.split('/')[-1] if latest_file.excel_file else 'File'}):\n{json.dumps(sample_records, ensure_ascii=False)}\n"
            
            # Combine any frontend context with our backend workspace context
            comprehensive_context = f"Workspace Context:\n{workspace_context}\n\nUser Context:\n{file_context}"

            if not topic:
                return JsonResponse({"status": "error", "message": "Topic is required"}, status=400)

            from dashboard.services.ai_service import GeminiAIService
            ai_service = GeminiAIService()
            debate_result = ai_service.generate_boardroom_debate(topic, file_context=comprehensive_context)

            from django.contrib.auth.models import User
            user = request.user if request.user.is_authenticated else User.objects.first()
            
            # Save session
            if user:
                session = BoardroomSession.objects.create(
                    user=user,
                    topic=topic,
                debate_history=debate_result.get("speakers", []),
                final_resolution=debate_result.get("resolution", {}).get("decision", ""),
                action_items=debate_result.get("resolution", {}).get("action_items", [])
            )

            debate_result["session_id"] = session.id
            return JsonResponse({"status": "success", "data": debate_result})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


@login_required
def api_create_custom_agent(request):
    """
    API to create a new user-defined Custom Agent.
    """
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode('utf-8'))
            name = data.get("name", "").strip()
            role_title = data.get("role_title", "").strip()
            department = data.get("department", "general").strip()
            icon = data.get("icon", "bot").strip()
            color = data.get("color", "indigo").strip()
            system_prompt = data.get("system_prompt", "").strip()
            knowledge_notes = data.get("knowledge_notes", "").strip()

            if not name or not role_title or not system_prompt:
                return JsonResponse({"status": "error", "message": "Name, role, and system prompt are required"}, status=400)

            agent = CustomAgent.objects.create(
                user=request.user,
                name=name,
                role_title=role_title,
                department=department,
                icon=icon,
                color=color,
                system_prompt=system_prompt,
                knowledge_notes=knowledge_notes
            )
            return JsonResponse({
                "status": "success",
                "agent": {
                    "id": agent.id,
                    "name": agent.name,
                    "role_title": agent.role_title,
                    "icon": agent.icon,
                    "color": agent.color,
                    "department": agent.department
                }
            })
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


@login_required
def api_delete_custom_agent(request, agent_id):
    """
    API to delete a custom agent.
    """
    if request.method == "POST":
        try:
            agent = CustomAgent.objects.get(id=agent_id, user=request.user)
            agent.delete()
            return JsonResponse({"status": "success"})
        except CustomAgent.DoesNotExist:
            return JsonResponse({"status": "error", "message": "Agent not found"}, status=404)
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)



@login_required
def datasets(request):
    if request.method == "POST" and request.FILES.get("excel_file"):
        excel = request.FILES.get("excel_file")
        
        # Pre-check size (20 MB limit)
        max_bytes = 20 * 1024 * 1024
        if excel.size > max_bytes:
            messages.error(request, "حجم الملف يتجاوز الحد المسموح به (20 ميجابايت). يرجى اختيار ملف أصغر. / File size exceeds maximum limit (20MB).")
            return redirect('datasets')

        try:
            from dashboard.services.validation_service import validate_financial_file
            validation = validate_financial_file(excel)
            is_valid, status, msg = validation["is_valid"], validation["status"], validation["message"]
            accepted_sheets = validation["accepted_sheets"]
            if not is_valid:
                messages.error(request, msg)
                return redirect("datasets")
            elif status == "warning":
                messages.warning(request, msg)
        except Exception as exc:
            messages.error(request, f"حدث خطأ أثناء فحص الملف: {str(exc)}")
            return redirect("datasets")

        excel.seek(0)

        project_file = ProjectFile.objects.create(user=request.user, excel_file=excel)
        success, error_msg = process_excel_to_db(
            project_file, request.user, accepted_sheets,
            extracted_rows=validation.get("extracted_rows"),
        )

        if not success:
            project_file.delete()
            messages.error(request, f"فشل في قراءة وتجميع بيانات الملف. التفاصيل: {error_msg}")
            return redirect('datasets')

        try:
            from dashboard.services.retrieval_service import index_accepted_sheets
            index_accepted_sheets(project_file, accepted_sheets)
        except Exception as idx_err:
            print(f"Retrieval indexing error: {idx_err}")

        request.session['active_file_id'] = project_file.id

        import threading
        def bg_ai_tasks(user, project_file_path):
            from .models import Notification
            from dashboard.services.ai_service import GeminiAIService
            import pandas as pd
            import json
            try:
                df = read_file_to_df(project_file_path)
                if df is not None and not df.empty:
                    num_cols = df.select_dtypes(include=['number']).columns
                    key_metric = f"Sum of {num_cols[0]}: {df[num_cols[0]].sum()}" if len(num_cols) > 0 else "N/A"
                    df_summary = f"Columns: {', '.join(df.columns)}\nTotal Rows: {len(df)}\nKey Metric: {key_metric}"
                    
                    ai_service = GeminiAIService()
                    ai_result = ai_service.analyze_dataset_for_mobile(df_summary)
                    insight = ai_result.get("ai_insight", "تم فحص الملف وتحديث لوحات التحكم بنجاح.")
                    
                    notif_title = "تنبيه من بصيرة" if ("خطر" in insight or "فجوة" in insight or "انخفاض" in insight or "عجز" in insight) else "اكتمل تحليل الملف"
                    notif_type = "warning" if notif_title == "تنبيه من بصيرة" else "success"
                    
                    Notification.objects.create(
                        user=user,
                        title=notif_title,
                        message=insight,
                        type=notif_type
                    )
                    
                    # Generate Dynamic Business Pulse Digest
                    try:
                        df_sample = df.head(100).to_dict(orient='records')
                        sample_str = json.dumps(df_sample, ensure_ascii=False)
                        ai_service.generate_weekly_digest_for_user(sample_str, user)
                    except Exception as digest_err:
                        print(f"Error generating dynamic digest: {digest_err}")
                else:
                    Notification.objects.create(
                        user=user,
                        title="اكتمل تحليل الملف",
                        message="تم حفظ بياناتك بنجاح.",
                        type="success"
                    )
            except Exception as e:
                print(f"Error generating notification: {e}")
                Notification.objects.create(
                    user=user,
                    title="تم رفع الملف بنجاح",
                    message="لوحات التحكم والتحليلات التنبؤية جاهزة للاستعراض.",
                    type="success"
                )

        threading.Thread(target=bg_ai_tasks, args=(request.user, project_file.excel_file.path), daemon=True).start()

        messages.success(request, "تم رفع وتحليل المستند بنجاح! / File uploaded and analyzed successfully!")
        return redirect('dashboard')

    files = ProjectFile.objects.filter(user=request.user).order_by("-uploaded_at")
    
    from dashboard.models import ApprovedPlan
    approved_plans = ApprovedPlan.objects.filter(user=request.user).order_by("-created_at")
    
    return render(request, "dashboard/datasets.html", {"files": files, "approved_plans": approved_plans})

@login_required
def delete_dataset(request, file_id):
    if request.method == "POST":
        project_file = ProjectFile.objects.filter(id=file_id, user=request.user).first()
        if project_file:
            # When deleting a file, we want to delete its associated DynamicRecords
            DynamicRecord.objects.filter(project_file=project_file).delete()
            # If it has an actual file attached on disk, delete it
            if project_file.excel_file:
                import os
                if os.path.isfile(project_file.excel_file.path):
                    os.remove(project_file.excel_file.path)
            # Delete the record itself
            project_file.delete()
            
            # If this was the active file in session, remove it from session
            if request.session.get('active_file_id') == str(file_id) or request.session.get('active_file_id') == file_id:
                del request.session['active_file_id']
                
            messages.success(request, "تم حذف الملف وبياناته بنجاح / File and its data deleted successfully!")
    return redirect("datasets")

@login_required
def connect_live_web(request):
    if request.method == "POST":
        sheet_url = request.POST.get("sheet_url")
        try:
            validate_ssrf_url(sheet_url, allowed_hosts={"docs.google.com", "spreadsheets.google.com"})
        except ValueError:
            messages.error(request, "رابط غير مصرح به / Invalid Google Sheets URL")
            return redirect("datasets")
            
        try:
            if "/edit" in sheet_url:
                export_url = sheet_url.split("/edit")[0] + "/export?format=csv"
            else:
                export_url = sheet_url
                
            import urllib.request
            from django.core.files.base import ContentFile
            
            response = urllib.request.urlopen(export_url)
            file_content = response.read()
            
            project_file = ProjectFile.objects.create(user=request.user)
            project_file.excel_file.save("Live_Connection.csv", ContentFile(file_content))
            
            process_excel_to_db(project_file, request.user)
            request.session['active_file_id'] = project_file.id
            messages.success(request, "تم الربط المباشر بنجاح! / Live Sheet connected successfully!")
            return redirect("dashboard")
            
        except Exception as e:
            messages.error(request, f"فشل في الاتصال (Failed to connect): {e}")
            return redirect("datasets")
    return redirect("datasets")

@login_required
def reports(request):
    files = ProjectFile.objects.filter(user=request.user).order_by("-uploaded_at")
    return render(request, "dashboard/reports.html", {"files": files})


@login_required
def export_excel_report(request):
    from django.http import HttpResponse
    import io
    from urllib.parse import quote
    from .models import ProjectFile, Profile, DynamicRecord
    from .report_generator import generate_baseera_excel

    filename_param = request.GET.get("filename", "تقرير_بصيرة.xlsx")
    file_id = request.GET.get("file_id")

    user_lang = request.session.get("lang") or request.COOKIES.get("lang", "ar")
    user_lang = user_lang.upper()

    profile, _ = Profile.objects.get_or_create(user=request.user)

    rows_data = []

    if file_id:
        records = DynamicRecord.objects.filter(user=request.user, project_file_id=file_id).order_by("created_at")
        if records.exists():
            rows_data = [rec.row_data for rec in records if rec.row_data]
        else:
            project_file = ProjectFile.objects.filter(id=file_id, user=request.user).first()
            if project_file and project_file.excel_file:
                try:
                    df = read_file_to_df(project_file.excel_file.path)
                    if df is not None:
                        df.columns = df.columns.astype(str).str.strip()
                        df = df.dropna(how="all")
                        rows_data = json.loads(df.to_json(orient="records", date_format="iso"))
                except Exception as e:
                    print(f"Error parsing project file for report export: {e}")
    else:
        # Aggregate ALL user data across all uploaded files
        records = DynamicRecord.objects.filter(user=request.user).order_by("created_at")
        if records.exists():
            rows_data = [rec.row_data for rec in records if rec.row_data]
        else:
            # Fallback: Read all files uploaded by user
            user_files = ProjectFile.objects.filter(user=request.user).order_by("uploaded_at")
            for pf in user_files:
                if pf.excel_file:
                    try:
                        df = read_file_to_df(pf.excel_file.path)
                        if df is not None:
                            df.columns = df.columns.astype(str).str.strip()
                            df = df.dropna(how="all")
                            file_rows = json.loads(df.to_json(orient="records", date_format="iso"))
                            if isinstance(file_rows, list):
                                rows_data.extend(file_rows)
                    except Exception as e:
                        print(f"Error parsing file {pf.id}: {e}")

    # Reconciliation Layer (spec section 6): verify the report's aggregated
    # numbers against the source rows BEFORE any file is generated. rows_data
    # already only ever comes from DynamicRecord (populated exclusively from
    # ACCEPTED sheets, see process_excel_to_db) or a freshly-parsed accepted
    # file, so a rejected sheet's data can never reach this point either way.
    from dashboard.services.reconciliation_service import reconcile_report_items, ReconciliationError

    try:
        verified_items = reconcile_report_items(rows_data, lang=user_lang.lower())
    except ReconciliationError as recon_err:
        return HttpResponse(recon_err.message, status=409, content_type="text/plain; charset=utf-8")

    client_payload = {
        "company_name": profile.company_name if profile.company_name else (request.user.get_full_name() or request.user.username),
        "items": rows_data,
        "_normalized_items": verified_items,
    }

    buffer = io.BytesIO()
    generate_baseera_excel(
        user_language=user_lang, client_data=client_payload, output_target=buffer
    )
    buffer.seek(0)

    # ALWAYS set the download filename to "تقرير_بصيرة.xlsx" (or "Baseera_Report.xlsx" in English)
    clean_name = "تقرير_بصيرة.xlsx" if user_lang == "AR" else "Baseera_Report.xlsx"

    encoded_filename = quote(clean_name.encode("utf-8"))

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}; filename=\"{clean_name}\""
    return response


@login_required
def export_user_data_csv(request):
    from django.http import HttpResponse
    import csv
    from .models import DynamicRecord
    
    file_id = request.GET.get("file_id")
    target_hash = request.GET.get("hash")
    records = DynamicRecord.objects.filter(user=request.user)
    if file_id:
        records = records.filter(project_file_id=file_id)
    elif target_hash:
        records = records.filter(schema_hash=target_hash)
    
    records = records.order_by('created_at')
    
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = 'attachment; filename="user_cumulative_data.csv"'
    
    writer = csv.writer(response)
    
    if not records.exists():
        return response
    
    # Extract headers from the first record (since they share the same schema_hash)
    first_record = records.first()
    headers = list(first_record.row_data.keys())
    writer.writerow(headers)
    
    for record in records:
        row = [record.row_data.get(h, "") for h in headers]
        writer.writerow(row)
        
    return response



@login_required
def user_settings(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        if "update_profile" in request.POST:
            username = request.POST.get("username", "").strip()
            email = request.POST.get("email", "").strip()
            phone = request.POST.get("phone", "").strip()
            company_name = request.POST.get("company_name", "").strip()
            cr_number = request.POST.get("cr_number", "").strip()

            if username and User.objects.filter(username=username).exclude(pk=request.user.pk).exists():
                messages.error(request, "اسم المستخدم مسجل مسبقاً. / Username already taken.")
            elif email and User.objects.filter(email=email).exclude(pk=request.user.pk).exists():
                messages.error(request, "البريد الإلكتروني مسجل مسبقاً. / Email already in use.")
            else:
                old_username = request.user.username
                if username and username != old_username:
                    request.user.username = username
                    # Send Email Notification
                    if request.user.email:
                        try:
                            send_mail(
                                subject="تنبيه: تم تغيير اسم المستخدم الخاص بك | Security Alert: Username Changed",
                                message=f"مرحباً {username}،\n\nنود إعلامك بأنه تم تغيير اسم المستخدم الخاص بك بنجاح من {old_username} إلى {username}.\nإذا لم تقم بهذا الإجراء، يرجى التواصل مع الدعم الفني فوراً.",
                                from_email=settings.DEFAULT_FROM_EMAIL,
                                recipient_list=[request.user.email],
                                fail_silently=True,
                            )
                        except Exception as e:
                            print(f"Failed to send email: {e}")

                if email:
                    request.user.email = email
                profile.phone_number = phone
                profile.company_name = company_name
                profile.commercial_register = cr_number
                request.user.save()
                profile.save()

                messages.success(request, "تم تحديث الإعدادات بنجاح. / Settings updated successfully.")
            return redirect("settings")

        elif "change_password" in request.POST:
            current_password = request.POST.get("current_password", "")
            new_password = request.POST.get("new_password", "")
            confirm_password = request.POST.get("confirm_password", "")

            if not request.user.check_password(current_password):
                messages.error(request, "كلمة المرور الحالية غير صحيحة. / Current password is incorrect.")
            elif new_password != confirm_password:
                messages.error(request, "كلمتا المرور الجديدتان غير متطابقتين. / Passwords do not match.")
            elif len(new_password) < 6:
                messages.error(request, "يجب أن تتكون كلمة المرور من 6 أحرف على الأقل. / Password must be at least 6 characters.")
            else:
                request.user.set_password(new_password)
                request.user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
                
                if request.user.email:
                    try:
                        send_mail(
                            subject="تنبيه أمني: تم تغيير كلمة المرور بنجاح | Password Change Alert",
                            message=f"مرحباً {request.user.username}،\n\nتم تغيير كلمة المرور الخاصة بحسابك في منصة بصيرة بنجاح.\nإذا لم تقم بهذا الإجراء بنفسك، يرجى التواصل مع الدعم الفني فوراً.",
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[request.user.email],
                            fail_silently=True,
                        )
                    except Exception as e:
                        print(f"Failed to send email: {e}")

                messages.success(request, "تم تغيير كلمة المرور بنجاح. / Password changed successfully.")
            return redirect("settings")

        elif "delete_account" in request.POST:
            request.user.delete()
            messages.success(request, "تم حذف الحساب بنجاح. / Account deleted successfully.")
            return redirect("welcome")

    return render(request, "dashboard/settings.html", {"profile": profile})


@login_required
def strategic_profile(request):
    """
    Company Strategic Profile — the hard constraints & priority ranking that
    the Orchestrator (see dashboard/services/orchestrator.py) must consult
    before resolving any conflict between agent recommendations.
    """
    from .forms import CompanyStrategicProfileForm
    from .models import CompanyStrategicProfile

    instance = CompanyStrategicProfile.objects.filter(user=request.user).first()

    if request.method == "POST":
        form = CompanyStrategicProfileForm(request.POST, instance=instance)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            messages.success(request, "تم حفظ الملف الاستراتيجي للشركة بنجاح. / Company strategic profile saved successfully.")
            return redirect("strategic_profile")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه. / Please correct the errors below.")
    else:
        form = CompanyStrategicProfileForm(instance=instance)

    return render(request, "dashboard/strategic_profile.html", {"form": form, "instance": instance})


def password_reset(request):
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings

    step = request.GET.get("step", "1")
    email_param = request.GET.get("email", "")
    token_param = request.GET.get("token", "")

    if request.method == "POST":
        action = request.POST.get("action", "")
        email = request.POST.get("email", "").strip()
        new_password = request.POST.get("new_password", "")

        if action == "send_email" or (email and not new_password and action != "verify_pin"):
            from django.utils.crypto import get_random_string
            reset_pin = get_random_string(length=6, allowed_chars="0123456789")
            request.session["reset_pin"] = reset_pin
            request.session["reset_email"] = email
            request.session["reset_pin_verified"] = False
            request.session.set_expiry(15 * 60)
            
            subject = " كود تأكيد تغيير كلمة المرور"
            
            html_content = f"""
            <div dir="rtl" style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f7fa; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 30px; border-radius: 8px; border: 1px solid #e0e0e0;">
                    <h2 style="color: #1B365D; text-align: center;">طلب تغيير كلمة المرور</h2>
                    <p>وصلنا طلب لتغيير كلمة المرور الخاصة بحسابك المسجل بهذا البريد الإلكتروني ({email}).</p>
                    <p>كود التأكيد الخاص بك هو:</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <span style="background-color: #f4f7fa; color: #1B365D; padding: 14px 28px; border-radius: 6px; font-weight: bold; font-size: 24px; letter-spacing: 5px; display: inline-block; border: 2px dashed #1B365D;">{reset_pin}</span>
                    </div>
                    <p>يرجى إدخال هذا الكود في صفحة استعادة كلمة المرور لإتمام العملية.</p>
                    <p style="font-size: 12px; color: #777;">ملاحظة: هذا الكود صالح لمدة مؤقتة. إذا لم تطلب تغيير كلمة المرور، يمكنك تجاهل هذه الرسالة.</p>
                </div>
            </div>
            """
            text_content = f"طلب تغيير كلمة المرور: رمز التأكيد الخاص بك هو: {reset_pin}"

            try:
                msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [email])
                msg.attach_alternative(html_content, "text/html")
                msg.send(fail_silently=False)
                return render(request, "dashboard/password_reset.html", {"initial_step": 2, "email": email})
            except Exception:
                logger.exception("Password reset email delivery failed")
                messages.error(request, "تعذر إرسال البريد حالياً. تحقق من إعدادات SMTP وحاول لاحقاً.")
                return render(request, "dashboard/password_reset.html", {"initial_step": 1, "email": email})
            
        elif action == "verify_pin":
            user_pin = request.POST.get("pin", "").strip()
            session_pin = request.session.get("reset_pin")
            session_email = request.session.get("reset_email")
            
            if not session_pin or not session_email or email != session_email:
                messages.error(request, "انتهت صلاحية الرمز، يرجى المحاولة مرة أخرى.")
                return render(request, "dashboard/password_reset.html", {"initial_step": 1, "email": email})
                
            if user_pin == session_pin:
                request.session["reset_pin_verified"] = True
                return render(request, "dashboard/password_reset.html", {"initial_step": 3, "email": email})
            else:
                messages.error(request, "رمز التأكيد (PIN) غير صحيح.")
                return render(request, "dashboard/password_reset.html", {"initial_step": 2, "email": email})

        elif new_password:
            session_email = request.session.get("reset_email")
            if not request.session.get("reset_pin_verified") or not session_email or email != session_email:
                messages.error(request, "يجب التحقق من رمز الاستعادة أولاً.")
                return render(request, "dashboard/password_reset.html", {"initial_step": 1, "email": email})
            user = User.objects.filter(email=session_email).first()

            if user:
                user.set_password(new_password)
                user.save()
                request.session.pop("reset_pin", None)
                request.session.pop("reset_email", None)
                request.session.pop("reset_pin_verified", None)
                request.session.flush()
                messages.success(
                    request,
                    "تم إعادة تعيين كلمة المرور بنجاح! يمكنك الآن تسجيل الدخول. / Password reset successfully!",
                )
                return redirect("login")
            else:
                messages.error(
                    request,
                    "لم يتم العثور على المستخدم. / User not found.",
                )

    initial_step = 3 if (step == "3" or token_param) else 1
    return render(request, "dashboard/password_reset.html", {"initial_step": initial_step, "email": email_param})


def privacy(request):
    return render(request, "dashboard/privacy.html")


def terms(request):
    return render(request, "dashboard/terms.html")

def pricing(request):
    return render(request, "dashboard/pricing.html")

@login_required
def notifications(request):
    from .models import Notification
    user_notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    
    if not user_notifications.exists():
        Notification.objects.create(
            user=request.user,
            title="مرحباً بك في بصيرة",
            message="يسعدنا انضمامك إلى منصة بصيرة! ابدأ الآن برفع أول ملف بيانات لتكتشف قوة التحليل الذكي.",
            type="info"
        )
        user_notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
        
    return render(request, "dashboard/notifications.html", {"notifications": user_notifications})


@login_required
def admin_settings(request):
    if not (request.user.is_staff or request.user.is_superuser or request.user.username == "admin"):
        messages.error(request, "عذراً، هذه الصفحة مخصصة لمدير النظام فقط (Super Admin).")
        return redirect("dashboard")
    return redirect("/super-admin/?tab=settings")


@login_required
def admin_dashboard(request):
    # Strict Super Admin Access Verification
    if not (request.user.is_staff or request.user.is_superuser or request.user.username == "admin"):
        messages.error(request, "عذراً، هذه الصفحة مخصصة لمدير النظام فقط (Super Admin). / Access restricted to Admin only.")
        return redirect("dashboard")

    if request.method == "POST":
        if "toggle_user_id" in request.POST:
            uid = request.POST.get("toggle_user_id")
            target_user = User.objects.filter(pk=uid).first()
            if target_user:
                target_user.is_active = not target_user.is_active
                target_user.save()
                new_status = "تفعيل" if target_user.is_active else "تجميد"
                messages.success(request, f"تم {new_status} حساب المستخدم {target_user.username} بنجاح.")
                SystemLog.objects.create(
                    user=request.user,
                    action_type="User Management",
                    details=f"Admin toggled active status for user '{target_user.username}' to {target_user.is_active}."
                )
            return redirect("admin_dashboard")

        elif "create_user" in request.POST:
            username = request.POST.get("username", "").strip()
            email = request.POST.get("email", "").strip()
            password = request.POST.get("password", "").strip()
            company_name = request.POST.get("company_name", "").strip()
            phone_number = request.POST.get("phone_number", "").strip()
            role = request.POST.get("role", "user")

            if username and password:
                if User.objects.filter(username=username).exists():
                    messages.error(request, "اسم المستخدم مستخدم بالفعل. يرجى اختيار اسم آخر.")
                else:
                    new_user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=password,
                        is_staff=(role == "admin"),
                        is_superuser=(role == "admin")
                    )
                    profile, _ = Profile.objects.get_or_create(user=new_user)
                    if company_name:
                        profile.company_name = company_name
                    if phone_number:
                        profile.phone_number = phone_number
                    profile.save()

                    messages.success(request, f"تم إنشاء حساب المستخدم {username} بنجاح.")
                    SystemLog.objects.create(
                        user=request.user,
                        action_type="User Created",
                        details=f"Admin created new user account '{username}' ({role})."
                    )
            else:
                messages.error(request, "يرجى تعبئة جميع الحقول المطلوبة (اسم المستخدم وكلمة المرور).")
            return redirect("admin_dashboard")

        elif "delete_user_id" in request.POST:
            uid = request.POST.get("delete_user_id")
            target_user = User.objects.filter(pk=uid).first()
            if target_user and target_user != request.user:
                deleted_name = target_user.username
                target_user.delete()
                messages.success(request, f"تم حذف حساب المستخدم {deleted_name} بنجاح.")
                SystemLog.objects.create(
                    user=request.user,
                    action_type="User Deleted",
                    details=f"Admin deleted user account '{deleted_name}'."
                )
            return redirect("admin_dashboard")

        elif "create_announcement" in request.POST:
            title = request.POST.get("title", "").strip()
            message = request.POST.get("message", "").strip()
            ann_type = request.POST.get("type", "info")
            if title and message:
                Announcement.objects.create(title=title, message=message, type=ann_type, is_active=True)
                messages.success(request, "تم نشر التنبيه العام بنجاح لجميع المستخدمين.")
                SystemLog.objects.create(
                    user=request.user,
                    action_type="Announcement Created",
                    details=f"Admin published announcement: '{title}'."
                )
            return redirect("admin_dashboard")

        elif "delete_announcement_id" in request.POST:
            ann_id = request.POST.get("delete_announcement_id")
            ann = Announcement.objects.filter(pk=ann_id).first()
            if ann:
                ann.delete()
                messages.success(request, "تم حذف التنبيه بنجاح.")
            return redirect("admin_dashboard")

    users_list = User.objects.select_related("profile").all().order_by("-date_joined")

    # Calculate sector_counts
    from django.db.models import Count
    sector_data = Profile.objects.values('project_type').annotate(count=Count('id'))
    
    sector_counts = {
        'pharmacy': 0,
        'real_estate': 0,
        'retail': 0,
        'fnb': 0,
        'other': 0,
    }
    
    for item in sector_data:
        ptype = item['project_type']
        if ptype in sector_counts:
            sector_counts[ptype] = item['count']

    total_sector_records = sum(sector_counts.values()) or 1

    sector_stats = [
        {
            'key': 'pharmacy',
            'name_ar': 'صيدلية ورعاية صحية',
            'name_en': 'Pharmacy & Healthcare',
            'icon': 'pill',
            'bg_color': 'bg-teal-500/10 text-teal-600 dark:text-teal-400',
            'bar_color': 'bg-[#14b8a6]',
            'count': sector_counts['pharmacy'],
            'pct': round((sector_counts['pharmacy'] / total_sector_records) * 100) if total_sector_records > 0 else 0,
        },
        {
            'key': 'real_estate',
            'name_ar': 'عقارات وتطوير عقاري',
            'name_en': 'Real Estate & Property',
            'icon': 'building-2',
            'bg_color': 'bg-[#2b2470]/10 text-[#2b2470] dark:text-purple-300',
            'bar_color': 'bg-[#2b2470] dark:bg-[#b9a6f2]',
            'count': sector_counts['real_estate'],
            'pct': round((sector_counts['real_estate'] / total_sector_records) * 100) if total_sector_records > 0 else 0,
        },
        {
            'key': 'retail',
            'name_ar': 'تجارة وتجزئة',
            'name_en': 'Retail & Commerce',
            'icon': 'shopping-bag',
            'bg_color': 'bg-[#7c6cf0]/10 text-[#7c6cf0]',
            'bar_color': 'bg-[#7c6cf0]',
            'count': sector_counts['retail'],
            'pct': round((sector_counts['retail'] / total_sector_records) * 100) if total_sector_records > 0 else 0,
        },
        {
            'key': 'fnb',
            'name_ar': 'مطاعم ومقاهي (F&B)',
            'name_en': 'Food & Beverage',
            'icon': 'utensils',
            'bg_color': 'bg-amber-500/10 text-amber-600',
            'bar_color': 'bg-[#f59e0b]',
            'count': sector_counts['fnb'],
            'pct': round((sector_counts['fnb'] / total_sector_records) * 100) if total_sector_records > 0 else 0,
        },
        {
            'key': 'other',
            'name_ar': 'أنشطة وخدمات أخرى',
            'name_en': 'Other Sectors',
            'icon': 'layers',
            'bg_color': 'bg-sky-500/10 text-sky-600 dark:text-sky-400',
            'bar_color': 'bg-[#0ea5e9]',
            'count': sector_counts['other'],
            'pct': round((sector_counts['other'] / total_sector_records) * 100) if total_sector_records > 0 else 0,
        },
    ]

    total_tenants = users_list.count()
    active_count = users_list.filter(is_active=True).count()
    pending_count = users_list.filter(is_active=False).count()
    total_dashboards = ProjectFile.objects.count()

    from .models import UserFeedback
    feedback_list = UserFeedback.objects.all().order_by("-created_at")
    
    # Calculate AI Usage count (Total AI queries)
    ai_usage_count = AIUsageLog.objects.count()

    # Get active announcements
    active_announcements = Announcement.objects.filter(is_active=True).order_by("-created_at")

    # Invoices & Finance
    invoices = Invoice.objects.all().order_by("-created_at")
    total_revenue = sum(inv.amount for inv in invoices)

    # System Activity Logs
    logs = SystemLog.objects.all().order_by("-timestamp")[:100]

    return render(
        request,
        "dashboard/admin_dashboard.html",
        {
            "active_page": "admin",
            "users_list": users_list,
            "feedback_list": feedback_list,
            "total_tenants": total_tenants,
            "active_count": active_count,
            "pending_count": pending_count,
            "total_dashboards": total_dashboards,
            "sector_counts": sector_counts,
            "sector_stats": sector_stats,
            "ai_usage_count": ai_usage_count,
            "announcements": active_announcements,
            "invoices": invoices,
            "total_revenue": total_revenue,
            "logs": logs,
        },
    )



def templates_feedback(request):
    from .models import UserFeedback

    if request.method == "POST":
        feedback_type = request.POST.get("feedback_type", "rating")
        try:
            rating = int(request.POST.get("rating", 5))
        except ValueError:
            rating = 5
        email = request.POST.get("email", "").strip()
        message = request.POST.get("message", "").strip()

        if email and message:
            UserFeedback.objects.create(
                user=request.user if request.user.is_authenticated else None,
                feedback_type=feedback_type,
                rating=rating,
                email=email,
                message=message,
            )
            messages.success(
                request,
                "تم إرسال رسالتك بنجاح! شكرًا لك، فريق بصيرة يراجع كل الملاحظات باهتمام.",
            )
            return redirect("templates_feedback")

    return render(request, "dashboard/templates_feedback.html", {"active_page": "templates_feedback"})
@login_required
def process_payment(request):
    if request.method == "POST":
        plan = request.POST.get("plan", "Pro")
        amount = request.POST.get("amount", "15 OMR")
        allowed_plans = {"starter", "growth", "enterprise", "pro"}
        if plan.lower() not in allowed_plans:
            messages.error(request, "الباقة المطلوبة غير متاحة.")
            return redirect("pricing")

        idempotency_key = request.headers.get("Idempotency-Key") or request.POST.get("idempotency_key")
        if idempotency_key:
            from .models import PaymentIdempotency
            from django.db import IntegrityError
            try:
                _, created = PaymentIdempotency.objects.get_or_create(
                    user=request.user,
                    key=idempotency_key[:128],
                    defaults={"status": "processing"},
                )
            except IntegrityError:
                created = False
            if not created:
                messages.info(request, "تمت معالجة عملية الدفع مسبقاً.")
                return redirect("dashboard")
        
        # Simulate payment processing...
        import datetime
        from django.core.mail import EmailMultiAlternatives
        
        # 1. Email to Admin
        subject = f" دفعة جديدة: اشتراك {plan}"
        admin_email = "baseera.ai0@gmail.com"
        html_content = f"""
        <div dir="rtl" style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f7fa; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; background: #ffffff; padding: 30px; border-radius: 8px; border: 1px solid #e0e0e0;">
                <h2 style="color: #10b981; text-align: center;">تم استلام دفعة جديدة بنجاح!</h2>
                <hr style="border: 0; height: 1px; background: #e0e0e0; margin: 20px 0;">
                <p><strong>تفاصيل الفاتورة والمشترك:</strong></p>
                <ul>
                    <li><strong>اسم المشترك:</strong> {request.user.username}</li>
                    <li><strong>البريد الإلكتروني:</strong> {request.user.email}</li>
                    <li><strong>الشركة:</strong> {request.user.profile.company_name if hasattr(request.user, 'profile') else 'N/A'}</li>
                    <li><strong>الباقة:</strong> {plan}</li>
                    <li><strong>المبلغ المدفوع:</strong> {amount}</li>
                    <li><strong>تاريخ الدفع:</strong> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</li>
                </ul>
                <div style="text-align: center; margin-top: 30px;">
                    <a href="http://localhost:8080/super-admin/" style="background-color: #1B365D; color: white; text-decoration: none; padding: 10px 20px; border-radius: 6px;">الذهاب للوحة الإدارة</a>
                </div>
            </div>
        </div>
        """
        text_content = f"New Payment Received from {request.user.username} for plan {plan}. Amount: {amount}"
        
        try:
            profile, _ = Profile.objects.get_or_create(user=request.user)
            profile.is_subscribed = True
            profile.subscription_plan = plan.lower()
            profile.save()
        except Exception as pe:
            print("Error updating profile subscription:", pe)

        try:
            msg = EmailMultiAlternatives(subject, text_content, settings.DEFAULT_FROM_EMAIL, [admin_email])
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)
            
            Invoice.objects.create(
                user=request.user,
                plan_name=plan,
                amount=amount,
                currency="OMR"
            )
            
            SystemLog.objects.create(
                user=request.user,
                action_type="دفع اشتراك / Payment",
                details=f"دفع اشتراك لباقة {plan} بمبلغ {amount}."
            )
            
            messages.success(request, "تمت عملية الدفع بنجاح! وتم تفعيل اشتراكك وإرسال نسخة من الفاتورة إلى الإدارة.")
        except Exception as e:
            print("Failed to send invoice email:", e)
            messages.success(request, "تم الدفع وتفعيل الاشتراك بنجاح!")
            
        return redirect("dashboard")
    
    return redirect("pricing")

@login_required
def admin_logs(request):
    if not (request.user.is_staff or request.user.is_superuser or request.user.username == "admin"):
        messages.error(request, "عذراً، هذه الصفحة مخصصة لمدير النظام فقط (Super Admin). / Access restricted to Admin only.")
        return redirect("dashboard")
    return redirect("admin_dashboard")

@login_required
def admin_finance(request):
    if not (request.user.is_staff or request.user.is_superuser or request.user.username == "admin"):
        messages.error(request, "عذراً، هذه الصفحة مخصصة لمدير النظام فقط.")
        return redirect("dashboard")
    return redirect("admin_dashboard")

@login_required
def impersonate_user(request, user_id):
    if not (request.user.is_staff or request.user.is_superuser or request.user.username == "admin"):
        return redirect("dashboard")
        
    target_user = User.objects.filter(id=user_id).first()
    if target_user:
        original_user_id = request.user.id
        login(request, target_user, backend='django.contrib.auth.backends.ModelBackend')
        request.session['impersonated_from'] = original_user_id
        
        SystemLog.objects.create(
            user=target_user,
            action_type="Impersonation",
            details=f"Admin (ID:{original_user_id}) is impersonating this user."
        )
        messages.success(request, f"أنت الآن تتصفح كالمستخدم: {target_user.username}")
        
    return redirect("dashboard")

@login_required
def stop_impersonate(request):
    original_user_id = request.session.get('impersonated_from')
    if original_user_id:
        original_user = User.objects.filter(id=original_user_id).first()
        if original_user:
            # Pop the key before logging in, because login() flushes the session
            request.session.pop('impersonated_from', None)
            login(request, original_user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, "تمت العودة إلى حساب الإدارة بنجاح.")
            return redirect("admin_dashboard")
            
    return redirect("dashboard")

import json
from django.http import JsonResponse
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

@login_required
def record_ai_usage(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            query = data.get("query", "")
            AIUsageLog.objects.create(user=request.user, query=query)
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))})
    return JsonResponse({"status": "invalid_method"})

from django.http import StreamingHttpResponse
import os
import json

from django.views.decorators.csrf import csrf_exempt


def _direct_reply_event_stream(text, suggested_actions=None):
    """
    SSE generator matching the existing GeminiAIService streaming protocol,
    used by the Orchestrator/Router (spec section 4) to answer directly
    without invoking any agent/LLM — e.g. Route 1 (off-topic) or an
    unresolved/ambiguous Retrieval Layer match. Emits the text as a single
    chunk, followed by a distinct `suggested_actions` SSE payload (spec
    section 4.3) and the standard STATUS___:DONE terminator.
    """
    yield f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': text}]}}]})}\n\n"
    if suggested_actions:
        yield f"data: {json.dumps({'suggested_actions': suggested_actions}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': 'STATUS___:DONE'}]}}]})}\n\n"


@login_required
def chat_api(request):
    if request.method == "POST":
        try:
            import re
            data = json.loads(request.body)
            messages_list = data.get("messages", [])
            if not messages_list and "message" in data:
                messages_list = [{"role": "user", "content": data["message"]}]
            # Task 5 hardening: this text comes straight from the client
            # (built from the raw dataset the user uploaded), so it's the
            # main indirect-prompt-injection surface for the chat -- a cell
            # value could otherwise smuggle a fake [[ACTION:...]] tag or
            # <internal_simulation> block into the model's context.
            file_context = sanitize_cell_for_prompt(data.get("fileContext") or "", max_len=6000)
            agent_id = data.get("agent_id", "general")
            agent_ids = data.get("agent_ids")
            if not agent_ids and agent_id:
                agent_ids = [agent_id]

            # Detect language directly from the latest user message:
            last_user_query = ""
            for m in reversed(messages_list):
                if m.get("role") == "user" and m.get("content"):
                    last_user_query = m.get("content")
                    break

            if re.search(r'[\u0600-\u06FF]', last_user_query):
                lang = "ar"
            elif re.search(r'[a-zA-Z]', last_user_query) and not re.search(r'[\u0600-\u06FF]', last_user_query):
                lang = "en"
            else:
                lang = data.get("lang", "ar")

            from dashboard.services.ai_service import GeminiAIService
            ai_service = GeminiAIService()

            user_id = request.user.id if request.user.is_authenticated else None
            confirmed_sheet = data.get("confirmed_sheet")

            # Orchestrator / Router (spec section 4): classify the message into
            # off_topic / single_file / multi_agent BEFORE any agent is invoked.
            from dashboard.services import orchestrator
            route_info = orchestrator.route_message(
                user_id, last_user_query, lang=lang, ai_service=ai_service,
                confirmed_sheet=confirmed_sheet,
            )

            if route_info["direct_reply"] is not None:
                # Route 1 (off-topic) or an unresolved/ambiguous file match:
                # answer directly, no agent/LLM call, no hallucination risk.
                return StreamingHttpResponse(
                    _direct_reply_event_stream(route_info["direct_reply"], route_info["suggested_actions"]),
                    content_type='text/event-stream',
                )

            if route_info["needs_confirmation"]:
                # Ambiguous file match with no canned message yet (e.g. two
                # close candidates): ask the user to confirm before computing.
                confirm_msg = (
                    "\u0648\u062C\u062F\u062A \u0623\u0643\u062B\u0631 \u0645\u0646 \u0645\u0644\u0641/\u062C\u062F\u0648\u0644 \u0645\u062D\u062A\u0645\u0644 \u0645\u0637\u0627\u0628\u0642 \u0644\u0633\u0624\u0627\u0644\u0643. \u064A\u0631\u062C\u0649 \u062A\u0623\u0643\u064A\u062F \u0627\u0644\u0645\u0644\u0641 \u0627\u0644\u0635\u062D\u064A\u062D \u0642\u0628\u0644 \u0627\u0644\u0645\u062A\u0627\u0628\u0639\u0629:"
                    if lang == "ar" else
                    "I found more than one file/sheet that could match your question. Please confirm the correct one before I proceed:"
                )
                return StreamingHttpResponse(
                    _direct_reply_event_stream(confirm_msg, route_info["suggested_actions"]),
                    content_type='text/event-stream',
                )

            # Client explicitly picked a specific agent or a manual multi-agent
            # committee -> respect that choice. Otherwise (the default single
            # "general" agent) let the router decide dynamically.
            client_made_explicit_choice = bool(agent_ids) and agent_ids != ["general"]
            effective_agent_ids = agent_ids if client_made_explicit_choice else route_info["agent_ids"]

            effective_file_context = file_context
            if route_info["matched_sheet_note"]:
                effective_file_context = (file_context or "") + "\n" + route_info["matched_sheet_note"]

            event_stream = ai_service.generate_chat_stream(
                messages_list,
                effective_file_context,
                user_id=user_id,
                agent_id=effective_agent_ids[0] if effective_agent_ids else agent_id,
                agent_ids=effective_agent_ids,
                lang=lang
            )

            return StreamingHttpResponse(event_stream, content_type='text/event-stream')
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=400)


@login_required
def api_ai_health(request):
    """
    Task 3 (P0 - Resilience): an *active* health check of the live Gemini
    model -- not just "did the client construct", which only proves an API
    key is present, but a real, cheap call against the configured model so
    the frontend can tell "the live AI engine is up" apart from "silently
    degraded to rule-based fallback text" instead of finding out only when
    a chat reply quietly turns into canned analysis.

    Cached briefly so repeated UI polls (page load, periodic re-checks)
    don't multiply real API calls.
    """
    from django.core.cache import cache

    cache_key = "baseera:ai_health_status"
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    from dashboard.services.ai_service import GeminiAIService, GEMINI_MODEL

    status = {"available": False, "model": GEMINI_MODEL, "detail": "no_api_key_configured"}

    ai_service = GeminiAIService()
    if getattr(ai_service, "client", None) and hasattr(ai_service.client, "models"):
        try:
            ai_service.client.models.count_tokens(model=GEMINI_MODEL, contents="ping")
            status = {"available": True, "model": GEMINI_MODEL, "detail": "ok"}
        except Exception as e:
            status = {"available": False, "model": GEMINI_MODEL, "detail": safe_error_message(str(e))}

    # Short TTL: fast enough to notice a real outage within the pilot
    # window, cheap enough not to burn quota on every page load/poll.
    cache.set(cache_key, status, timeout=20)
    return JsonResponse(status)


import datetime
import math
from django.http import FileResponse

@login_required
def workspace_view(request):
    workspace_dir = os.path.join(settings.BASE_DIR, 'sandbox', 'workspace')
    os.makedirs(workspace_dir, exist_ok=True)
    
    # Handle direct file upload in workspace
    if request.method == 'POST' and request.FILES.get('workspace_file'):
        uploaded_file = request.FILES['workspace_file']
        clean_name = os.path.basename(uploaded_file.name)
        file_path = os.path.join(workspace_dir, clean_name)
        with open(file_path, 'wb+') as destination:
            for chunk in uploaded_file.chunks():
                destination.write(chunk)
        messages.success(request, f"تم حفظ الملف ({clean_name}) في مساحة العمل بنجاح.")
        return redirect('workspace')

    files_dict = {}
    
    # 1. Read files from sandbox/workspace directory
    for f in os.listdir(workspace_dir):
        full_path = os.path.join(workspace_dir, f)
        if os.path.isfile(full_path):
            stat = os.stat(full_path)
            files_dict[f] = {
                'name': f,
                'size': f'{stat.st_size / 1024:.2f} KB',
                'date': datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                'type': 'Code' if f.endswith('.py') else 'Data' if f.endswith(('.csv', '.xlsx', '.json')) else 'Chart' if f.endswith(('.png', '.jpg', '.svg')) else 'Document' if f.endswith(('.md', '.pdf', '.docx', '.txt')) else 'Other'
            }

    # 2. Include user uploaded datasets from ProjectFile
    user_files = ProjectFile.objects.filter(user=request.user).order_by('-uploaded_at')
    for pf in user_files:
        if pf.excel_file:
            fname = os.path.basename(pf.excel_file.name)
            if fname not in files_dict and os.path.exists(pf.excel_file.path):
                stat = os.stat(pf.excel_file.path)
                files_dict[fname] = {
                    'name': fname,
                    'size': f'{stat.st_size / 1024:.2f} KB',
                    'date': pf.uploaded_at.strftime('%Y-%m-%d %H:%M'),
                    'type': 'Data'
                }

    files = list(files_dict.values())
    context = {
        'files': sorted(files, key=lambda x: x['date'], reverse=True),
        'active_page': 'workspace'
    }
    return render(request, 'dashboard/workspace.html', context)


@login_required
def download_workspace_file(request, filename):
    clean_name = os.path.basename(filename)
    workspace_dir = os.path.join(settings.BASE_DIR, 'sandbox', 'workspace')
    full_path = os.path.join(workspace_dir, clean_name)
    if os.path.exists(full_path):
        return FileResponse(open(full_path, 'rb'), as_attachment=True, filename=clean_name)
    
    # Check ProjectFile fallback
    pf = ProjectFile.objects.filter(user=request.user, excel_file__icontains=clean_name).first()
    if pf and pf.excel_file and os.path.exists(pf.excel_file.path):
        return FileResponse(open(pf.excel_file.path, 'rb'), as_attachment=True, filename=clean_name)
        
    messages.error(request, "الملف غير موجود.")
    return redirect('workspace')


@login_required
def delete_workspace_file(request, filename):
    clean_name = os.path.basename(filename)
    workspace_dir = os.path.join(settings.BASE_DIR, 'sandbox', 'workspace')
    full_path = os.path.join(workspace_dir, clean_name)
    deleted = False
    
    if os.path.exists(full_path):
        try:
            os.remove(full_path)
            deleted = True
        except Exception as e:
            print(f"Error removing workspace file: {e}")

    pfs = ProjectFile.objects.filter(user=request.user, excel_file__icontains=clean_name)
    if pfs.exists():
        pfs.delete()
        deleted = True

    if deleted:
        messages.success(request, f"تم حذف الملف ({clean_name}) بنجاح.")
    else:
        messages.error(request, "لم يتم العثور على الملف لحذفه.")
        
    return redirect('workspace')


def extract_products_from_file_or_records(user, file_id=None):
    """
    استخراج المنتجات والأسعار من ملفات المستخدم أو السجلات التراكمية
    """
    products = []
    
    # 1. إذا تم تحديد ملف معين
    if file_id:
        try:
            pf = ProjectFile.objects.filter(user=user, id=file_id).first()
            if pf and pf.excel_file and os.path.exists(pf.excel_file.path):
                file_path = pf.excel_file.path
                if file_path.endswith('.csv'):
                    df = pd.read_csv(file_path)
                else:
                    df = pd.read_excel(file_path)
                
                # البحث عن أعمدة المنتجات والأسعار
                prod_col = None
                price_col = None
                for col in df.columns:
                    c_lower = str(col).lower()
                    if any(k in c_lower for k in ['product', 'item', 'منتج', 'صنف', 'سلعة', 'المنتج', 'اسم']):
                        prod_col = col
                    if any(k in c_lower for k in ['price', 'cost', 'سعر', 'السعر', 'قيمة', 'تكلفة', 'مبلغ', 'total']):
                        price_col = col
                
                if prod_col:
                    unique_prods = df[prod_col].dropna().unique()[:8]
                    for p in unique_prods:
                        p_str = str(p).strip()
                        avg_price = 10.0
                        if price_col:
                            try:
                                avg_price = round(float(df[df[prod_col] == p][price_col].mean()), 2)
                            except:
                                avg_price = 10.0
                        if avg_price <= 0 or math.isnan(avg_price):
                            avg_price = 10.0
                        products.append({"name": p_str, "price": avg_price})
        except Exception as e:
            print(f"Error parsing file products: {e}")
            
    # 2. إذا لم نجد منتجات من الملف المحدد، نفحص السجلات الديناميكية
    if not products:
        try:
            from .models import DynamicRecord
            records = DynamicRecord.objects.filter(user=user)
            if records.exists():
                seen = set()
                for r in records:
                    row = r.row_data or {}
                    p_val = row.get('product') or row.get('منتج') or row.get('المنتج') or row.get('item') or row.get('اسم المنتج')
                    if p_val and str(p_val) not in seen:
                        seen.add(str(p_val))
                        pr = row.get('price') or row.get('سعر') or row.get('السعر') or 12.5
                        try: pr = round(float(pr), 2)
                        except: pr = 12.5
                        products.append({"name": str(p_val), "price": pr})
                        if len(products) >= 8: break
        except Exception as e:
            print(f"Error parsing dynamic records: {e}")

    # 3. إذا لم تتوفر ملفات، نستخدم منتجات افتراضية واقعية
    if not products:
        products = [
            {"name": "منتج A - باقة الحلويات الفاخرة", "price": 10.0},
            {"name": "منتج B - العصير الطبيعي 1 لتر", "price": 4.5},
            {"name": "منتج C - صندوق التوفير العائلي", "price": 25.0},
            {"name": "منتج D - قهوة مختصة فاخرة", "price": 8.0},
        ]
        
    return products


def generate_ai_scenarios_for_product(prod_name, price, lang="ar", sector="other"):
    """
    توليد سيناريوهات تسويقية ذكية شاملة مع نصوص إعلانات للمنصات والتوقيت الذهبي وحاسبة الأرباح
    """
    p1 = round(price * 0.85, 2)
    p2_bundle = round(price * 1.25, 2)
    p3_premium = round(price * 1.15, 2)
    curr = "ر.ع" if lang == "ar" else "OMR"
    est_cost = round(price * 0.55, 2)
    
    if lang == "en":
        scenarios = [
            {
                "id": 0,
                "badge": "Price Adjustment",
                "title": "1. Flexible Dynamic Pricing",
                "impact": "Expected Revenue Increase: +18%",
                "priceSimulation": f"Reducing '{prod_name}' price from {price} {curr} to {p1} {curr} increases expected volume by +35%.",
                "offer": f"Flash 15% discount for 3 days only on {prod_name} with code SAVE15.",
                "benchmark": f"Current price is 12% higher than similar market offerings.",
                "reason": "High price elasticity in your market segment; lower barrier drives higher immediate conversion.",
                "discount_pct": 15,
                "new_price": p1,
                "projected_units_uplift": 35,
                "ad_copies": {
                    "instagram": f" Special Flash Offer! Enjoy 15% OFF on {prod_name} for 3 days only. \n\n Use promo code: SAVE15 at checkout.\n Order now via the link in bio!\n\n#SpecialOffer #Deals #Shopping #Discount #{prod_name.replace(' ', '')}",
                    "whatsapp": f" Hello! Don't miss our exclusive flash sale on *{prod_name}* at just *{p1} {curr}* instead of ~{price} {curr}~!\n\n Use Code: *SAVE15*\n Order directly here: https://yourstore.com/offer",
                    "tiktok": f" [Video Hook]: Stop overpaying! Get your favorite {prod_name} with a limited 15% OFF!\n[Voiceover]: Don't wait, grab yours before stock runs out. Link in bio! \n#TikTokMadeMeBuyIt #Trending #SpecialSale",
                    "twitter": f" Limited 3-Day Flash Sale! Get {prod_name} for just {p1} {curr} (Was {price} {curr}).\nUse code SAVE15 at checkout \nhttps://yourstore.com/offer"
                },
                "timing": {
                    "best_days": "Thursday & Friday (Weekend Surge)",
                    "best_hours": "7:00 PM – 10:30 PM",
                    "target_audience": "Price-sensitive buyers and deal hunters"
                }
            },
            {
                "id": 1,
                "badge": "Promotional Bundle",
                "title": "2. Value Bundle & Cross-Sell",
                "impact": "Expected Revenue Increase: +25%",
                "priceSimulation": f"Bundle '{prod_name}' at package price of {p2_bundle} {curr} with a complementary secondary item.",
                "offer": f"Buy {prod_name} and get the secondary item at 50% OFF as a full value pack.",
                "benchmark": "Clears slow-moving secondary stock while boosting Average Order Value (AOV).",
                "reason": "Increases customer basket size and eliminates dead stock without eroding main product perceived value.",
                "discount_pct": 20,
                "new_price": p2_bundle,
                "projected_units_uplift": 40,
                "ad_copies": {
                    "instagram": f" Double the Value! Get {prod_name} + our bestseller complimentary item at an exclusive bundle price of only {p2_bundle} {curr}!\n\n Limited bundle quantities available.\n Tap the link in bio to claim yours!\n\n#BundleDeal #SmartShopping #BestValue #{prod_name.replace(' ', '')}",
                    "whatsapp": f" Special Bundle Alert! Upgrade your order with {prod_name} bundle pack for just *{p2_bundle} {curr}*!\n\n Claim your bundle now: https://yourstore.com/bundle",
                    "tiktok": f" [Visual Hook]: The ultimate combo you didn't know you needed!\n[Voiceover]: Get {prod_name} bundled with our top add-on for a huge discount! \n#BundleHacks #MustHave #ViralDeal",
                    "twitter": f" Why buy one when you can get the full bundle? {prod_name} combo package now available for {p2_bundle} {curr}! \nhttps://yourstore.com/bundle"
                },
                "timing": {
                    "best_days": "Payday Week (25th - 30th of month)",
                    "best_hours": "1:00 PM – 4:00 PM & 8:00 PM",
                    "target_audience": "Families and high-basket shoppers"
                }
            },
            {
                "id": 2,
                "badge": "Premium Campaign",
                "title": "3. Premium Positioning & Express Delivery",
                "impact": "Expected Revenue Increase: +12%",
                "priceSimulation": f"Maintain price at {p3_premium} {curr} bundled with Free Express Shipping and VIP packaging.",
                "offer": f"Complimentary VIP Gift Box + Same-day priority delivery for every order of {prod_name}.",
                "benchmark": f"'{prod_name}' has 20% higher quality ratings than competitors.",
                "reason": "Enhances brand prestige and attracts premium clientele who value reliability and experience over discounts.",
                "discount_pct": 0,
                "new_price": p3_premium,
                "projected_units_uplift": 15,
                "ad_copies": {
                    "instagram": f" Premium Quality You Deserve. Experience the luxury of {prod_name} with complimentary VIP packaging & Express Delivery.\n\n Order today for prompt luxury delivery.\n Link in bio.\n\n#Luxury #PremiumQuality #Exclusive #{prod_name.replace(' ', '')}",
                    "whatsapp": f" Elevate your experience with our premium *{prod_name}*. Includes free luxury gift packaging & priority delivery today!\n\n Order here: https://yourstore.com/premium",
                    "tiktok": f" [Aesthetic Unboxing]: Watch how we package our luxury {prod_name} with gold-tier care.\n[Voiceover]: Treat yourself to unmatched quality. Fast delivery guaranteed. \n#LuxuryLifestyle #Unboxing #PremiumBrand",
                    "twitter": f" Luxury meets reliability. Order {prod_name} today and enjoy complimentary VIP packaging and same-day delivery \nhttps://yourstore.com/premium"
                },
                "timing": {
                    "best_days": "Sunday & Monday (Start of week planning)",
                    "best_hours": "10:00 AM – 2:00 PM",
                    "target_audience": "Corporate, gift givers & luxury consumers"
                }
            }
        ]
    else:
        scenarios = [
            {
                "id": 0,
                "badge": "تعديل سعر وخصم مباشر",
                "title": "1. استراتيجية التسعير المرن (خصم مباشر)",
                "impact": "زيادة الإيرادات المتوقعة: +18%",
                "priceSimulation": f"تخفيض سعر '{prod_name}' من {price} {curr} إلى {p1} {curr} يرفع حجم الطلب المتوقع بنسبة +35%.",
                "offer": f"خصم خاطف 15% لمدة 3 أيام على {prod_name} باستخدام كود التخفيض: SAVE15.",
                "benchmark": f"سعر المنتج حالياً أعلى بنسبة 12% مقارنة بالمنافسين، والتخفيض يجعله الأكثر جاذبية بالسوق.",
                "reason": "مرونة الطلب على هذا المنتج مرتفعة؛ التخفيض المؤقت يضاعف التحويل دون التأثير على قيمة العلامة التجارية.",
                "discount_pct": 15,
                "new_price": p1,
                "projected_units_uplift": 35,
                "ad_copies": {
                    "instagram": f" عرض خاطف لفترة محدودة! استمتع بخصم 15% على {prod_name} لمدة 3 أيام فقط \n\n كود الخصم: SAVE15\n اطلب الآن عبر الرابط في البايو قبل نفاد الكمية!\n\n#عروض #خصومات #توفير #تسوق #{prod_name.replace(' ', '_')}",
                    "whatsapp": f"مرحباً بك!  لا يفوتك عرضنا الخاص على *{prod_name}* بسعر *{p1} {curr}* فقط بدلاً من ~{price} {curr}~!\n\n كود الخصم: *SAVE15*\n للطلب المباشر اضغط هنا: https://yourstore.com/offer",
                    "tiktok": f" [المشهد الأول]: لا تفوت هذا العرض على {prod_name}!\n[الصوت]: خصم 15% لمدة 3 أيام فقط.. الرابط في البايو اطلب قبل نفاذ الكمية \n#عروض_تيك_توك #ترند #تسوق_أونلاين",
                    "twitter": f" عرض خاطف لمدة 3 أيام! احصل على {prod_name} بسعر {p1} {curr} فقط (بدلاً من {price} {curr}) \nاستخدم كود: SAVE15 عبر الرابط \nhttps://yourstore.com/offer"
                },
                "timing": {
                    "best_days": "الخميس والجمعة (أيام العطلة والتسوق الأسبوعي)",
                    "best_hours": "7:00 مساءً – 11:00 مساءً",
                    "target_audience": "الباحثون عن التوفير والصفقات السريعة"
                }
            },
            {
                "id": 1,
                "badge": "حزمة ترويجية (Bundle)",
                "title": "2. استراتيجية باقة التوفير والمنتج التكميلي",
                "impact": "زيادة الإيرادات المتوقعة: +25%",
                "priceSimulation": f"دمج '{prod_name}' بسعر تجميعي {p2_bundle} {curr} مع منتج تكميلي ثانوي يرفع متوسط قيمة السلة (AOV).",
                "offer": f"اشتري {prod_name} واحصل على المنتج التكميلي بخصم 50% ضمن باقة واحدة متكاملة.",
                "benchmark": "يساعد في تصريف المخزون التكميلي الراكد ورفع إجمالي الدخل من كل عميل.",
                "reason": "العميل يشعر بقيمة توفير مضاعفة، مما يحفزه على الشراء الفوري بدلاً من التردد.",
                "discount_pct": 20,
                "new_price": p2_bundle,
                "projected_units_uplift": 40,
                "ad_copies": {
                    "instagram": f" باقة التوفير الشاملة! احصل على {prod_name} + المنتج الإضافي بسعر مميز جداً: {p2_bundle} {curr} فقط!\n\n الكمية محدودة جداً لهذه الباقة.\n الرابط متاح في البايو للطلب الفوري.\n\n#باقة_توفير #عروض_خاصة #توفير_أكثر #{prod_name.replace(' ', '_')}",
                    "whatsapp": f" عرض الباقة المميزة! وفر أكثر واحصل على باقة *{prod_name}* بسعر خاص *{p2_bundle} {curr}* شاملة الإضافات!\n\n اطلب باقتك الآن: https://yourstore.com/bundle",
                    "tiktok": f" [المشهد]: مقارنة بين شراء منتج واحد وشراء الباقة الكاملة!\n[الصوت]: وفر نصف السعر مع باقة {prod_name} التوفيرية \n#توفير #باكج #عروض_حصرية",
                    "twitter": f" وفر أكثر مع باقة {prod_name} المتكاملة بسعر خاص {p2_bundle} {curr}! متوفرة لفترة محدودة \nhttps://yourstore.com/bundle"
                },
                "timing": {
                    "best_days": "أسبوع الرواتب (25 إلى 30 من الشهر)",
                    "best_hours": "1:00 ظهراً – 4:00 عصراً و 8:00 مساءً",
                    "target_audience": "العائلات والمتسوقون ذوو السلات الكبيرة"
                }
            },
            {
                "id": 2,
                "badge": "حملة تميز وجودة",
                "title": "3. استراتيجية القيمة المضافة والتوصيل السريع",
                "impact": "زيادة الإيرادات المتوقعة: +12%",
                "priceSimulation": f"الحفاظ على السعر عند {p3_premium} {curr} مع تقديم تغليف هدايا فاخر وتوصيل سريع مجاني.",
                "offer": f"تغليف VIP مجاني + شحن سريع بنفس اليوم عند طلب {prod_name}.",
                "benchmark": f"يمتلك '{prod_name}' تقييمات جودة تفوق 90% من منتجات السوق المماثلة.",
                "reason": "يعزز صورة العلامة التجارية ويجذب شريحة العملاء الذين يفضلون الجودة والسرعة على الخصومات.",
                "discount_pct": 0,
                "new_price": p3_premium,
                "projected_units_uplift": 15,
                "ad_copies": {
                    "instagram": f" الجودة الفاخرة التي تستحقها. استمتع بتجربة {prod_name} مع تغليف هدايا راقٍ وشحن سريع مجاني إلى باب بيتك.\n\n اطلب الآن لتجربة استثنائية.\n الرابط في البايو.\n\n#فخامة #جودة_عالية #تميز #{prod_name.replace(' ', '_')}",
                    "whatsapp": f" تميز بتجربة فريدة مع *{prod_name}*. احصل على تغليف هدايا فاخر وشحن سريع مجاني اليوم!\n\n الرابط للطلب المباشر: https://yourstore.com/premium",
                    "tiktok": f" [فيديو فتح الصندوق Unboxing]: استعراض التغليف الفاخر لـ {prod_name}!\n[الصوت]: الجودة التي تبحث عنها وصلت، اطلب واستمتع بالتوصيل الفوري \n#فخامة #تجربة_تسوق #ريفيو",
                    "twitter": f" جودة استثنائية وتوصيل سريع! اطلب {prod_name} الآن واحصل على تغليف VIP مجاناً \nhttps://yourstore.com/premium"
                },
                "timing": {
                    "best_days": "الأحد والاثنين (بداية الأسبوع)",
                    "best_hours": "10:00 صباحاً – 2:00 ظهراً",
                    "target_audience": "الباحثون عن الهدايا والجودة العالية"
                }
            }
        ]
    return scenarios, {
        "base_price": price,
        "estimated_cost": est_cost,
        "currency": curr
    }


@login_required
def save_manual_note(request):
    if request.method == "POST":
        title = request.POST.get("note_title", "ملاحظة مالية").strip()
        content = request.POST.get("note_content", "").strip()
        if content:
            from django.core.files.base import ContentFile
            file_name = f"{title}_{request.user.username}.txt"
            content_file = ContentFile(f"العنوان: {title}\nالمحتوى:\n{content}".encode('utf-8'), name=file_name)
            ProjectFile.objects.create(user=request.user, excel_file=content_file)
            messages.success(request, "تم حفظ الملاحظة المالية بنجاح / Note saved successfully!")
    return redirect("datasets")


@login_required
def chat_history_view(request):
    from .models import CommitteeThread
    ai_logs = AIUsageLog.objects.filter(user=request.user).order_by('-created_at')[:50]
    committee_threads = CommitteeThread.objects.filter(user=request.user).order_by('-created_at')[:30]
    boardroom_sessions = BoardroomSession.objects.filter(user=request.user).order_by('-created_at')[:20]
    return render(request, "dashboard/chat_history.html", {
        "ai_logs": ai_logs,
        "logs": ai_logs,
        "committee_threads": committee_threads,
        "boardroom_sessions": boardroom_sessions,
    })


@login_required
def notifications_view(request):
    from .models import Notification
    notifs = Notification.objects.filter(user=request.user).order_by('-created_at')[:50]
    return render(request, "dashboard/notifications.html", {"notifications": notifs})


@login_required
def export_note_report(request):
    from django.http import HttpResponse
    from .models import ProjectFile, DynamicRecord, WeeklyDigest, Profile
    import datetime, re, urllib.parse

    user = request.user
    profile, _ = Profile.objects.get_or_create(user=user)
    company_name = profile.company_name or user.username
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    lang = request.GET.get("lang") or request.session.get("django_language") or "ar"
    is_ar = (lang == "ar")

    file_id = request.GET.get("file_id")
    default_name = "ملف_البيانات_التنفيذي" if is_ar else "Executive_Dataset_Report"
    filename_param = request.GET.get("filename", default_name)
    
    records_qs = DynamicRecord.objects.filter(user=user)
    if file_id and file_id != "all":
        records_qs = records_qs.filter(project_file_id=file_id)

    records = list(records_qs.values_list('row_data', flat=True)[:5000])
    total_records = len(records)
    
    total_revenue = 0.0
    categories_set = set()
    for r in records:
        for k, v in r.items():
            k_low = str(k).lower()
            if any(w in k_low for w in ['category', 'فئة', 'نوع', 'تصنيف', 'item', 'product']):
                categories_set.add(str(v))
            if any(w in k_low for w in ['sales', 'revenue', 'مبيعات', 'إيراد', 'سعر', 'total', 'price']):
                try:
                    num = float(str(v).replace(',', '').replace('OMR', '').replace('ر.ع.', '').strip())
                    if num > 0:
                        total_revenue += num
                except (ValueError, TypeError):
                    pass

    digest = WeeklyDigest.objects.filter(user=user).order_by('-created_at').first()

    if is_ar:
        report_lines = [
            "=" * 72,
            "                    تقرير منصة بصيرة للذكاء المالي",
            "                (Baseera Executive Intelligence Report)",
            "=" * 72,
            f"تاريخ التقرير: {now_str}",
            f"المنشأة: {company_name}",
            f"المستخدم: {user.username}",
            f"نطاق التحليل: {filename_param}",
            "-" * 72,
            "",
            "[1] ملخص الأداء العام والمؤشرات المالية:",
            "-" * 72,
            f"• إجمالي السجلات والمعاملات المفحوصة: {total_records:,} معاملة",
            f"• إجمالي الإيرادات المرصودة: {total_revenue:,.2f} ر.ع.",
            f"• القطاعات والأنشطة المرتبطة: {', '.join(list(categories_set)[:5]) if categories_set else 'قطاعات عامة'}",
            "",
        ]

        if digest:
            report_lines.extend([
                f"• تقييم نبض الأعمال:",
                f"  {digest.summary_text}",
                "",
                "-" * 72,
                "[2] أبرز المخاطر والتنبيهات المكتشفة:",
                "-" * 72,
            ])
            for idx, risk in enumerate(digest.top_risks, 1):
                report_lines.append(f"{idx}. {risk}")
            if not digest.top_risks:
                report_lines.append("لا توجد مخاطر حرجة مرصودة حالياً.")

            report_lines.extend([
                "",
                "-" * 72,
                "[3] خطة العمل والتوصيات التنفيذية:",
                "-" * 72,
            ])
            for idx, act in enumerate(digest.action_plan, 1):
                report_lines.append(f"{idx}. {act}")
            if not digest.action_plan:
                report_lines.append("العمليات مستقرة وتخضع للرقابة الدورية.")
        else:
            report_lines.extend([
                "• تقييم نبض الأعمال: أظهر الفحص استقراراً في مؤشرات الأداء الأساسية.",
                "",
                "-" * 72,
                "[2] التوصيات التنفيذية:",
                "-" * 72,
                "1. المتابعة المستمرة للمؤشرات المالية ومعدل دوران المخزون.",
                "2. تحديث ومزامنة البيانات دورياً لتوليد تقارير استباقية.",
            ])

        report_lines.extend([
            "",
            "=" * 72,
            "تم التوثيق بواسطة: منصة بصيرة للذكاء الاصطناعي وإدارة الأعمال (Baseera.om)",
            "جميع الحقوق محفوظة 2026 - وثيقة رسمية",
            "=" * 72,
        ])
    else:
        report_lines = [
            "=" * 72,
            "                    Baseera Executive Intelligence Report",
            "                  (Autonomous Business Intelligence Suite)",
            "=" * 72,
            f"Report Date: {now_str}",
            f"Organization: {company_name}",
            f"User: {user.username}",
            f"Analysis Scope: {filename_param}",
            "-" * 72,
            "",
            "[1] Executive Performance Summary & Financial KPIs:",
            "-" * 72,
            f"• Total Records Analyzed: {total_records:,} transactions",
            f"• Total Revenue Tracked: {total_revenue:,.2f} OMR",
            f"• Key Categories / Sectors: {', '.join(list(categories_set)[:5]) if categories_set else 'General Business'}",
            "",
        ]

        if digest:
            report_lines.extend([
                f"• Business Pulse Diagnostic:",
                f"  {digest.summary_text}",
                "",
                "-" * 72,
                "[2] Key Risks & Discovered Alerts:",
                "-" * 72,
            ])
            for idx, risk in enumerate(digest.top_risks, 1):
                report_lines.append(f"{idx}. {risk}")
            if not digest.top_risks:
                report_lines.append("No critical risks flagged at this time.")

            report_lines.extend([
                "",
                "-" * 72,
                "[3] Strategic Action Plan & Executive Decisions:",
                "-" * 72,
            ])
            for idx, act in enumerate(digest.action_plan, 1):
                report_lines.append(f"{idx}. {act}")
            if not digest.action_plan:
                report_lines.append("Operations are stable and under periodic review.")
        else:
            report_lines.extend([
                "• Business Pulse Diagnostic: Overall operational KPIs indicate steady baseline stability.",
                "",
                "-" * 72,
                "[2] Recommended Next Steps:",
                "-" * 72,
                "1. Regularly review working capital and cash conversion cycles.",
                "2. Maintain periodic data syncs for autonomous proactive forecasting.",
            ])

        report_lines.extend([
            "",
            "=" * 72,
            "Generated & Certified by: Baseera Intelligence Suite (Baseera.om)",
            "All Rights Reserved 2026 - Confidential Official Document",
            "=" * 72,
        ])

    full_text = "\n".join(report_lines)
    response = HttpResponse(full_text, content_type="text/plain; charset=utf-8")
    
    safe_ascii = re.sub(r'[^\w\s-]', '', filename_param or 'report').strip().replace(' ', '_')
    encoded_name = urllib.parse.quote(f"{filename_param}_note.txt")
    response['Content-Disposition'] = f'attachment; filename="{safe_ascii}_note.txt"; filename*=UTF-8\'\'{encoded_name}\''
    return response



def agents_workspace(request):
    """
    Sully.ai Inspired Multi-Agent Workspace
    """
    context = {
        'active_page': 'agents_workspace',
        'user': request.user
    }
    return render(request, 'dashboard/agents_workspace.html', context)


def api_dynamic_chat(request):
    """
    Alias for chat_api, specifically for the Multi-Agent Workspace.
    """
    return chat_api(request)


@login_required
def save_receipt_record(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body.decode('utf-8'))
            return JsonResponse({"status": "success", "message": "Receipt recorded successfully"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


@login_required
def api_committee_save_thread(request):
    """Saves or updates a multi-agent committee thread and messages."""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            thread_id = data.get("thread_id")
            title = data.get("title", "محادثة جماعية جديدة")
            selected_agents = data.get("selected_agents", [])
            messages_data = data.get("messages", [])

            if request.user.is_authenticated:
                user = request.user
                if thread_id:
                    thread, _ = CommitteeThread.objects.get_or_create(
                        id=thread_id,
                        user=user,
                        defaults={"title": title, "selected_agents": selected_agents}
                    )
                    thread.title = title
                    thread.selected_agents = selected_agents
                    thread.save()
                else:
                    thread = CommitteeThread.objects.create(
                        user=user,
                        title=title,
                        selected_agents=selected_agents
                    )

                # Clear and rewrite or append messages
                if messages_data:
                    thread.messages.all().delete()
                    for m in messages_data:
                        CommitteeMessage.objects.create(
                            thread=thread,
                            role=m.get("role", "assistant"),
                            agent_id=m.get("agent_id", "general"),
                            agent_name=m.get("agent_name", "بصيرة"),
                            agent_avatar=m.get("agent_avatar", "sparkles"),
                            agent_color=m.get("agent_color", "indigo"),
                            content=m.get("content", "")
                        )

                return JsonResponse({"status": "success", "thread_id": thread.id})
            return JsonResponse({"status": "guest", "message": "Guest thread handled client-side"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=400)


def api_committee_get_threads(request):
    """Retrieves all committee threads for the authenticated user."""
    if not request.user.is_authenticated:
        return JsonResponse({"status": "guest", "threads": []})
    
    threads = CommitteeThread.objects.filter(user=request.user).order_by("-updated_at")
    result = []
    for t in threads:
        result.append({
            "id": t.id,
            "title": t.title,
            "selected_agents": t.selected_agents,
            "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M"),
            "messages_count": t.messages.count()
        })
    return JsonResponse({"status": "success", "threads": result})


@login_required
def api_auto_save_document(request):
    """
    Asynchronously auto-archives Ask Basira generated executive reports & recommendations
    into the user's Documents section (ApprovedPlan / sandbox).
    """
    if request.method == "POST":
        try:
            import json, os, datetime, re
            from django.conf import settings
            from .models import ApprovedPlan, Notification

            data = json.loads(request.body)
            title = data.get("title", "").strip()
            content = data.get("content", "").strip()
            category = data.get("category", "Executive Insights & Strategies")
            source = data.get("source", "Ask Basira")
            user_query = data.get("user_query", "").strip()

            if not content or len(content) < 40:
                return JsonResponse({"status": "ignored", "message": "Content too short for archiving"})

            if not title:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                if user_query:
                    clean_q = re.sub(r'[^\w\s\u0600-\u06FF]', '', user_query)[:40].strip()
                    title = f"تقرير: {clean_q} ({now_str})"
                else:
                    title = f"تقرير تحليلي من بصيرة ({now_str})"

            plans_dir = os.path.join(settings.BASE_DIR, 'sandbox', 'approved_plans')
            os.makedirs(plans_dir, exist_ok=True)
            timestamp_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            file_rel_path = f"sandbox/approved_plans/doc_{request.user.id}_{timestamp_str}.md"
            full_file_path = os.path.join(settings.BASE_DIR, file_rel_path)

            doc_header = f"# {title}\n**المصدر / Source:** {source}\n**التصنيف / Category:** {category}\n**التاريخ / Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
            with open(full_file_path, "w", encoding="utf-8") as f:
                f.write(doc_header + content)

            justification = f"أرشفة تلقائية من {source} - {category}"
            approved_plan = ApprovedPlan.objects.create(
                user=request.user,
                file_name=title,
                file_path=file_rel_path,
                justification=justification
            )

            return JsonResponse({
                "status": "success",
                "message": "تم حفظ التحليل والتوصيات في المستندات بنجاح",
                "doc_id": approved_plan.id,
                "title": title
            })
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=405)


@login_required
def api_apply_agent_decision(request):
    """
    Endpoint triggered by the frontend when a user clicks the "Apply Action" button
    generated by an agent's tool call.
    Applies the decision into Approved Plans, Notifications, Agent Memory, DecisionMetrics, and Weekly Digest Action Plan.
    """
    if request.method == "POST":
        try:
            import json, os, datetime, re
            from django.conf import settings
            from .models import ApprovedPlan, Notification, AgentMemory, AnomalyAlert, WeeklyDigest, DecisionMetric
            
            data = json.loads(request.body)
            action_payload = data.get("action_payload", "")
            plan_content = data.get("plan_content", "").strip()
            custom_title = data.get("plan_title", "").strip()

            # Determine plan title and details
            if custom_title:
                plan_title = custom_title
            elif "waste" in action_payload.lower() or "audit" in action_payload.lower():
                plan_title = "خطة معالجة الهدر والتدقيق المالي"
            elif "pricing" in action_payload.lower():
                plan_title = "خطة تحسين هيكل وهوامش التسعير"
            elif "finance" in action_payload.lower() or "cash" in action_payload.lower():
                plan_title = "خطة ضبط السيولة والتدفق النقدي"
            elif "retention" in action_payload.lower() or "churn" in action_payload.lower():
                plan_title = "خطة تعزيز ولاء العملاء والاحتفاظ"
            elif "supply" in action_payload.lower() or "inventory" in action_payload.lower():
                plan_title = "خطة إدارة المخزون وسلاسل الإمداد"
            else:
                plan_title = "الخطة الاستراتيجية المعتمدة لتحسين الأداء"

            justification = f"تم اعتماد الخطة التنفيذية بناءً على مخرجات التحليل الذكي وموافقة الإدارة لمتابعة التنفيذ الفوري."

            # Save full detailed plan text to file in sandbox/approved_plans
            plans_dir = os.path.join(settings.BASE_DIR, 'sandbox', 'approved_plans')
            os.makedirs(plans_dir, exist_ok=True)
            timestamp_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            file_rel_path = f"sandbox/approved_plans/plan_{request.user.id}_{timestamp_str}.txt"
            full_file_path = os.path.join(settings.BASE_DIR, file_rel_path)

            final_body_text = plan_content if plan_content else justification
            with open(full_file_path, "w", encoding="utf-8") as pf:
                pf.write(final_body_text)

            # 1. Save to Approved Plans
            approved_plan = ApprovedPlan.objects.create(
                user=request.user,
                file_name=plan_title,
                file_path=file_rel_path,
                justification=justification
            )

            # 2. Add Notification
            Notification.objects.create(
                user=request.user,
                title="قرار تنفيذي معتمد",
                message=f"تم اعتماد قرار تنفيذي جديد: {plan_title}",
                type="success"
            )

            # 3. Add to Agent Memory
            AgentMemory.objects.create(
                user=request.user,
                content=f"قرار معتمد: {plan_title} - الإجراء: {action_payload}",
                embedding=[]
            )

            # 4. Update or append to Weekly Digest Action Plan with parsed actionable recommendations
            weekly_digest = WeeklyDigest.objects.filter(user=request.user).order_by('-created_at').first()
            if not weekly_digest:
                weekly_digest = WeeklyDigest.objects.create(
                    user=request.user,
                    week_label=datetime.datetime.now().strftime("الأسبوع %W - %Y"),
                    summary_text="تم إنشاء خطة عمل تنفيذية بناءً على قرارات الذكاء الاصطناعي المعتمدة.",
                    top_risks=[],
                    top_opportunities=[],
                    action_plan=[]
                )

            if not weekly_digest.action_plan or not isinstance(weekly_digest.action_plan, list):
                weekly_digest.action_plan = []
            
            # Extract bullet recommendations if present
            extracted_bullets = []
            if plan_content:
                for line in plan_content.splitlines():
                    clean_line = line.strip()
                    if (clean_line.startswith(('-', '*', '•', '1.', '2.', '3.', '4.', '5.'))) and len(clean_line) > 15:
                        clean_item = re.sub(r'^[-*•\d.]+\s*', '', clean_line).strip()
                        clean_item = re.sub(r'[*_`#]', '', clean_item).strip()
                        if clean_item and len(clean_item) > 10:
                            extracted_bullets.append(clean_item)

            new_main_item = f"✓ [خطة معتمدة] {plan_title}"
            if new_main_item not in weekly_digest.action_plan:
                weekly_digest.action_plan.insert(0, new_main_item)
            
            # Insert top 2 extracted actionable points
            for bullet in extracted_bullets[:2]:
                bullet_entry = f"• {bullet}"
                if bullet_entry not in weekly_digest.action_plan:
                    weekly_digest.action_plan.insert(1, bullet_entry)

            weekly_digest.save()

            # 5. Create or Update DecisionMetric
            metric_status = "improving"
            if "declining" in action_payload.lower():
                metric_status = "declining"
            elif "stable" in action_payload.lower():
                metric_status = "stable"
                
            DecisionMetric.objects.update_or_create(
                user=request.user,
                name=plan_title,
                defaults={
                    "status": metric_status,
                    "last_modified_by_agent": "Ask Basira"
                }
            )

            # 6. Dismiss any matching anomaly alerts if this was a resolution
            if "waste" in action_payload.lower() or "audit" in action_payload.lower() or "cost" in action_payload.lower():
                AnomalyAlert.objects.filter(user=request.user, is_dismissed=False).update(is_dismissed=True)

            return JsonResponse({
                "status": "success",
                "message": "تم اعتماد الخطة وتحديث منصة القرارات والمستندات بنجاح!",
                "plan_id": approved_plan.id,
                "plan_title": plan_title,
                "redirect_url": "/dashboard/?applied=true#action-plan"
            })
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=405)



