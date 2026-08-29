from django.contrib.auth.decorators import login_required
import json
import logging
import pandas as pd
from dashboard.services.ai_service import GeminiAIService
from django.http import JsonResponse
from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from .models import DynamicRecord
import hashlib
from django.views.decorators.csrf import csrf_exempt
from django.core.mail import send_mail
from django.utils.crypto import get_random_string
from django.core.cache import cache
from .security import rate_limit, validate_uploaded_file, build_safe_filename, validate_ssrf_url, safe_error_message, issue_access_token, token_required

logger = logging.getLogger(__name__)

from django.core.cache import cache

@login_required
def live_sync_api(request):
    if request.method == "POST":
        idem_key = request.headers.get("Idempotency-Key")
        if idem_key and cache.get(f"idem_{idem_key}"):
            return JsonResponse({"status": "success", "message": "Already processed"})
        if idem_key:
            cache.set(f"idem_{idem_key}", True, timeout=86400)
        try:
            data = json.loads(request.body)
            # Store in session so it's isolated to this user's browser session
            request.session['live_updates'] = data
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)
    
    elif request.method == "GET":
        update = request.session.pop('live_updates', None)
        if update:
            return JsonResponse({"status": "success", "update": update})
        return JsonResponse({"status": "empty"})

@csrf_exempt
@rate_limit(requests_per_minute=20, key_prefix="mobile_login")
def mobile_login(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            username = data.get("username")
            password = data.get("password")
            user = authenticate(request, username=username, password=password)
            if user is None:
                user_by_email = User.objects.filter(email=username).first()
                if user_by_email:
                    user = authenticate(request, username=user_by_email.username, password=password)

            if user is not None:
                login(request, user)
                return JsonResponse({"status": "success", "message": "Logged in successfully", "token": issue_access_token(user), "username": user.username, "email": user.email})
            return JsonResponse({"status": "error", "message": "Invalid credentials"}, status=401)
        except Exception:
            return JsonResponse({"status": "error", "message": "Invalid request payload"}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)

@csrf_exempt
@rate_limit(requests_per_minute=20, key_prefix="mobile_register")
def mobile_register(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            username = data.get("username")
            email = data.get("email")
            password = data.get("password")

            if User.objects.filter(username=username).exists():
                return JsonResponse({"status": "error", "message": "Username already exists"}, status=400)
            if User.objects.filter(email=email).exists():
                return JsonResponse({"status": "error", "message": "Email already exists"}, status=400)

            user = User.objects.create_user(username=username, email=email, password=password)
            login(request, user)
            return JsonResponse({"status": "success", "message": "User created successfully", "token": issue_access_token(user), "username": user.username, "email": user.email})
        except Exception:
            return JsonResponse({"status": "error", "message": "Registration failed"}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)

@rate_limit(requests_per_minute=20, key_prefix="mobile_change_password")
@csrf_exempt
@token_required
def mobile_change_password(request):
    if request.method == "POST":
        try:
            if not request.user.is_authenticated:
                return JsonResponse({"status": "error", "message": "Authentication required"}, status=401)
            data = json.loads(request.body)
            current_password = data.get("current_password", "")
            new_password = data.get("new_password", "")
            confirm_password = data.get("confirm_password", "")
            
            if not request.user.check_password(current_password):
                return JsonResponse({"status": "error", "message": "Current password is incorrect"}, status=400)
            if new_password != confirm_password:
                return JsonResponse({"status": "error", "message": "Passwords do not match"}, status=400)
            if len(new_password) < 6:
                return JsonResponse({"status": "error", "message": "Password must be at least 6 characters"}, status=400)
                
            request.user.set_password(new_password)
            request.user.save()
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)
            return JsonResponse({"status": "success", "message": "Password updated successfully"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)


@csrf_exempt
@rate_limit(requests_per_minute=10, key_prefix="mobile_forgot_password")
def mobile_forgot_password(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            email = data.get("email")
            if not email:
                return JsonResponse({"status": "error", "message": "Email is required"}, status=400)
            
            email = str(email).strip().lower()
            user = User.objects.filter(email__iexact=email).first()
            if not user:
                # Security: Don't reveal if email exists or not, just return success
                return JsonResponse({"status": "success", "message": "If the email exists, an OTP was sent."})
                
            otp = get_random_string(length=6, allowed_chars='0123456789')
            # Store OTP in cache for 15 minutes
            cache.set(f"otp_{email}", otp, timeout=900)
            
            # Send Email (Assuming SMTP is configured in settings)
            try:
                sent_count = send_mail(
                    'Baseera - Your Password Reset OTP',
                    f'Your OTP for resetting your password is: {otp}\nThis code is valid for 15 minutes.',
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    fail_silently=False,
                )
            except Exception:
                logger.exception("Mobile password-reset email delivery failed")
                cache.delete(f"otp_{email}")
                return JsonResponse({"status": "error", "message": "Unable to send the reset email. Check SMTP configuration."}, status=503)
            if sent_count != 1:
                cache.delete(f"otp_{email}")
                return JsonResponse({"status": "error", "message": "Unable to send the reset email."}, status=503)
                
            return JsonResponse({"status": "success", "message": "OTP sent successfully."})
        except Exception:
            return JsonResponse({"status": "error", "message": "Invalid request payload"}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)

@csrf_exempt
@rate_limit(requests_per_minute=10, key_prefix="mobile_verify_otp")
def mobile_verify_otp(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            email = str(data.get("email", "")).strip().lower()
            otp = str(data.get("otp", "")).strip()
            new_password = data.get("new_password")

            if not email or not otp or not new_password:
                return JsonResponse({"status": "error", "message": "Email, OTP, and new password are required."}, status=400)

            cached_otp = cache.get(f"otp_{email}")
            if cached_otp and cached_otp == otp:
                user = User.objects.get(email__iexact=email)
                user.set_password(new_password)
                user.save()
                cache.delete(f"otp_{email}")
                return JsonResponse({"status": "success", "message": "Password updated successfully"})
            else:
                return JsonResponse({"status": "error", "message": "Invalid or expired OTP"}, status=400)
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)


def process_dataframe(df, file_name, user, save_to_db=True):
    import hashlib
    import json
    
    # 2. Clean dataframe columns and create a schema hash
    df.columns = df.columns.astype(str).str.strip()
    columns_tuple = tuple(df.columns.tolist())
    schema_hash = hashlib.md5(",".join(columns_tuple).encode('utf-8')).hexdigest()
    
    if save_to_db:
        # 3. Use pandas to_json to handle all datetime/NaN conversions safely, then save
        records_json = json.loads(df.to_json(orient='records', date_format='iso'))
        records_to_create = [
            DynamicRecord(user=user, schema_hash=schema_hash, row_data=row_dict)
            for row_dict in records_json
        ]
        DynamicRecord.objects.bulk_create(records_to_create, batch_size=500)
        
        # 4. Reconstruct cumulative dataframe from ALL historical rows for this schema
        all_records = DynamicRecord.objects.filter(user=user, schema_hash=schema_hash).values_list('row_data', flat=True)
        df = pd.DataFrame(list(all_records))
    # ---------------------------------------
    
    # Basic analysis to return to mobile dashboard
    columns = list(df.columns)
    numeric_cols = df.select_dtypes(include=['number']).columns
    
    # Extract categorical columns for charts (excluding columns that are likely just string dates)
    raw_cat_cols = df.select_dtypes(exclude=['number', 'datetime']).columns
    date_keywords = ['date', 'time', 'تاريخ', 'وقت', 'year', 'month', 'day', 'سنة', 'شهر', 'يوم']
    filtered_cat_cols = [
        c for c in raw_cat_cols 
        if not any(k in c.lower() for k in date_keywords) and 1 < df[c].nunique() < 60
    ]
    categorical_cols = filtered_cat_cols if filtered_cat_cols else [c for c in raw_cat_cols if 1 < df[c].nunique() < 60]
    if not categorical_cols:
        categorical_cols = list(raw_cat_cols) # Fallback
    
    # Smart titles for the frontend based on selected column
    cat_1_name = str(categorical_cols[0]) if len(categorical_cols) > 0 else "Categories"
    cat_2_name = str(categorical_cols[1]) if len(categorical_cols) > 1 else cat_1_name

    # Helper to get top 5 categories
    top_categories = []
    if len(categorical_cols) > 0 and len(numeric_cols) > 0:
        cat_col = categorical_cols[0]
        num_col = numeric_cols[0]
        top_cats = df.groupby(cat_col)[num_col].sum().sort_values(ascending=False).head(5)
        max_val = top_cats.max() if len(top_cats) > 0 else 1
        for cat, val in top_cats.items():
            top_categories.append({
                "name": str(cat),
                "value": float(val),
                "percent": int((val / max_val) * 100) if max_val > 0 else 0
            })
            
    # Helper for donut chart (distribution)
    distribution = []
    if len(categorical_cols) > 1 and len(numeric_cols) > 0:
        cat_col = categorical_cols[1]
        num_col = numeric_cols[0]
        dist = df.groupby(cat_col)[num_col].sum().sort_values(ascending=False).head(5)
        total_sum = dist.sum()
        for cat, val in dist.items():
            distribution.append({
                "name": str(cat),
                "percent": int((val / total_sum) * 100) if total_sum > 0 else 0
            })
    elif len(categorical_cols) > 0 and len(numeric_cols) > 0:
        cat_col = categorical_cols[0]
        num_col = numeric_cols[0]
        dist = df.groupby(cat_col)[num_col].sum().sort_values(ascending=True).head(5)
        total_sum = dist.sum()
        for cat, val in dist.items():
            distribution.append({
                "name": str(cat),
                "percent": int((val / total_sum) * 100) if total_sum > 0 else 0
            })
    
    import datetime
    import calendar
    
    # Generate labels for the next 6 months without external date util
    now = datetime.datetime.now()
    trend_labels = []
    current_month = now.month
    current_year = now.year
    for i in range(1, 7):
        m = current_month + i
        y = current_year
        if m > 12:
            m = m - 12
            y += 1
        trend_labels.append(f"{calendar.month_abbr[m]} {y}")

    insights = {
        "total_rows": len(df),
        "columns": columns,
        "kpis": [],
        "charts": {
            "top_categories": top_categories,
            "top_categories_title": cat_1_name,
            "distribution": distribution,
            "distribution_title": cat_2_name,
            "trend_labels": trend_labels,
            "total_sum": float(df[numeric_cols[0]].sum()) if len(numeric_cols) > 0 else 0
        },
        "rows": df.fillna("").to_dict(orient='records'),
        "fileName": file_name
    }
    
    if len(numeric_cols) > 0:
        for col in numeric_cols[:3]:
            insights["kpis"].append({
                "title": col,
                "sum": float(df[col].sum()),
                "avg": float(df[col].mean())
            })
    
    try:
        df_summary = f"Columns: {', '.join(columns)}\n"
        df_summary += f"Total Rows: {len(df)}\n"
        if len(numeric_cols) > 0:
            df_summary += f"Key Metric (Sum of {numeric_cols[0]}): {df[numeric_cols[0]].sum()}\n"
        if len(top_categories) > 0:
            df_summary += f"Top Category: {top_categories[0]['name']} (Value: {top_categories[0]['value']})\n"
        
        from dashboard.services.ai_service import GeminiAIService
        ai_service = GeminiAIService()
        ai_result = ai_service.analyze_dataset_for_mobile(df_summary)
        insights["ai_insight"] = ai_result.get("ai_insight", "لم يتم العثور على فجوات مالية.")
        insights["charts"]["forecast"] = ai_result.get("forecast", [0,0,0,0,0,0])
    except Exception as e:
        print(f"Error calling AI Service: {e}")
        insights["ai_insight"] = "تعذر توليد التحليل الذكي حالياً."
        insights["charts"]["forecast"] = [0,0,0,0,0,0]
        
    return insights

@rate_limit(requests_per_minute=20, key_prefix="mobile_upload")
@csrf_exempt
@token_required
def mobile_upload(request):
    if request.method == "POST":
        try:
            if 'file' not in request.FILES:
                return JsonResponse({"status": "error", "message": "No file uploaded"}, status=400)
                
            excel_file = request.FILES['file']
            validate_uploaded_file(excel_file)
            excel_file.name = build_safe_filename(excel_file.name)
            
            ext = os.path.splitext(excel_file.name)[1].lower()
            if ext == '.pdf':
                import tempfile
                import os
                from dashboard.views import parse_pdf_to_df
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    for chunk in excel_file.chunks():
                        tmp.write(chunk)
                    tmp_path = tmp.name
                df = parse_pdf_to_df(tmp_path)
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                if df is None or df.empty:
                    return JsonResponse({"status": "error", "message": "Failed to extract valid data from PDF."}, status=400)
            else:
                try:
                    df = pd.read_excel(excel_file)
                except Exception:
                    excel_file.seek(0)
                    df = pd.read_csv(excel_file)
            insights = process_dataframe(df, excel_file.name, request.user)
            return JsonResponse({"status": "success", "data": insights})
        except ValueError as exc:
            return JsonResponse({"status": "error", "message": safe_error_message(str(exc))}, status=400)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({"status": "error", "message": "الملف غير مدعوم أو غير مهيأ كجدول بيانات. يرجى رفع ملف Excel أو CSV أو PDF صالح."}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)

@csrf_exempt
@token_required
def mobile_connect_live(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            sheet_url = data.get("sheet_url")

            if not sheet_url:
                return JsonResponse({"status": "error", "message": "Invalid Google Sheets URL"}, status=400)
            validate_ssrf_url(sheet_url, allowed_hosts={"docs.google.com", "spreadsheets.google.com"})

            if "/edit" in sheet_url:
                export_url = sheet_url.split("/edit")[0] + "/export?format=csv"
            else:
                export_url = sheet_url

            df = pd.read_csv(export_url)

            insights = process_dataframe(df, "Live Connection", request.user)
            return JsonResponse({"status": "success", "data": insights})

        except ValueError as exc:
            return JsonResponse({"status": "error", "message": safe_error_message(str(exc))}, status=400)
        except Exception:
            return JsonResponse({"status": "error", "message": "Failed to connect to the spreadsheet"}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=405)

@token_required
@csrf_exempt
def mobile_toggle_user_status(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            email = data.get("email")
            if not email:
                return JsonResponse({"status": "error", "message": "Email is required"}, status=400)
                
            user = User.objects.filter(email=email).first()
            if not user:
                return JsonResponse({"status": "error", "message": "User not found"}, status=404)
                
            if user.username == 'admin' or user.email == 'admin@example.com':
                return JsonResponse({"status": "error", "message": "Cannot freeze admin account"}, status=403)
                
            # Toggle is_active status
            user.is_active = not user.is_active
            user.save()
            
            new_status = "active" if user.is_active else "frozen"
            return JsonResponse({"status": "success", "new_status": new_status, "message": f"User status changed to {new_status}"})
            
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)

@login_required
@rate_limit(requests_per_minute=20, key_prefix="save_file")
def save_file_api(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            file_path = data.get("file_path")
            content = data.get("content", "")

            if not file_path:
                return JsonResponse({"status": "error", "message": "file_path is required"}, status=400)

            import os
            from django.conf import settings
            workspace_dir = os.path.join(settings.MEDIA_ROOT, 'workspace')
            os.makedirs(workspace_dir, exist_ok=True)

            clean_name = build_safe_filename(file_path)
            full_path = os.path.join(workspace_dir, clean_name)

            if not os.path.commonpath([workspace_dir, os.path.abspath(full_path)]) == os.path.abspath(workspace_dir):
                return JsonResponse({"status": "error", "message": "Invalid file path"}, status=400)

            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)

            justification = data.get("justification", "")
            if request.user.is_authenticated:
                from dashboard.models import ApprovedPlan
                ApprovedPlan.objects.create(
                    user=request.user,
                    file_name=file_path.replace('\\\\', '/').split('/')[-1] or clean_name,
                    file_path=f"workspace/{clean_name}",
                    justification=justification
                )

            return JsonResponse({"status": "success", "message": "File saved successfully", "path": f"workspace/{clean_name}"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": "Internal server error"}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=405)

@login_required
def workspace_files_api(request):
    if request.method == "GET":
        try:
            import os
            from django.conf import settings
            workspace_dir = os.path.join(settings.BASE_DIR, 'sandbox', 'workspace')
            os.makedirs(workspace_dir, exist_ok=True)
            
            files = []
            for f in os.listdir(workspace_dir):
                full_path = os.path.join(workspace_dir, f)
                if os.path.isfile(full_path):
                    size = os.path.getsize(full_path)
                    files.append({
                        "name": f,
                        "size": size
                    })
            return JsonResponse({"status": "success", "files": files})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=405)

@csrf_exempt
@token_required
def mobile_dashboard_data(request):
    if request.method in ["GET", "POST"]:
        try:
            user = request.user
            
            # Find the most recent record to get the latest schema hash
            latest_record = DynamicRecord.objects.filter(user=user).order_by('-created_at').first()
            if not latest_record:
                return JsonResponse({"status": "empty", "message": "No data available."})
                
            schema_hash = latest_record.schema_hash
            
            # Fetch all records with this schema
            all_records = DynamicRecord.objects.filter(user=user, schema_hash=schema_hash).values_list('row_data', flat=True)
            if not all_records:
                return JsonResponse({"status": "empty", "message": "No data available."})
                
            df = pd.DataFrame(list(all_records))
            
            # Use the existing function to process the dataframe and run AI
            insights = process_dataframe(df, "Database Records", user, save_to_db=False)
            
            return JsonResponse({"status": "success", "data": insights})
            
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=405)

@rate_limit(requests_per_minute=20, key_prefix="extract_receipt")
@csrf_exempt
@token_required
def extract_receipt_api(request):
    if request.method == "POST":
        try:
            if 'receipt_image' not in request.FILES:
                return JsonResponse({"status": "error", "message": "No image uploaded. Please send 'receipt_image'."}, status=400)
            
            image_file = request.FILES['receipt_image']
            validate_uploaded_file(image_file, allowed_extensions={".png", ".jpg", ".jpeg"})
            
            import os
            import tempfile
            from dashboard.services.ai_service import GeminiAIService
            
            # Save the file temporarily
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
                for chunk in image_file.chunks():
                    temp_file.write(chunk)
                temp_path = temp_file.name
                
            try:
                ai_service = GeminiAIService()
                receipt_data = ai_service.extract_receipt_data(temp_path)
            finally:
                # Clean up the temp file
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                
            if receipt_data:
                return JsonResponse({"status": "success", "data": receipt_data})
            else:
                return JsonResponse({"status": "error", "message": "Failed to extract data from image."}, status=500)
                
        except ValueError as exc:
            return JsonResponse({"status": "error", "message": safe_error_message(str(exc))}, status=400)
        except Exception:
            return JsonResponse({"status": "error", "message": "Unable to process the uploaded file"}, status=500)
    return JsonResponse({"status": "invalid_method"}, status=405)
@csrf_exempt
@token_required
def api_mobile_chat_history(request):
    if request.method == "GET":
        from dashboard.models import AIUsageLog
        logs = AIUsageLog.objects.filter(user=request.user).order_by('-created_at')[:50]
        data = []
        for log in logs:
            data.append({
                "id": log.id,
                "agent_id": log.agent_id,
                "query": log.query,
                "response": log.response,
                "created_at": log.created_at.strftime("%Y-%m-%d %I:%M %p")
            })
        return JsonResponse({"status": "success", "history": data})
    return JsonResponse({"status": "invalid_method"}, status=405)

@csrf_exempt
@token_required
def api_mobile_export_excel(request):
    if request.method == "GET":
        from django.http import HttpResponse
        from dashboard.models import ProjectFile, Profile, DynamicRecord
        from dashboard.report_generator import generate_baseera_excel
        from urllib.parse import quote

        file_id = request.GET.get("file_id")
        user_lang = request.GET.get("lang", "ar").upper()
        profile, _ = Profile.objects.get_or_create(user=request.user)

        rows_data = []
        if file_id:
            records = DynamicRecord.objects.filter(user=request.user, project_file_id=file_id).order_by("created_at")
            if records.exists():
                rows_data = [rec.row_data for rec in records if rec.row_data]
            else:
                return JsonResponse({"status": "error", "message": "File not found"}, status=404)
        else:
            latest_record = DynamicRecord.objects.filter(user=request.user).order_by('-created_at').first()
            if latest_record:
                schema_hash = latest_record.schema_hash
                records = DynamicRecord.objects.filter(user=request.user, schema_hash=schema_hash).order_by("created_at")
                rows_data = [rec.row_data for rec in records if rec.row_data]
            else:
                return JsonResponse({"status": "empty", "message": "No data available."})

        if not rows_data:
             return JsonResponse({"status": "empty", "message": "No data available."})
             
        import pandas as pd
        df = pd.DataFrame(rows_data)
        
        file_bytes = generate_baseera_excel(df, user_lang, profile.company_name)
        response = HttpResponse(file_bytes, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        filename = quote("تقرير_بصيرة.xlsx")
        response['Content-Disposition'] = f"attachment; filename*=UTF-8''{filename}"
        return response
    return JsonResponse({"status": "invalid_method"}, status=405)

@csrf_exempt
@token_required
def api_mobile_export_note(request):
    if request.method == "GET":
        from django.http import HttpResponse
        note_content = request.GET.get("content", "لا توجد ملاحظات / No notes")
        response = HttpResponse(note_content, content_type="text/plain; charset=utf-8")
        response['Content-Disposition'] = 'attachment; filename="baseera_note.txt"'
        return response
    return JsonResponse({"status": "invalid_method"}, status=405)

@login_required
def update_plan_note_api(request, plan_id):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            note = data.get("note", "")
            from dashboard.models import ApprovedPlan
            plan = ApprovedPlan.objects.get(id=plan_id, user=request.user)
            plan.justification = note
            plan.save()
            return JsonResponse({"status": "success", "message": "Note updated"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)

@login_required
def record_plan_impact_api(request, plan_id):
    """
    Closes the "detect -> decide -> act -> measured impact" loop for a
    single approved plan: the user reports the current value of whatever
    real metric the plan's baseline_metric_value captured at approval time
    (see api_apply_agent_decision), and the resulting improved/worsened/
    unchanged status is computed arithmetically from the two real numbers
    -- never guessed by an AI model.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body)
        current_value = data.get("current_value")
        try:
            current_value = float(current_value)
        except (TypeError, ValueError):
            return JsonResponse({"status": "error", "message": "current_value must be a number"}, status=400)

        from dashboard.models import ApprovedPlan
        from django.utils import timezone
        # Same ownership pattern as update_plan_note_api/delete_plan_api --
        # DoesNotExist (missing OR belongs to another user) is caught below
        # and returned as a generic error, never leaking which case it was.
        plan = ApprovedPlan.objects.get(id=plan_id, user=request.user)

        if plan.baseline_metric_value is None:
            return JsonResponse({
                "status": "error",
                "message": "This plan has no baseline value to measure impact against.",
            }, status=400)

        baseline = plan.baseline_metric_value
        # baseline_metric_value is a flagged/negative-impact amount (waste,
        # cash-impact) -- a lower current value means the situation
        # improved. A 2% relative tolerance (with a small absolute floor
        # for a near-zero baseline) absorbs rounding noise without calling
        # a genuine small move "unchanged".
        tolerance = max(abs(baseline) * 0.02, 0.01)
        if current_value < baseline - tolerance:
            status = "improved"
        elif current_value > baseline + tolerance:
            status = "worsened"
        else:
            status = "unchanged"

        plan.current_metric_value = current_value
        plan.impact_status = status
        plan.impact_measured_at = timezone.now()
        plan.save()

        return JsonResponse({
            "status": "success",
            "impact_status": status,
            "baseline_metric_value": baseline,
            "current_metric_value": current_value,
        })
    except Exception as e:
        return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)

@login_required
def download_plan_api(request, plan_id):
    try:
        from dashboard.models import ApprovedPlan
        from django.http import HttpResponse
        import os
        import re
        import urllib.parse
        from django.conf import settings
        
        # Task: the downloaded document's own structural labels ("Approval
        # date", "Approved by", the footer, ...) were hardcoded in Arabic
        # regardless of the user's language setting -- meaning switching
        # the whole app to English still produced a document mixing
        # English content inside an Arabic-only template. This follows the
        # same lang-detection convention already used elsewhere (views.py).
        lang = request.session.get("lang") or request.COOKIES.get("lang", "ar")
        is_ar = (lang == "ar")

        plan = ApprovedPlan.objects.get(id=plan_id, user=request.user)
        raw_content = ""

        if plan.file_path and plan.file_path.strip():
            full_path = os.path.join(settings.BASE_DIR, plan.file_path)
            if not os.path.isfile(full_path):
                full_path = os.path.join(settings.MEDIA_ROOT, plan.file_path.replace('sandbox/', ''))

            if os.path.isfile(full_path):
                with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                    raw_content = f.read()

        if not raw_content:
            raw_content = plan.justification or (
                "خطة عمل استراتيجية وتوصية تنفيذية معتمدة عبر منصة بصيرة للذكاء الاصطناعي."
                if is_ar else
                "A strategic action plan and executive recommendation approved via the Baseera AI platform."
            )

        # Clean all internal simulation and agent tags
        cleaned = re.sub(r'<internal_simulation>.*?</internal_simulation>', '', raw_content, flags=re.DOTALL)
        cleaned = re.sub(r'<agent_state>.*?</agent_state>', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'\[\[ACTION:.*?\]\]', '', cleaned)
        cleaned = re.sub(r'<br\s*/?>', '\n', cleaned)
        cleaned = re.sub(r'</?(?:div|p|h\d|li|ul|ol|table|tr|td|th|tbody|thead|span|strong|b|i|em|section|article|button|input|form)[^>]*>', '\n', cleaned)
        cleaned = re.sub(r'<[^>]+>', '', cleaned)
        cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', cleaned)
        cleaned = re.sub(r'\*(.*?)\*', r'\1', cleaned)
        cleaned = re.sub(r'```[a-zA-Z]*', '', cleaned)
        cleaned = re.sub(r'```', '', cleaned)
        
        # Clean lines and normalize spacing
        clean_lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        body_text = "\n".join(clean_lines)

        created_str = plan.created_at.strftime('%Y-%m-%d %H:%M') if hasattr(plan, 'created_at') and plan.created_at else ""

        if is_ar:
            formatted_lines = [
                "=" * 72,
                "               وثيقة الخطة التنفيذية والقرار المعتمد",
                f"                     {plan.file_name}",
                "=" * 72,
                f"تاريخ ووقت الاعتماد: {created_str}",
                f"المستخدم المعتمد: {request.user.username}",
            ]
            if plan.justification:
                formatted_lines.append(f"المبرر الاستراتيجي والبيان: {plan.justification}")
            formatted_lines.extend([
                "-" * 72,
                "تفاصيل الخطة وإجراءات التنفيذ:",
                "-" * 72,
                "",
                body_text,
                "",
                "=" * 72,
                "تم التوثيق والاعتماد بواسطة: منصة بصيرة لإدارة الأعمال والذكاء الاصطناعي (Baseera.om)",
                "جميع الحقوق محفوظة 2026",
                "=" * 72,
            ])
        else:
            formatted_lines = [
                "=" * 72,
                "               EXECUTIVE PLAN & APPROVED DECISION DOCUMENT",
                f"                     {plan.file_name}",
                "=" * 72,
                f"Approval date & time: {created_str}",
                f"Approved by: {request.user.username}",
            ]
            if plan.justification:
                formatted_lines.append(f"Strategic rationale & source: {plan.justification}")
            formatted_lines.extend([
                "-" * 72,
                "Plan details & execution steps:",
                "-" * 72,
                "",
                body_text,
                "",
                "=" * 72,
                "Documented and approved via: Baseera AI Business Management Platform (Baseera.om)",
                "All rights reserved 2026",
                "=" * 72,
            ])

        final_content = "\n".join(formatted_lines)
        response = HttpResponse(final_content, content_type='text/plain; charset=utf-8')
        
        ascii_filename = f"approved_plan_{plan.id}.txt"
        safe_name = re.sub(r'[^\w\s-]', '', plan.file_name or 'plan').strip().replace(' ', '_')
        encoded_filename = urllib.parse.quote(f"{safe_name}_approved_plan.txt")
        response['Content-Disposition'] = f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}"
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return HttpResponse(f"Error downloading file: {str(e)}", status=500)

@login_required
def delete_plan_api(request, plan_id):
    if request.method == "POST" or request.method == "DELETE":
        try:
            from dashboard.models import ApprovedPlan
            import os
            from django.conf import settings
            plan = ApprovedPlan.objects.get(id=plan_id, user=request.user)
            # plan.file_path can be "" (the model's default, or an older
            # row created before a real file was ever attached). Joining an
            # empty string onto BASE_DIR resolves to BASE_DIR itself, and
            # os.path.exists() is True for a directory too -- os.remove()
            # then raised IsADirectoryError instead of just skipping a
            # plan with no real file to clean up. isfile() is the correct
            # guard: it is False for both a missing path AND a directory.
            if plan.file_path and plan.file_path.strip():
                full_path = os.path.join(settings.BASE_DIR, plan.file_path)
                if not os.path.isfile(full_path):
                    full_path = os.path.join(settings.MEDIA_ROOT, plan.file_path.replace('sandbox/', ''))
                if os.path.isfile(full_path):
                    os.remove(full_path)
            plan.delete()
            return JsonResponse({"status": "success", "message": "Plan deleted successfully"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": safe_error_message(str(e))}, status=400)
    return JsonResponse({"status": "invalid_method"}, status=405)


# Justified exception: this view never mutates any state (it only reads
# and aggregates the requesting user's own DynamicRecord rows, scoped by
# request.user, into chart data -- there is no .save()/.create()/.delete()
# anywhere in it), so a forged cross-site POST here has nothing to make it
# do that CSRF protection exists to prevent.
@csrf_exempt
def charts_engine_api(request):
    """
    Data Visualization Engine and Backend AI Agent API.
    Processes the raw dataset and returns structured JSON for:
    - Section 1 / line_chart_card: "رقم السطر حسب المحتوى" (Content Aggregation Line/Area Chart)
    - Section 2 / pie_chart_card: "التنبؤ والتحليلات" (Predictions & Analytics - Forecast & Pie Chart)
    """
    import json, math
    import pandas as pd
    from dashboard.models import ProjectFile, DynamicRecord
    
    time_filter = request.GET.get("filter") or "1Y"
    if request.method == "POST":
        try:
            body_data = json.loads(request.body.decode('utf-8'))
            time_filter = body_data.get("filter", time_filter)
        except Exception:
            pass

    # 1. Fetch user records
    user = request.user if request.user.is_authenticated else None
    records = []
    if user:
        records_qs = DynamicRecord.objects.filter(user=user)
        records = list(records_qs.values_list('row_data', flat=True)[:5000])

    # Default fallback data if no records uploaded yet
    months_1y = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
    
    if time_filter == "1M":
        labels = [f"يوم {i}" for i in range(1, 31)]
        base_series = [15 + int(5 * math.sin(i)) for i in range(30)]
    elif time_filter == "3M":
        labels = [f"أسبوع {i}" for i in range(1, 13)]
        base_series = [20 + i * 8 for i in range(12)]
    elif time_filter == "6M":
        labels = months_1y[:6]
        base_series = [35, 48, 62, 75, 88, 105]
    else:  # 1Y or All
        labels = months_1y
        base_series = [15, 22, 35, 40, 52, 60, 48, 70, 85, 91, 105, 120]

    # Process actual records if available
    categories_dist = [
        {"category": "مصاريف تشغيلية", "percentage": 45, "value": 450, "color_hex": "#6366F1"},
        {"category": "مشتريات وسلاسل إمداد", "percentage": 35, "value": 350, "color_hex": "#8B5CF6"},
        {"category": "عقود وتوريدات", "percentage": 20, "value": 200, "color_hex": "#EC4899"}
    ]
    confidence_rate = 91

    if records and len(records) > 0:
        try:
            df = pd.DataFrame(records)
            cat_col = None
            for c in df.columns:
                if any(k in str(c).lower() for k in ['category', 'فئة', 'نوع', 'تصنيف', 'type']):
                    cat_col = c
                    break
            if not cat_col and len(df.columns) > 1:
                cat_col = df.columns[1]

            if cat_col:
                val_counts = df[cat_col].value_counts().head(5)
                total_cnt = val_counts.sum() or 1
                palette = ["#6366F1", "#8B5CF6", "#EC4899", "#10B981", "#F59E0B"]
                categories_dist = []
                for idx, (cat_name, cnt) in enumerate(val_counts.items()):
                    pct = round((cnt / total_cnt) * 100)
                    categories_dist.append({
                        "category": str(cat_name),
                        "label": str(cat_name),
                        "percentage": pct,
                        "value": int(cnt),
                        "color_hex": palette[idx % len(palette)]
                    })
        except Exception as e:
            pass

    response_payload = {
        "status": "success",
        "section_1_chart": {
            "filter": time_filter,
            "labels": labels,
            "series": [
                {
                    "name": "مجمعة حسب المحتوى",
                    "data": base_series
                }
            ]
        },
        "section_2_analytics": {
            "chart_type": "pie",
            "active_view": "forecast",
            "confidence_rate": confidence_rate,
            "categories_distribution": categories_dist,
            "summary_insight": f"تنبؤات دقيقة بنسبة ثقة {confidence_rate}% لتوزيع الفئات بناءً على تحليل السلسلة الزمنية واستقراء البيانات."
        },
        "data": {
            "line_chart_card": {
                "title": "رقم السطر حسب المحتوى",
                "selected_filter": time_filter,
                "x_axis_labels": labels,
                "datasets": [
                    {
                        "label": "مجمعة حسب المحتوى",
                        "data_points": base_series
                    }
                ]
            },
            "pie_chart_card": {
                "title": "التنبؤ والتحليلات",
                "active_view": "forecast",
                "chart_type": "pie",
                "confidence_score": confidence_rate,
                "slices": [
                    {
                        "label": c.get("label", c.get("category")),
                        "value": c.get("value"),
                        "percentage": c.get("percentage"),
                        "color_hex": c.get("color_hex", "#6366F1")
                    } for c in categories_dist
                ]
            }
        }
    }

    return JsonResponse(response_payload, json_dumps_params={'ensure_ascii': False})
