import os
import magic  # python-magic, for true MIME type checking
import pandas as pd
import logging
from typing import Dict

logger = logging.getLogger(__name__)

FINANCIAL_KEYWORDS = ['سعر', 'تكلفة', 'إجمالي', 'كمية', 'تاريخ', 'إيراد', 'مصروف', 'فاتورة',
                      'رقم الفاتورة', 'price', 'cost', 'total', 'amount', 'qty', 'date',
                      'revenue', 'expense', 'invoice']


def validate_financial_file(file_obj) -> Dict:
    """
    Returns a dict:
    {
        "is_valid": bool,
        "status": "accept" | "warning" | "reject",
        "message": str,               # رسالة عرض للمستخدم
        "accepted_sheets": List[str],  # أسماء الأوراق/الجداول المقبولة فعلياً (بيانات منظمة)
        "rejected_sheets": List[str],
    }
    """

    result = {
        "is_valid": False,
        "status": "reject",
        "message": "",
        "accepted_sheets": [],
        "rejected_sheets": [],
    }

    # ==========================================
    # المرحلة 1: الفحص الشكلي (قبل فتح الملف)
    # ==========================================
    allowed_extensions = ['.xlsx', '.xls', '.csv', '.pdf']
    ext = os.path.splitext(file_obj.name)[1].lower()

    if ext not in allowed_extensions:
        result["message"] = "الصيغة غير مدعومة، الرجاء رفع ملف بصيغة Excel أو CSV أو PDF فاتورة."
        return result

    if file_obj.size < 1024:
        result["message"] = "الملف فارغ أو صغير جداً."
        return result

    if file_obj.size > 20 * 1024 * 1024:
        result["message"] = "حجم الملف يتجاوز الحد الأقصى (20MB)."
        return result

    # NOTE: python-magic needs more than the first 2KB to reliably tell a real
    # .xlsx/.xls (an OOXML zip container) apart from a generic "application/zip":
    # with only 2048 bytes it frequently misses the "[Content_Types].xml" zip
    # entry and reports plain "application/zip", which would reject a 100%
    # valid spreadsheet. Verified empirically that 8KB is enough in practice
    # (small test fixtures already fail at 2048 and pass at 4096+), so we
    # widen the sniff window here without changing anything else about the
    # validation contract.
    file_mime = magic.from_buffer(file_obj.read(8192), mime=True)
    file_obj.seek(0)

    if ext in ['.xlsx', '.xls'] and 'spreadsheet' not in file_mime and 'excel' not in file_mime:
        result["message"] = "محتوى الملف لا يتطابق مع امتداده. يرجى التأكد من صحة الملف."
        return result
    if ext == '.pdf' and 'pdf' not in file_mime:
        result["message"] = "محتوى الملف ليس PDF حقيقي."
        return result

    # ==========================================
    # المرحلة 2: الفحص الهيكلي لـ Excel/CSV — لكل ورقة على حدة
    # ==========================================
    if ext in ['.xlsx', '.xls', '.csv']:
        try:
            dfs_to_validate = {}
            if ext == '.csv':
                dfs_to_validate['csv_file'] = pd.read_csv(file_obj)
            else:
                xls = pd.ExcelFile(file_obj)
                for sheet_name in xls.sheet_names:
                    df_sheet = xls.parse(sheet_name)
                    if not df_sheet.empty:
                        dfs_to_validate[sheet_name] = df_sheet

                if not dfs_to_validate:
                    result["message"] = "الملف لا يحتوي على أي بيانات جدولية مقروءة (جميع الأوراق فارغة)."
                    return result

            accepted_sheets = []
            rejected_sheets = []
            warnings = []

            for sheet_name, df in dfs_to_validate.items():
                if df.empty or len(df.columns) == 0:
                    rejected_sheets.append(f"{sheet_name} (بدون رؤوس)")
                    continue

                if len(df) < 3:
                    rejected_sheets.append(f"{sheet_name} (أقل من 3 صفوف)")
                    continue

                # محاولة تحويل الأعمدة النصية إلى أرقام أو تواريخ
                for col in df.columns:
                    if df[col].dtype == 'object':
                        # محاولة التحويل إلى رقم (بعد تنظيف الرموز المالية).
                        # ملاحظة: تنظيف الحروف غير الرقمية حرفاً بحرف (إزالة كل ما ليس رقماً
                        # أو نقطة أو إشارة سالب) يفشل مع رموز عملات تحتوي نقاطاً داخلية
                        # مثل "ر.س" (مثال: "1,200.50 ر.س" تتحول إلى "1200.50." وهي رقم غير
                        # صالح). لذلك نزيل الفواصل أولاً ثم نستخرج أول رمز رقمي صالح فقط.
                        cleaned = df[col].astype(str).str.replace(',', '', regex=False)
                        numeric_token = cleaned.str.extract(r'(-?\d+\.?\d*)', expand=False)
                        numeric_attempt = pd.to_numeric(numeric_token, errors='coerce')
                        if numeric_attempt.notna().sum() > (len(df) * 0.5):
                            df[col] = numeric_attempt
                            continue

                        # محاولة التحويل إلى تاريخ (إصلاح مضاف)
                        date_attempt = pd.to_datetime(df[col], errors='coerce')
                        if date_attempt.notna().sum() > (len(df) * 0.5):
                            df[col] = date_attempt

                numeric_cols = df.select_dtypes(include=['number']).columns
                date_cols = df.select_dtypes(include=['datetime', 'datetime64[ns]']).columns
                total_valid_cols = len(numeric_cols) + len(date_cols)

                if total_valid_cols < 2:
                    rejected_sheets.append(f"{sheet_name} (بيانات رقمية/تاريخية غير كافية)")
                    continue

                headers = [str(col).lower() for col in df.columns]
                has_financial_keyword = any(
                    any(keyword in header for keyword in FINANCIAL_KEYWORDS) for header in headers
                )

                if not has_financial_keyword:
                    rejected_sheets.append(f"{sheet_name} (غياب دلالات مالية)")
                    continue

                if len(numeric_cols) > 0:
                    empty_ratio = df[numeric_cols].isnull().sum().sum() / (len(df) * len(numeric_cols))
                    if empty_ratio > 0.70:
                        warnings.append(f"{sheet_name} (نسبة بيانات مفقودة > 70%)")

                accepted_sheets.append(sheet_name)

            result["accepted_sheets"] = accepted_sheets
            result["rejected_sheets"] = rejected_sheets

            if not accepted_sheets:
                reasons = ", ".join(rejected_sheets)
                result["message"] = f"تم رفض جميع الجداول لعدم مطابقتها للمعايير المالية: {reasons}"
                return result

            msg = f"تم قبول {len(accepted_sheets)} جدول: {', '.join(accepted_sheets)}."
            if rejected_sheets:
                msg += f" وتم تجاهل جداول غير صالحة: {', '.join(rejected_sheets)}."

            if warnings:
                msg += f" تحذير: {', '.join(warnings)}"
                result.update(is_valid=True, status='warning', message=msg)
                return result

            result.update(is_valid=True, status='accept', message=msg)
            return result

        except Exception as e:
            logger.exception("File validation failed for %s", file_obj.name)
            result["message"] = "فشل في قراءة بيانات الملف. تأكد من خلوه من التشفير والفساد وأن بنيته سليمة."
            return result

    # ==========================================
    # المرحلة 3: الفحص الهيكلي لـ PDF
    # ==========================================
    elif ext == '.pdf':
        import pdfplumber
        try:
            with pdfplumber.open(file_obj) as pdf:
                if len(pdf.pages) == 0:
                    result["message"] = "ملف الـ PDF فارغ."
                    return result

                text = ""
                for page in pdf.pages[:3]:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted

                if not text.strip():
                    result["message"] = "لم يتم العثور على نص قابل للاستخراج في الفاتورة (Scan غير مدعوم حالياً)."
                    return result

                text_lower = text.lower()
                has_invoice_keyword = any(kw in text_lower for kw in FINANCIAL_KEYWORDS)
                has_numbers = any(char.isdigit() for char in text)

                if not (has_invoice_keyword and has_numbers):
                    result["message"] = "الملف لا يحتوي على بيانات فاتورة مالية مقروءة."
                    return result

            result.update(is_valid=True, status='accept', message="تم قبول الفاتورة بنجاح.",
                           accepted_sheets=["invoice"])
            return result

        except Exception as e:
            logger.exception("PDF validation failed for %s", file_obj.name)
            result["message"] = "فشل في معالجة الفاتورة. تأكد أن الملف سليم."
            return result

    result["message"] = "خطأ غير معروف."
    return result
