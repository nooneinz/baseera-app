import os
import magic # for true MIME type checking
import pandas as pd
from typing import Tuple, Optional, List

FINANCIAL_KEYWORDS = ['سعر', 'تكلفة', 'إجمالي', 'كمية', 'تاريخ', 'إيراد', 'مصروف', 'فاتورة', 
                      'رقم الفاتورة', 'price', 'cost', 'total', 'amount', 'qty', 'date', 
                      'revenue', 'expense', 'invoice']

def validate_financial_file(file_obj) -> Tuple[bool, str, str, List[str]]:
    """
    Returns: (is_valid, status: 'accept'|'warning'|'reject', message, accepted_sheets_list)
    """
    
    # ==========================================
    # المرحلة 1: الفحص الشكلي (قبل فتح الملف)
    # ==========================================
    allowed_extensions = ['.xlsx', '.xls', '.csv', '.pdf']
    ext = os.path.splitext(file_obj.name)[1].lower()
    
    if ext not in allowed_extensions:
        return False, 'reject', "الصيغة غير مدعومة، الرجاء رفع ملف بصيغة Excel أو CSV أو PDF فاتورة.", []
        
    if file_obj.size < 1024:  # أقل من 1KB
        return False, 'reject', "الملف فارغ أو صغير جداً.", []
        
    if file_obj.size > 20 * 1024 * 1024: # أكبر من 20MB
        return False, 'reject', "حجم الملف يتجاوز الحد الأقصى (20MB).", []
        
    # فحص MIME الفعلي
    file_mime = magic.from_buffer(file_obj.read(2048), mime=True)
    file_obj.seek(0)
    
    # التأكد من تطابق MIME مع الامتداد
    if ext in ['.xlsx', '.xls'] and 'spreadsheet' not in file_mime and 'excel' not in file_mime:
         return False, 'reject', "محتوى الملف لا يتطابق مع امتداده. يرجى التأكد من صحة الملف.", []
    if ext == '.pdf' and 'pdf' not in file_mime:
         return False, 'reject', "محتوى الملف ليس PDF حقيقي.", []

    # ==========================================
    # المرحلة 2: الفحص الهيكلي لـ Excel/CSV
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
                     return False, 'reject', "الملف لا يحتوي على أي بيانات جدولية مقروءة (جميع الأوراق فارغة).", []
            
            accepted_sheets = []
            rejected_sheets = []
            warnings = []

            for sheet_name, df in dfs_to_validate.items():
                # القاعدة 1: وجود صف رؤوس (Header Row)
                if df.empty or len(df.columns) == 0:
                    rejected_sheets.append(f"الورقة '{sheet_name}' (بدون رؤوس)")
                    continue
                    
                # القاعدة 2: الحد الأدنى للصفوف
                if len(df) < 3:
                    rejected_sheets.append(f"الورقة '{sheet_name}' (أقل من 3 صفوف)")
                    continue
                    
                # القاعدة 3: تحويل النصوص لأرقام أو تواريخ وتحديد الأعمدة الصالحة
                for col in df.columns:
                    if df[col].dtype == 'object':
                        # 1. محاولة التحويل لتاريخ أولاً
                        try:
                            date_attempt = pd.to_datetime(df[col], errors='coerce')
                            if date_attempt.notna().sum() > (len(df) * 0.5):
                                df[col] = date_attempt
                                continue # نجح كعمود تاريخ، ننتقل للعمود التالي
                        except:
                            pass
                            
                        # 2. إذا لم ينجح، نحاول التنظيف وتحويله لرقم
                        cleaned = df[col].astype(str).str.replace(r'[^\d\.\-]', '', regex=True)
                        try:
                            numeric_attempt = pd.to_numeric(cleaned, errors='coerce')
                            if numeric_attempt.notna().sum() > (len(df) * 0.5):
                                df[col] = numeric_attempt
                        except:
                            pass
                            
                numeric_cols = df.select_dtypes(include=['number']).columns
                date_cols = df.select_dtypes(include=['datetime']).columns
                total_valid_cols = len(numeric_cols) + len(date_cols)
                
                if total_valid_cols < 2:
                    rejected_sheets.append(f"الورقة '{sheet_name}' (بيانات رقمية غير كافية)")
                    continue

                # القاعدة 4: رؤوس ذات دلالة مالية/تجارية
                headers = [str(col).lower() for col in df.columns]
                has_financial_keyword = any(any(keyword in header for keyword in FINANCIAL_KEYWORDS) for header in headers)
                
                if not has_financial_keyword:
                    rejected_sheets.append(f"الورقة '{sheet_name}' (غياب دلالات مالية)")
                    continue
                    
                # القاعدة 5: نسبة الخلايا الفارغة
                empty_ratio = df[numeric_cols].isnull().sum().sum() / (len(df) * len(numeric_cols)) if len(numeric_cols) > 0 else 0
                if empty_ratio > 0.70:
                    warnings.append(f"الورقة '{sheet_name}' تحتوي على أكثر من 70% بيانات فارغة.")
                
                accepted_sheets.append(sheet_name)

            if not accepted_sheets:
                reasons = ", ".join(rejected_sheets)
                return False, 'reject', f"تم رفض جميع الجداول لعدم مطابقتها للمعايير المالية: {reasons}", []

            msg = f"تم قبول {len(accepted_sheets)} جدول."
            if rejected_sheets:
                msg += f" وتم تجاهل جداول غير صالحة: {', '.join(rejected_sheets)}."
            if warnings:
                msg += f" تحذير: {', '.join(warnings)}"
                return True, 'warning', msg, accepted_sheets

            return True, 'accept', msg, accepted_sheets

        except Exception as e:
            return False, 'reject', "فشل في قراءة بيانات الملف. تأكد من خلوه من التشفير والفساد وأن بنيته سليمة.", []

    # ==========================================
    # المرحلة 3: الفحص الهيكلي لـ PDF
    # ==========================================
    elif ext == '.pdf':
        try:
            import pdfplumber
            with pdfplumber.open(file_obj) as pdf:
                if len(pdf.pages) == 0:
                    return False, 'reject', "ملف الـ PDF فارغ.", []
                
                text = ""
                for page in pdf.pages[:3]: # فحص أول 3 صفحات
                    extracted = page.extract_text()
                    if extracted: text += extracted
                    
                if not text.strip():
                    return False, 'reject', "لم يتم العثور على نص قابل للاستخراج في الفاتورة (Scan غير مدعوم حالياً).", []
                
                # البحث عن مؤشرات فواتير
                text_lower = text.lower()
                has_invoice_keyword = any(kw in text_lower for kw in FINANCIAL_KEYWORDS)
                has_numbers = any(char.isdigit() for char in text)
                
                if not (has_invoice_keyword and has_numbers):
                    return False, 'reject', "الملف لا يحتوي على بيانات فاتورة مالية مقروءة.", []
                    
            return True, 'accept', "تم قبول الفاتورة بنجاح.", [file_obj.name]
            
        except Exception as e:
             return False, 'reject', "فشل في معالجة الفاتورة. تأكد أن الملف سليم.", []
             
    return False, 'reject', "خطأ غير معروف.", []
