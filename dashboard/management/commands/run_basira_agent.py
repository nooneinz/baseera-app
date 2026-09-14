import os
import json
import time
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from dashboard.models import Profile, DynamicRecord, Notification
from dashboard.services.ai_service import GeminiAIService

class Command(BaseCommand):
    help = 'Runs the autonomous Basira background agent to monitor user data and issue proactive alerts.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("Starting Basira Night Watchman Agent (Background Cron-Agent)..."))
        
        users = User.objects.all()
        ai_service = GeminiAIService()
        
        for user in users:
            try:
                profile = Profile.objects.get(user=user)
                self.stdout.write(f"Analyzing data for user: {user.username} ({profile.company_name})")
                
                # Fetch recent records
                recent_records = DynamicRecord.objects.filter(user=user).order_by('-created_at')[:50]
                if not recent_records.exists():
                    self.stdout.write("  No records found. Skipping.")
                    continue
                    
                # Format context
                context = f"Company: {profile.company_name} | Sector: {profile.get_project_type_display()}\n\n"
                context += "Recent Data Snapshot (Latest 50 records):\n"
                from dashboard.services.privacy import redact_row
                for record in recent_records:
                    context += f"- {json.dumps(redact_row(record.row_data), ensure_ascii=False)}\n"
                    
                # The Proactive Prompt
                agent_prompt = """
أنتِ "بصيرة"، وكيلة الذكاء الاصطناعي التي تعمل في الخلفية ليلاً لحراسة أموال النظام.
مهمتك الآن:
1. اقرئي أحدث بيانات هذا المستخدم (File Context).
2. ابحثي عن أي شيء خطير (مثل انخفاض مفاجئ في المبيعات، أو تكاليف مرتفعة جداً، أو نفاد مخزون).
3. ابحثي عن أي فرصة ذهبية (منتج يحقق أرباحاً عالية يجب التركيز عليه).
4. إذا لم تجدي شيئاً يستحق، لا تكتبي أي أكواد.
5. إذا وجدتِ خطراً أو فرصة، يجب عليكِ فوراً استخدام أداة الإشعارات لكتابة إشعار استباقي للمستخدم كالتالي في نهاية الرد:
[[ACTION:CREATE_NOTIFICATION|تنبيه استباقي: عنوان التنبيه|رسالة التنبيه التي تصف المشكلة أو الفرصة بالتفصيل|warning أو success]]

لا تطلبي مساعدة المستخدم ولا تطرحي أسئلة، أنتِ الآن تعملين وحدك (Agent) وتتخذين القرار بناءً على البيانات المتوفرة لخدمة المستخدم بأفضل شكل ممكن.
                """
                
                # Run the AI generator
                stream = ai_service.generate_chat_stream(
                    messages_list=[{"role": "user", "content": agent_prompt}],
                    file_context=context,
                    user_id=user.id
                )
                
                # Consume stream (actions will be executed automatically by ai_service background thread)
                # We iterate until the generator is exhausted, ensuring the task completes.
                for _ in stream:
                    pass
                
                self.stdout.write(self.style.SUCCESS(f"  Finished analysis for {user.username}."))
                
                # Delay to respect API limits (2 seconds)
                time.sleep(2)
                
            except Profile.DoesNotExist:
                pass
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error processing user {user.username}: {str(e)}"))
                
        self.stdout.write(self.style.SUCCESS("Basira Night Watchman Agent cycle complete."))
