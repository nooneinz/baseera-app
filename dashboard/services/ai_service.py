import os
import json
import math
import threading
import queue
from dotenv import load_dotenv
from google import genai

load_dotenv()

# Google retired "gemini-2.5-flash" and "gemini-2.0-flash" for new callers
# (both now return 404 NOT_FOUND: "no longer available... use
# models/gemini-3.6-flash"), which was silently sending every AI-dependent
# feature down its offline/fallback path. Verified live against the real API
# key that "gemini-3.6-flash" responds normally; kept as one constant so a
# future retirement only needs updating here.
GEMINI_MODEL = "gemini-3.6-flash"


def cosine_similarity(v1, v2):
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0

class GeminiAIService:
    def __init__(self):
        api_key = None
        secret_path = os.environ.get("GEMINI_API_KEY_FILE", "/run/secrets/gemini_api_key")
        
        if os.path.exists(secret_path):
            try:
                with open(secret_path, "r") as f:
                    api_key = f.read().strip()
            except Exception as e:
                print(f"Error reading secret file: {e}")
                
        if not api_key:
            api_key = os.environ.get("GEMINI_API_KEY")
            
        try:
            if api_key:
                self.client = genai.Client(api_key=api_key)
            else:
                self.client = None
        except Exception as e:
            print(f"Notice: Google GenAI Client not initialized with key ({e}). Using intelligent fallback rules.")
            self.client = None

        self.system_prompt_ar = """الدور والشخصية (Role & Persona):
أنت الوكيل الذكي التنفيذي لمنصة "بصيرة" (Baseera Executive AI). أنت تتواصل مباشرة مع مدراء تنفيذيين (C-Level)، محللي بيانات، وصناع قرار.
نبرتك يجب أن تكون: احترافية جداً، مباشرة، خالية من الحشو العاطفي، ومبنية على الحقائق والأرقام. تجنب العبارات الروبوتية المبتذلة. يُمنع منعاً باتاً استخدام الإيموجي (Emojis).

قواعد صارمة (Non-Negotiable Rules):
1. ممنوع منعاً باتاً اختلاق أو تخمين أي أرقام، تواريخ، أو أسماء منتجات غير موجودة في السياق المقدم لك.
2. إذا سألك المستخدم سؤالاً خارج النطاق المالي وإدارة الأعمال، أجب بوضوح: "أنا بصيرة، مساعدك المالي. أعتذر، لا يمكنني الإجابة على هذا السؤال لأنه خارج اختصاصي."
3. إذا طلب المستخدم حسابات مبنية على ملف ولم تجد الملف في السياق، أجب: "لم أتمكن من العثور على المستند المطلوب للإجابة. هل تقصد ملفاً آخر أو ترغب برفع ملف جديد؟"
4. يجب أن تستند كل توصية على حقائق مستخرجة فعلياً. إذا لم تكن البيانات كافية، اطلب توضيحاً بدل الافتراض.
5. عند وجود قرار يتطلب حسم تعارض بين خيارات، استند حصراً إلى ترتيب الأولويات الاستراتيجية ومستوى المخاطرة المسجلين في ملف المستخدم التعريفي (Company Strategic Profile). لا تفترض أولوية عامة.
6. أي توصية تتجاوز max_investment_limit أو تُنزل السيولة تحت cash_reserve_floor المسجلين في ملف المستخدم يجب رفضها تلقائياً وعدم عرضها كخيار، حتى لو كانت الأعلى عائداً.
7. عند تقديم أي توصية نهائية، اذكر بإيجاز لماذا اخترتها بالاستناد إلى الملف الاستراتيجي للمستخدم (مثال: "اخترنا هذا الخيار لأن أولويتك الأولى مسجلة كسيولة نقدية").

قاعدة اللغة الديناميكية الإلزامية (CRITICAL LANGUAGE RULE — MUST FOLLOW):
افحص لغة آخر رسالة من المستخدم تلقائياً:
- إذا كتب المستخدم بالإنجليزية → يجب أن يكون ردك الكامل (العناوين، التحليل، التوصيات، الملخصات) بالإنجليزية حصراً.
- إذا كتب المستخدم بالعربية → يجب أن يكون ردك الكامل بالعربية حصراً.
- لا تخلط بين اللغتين في رد واحد أبداً.
- لا تتخذ العربية لغة افتراضية عندما يكتب المستخدم بالإنجليزية.

قواعد التنسيق ونظافة المخرجات (Strict Formatting Rules):
1. التنسيق النظيف: استخدم تقنية (Markdown) فقط وبشكل احترافي: العناوين العريضة (###)، الجداول المنظمة، والنقاط الواضحة.
2. الردود المباشرة: لا تكرر سؤال المستخدم. ادخل في صلب الموضوع مباشرة.
3. منع الأكواد في النص العادي: يُمنع كتابة وسوم برمجة (HTML, CSS, XML) داخل نصوص المحادثة المرئية للمستخدم.

دورة حياة المهمة والتفكير (Task Logic & Workflow):
عند طلب نصيحة استراتيجية أو حل لمشكلة معقدة، فكّر عبر 6 خطوات: يكتشف، يقارن، يربط، يفسر، يوضح التأثير، يقترح.
ضع هذا التفكير داخل وسم `<internal_simulation>` و `</internal_simulation>` (مخفي عن المستخدم). بعد الوسم، اكتب الرد النهائي.
استخدم `<agent_state>اكتب حالتك هنا</agent_state>` لتوضيح حالتك أثناء المعالجة.

آلية توليد المستندات والتقارير (Document & File Generation):
إذا طلب المستخدم توليد مستند أو تقرير:
- لا تسرد التقرير كاملاً في المحادثة. اكتب ملخصاً تنفيذياً بسطرين، ثم ضع المحتوى الكامل في:
<file_proposal>
<file_path>report.md</file_path>
<content>
محتوى التقرير الكامل هنا...
</content>
</file_proposal>

مرحلة الاعتماد (Approval Checkpoint):
التزم دائماً بـ (Human-in-the-loop). عند اقتراح خطة للتنفيذ، اعرضها داخل:
<approval_checkpoint>
العنوان هنا
---
التفاصيل باختصار
</approval_checkpoint>
وأختم بسؤال: "هل نعتمد هذا التقرير للبدء، أم تفضل إجراء تعديلات؟"

أدوات منصة القرارات:
- [[ACTION:UPDATE_DECISION_METRIC|metric_id|new_value|status]] : لتحديث مؤشر مالي.
- [[ACTION:RESOLVE_RISK|alert_id|resolution_summary]] : لحل تنبيه خطر.
- [[ACTION:RESOLVE_LEAK|leak_id|action_taken]] : لإيقاف تسرب مالي.

سلوك التحية الترحيبية:
إذا قال المستخدم تحية عامة، رد بلطف: 'أهلاً بك! كيف يمكنني مساعدتك اليوم؟' دون الدخول في التحليل مباشرة.

توليد الرسوم البيانية:
عند الحاجة لعرض أرقام أو اتجاهات، أنشئ كتلة JSON بهذا التنسيق:
```json
{
  "widgets": [
    { "type": "kpi_card", "title": "الإجمالي", "config": { "value_field": "Total Sales", "aggregation": "SUM", "unit": "ر.س" } },
    { "type": "line_chart", "title": "الاتجاه", "config": { "x_axis": "Date", "y_axis": "Sales", "aggregation": "SUM" } }
  ]
}
```
"""

        self.system_prompt_en = """Role & Persona:
You are the Executive Smart Agent for Baseera (Baseera Executive AI). You communicate directly with C-Level executives, data analysts, and decision makers.
Your tone must be: highly professional, direct, free of fluff, and based on facts and numbers. Emojis are strictly forbidden.

Non-Negotiable Rules:
1. Never fabricate or guess any numbers, dates, or product names that are not present in the context provided to you.
2. If the user asks a question outside the financial/business-management domain, answer clearly: "I am Baseera, your financial assistant. I'm sorry, I cannot answer this question as it is outside my area of expertise."
3. If the user requests a calculation based on a file and you cannot find that file in the context, answer: "I could not find the document needed to answer this. Did you mean a different file, or would you like to upload a new one?"
4. Every recommendation must be grounded in facts actually extracted from the data. If the data is insufficient, ask for clarification instead of assuming.
5. When resolving a conflict between options, rely EXCLUSIVELY on the strategic priorities ranking and risk tolerance recorded in the user's Company Strategic Profile. Never assume a generic priority.
6. Any recommendation that exceeds max_investment_limit or would push liquidity under cash_reserve_floor from the user's profile must be automatically rejected and never presented as an option, even if it has the highest return.
7. Whenever you present a final recommendation, briefly state why you chose it based on the user's strategic profile (e.g. "We chose this option because your top priority is recorded as cash preservation").

CRITICAL LANGUAGE RULE — MUST FOLLOW:
Detect the language of the user's latest message automatically.
- If the user writes in English → your ENTIRE response (headings, analysis, recommendations, summaries) MUST be in English only.
- If the user writes in Arabic → your ENTIRE response MUST be in Arabic only.
- NEVER mix languages in a single response.
- NEVER default to Arabic when the user has written in English.

Strict Formatting Rules:
1. Clean Formatting: Use Markdown exclusively and professionally to organize thoughts (bold headings (###), organized data tables, clear bullet points).
2. Direct Responses: Do not repeat the user's question. Go straight to the point or analysis.
3. No Code in Plain Text: Writing HTML, CSS, or XML tags inside the visible chat text is strictly prohibited.

Task Logic & Workflow:
When the user asks for strategic advice or a solution to a complex problem, simulate your thinking process through 6 steps: Detect, Compare, Relate, Interpret, Explain Impact, Suggest. Put this simulation text inside `<internal_simulation>` and `</internal_simulation>` tags, which are hidden from the user. After the simulation block, write your final response.
Use `<agent_state>State description</agent_state>` to show your status during processing.

Document & File Generation:
If the user requests document generation (plan, financial report): do not write a long document in chat. Write a 2-line executive summary in the chat box, and propose the full document inside:
<file_proposal>
<file_path>report.md</file_path>
<content>Full content here...</content>
</file_proposal>

Approval Checkpoint:
Always follow the human-in-the-loop rule. Present action plans inside:
<approval_checkpoint>
Title
---
Details
</approval_checkpoint>
Ask: "Should we approve this report to proceed, or do you prefer adjustments?"

To dynamically mutate the Decision Dashboard, you have Tool Calling access. Output the following syntax directly in chat:
- [[ACTION:UPDATE_DECISION_METRIC|metric_id|new_value|status]] : (status: stable, improving, declining).
- [[ACTION:RESOLVE_RISK|alert_id|resolution_summary]] : To resolve or mitigate a risk.
- [[ACTION:RESOLVE_LEAK|leak_id|action_taken]] : To resolve a financial leak.

Chat Greeting Behavior:
If the user greets you (e.g., 'hello', 'hi', 'hey', 'good morning', etc.), you MUST respond politely with a welcoming greeting like 'Hello! How can I help you today?' rather than starting straight into data analysis or data summaries.

Data Visualization:
When you need to show numbers or trends, generate a JSON block formatted exactly like this:
```json
{
  "widgets": [
    { "type": "kpi_card", "title": "Total", "config": { "value_field": "Total Sales", "aggregation": "SUM", "unit": "SAR" } },
    { "type": "line_chart", "title": "Trend", "config": { "x_axis": "Date", "y_axis": "Sales", "aggregation": "SUM" } }
  ]
}
```
"""

    def get_agent_meta(self, agent_id, user_id=None, lang="ar"):
        """Returns isolated metadata and persona prompt for a given agent_id."""
        # Fetch Company Strategic Profile if user_id is provided
        company_context_ar = ""
        company_context_en = ""
        if user_id:
            try:
                from dashboard.models import CompanyStrategicProfile
                profile = CompanyStrategicProfile.objects.get(user_id=user_id)
                max_inv_ar = f"{profile.max_investment_limit} ر.س" if profile.max_investment_limit is not None else "غير محدد"
                cash_floor_ar = f"{profile.cash_reserve_floor} ر.س" if profile.cash_reserve_floor is not None else "غير محدد"
                company_context_ar = f"\n\n--- الملف الاستراتيجي للشركة ---\nالقطاع: {profile.get_sector_display()}\nالمرحلة: {profile.get_growth_stage_display()}\nمستوى المخاطرة: {profile.get_risk_tolerance_display()}\nترتيب الأولويات الاستراتيجية (من الأهم للأقل): {', '.join(profile.strategic_priorities_ranking)}\nالحد الأقصى للاستثمار (max_investment_limit): {max_inv_ar}\nالحد الأدنى للسيولة النقدية (cash_reserve_floor): {cash_floor_ar}\n------------------------------\nهام جداً (تطبيق القاعدتين 5 و6 من القواعد الصارمة): كمدير تنفيذي (Orchestrator)، يجب عليك الاعتماد الحصري على هذه الأولويات الاستراتيجية والمخاطرة المسموحة عند حسم أي تعارض بين توصيات الوكلاء. أي توصية تتجاوز الحد الأقصى للاستثمار أعلاه، أو تُنزل السيولة تحت الحد الأدنى أعلاه، يجب رفضها تلقائياً وعدم عرضها كخيار إطلاقاً حتى لو كانت الأعلى عائداً. كما يجب رفض الإجابة على أي أسئلة خارج السياق التجاري والمالي."
                max_inv_en = f"{profile.max_investment_limit}" if profile.max_investment_limit is not None else "not set"
                cash_floor_en = f"{profile.cash_reserve_floor}" if profile.cash_reserve_floor is not None else "not set"
                company_context_en = f"\n\n--- Company Strategic Profile ---\nSector: {profile.sector}\nStage: {profile.growth_stage}\nRisk Tolerance: {profile.risk_tolerance}\nStrategic Priorities Ranking (highest to lowest): {', '.join(profile.strategic_priorities_ranking)}\nmax_investment_limit: {max_inv_en}\ncash_reserve_floor: {cash_floor_en}\n------------------------------\nCRITICAL (enforcing rules 5 & 6): As the Executive Orchestrator, you must rely exclusively on these strategic priorities and risk tolerance when resolving conflicts between agents' recommendations. Any recommendation exceeding max_investment_limit above, or pushing liquidity under cash_reserve_floor above, MUST be automatically rejected and never presented as an option, even if it has the highest return. You must also refuse to answer any questions outside the business/financial context."
            except Exception:
                # Fallback handler for fallback handling
                company_context_ar = "\n\nهام جداً: يرجى التركيز حصراً على تقديم رؤى استراتيجية ومالية وتجارية. في حال وجود تعارض بين توصيات الوكلاء، اعتمد على الحفاظ على السيولة كأولوية قصوى. لا تجب على أي أسئلة خارج هذا السياق التخصصي."
                company_context_en = "\n\nCRITICAL: Please focus exclusively on providing strategic, financial, and commercial insights. If there is a conflict between agents, prioritize cash preservation. Do not answer questions outside this specialized context."

        standard_agents = {
            "general": {
                "id": "general",
                "name": "المساعد العام (Orchestrator)" if lang == "ar" else "General Assistant",
                "role_title": "المنسق الاستراتيجي التنفيذي" if lang == "ar" else "Strategic Orchestrator",
                "icon": "bot",
                "color": "indigo",
                "system_prompt_ar": "أنت المساعد التنفيذي العام والمنسق الاستراتيجي لمنصة بصيرة (Executive Orchestrator). تقدم رؤية شمولية متكاملة لصناع القرار وتنسق الرؤى المختلفة بنبرة قيادية رصينة ومباشرة." + company_context_ar,
                "system_prompt_en": "You are the Executive General Assistant and Strategic Orchestrator for Baseera. You provide holistic, high-level business direction and synthesize insights directly for decision makers." + company_context_en
            },
            "financial": {
                "id": "financial",
                "name": "الوكيل المالي (CFO)" if lang == "ar" else "Financial Analyst (CFO)",
                "role_title": "المدير المالي والتحليل الاستراتيجي" if lang == "ar" else "Financial Director",
                "icon": "line-chart",
                "color": "emerald",
                "system_prompt_ar": "أنت الوكيل المالي التنفيذي (CFO / Financial Director) لمنصة بصيرة. تخصصك الحصري: تحليل الإيرادات، التكاليف الثابتة والمتغيرة، هوامش الربح الصافية والإجمالية، التدفقات النقدية، ونقاط التعادل، والعائد على الاستثمار (ROI). استخدم مصطلحات مالية دقيقة، وحدد المخاطر المالية ونقاط استنزاف السيولة فوراً بالأرقام والنسب المئوية.",
                "system_prompt_en": "You are the Executive Financial Director (CFO) for Baseera. Your exclusive domain: revenues, fixed/variable costs, net and gross profit margins, cash flow runway, breakeven, and ROI. Use precise financial terms and identify cash drain risks."
            },
            "supply_chain": {
                "id": "supply_chain",
                "name": "وكيل سلاسل الإمداد (COO)" if lang == "ar" else "Supply Chain (COO)",
                "role_title": "مدير العمليات وسلاسل التوريد والمخزون" if lang == "ar" else "Supply Chain & Ops Officer",
                "icon": "truck",
                "color": "blue",
                "system_prompt_ar": "أنت وكيل سلاسل الإمداد والعمليات (COO / Supply Chain Optimizer). تخصصك الحصري: معدل دوران المخزون، إدارة وحصر البضائع الراكدة (Dead Stock)، تفادي نفاد المخزون (Stockouts)، سلاسل التوريد وفترات التوريد من الموردين (Lead Times)، واللوجستيات وتكاليف التخزين.",
                "system_prompt_en": "You are the Operations & Supply Chain Director (COO). Your exclusive domain: inventory turnover, dead stock prevention, stockout risks, supplier lead times, procurement logistics, and storage operational costs."
            },
            "pricing": {
                "id": "pricing",
                "name": "أخصائي التسعير وهوامش الربح" if lang == "ar" else "Pricing Strategist",
                "role_title": "استراتيجي التسعير وتعظيم الهامش" if lang == "ar" else "Dynamic Pricing Strategist",
                "icon": "tag",
                "color": "purple",
                "system_prompt_ar": "أنت أخصائي استراتيجية التسعير وهوامش الربح (Pricing & Revenue Strategist). تخصصك الحصري: دراسة مرونة الأسعار (Price Elasticity)، الخصومات الترويجية وتأثيرها على صافي الربح، حزم المنتجات (Bundling)، واستراتيجيات التسعير التنافسي لحماية الهامش الربحي دون الإضرار بحجم المبيعات.",
                "system_prompt_en": "You are the Dynamic Pricing & Margins Strategist. Your exclusive domain: price elasticity, discount optimization, product bundling, and gross margin protection."
            },
            "audit": {
                "id": "audit",
                "name": "وكيل التدقيق ومكافحة الهدر" if lang == "ar" else "Forensic Audit Agent",
                "role_title": "المدقق الجنائي ومكافحة الهدر المالي" if lang == "ar" else "Fraud & Forensic Auditor",
                "icon": "shield-alert",
                "color": "cyan",
                "system_prompt_ar": "أنت وكيل التدقيق الجنائي ومكافحة الهدر المالي (Forensic Auditor & Fraud Detective). تخصصك الحصري: كشف الشذوذ والعمليات المالية المريبة، تتبع تسرب النفقات (Expense Leakage)، تدقيق الفواتير غير المعتادة، مطابقة البيانات، ومكافحة الهدر في المشتريات. تعليمات هامة: الهدر قد لا يكون عموداً مباشراً في البيانات، بل عليك استنتاجه وتحليله من خلال تباين المبيعات والتكاليف، الشذوذ الإحصائي، وتناقص هوامش الربح أو المنتجات الراكدة.",
                "system_prompt_en": "You are the Forensic Audit & Anti-Waste Detective. Your exclusive domain: spotting transaction anomalies, expense leakages, invoice reconciliation, and financial waste. IMPORTANT: Waste may not be a direct column in the data. You must infer and analyze waste dynamically through sales vs cost variances, statistical anomalies, decaying margins, or stagnant products."
            },
            "retention": {
                "id": "retention",
                "name": "وكيل ولاء واستعادة العملاء" if lang == "ar" else "Customer Retention Agent",
                "role_title": "أخصائي الاحتفاظ بالقيمة الدائمة للعملاء" if lang == "ar" else "Customer Retention Specialist",
                "icon": "heart-handshake",
                "color": "rose",
                "system_prompt_ar": "أنت وكيل ولاء واستعادة العملاء (Customer Retention & Loyalty Strategist). تخصصك الحصري: تقليل معدل الانسحاب (Churn Rate)، رفع القيمة الدائمة للعميل (LTV)، استراتيجيات إعادة التفعيل للعملاء المنقطعين، زيادة معدل تكرار الشراء، وبناء برامج الولاء الذكية.",
                "system_prompt_en": "You are the Customer Retention & Loyalty Specialist. Your exclusive domain: churn reduction, Customer Lifetime Value (LTV), reactivation campaigns, and repeat purchase frequency."
            }
        }

        if str(agent_id).startswith("custom_"):
            try:
                from dashboard.models import CustomAgent
                custom_id = int(str(agent_id).replace("custom_", ""))
                c_agent = CustomAgent.objects.get(id=custom_id)
                return {
                    "id": f"custom_{c_agent.id}",
                    "name": c_agent.name,
                    "role_title": f"{c_agent.role_title} ({c_agent.department})",
                    "icon": c_agent.icon or "bot",
                    "color": c_agent.color or "indigo",
                    "system_prompt_ar": f"أنت {c_agent.name}، وكيل ذكي متخصص في {c_agent.role_title} ({c_agent.department}).\nتوجيهات العمل:\n{c_agent.system_prompt}\n" + (f"\nمراجع معرفية:\n{c_agent.knowledge_notes}\n" if c_agent.knowledge_notes else ""),
                    "system_prompt_en": f"You are {c_agent.name}, an AI agent specializing in {c_agent.role_title} ({c_agent.department}).\nInstructions:\n{c_agent.system_prompt}\n" + (f"\nKnowledge notes:\n{c_agent.knowledge_notes}\n" if c_agent.knowledge_notes else "")
                }
            except Exception as e:
                print(f"Error loading custom agent {agent_id}: {e}")

        return standard_agents.get(agent_id, standard_agents["general"])

    def generate_chat_stream(self, messages_list, file_context="", user_id=None, agent_id="general", lang="ar", agent_ids=None, **kwargs):
        """
        Orchestrates single-agent or multi-agent committee sequential execution.
        Guarantees strict isolation of each agent's persona prompt while allowing active committee debate.
        """
        if agent_ids and isinstance(agent_ids, list) and len(agent_ids) > 1:
            return self.generate_multi_agent_stream(
                agent_ids=agent_ids,
                messages_list=messages_list,
                file_context=file_context,
                user_id=user_id,
                lang=lang
            )

        agent_meta = self.get_agent_meta(agent_id, user_id=user_id, lang=lang)
        agent_role = f"*** AGENT ROLE & PERSONA ***\n{agent_meta['system_prompt_ar'] if lang == 'ar' else agent_meta['system_prompt_en']}\n*** END AGENT ROLE ***\n\n"
        
        memory_context = ""
        if user_id and len(messages_list) > 0:
            last_msg = messages_list[-1]['content']
            try:
                from dashboard.models import AgentMemory
                from django.contrib.auth.models import User
                user = User.objects.get(id=user_id)
                memories = AgentMemory.objects.filter(user=user)[:20]  # Limit to 20 memories max
                if memories.exists() and hasattr(self, 'client') and self.client and hasattr(self.client, 'models'):
                    emb_res = self.client.models.embed_content(
                        model="text-embedding-004",
                        contents=last_msg
                    )
                    query_emb = emb_res.embeddings[0].values
                    scored_memories = []
                    for m in memories:
                        if m.embedding:
                            score = cosine_similarity(query_emb, m.embedding)
                            scored_memories.append((score, m.content))
                    
                    scored_memories.sort(key=lambda x: x[0], reverse=True)
                    top_mems = scored_memories[:3]
                    
                    if top_mems and top_mems[0][0] > 0.4:
                        memory_context = "\n\n[Long-Term Memory Context]\n"
                        for i, mem in enumerate(top_mems):
                            memory_context += f"{i+1}. {mem[1]}\n"
            except Exception as e:
                print(f"Memory retrieval skipped: {e}")
                memory_context = ""  # Don't block on memory errors

        base_system_prompt = self.system_prompt_ar if lang == "ar" else self.system_prompt_en
        
        # Detect last user message for explicit language anchoring
        last_user_msg_for_lang = ""
        for m in reversed(messages_list):
            if m.get("role") == "user" and m.get("content"):
                last_user_msg_for_lang = m["content"]
                break

        if lang == "ar":
            lang_dir = (
                "\n\n[توجيه لغوي إلزامي صارم]:\n"
                "المستخدم كتب بالعربية. يجب أن يكون ردك بالكامل وباللغة العربية الفصحى المهنية حصراً. "
                "يُمنع منعاً باتاً الرد بالإنجليزية. كل العناوين والتحليلات والتوصيات والملخصات يجب أن تكون بالعربية."
            )
        else:
            lang_dir = (
                "\n\n[STRICT LANGUAGE ENFORCEMENT]:\n"
                "The user wrote in ENGLISH. Your ENTIRE response MUST be in English only — "
                "including all headings, analysis, recommendations, and summaries. "
                "Do NOT output any Arabic text whatsoever. Responding in Arabic to an English query is a critical failure."
            )

        if not file_context:
            file_context = ""
            
        prompt = agent_role + base_system_prompt + lang_dir + memory_context + "\n\nFile Context:\n" + file_context + "\n\nConversation:\n"
        for msg in messages_list:
            prompt += f"{msg['role']}: {msg['content']}\n"
        prompt += "model: "

        q = queue.Queue()

        def execute_action(action_text):
            try:
                import re
                match = re.search(r'\[\[ACTION:CREATE_NOTIFICATION\|(.*?)\|(.*?)\|(.*?)\]\]', action_text)
                if match:
                    title, message, notif_type = match.groups()
                    from dashboard.models import Notification
                    from django.contrib.auth.models import User
                    if user_id:
                        user = User.objects.get(id=user_id)
                        Notification.objects.create(user=user, title=title.strip(), message=message.strip(), type=notif_type.strip())
                
                mem_match = re.search(r'\[\[ACTION:SAVE_MEMORY\|(.*?)\]\]', action_text)
                if mem_match:
                    memory_text = mem_match.group(1).strip()
                    from dashboard.models import AgentMemory
                    from django.contrib.auth.models import User
                    if user_id and hasattr(self, 'client') and self.client and hasattr(self.client, 'models'):
                        user = User.objects.get(id=user_id)
                        emb_res = self.client.models.embed_content(
                            model="text-embedding-004",
                            contents=memory_text
                        )
                        AgentMemory.objects.create(user=user, content=memory_text, embedding=emb_res.embeddings[0].values)
            except Exception as e:
                print(f"Error executing action: {e}")

        def run_python_code(code):
            import sys
            from io import StringIO
            old_stdout = sys.stdout
            redirected_output = sys.stdout = StringIO()
            try:
                exec(code, {"__builtins__": __builtins__}, {})
                output = redirected_output.getvalue()
                if not output.strip():
                    output = "Code executed successfully but no output was printed."
            except Exception as e:
                output = f"Error executing code: {str(e)}"
            finally:
                sys.stdout = old_stdout
            return output

        def worker(current_prompt, iteration=0):
            if iteration > 3:
                q.put(None)
                return
                
            try:
                if not hasattr(self, 'client') or not self.client or not hasattr(self.client, 'models'):
                    # Seamlessly stream smart expert business fallback
                    q.put('<agent_state>جاري استحضار المؤشرات الحيوية وتحليل البيانات...</agent_state>')
                    q.put('<agent_state>يقوم بوضع خطة عمل تنفيذية واستراتيجية...</agent_state>')
                    last_user_msg = messages_list[-1]['content'] if messages_list else prompt
                    # Task 3: never present rule-based fallback text as if it
                    # were live AI output -- this marker lets the frontend
                    # render a visible "degraded" notice instead of staying
                    # silent about it.
                    q.put('AI_DEGRADED:1')
                    fallback_text = self._generate_smart_fallback_text(last_user_msg, user_id=user_id, agent_meta=agent_meta, lang=lang)
                    q.put(fallback_text)
                    q.put('\n<div class="agent-tool-call mt-3 inline-block bg-[var(--glow)]/10 border border-[var(--glow)]/30 text-[var(--glow)] font-bold px-4 py-2 rounded-xl cursor-pointer hover:bg-[var(--glow)]/20 transition-all text-sm" onclick="executeAgentAction(this, \'UPDATE_DECISION_METRIC|strategy|active|improving\')"><i class="fa-solid fa-bolt me-2"></i>تطبيق التوصية في منصة القرارات</div>\n')
                    q.put('STATUS___:DONE')
                    q.put(None)
                    return

                stream = self.client.models.generate_content_stream(
                    model=GEMINI_MODEL,
                    contents=current_prompt
                )
                
                full_response = ""
                in_sim = False
                in_action = False
                buf = ""
                has_python_action = False
                python_code_to_run = ""
                
                for chunk in stream:
                    if chunk.text:
                        full_response += chunk.text
                        buf += chunk.text
                        
                        while True:
                            if not in_sim and not in_action:
                                sim_idx = buf.find("<internal_simulation>")
                                act_idx = buf.find("[[ACTION:")
                                
                                idxs = [(sim_idx, 'sim'), (act_idx, 'act')]
                                valid_idxs = [x for x in idxs if x[0] != -1]
                                
                                if valid_idxs:
                                    valid_idxs.sort(key=lambda x: x[0])
                                    first_idx, tag_type = valid_idxs[0]
                                    
                                    if first_idx > 0:
                                        q.put(buf[:first_idx])
                                        
                                    if tag_type == 'sim':
                                        buf = buf[first_idx + len("<internal_simulation>"):]
                                        in_sim = True
                                        q.put('<agent_state>يقوم بوضع خطة تفكير داخلية...</agent_state>')
                                    elif tag_type == 'act':
                                        buf = buf[first_idx + len("[[ACTION:"):]
                                        in_action = True
                                else:
                                    safe_len = max(0, len(buf) - 30)
                                    if safe_len > 0:
                                        q.put(buf[:safe_len])
                                        buf = buf[safe_len:]
                                    break
                                    
                            elif in_sim:
                                end_idx = buf.find("</internal_simulation>")
                                if end_idx != -1:
                                    buf = buf[end_idx + len("</internal_simulation>"):]
                                    in_sim = False
                                    q.put('<agent_state>جاري صياغة الرد النهائي...</agent_state>')
                                    q.put('<agent_state>أتم الوكيل عملية التفكير والمحاكاة بنجاح.</agent_state>')
                                else:
                                    break
                                    
                            elif in_action:
                                end_idx = buf.find("]]")
                                if end_idx != -1:
                                    action_content = buf[:end_idx]
                                    buf = buf[end_idx + len("]]"):]
                                    in_action = False
                                    
                                    clean_action = action_content.replace("[[ACTION:", "").strip()
                                    if clean_action.startswith("RUN_PYTHON|"):
                                        python_code_to_run = clean_action.split("RUN_PYTHON|")[1]
                                        has_python_action = True
                                    elif clean_action.startswith("CREATE_NOTIFICATION|"):
                                        q.put('AGENT_LOG: أصدر الوكيل إشعاراً استباقياً')
                                        execute_action(action_content + "]]")
                                    elif clean_action.startswith("SAVE_MEMORY|"):
                                        q.put('AGENT_LOG: تم حفظ الاستراتيجية في الذاكرة الدائمة')
                                        execute_action(action_content + "]]")
                                    elif clean_action.startswith("UPDATE_DECISION_METRIC|") or clean_action.startswith("RESOLVE_RISK|") or clean_action.startswith("RESOLVE_LEAK|"):
                                        action_payload = clean_action.replace("'", "&#39;")
                                        q.put(f'\n<div class="agent-tool-call mt-3 inline-block bg-[var(--glow)]/10 border border-[var(--glow)]/30 text-[var(--glow)] font-bold px-4 py-2 rounded-xl cursor-pointer hover:bg-[var(--glow)]/20 transition-all text-sm" onclick="executeAgentAction(this, \'{action_payload}\')"><i class="fa-solid fa-bolt me-2"></i>تطبيق التوصية في منصة القرارات</div>\n')
                                else:
                                    break

                        if has_python_action:
                            break
                
                if has_python_action:
                    q.put('STATUS___:جاري تنفيذ ومعالجة الكود برمجياً...')
                    q.put('AGENT_LOG: جاري تنفيذ كود بايثون...')
                    observation = run_python_code(python_code_to_run)
                    next_prompt = current_prompt + full_response + f"\n\nObservation: {observation}\n\n"
                    worker(next_prompt, iteration + 1)
                else:
                    if not in_sim and not in_action and buf:
                        q.put(buf)
                    q.put('STATUS___:DONE')
                    q.put(None)
                    
            except Exception as e:
                print(f"Notice: AI engine streaming fallback: {e}")
                q.put('<agent_state>يقوم بوضع خطة عمل تنفيذية واستراتيجية...</agent_state>')
                last_user_msg = messages_list[-1]['content'] if messages_list else prompt
                q.put('AI_DEGRADED:1')
                fallback_text = self._generate_smart_fallback_text(last_user_msg, user_id=user_id, agent_meta=agent_meta, lang=lang)
                q.put(fallback_text)
                q.put('\n<div class="agent-tool-call mt-3 inline-block bg-[var(--glow)]/10 border border-[var(--glow)]/30 text-[var(--glow)] font-bold px-4 py-2 rounded-xl cursor-pointer hover:bg-[var(--glow)]/20 transition-all text-sm" onclick="executeAgentAction(this, \'UPDATE_DECISION_METRIC|strategy|active|improving\')"><i class="fa-solid fa-bolt me-2"></i>تطبيق التوصية في منصة القرارات</div>\n')
                q.put('STATUS___:DONE')
                q.put(None)

        threading.Thread(target=worker, args=(prompt, 0), daemon=True).start()

        def event_stream():
            while True:
                text_chunk = q.get()
                if text_chunk is None:
                    break
                yield f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': text_chunk}]}}]})}\n\n"

        return event_stream()

    def _generate_smart_fallback_text(self, prompt, user_id=None, agent_meta=None, lang="ar"):
        """
        Generates an executive, data-driven expert analysis when external LLM API is unavailable.
        Fully bilingual (Arabic & English) with direct greeting detection.
        """
        import re
        prompt_str = (prompt or "").strip()
        prompt_lower = prompt_str.lower()
        
        # Determine language:
        if re.search(r'[\u0600-\u06FF]', prompt_str):
            is_ar = True
        elif re.search(r'[a-zA-Z]', prompt_str):
            is_ar = False
        else:
            is_ar = (lang == "ar")

        agent_title = agent_meta.get("name", "بصيرة" if is_ar else "Basira AI") if agent_meta else ("المستشار الذكي" if is_ar else "Executive AI Advisor")
        role_title = agent_meta.get("role_title", "المنسق الاستراتيجي" if is_ar else "Strategic Advisor") if agent_meta else ("التحليل الاستراتيجي" if is_ar else "Strategic Intelligence")

        # 1. Direct Greetings Recognition
        ar_greetings = ["هلا", "أهلا", "اهلا", "مرحبا", "مرحباً", "السلام عليكم", "صباح الخير", "مساء الخير", "يا هلا", "تحياتي"]
        en_greetings = ["hi", "hello", "hey", "good morning", "good evening", "greetings", "howdy", "what's up", "yo"]
        
        clean_words = [re.sub(r'[^\w\s]', '', w) for w in prompt_lower.split()]
        is_greeting = any(w in ar_greetings or prompt_lower == w for w in clean_words) or \
                      any(w in en_greetings or prompt_lower == w for w in clean_words) or \
                      prompt_lower in ["hi", "hello", "hey", "هلا", "مرحبا", "اهلا", "أهلا"]

        if is_greeting:
            if is_ar:
                return f"أهلاً بك! 👋 بصفتي **{agent_title}**، كيف يمكنني مساعدتك اليوم في إدارة أعمالك، تحليل البيانات، ومراجعة القرارات الاستراتيجية؟"
            else:
                return f"Hello! 👋 As your **{agent_title} ({role_title})**, how can I assist you today with your business operations, data analytics, and strategic decisions?"

        # 2. Topic-specific analyses (Waste / Cost reduction)
        if any(w in prompt_lower for w in ["هدر", "تالف", "خسار", "تكلف", "مصروف", "ضياع", "waste", "loss", "cost", "leakage"]):
            if is_ar:
                return f"""### 🛡️ استراتيجية ضبط وتقليص الهدر المالي والتشغيلي

بصفتي **{agent_title} ({role_title})**، أعددت لك خطة عمل فورية ومحكمة لوقف التسرب المالي وخفض الهدر:

---

#### 1. التدقيق الفوري في سلاسل الإمداد ومعدل دوران المخزون:
- **تطبيق معيار (JIT - Just in Time):** ضبط أوامر الشراء لتناسب متوسط الاستهلاك الفعلي وتجنب تكدس المخزون أو تقادمه.
- **تحديد نقطة إعادة الطلب الدقيقة:** مراجعة دورة حياة كل منتج ومطابقة التخزين مع فترات الصلاحية والطلب الأسبوعي.

#### 2. معالجة الثغرات التشغيلية والفواتير:
- **مطابقة الفواتير مع التسليم الفعلي:** التحقق الصارم من كل فاتورة استلام لمنع أي نقص في الكميات أو فروقات غير مبررة في الأسعار.
- **مراقبة الهدر غير المباشر:** حصر استهلاك الموارد التشغيلية والخدمية وتحديد سقف أعلى للمصروفات النثرية.

#### 3. التوصيات التنفيذية المباشرة:
1. **وضع مستهدف شهري:** خفض نسبة الهدر بنسبة **15% إلى 25%** خلال الـ 60 يوماً القادمة.
2. **ربط المسؤولية بالأداء:** تعيين مسؤول مباشر عن جرد الهدر أسبوعياً وتوثيق أسبابه.
3. **تحديث منصة القرارات:** ربط مؤشر كشف الهدر مباشرة ليعطي تنبيهاً مبكراً فور حدوث أي تباين."""
            else:
                return f"""### 🛡️ Financial & Operational Waste Reduction Strategy

As your **{agent_title} ({role_title})**, here is the immediate actionable roadmap to eliminate cost leakage and optimize resource allocation:

---

#### 1. Supply Chain Audit & Inventory Turnover:
- **Just-in-Time (JIT) Procurement:** Align purchase orders directly with verified consumption to eliminate excess inventory carrying costs.
- **Dynamic Reorder Points:** Review SKU velocity and buffer thresholds against weekly demand trends.

#### 2. Invoice Reconciliation & Operational Tightening:
- **Strict 3-Way Invoice Matching:** Ensure every vendor invoice strictly matches verified purchase orders and warehouse receipts.
- **Expense Capping:** Establish automated caps on discretionary overheads and recurring unused subscriptions.

#### 3. Strategic Action Roadmap:
1. **Target Savings:** Achieve a **15% - 25% waste reduction** within 60 days.
2. **Weekly Accountability:** Designate department leads to review reconciliation logs.
3. **Live Decision Platform:** Feed alerts directly into the Decision Platform for instant mitigation."""

        # 3. Topic-specific analyses (Revenue / Pricing / Growth)
        elif any(w in prompt_lower for w in ["ربح", "تسعير", "هامش", "مبيعات", "إيراد", "زيادة", "growth", "profit", "sales", "revenue", "price", "pricing"]):
            if is_ar:
                return f"""### 📈 خطة تنمية الإيرادات وتعظيم هامش الربحية

بصفتي **{agent_title} ({role_title})**، إليك خارطة الطريق التنفيذية لرفع الأرباح الصافية:

---

#### 1. هيكلة استراتيجية التسعير الديناميكي (Dynamic Pricing):
- **حساب هامش المساهمة الفعلي (Contribution Margin):** التركيز على بيع الأصناف والخدمات ذات الهامش الأعلى (>40%).
- **حزم العروض التكميلية (Bundling):** دمج المنتجات سريعة الحركة مع الأصناف عالية الربحية لرفع متوسط قيمة الفاتورة.

#### 2. تفعيل قنوات البيع الأكثر كفاءة:
- **تحسين مسار تحصيل الإيرادات:** خفض فترات الائتمان لتسريع دورة رأس المال العامل.
- **إعادة التفاوض على شروط التوريد:** طلب خصومات كمية عند الوصول لحجم مبيعات محدد لخفض تكلفة البضاعة المباعة (COGS).

#### 3. مؤشرات الأداء الحيوية ذات الأولوية:
1. **مراقبة صافي هامش الربح:** استهداف رفع الهامش الإجمالي بما لا يقل عن **5%**.
2. **تحفيز المبيعات المتكررة:** بناء ولاء العملاء لزيادة العائد على العميل الواحد (LTV)."""
            else:
                return f"""### 📈 Revenue Growth & Margin Optimization Plan

As your **{agent_title} ({role_title})**, here is the strategic plan to boost profitability and maximize top-line performance:

---

#### 1. Dynamic Pricing & Contribution Margin:
- **High-Margin Prioritization:** Shift sales focus towards products and services yielding contribution margins above 40%.
- **Strategic Bundling:** Package fast-moving items with premium services to increase Average Order Value (AOV).

#### 2. Working Capital & Distribution Channels:
- **Accelerate Cash Collection:** Tighten payment credit terms to improve working capital velocity.
- **Supplier Volume Rebates:** Restructure vendor terms for volume-tiered rebates, reducing COGS.

#### 3. Core KPI Milestones:
1. **Net Margin Target:** Expand gross margin by at least **5%** this quarter.
2. **Customer Lifetime Value (LTV):** Launch retention incentives to drive repeat purchases."""

        # 4. General Strategic Brief
        else:
            if is_ar:
                return f"""### 📋 التقرير التنفيذي والتوصيات الاستراتيجية

بصفتي **{agent_title} ({role_title})**، قمت بتحليل استفسارك ومؤشرات أعمالك، وإليك الخلاصة التنفيذية:

---

#### 1. التشخيص المالي والتشغيلي:
- استقرار العمليات يتطلب التوازن بين كفاءة التكاليف وسرعة دوران التدفقات النقدية.
- ضرورة المراقبة المستمرة للمؤشرات الأساسية لتفادي أي فجوة في السيولة أو ضغوط في الالتزامات.

#### 2. القرارات الموصى بها:
1. **تحديث ومزامنة البيانات دورياً:** رفع المستندات المالية أسبوعياً لتوليد تقارير نبض الأعمال الفورية.
2. **إدارة المخاطر الاستباقية:** توزيع مصادر الدخل لتفادي الاعتماد المفرط على عميل أو قطاع واحد.
3. **الالتزام بمستهدفات الأداء:** مراجعة الإنجاز مقابل الأهداف المالية المقررة شهرياً."""
            else:
                return f"""### 📋 Executive Brief & Strategic Recommendations

As your **{agent_title} ({role_title})**, I have analyzed your inquiry and operational metrics. Here is your executive summary:

---

#### 1. Financial & Operational Diagnostics:
- Sustainable growth requires balancing cost containment with working capital velocity.
- Continuous tracking of liquidity and operational KPIs is essential to prevent cash flow bottlenecks.

#### 2. Recommended Action Steps:
1. **Regular Data Sync:** Upload updated statements weekly to maintain real-time Decision Platform accuracy.
2. **Proactive Risk Diversification:** Distribute revenue channels to mitigate single-client dependency.
3. **Milestone Tracking:** Review progress against target monthly goals regularly."""

    def generate_multi_agent_stream(self, agent_ids, messages_list, file_context="", user_id=None, lang="ar"):
        """
        Sequential execution loop for Committee / Multi-Agent Group Conversation.
        Each agent maintains its own strictly isolated persona & system prompt.
        Agent 2+ receives prior agents' contributions in the debate context.
        """
        q = queue.Queue()

        def committee_worker():
            committee_transcript = []
            
            for index, aid in enumerate(agent_ids):
                meta = self.get_agent_meta(aid, user_id=user_id, lang=lang)
                
                # Signal the start of this specific agent's turn
                start_marker = f"[[AGENT_START:{meta['id']}:{meta['name']}:{meta['icon']}:{meta['color']}]]"
                q.put(start_marker)
                
                # Build strictly isolated persona prompt for THIS agent
                agent_role_prompt = meta['system_prompt_ar'] if lang == 'ar' else meta['system_prompt_en']
                
                # Committee debate context if prior agents have spoken
                committee_context = ""
                if committee_transcript:
                    debate_log = "\n".join([f"- [{t['name']}]: {t['content']}" for t in committee_transcript])
                    if lang == "ar":
                        committee_context = f"""
\n\n[سجل جلسة نقاش اللجنة التنفيذية الحالية / Current Committee Debate Transcript]:
{debate_log}

[توجيه المشاركة في اللجنة / Multi-Agent Collaboration Directive]:
أنت تشارك الآن في جلسة نقاش تنفيذية مشتركة بصفتك "{meta['name']}" ({meta['role_title']}).
المطلوب منك:
1. التزم حصرياً بدورك وتخصصك ({meta['role_title']}) ولا تتقمص أدوار زملائك.
2. تفاعل مباشرة وبذكاء مع ما طرحه زملاؤك أعلاه في اللجنة: أيد النقاط الصائبة، انتقد الثغرات من منظور تخصصك، أو قدم حلولاً وتوصيات تكميلية تنبع من مجال خبرتك.
3. ابدأ ردك مباشرة بالتحليل والمداخلة دون تكرار مقدمات عامة.
4. قاعدة ذهبية (تدقيق مالي صارم): يُمنع منعاً باتاً استنتاج أو اختلاق أي أرقام غير موجودة في البيانات المرفقة. إذا وجدت زميلاً قدم رقماً خاطئاً أو استنتج بيانات غير دقيقة، قم بتصحيحه فوراً وقم بحل النزاع بناءً على البيانات الفعلية فقط.
"""
                    else:
                        committee_context = f"""
\n\n[Current Committee Debate Transcript]:
{debate_log}

[Multi-Agent Collaboration Directive]:
You are actively participating in an executive committee debate as "{meta['name']}" ({meta['role_title']}).
Requirements:
1. Strictly maintain your own specific domain role ({meta['role_title']}).
2. Explicitly review and interact with the prior agents' statements above: agree, challenge, or expand on their arguments from your domain angle.
3. Dive straight into your specialized analysis.
4. GOLDEN RULE (Strict Financial Audit): You are strictly forbidden from hallucinating or inventing any numbers not present in the provided data. If a colleague presents a wrong number or inaccurate deduction, correct them immediately and resolve the conflict based ONLY on actual data.
"""
                
                base_system = self.system_prompt_ar if lang == "ar" else self.system_prompt_en
                if lang == "ar":
                    lang_dir = (
                        "\n\n[توجيه لغوي إلزامي صارم]:\n"
                        "المستخدم كتب بالعربية. يجب أن يكون ردك بالكامل وباللغة العربية الفصحى المهنية حصراً. "
                        "يُمنع منعاً باتاً الرد بالإنجليزية. كل العناوين والتحليلات والتوصيات والملخصات يجب أن تكون بالعربية."
                    )
                else:
                    lang_dir = (
                        "\n\n[STRICT LANGUAGE ENFORCEMENT]:\n"
                        "The user wrote in ENGLISH. Your ENTIRE response MUST be in English only — "
                        "including all headings, analysis, recommendations, and summaries. "
                        "Do NOT output any Arabic text whatsoever. Responding in Arabic to an English query is a critical failure."
                    )

                local_file_context = file_context if file_context else ""
                    
                agent_prompt = f"*** AGENT ROLE & PERSONA ***\n{agent_role_prompt}\n*** END AGENT ROLE ***\n\n" + base_system + lang_dir + committee_context + "\n\nFile Context:\n" + local_file_context + "\n\nConversation History:\n"
                for msg in messages_list:
                    agent_prompt += f"{msg['role']}: {msg['content']}\n"
                agent_prompt += f"model ({meta['name']}): "

                # Call LLM for this agent
                try:
                    if not hasattr(self, 'client') or not self.client or not hasattr(self.client, 'models'):
                        last_user_msg = messages_list[-1]['content'] if messages_list else ""
                        aid_name = meta.get('name', 'الوكيل')
                        aid_id = meta.get('id', 'agent')
                        q.put(f"<agent_state>({aid_name}) يقوم بإعداد المداخلة المتخصصة...</agent_state>")
                        q.put('AI_DEGRADED:1')
                        agent_text_accum = self._generate_smart_fallback_text(last_user_msg, user_id=user_id, agent_meta=meta)
                        q.put(agent_text_accum)
                        action_btn = f'\n<div class="agent-tool-call mt-3 inline-block bg-[var(--glow)]/10 border border-[var(--glow)]/30 text-[var(--glow)] font-bold px-4 py-2 rounded-xl cursor-pointer hover:bg-[var(--glow)]/20 transition-all text-sm" onclick="executeAgentAction(this, \'UPDATE_DECISION_METRIC|{aid_id}_plan|active|improving\')"><i class="fa-solid fa-bolt me-2"></i>تطبيق توصية {aid_name} في منصة القرارات</div>\n'
                        q.put(action_btn)
                    else:
                        stream = self.client.models.generate_content_stream(
                            model=GEMINI_MODEL,
                            contents=agent_prompt
                        )
                        
                        agent_text_accum = ""
                        buf = ""
                        in_sim = False
                        in_action = False
                        
                        for chunk in stream:
                            if chunk.text:
                                agent_text_accum += chunk.text
                                buf += chunk.text
                                
                                while True:
                                    if not in_sim and not in_action:
                                        sim_idx = buf.find("<internal_simulation>")
                                        act_idx = buf.find("[[ACTION:")
                                        
                                        idxs = [(sim_idx, 'sim'), (act_idx, 'act')]
                                        valid_idxs = [x for x in idxs if x[0] != -1]
                                        
                                        if valid_idxs:
                                            valid_idxs.sort(key=lambda x: x[0])
                                            first_idx, tag_type = valid_idxs[0]
                                            
                                            if first_idx > 0:
                                                q.put(buf[:first_idx])
                                                
                                            if tag_type == 'sim':
                                                buf = buf[first_idx + len("<internal_simulation>"):]
                                                in_sim = True
                                                q.put(f"<agent_state>({meta['name']}) يقوم بوضع خطة تفكير...</agent_state>")
                                            elif tag_type == 'act':
                                                buf = buf[first_idx + len("[[ACTION:"):]
                                                in_action = True
                                        else:
                                            safe_len = max(0, len(buf) - 30)
                                            if safe_len > 0:
                                                q.put(buf[:safe_len])
                                                buf = buf[safe_len:]
                                            break
                                    elif in_sim:
                                        end_idx = buf.find("</internal_simulation>")
                                        if end_idx != -1:
                                            buf = buf[end_idx + len("</internal_simulation>"):]
                                            in_sim = False
                                            q.put(f"<agent_state>({meta['name']}) أتم خطة التفكير.</agent_state>")
                                        else:
                                            break
                                    elif in_action:
                                        end_idx = buf.find("]]")
                                        if end_idx != -1:
                                            action_content = buf[:end_idx]
                                            buf = buf[end_idx + len("]]"):]
                                            in_action = False
                                            
                                            clean_action = action_content.replace("[[ACTION:", "").strip()
                                            if clean_action.startswith("UPDATE_DECISION_METRIC|") or clean_action.startswith("RESOLVE_RISK|") or clean_action.startswith("RESOLVE_LEAK|"):
                                                action_payload = clean_action.replace("'", "&#39;")
                                                q.put(f'\n<div class="agent-tool-call mt-3 inline-block bg-[var(--glow)]/10 border border-[var(--glow)]/30 text-[var(--glow)] font-bold px-4 py-2 rounded-xl cursor-pointer hover:bg-[var(--glow)]/20 transition-all text-sm" onclick="executeAgentAction(this, \'{action_payload}\')"><i class="fa-solid fa-bolt me-2"></i>تطبيق التوصية في منصة القرارات</div>\n')
                                        else:
                                            break

                        if not in_sim and not in_action and buf:
                            q.put(buf)
                            
                    # Clean agent response for the committee transcript buffer
                    clean_response = agent_text_accum
                    import re
                    clean_response = re.sub(r'<internal_simulation>[\s\S]*?<\/internal_simulation>', '', clean_response)
                    clean_response = re.sub(r'<agent_state>[\s\S]*?<\/agent_state>', '', clean_response).strip()
                    
                    committee_transcript.append({
                        "id": meta['id'],
                        "name": meta['name'],
                        "content": clean_response
                    })
                    
                except Exception as e:
                    print(f"Notice: Committee agent fallback: {e}")
                    last_user_msg = messages_list[-1]['content'] if messages_list else ""
                    q.put('AI_DEGRADED:1')
                    fallback_text = self._generate_smart_fallback_text(last_user_msg, user_id=user_id, agent_meta=meta, lang=lang)
                    q.put(fallback_text)
                    committee_transcript.append({
                        "id": meta['id'],
                        "name": meta['name'],
                        "content": fallback_text
                    })
                
                # Signal completion of this specific agent's response
                end_marker = f"[[AGENT_END:{meta['id']}]]"
                q.put(end_marker)

            q.put('STATUS___:DONE')
            q.put('[[COMMITTEE_DONE]]')
            q.put(None)

        threading.Thread(target=committee_worker, daemon=True).start()

        def event_stream():
            while True:
                text_chunk = q.get()
                if text_chunk is None:
                    break
                yield f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': text_chunk}]}}]})}\n\n"

        return event_stream()

    def analyze_dataset_for_mobile(self, df_summary, lang="en"):
        lang_instruction = "English text" if lang == "en" else "Arabic text"
        fallback_msg = "Analysis completed, but error in generating text." if lang == "en" else "تم تحليل البيانات بنجاح، ولكن الذكاء الاصطناعي واجه مشكلة في توليد النص النهائي."
        
        prompt = f"""You are Basira (بصيرة), an elite AI Financial Director.
The user uploaded a dataset with the following summary (enclosed in ``` delimiters):
```
{df_summary}
```

Analyze this data to:
1. Find any financial gaps, risks, or critical insights.
2. Forecast sales/revenue for the next 6 periods based on the trends in the data.

IMPORTANT: Generate the "ai_insight" analysis in {lang_instruction}. Do NOT include any introductory conversational greetings. Start the text directly with the analysis.

You MUST respond with ONLY a raw JSON object (no markdown, no backticks, no other text) matching exactly this format:
{{
    "ai_insight": "Your detailed {lang_instruction} analysis about the financial gap and what to do, directly without greetings.",
    "forecast": [100.5, 110.2, 115.0, 105.5, 120.0, 125.5]
}}
Ensure the forecast contains exactly 6 numeric values."""

        try:
            response = self.client.models.generate_content(
                model="gemini-flash-lite-latest",
                contents=prompt
            )
            text = response.text.strip()
            if text.startswith("```json"):
                text = text.replace("```json", "", 1).replace("```", "")
            if text.startswith("```"):
                text = text.replace("```", "")
            return json.loads(text.strip())
        except Exception as e:
            print(f"Error in analyze_dataset_for_mobile: {e}")
            return {
                "ai_insight": fallback_msg,
                "forecast": [0, 0, 0, 0, 0, 0]
            }

    def generate_boardroom_debate(self, topic, file_context=""):
        """
        Simulates an executive multi-agent boardroom debate on a strategic topic.
        Returns a structured JSON containing speeches from 4 distinct board directors and final resolution.
        """
        prompt = f"""You are the Executive Boardroom AI Engine for 'بصيرة' (Baseera Business Intelligence).
The business owner has convened an urgent executive board meeting on the following strategic decision/topic:
"{topic}"

Context/Dataset Summary:
{file_context if file_context else "No active dataset attached. Use realistic commercial and financial assumptions for retail/SME business."}

Simulate a realistic, highly intelligent debate between 4 distinct executive board members, followed by an official Board Resolution by Basira:
1.  المدير المالي (CFO): Prioritizes cost reduction, high margins, and immediate profitability.
2.  مدير العمليات وسلاسل الإمداد (COO / Supply Chain Officer): Highlights operational feasibility, stock constraints, supplier lead times, and capacity.
3.  أخصائي التسعير وهوامش الربح (Pricing & Revenue Strategist): Evaluates price elasticity, willingness to pay, unit economics, and bundling strategies.
4.  بصيرة - المستشار التنفيذي العام (Basira / Board Chair Resolution): Synthesizes the arguments into a definitive, actionable decision and 3 concrete next steps.

Language: Arabic (فصحى مهنية راقية).

You MUST return ONLY a valid JSON object matching EXACTLY this structure (no markdown fences, no raw text outside JSON):
{{
    "topic": "{topic}",
    "speakers": [
        {{
            "id": "financial",
            "name": "المحلل المالي (CFO)",
            "avatar_icon": "line-chart",
            "color": "emerald",
            "stance": "تحفظ مالي / حذر",
            "argument": "النص التفصيلي لمداخلة المحلل المالي..."
        }},

        {{
            "id": "supply_chain",
            "name": "مدير العمليات والإمداد (COO)",
            "avatar_icon": "truck",
            "color": "blue",
            "stance": "انضباط تشغيلي / تدقيق المخزون",
            "argument": "النص التفصيلي لمداخلة مدير العمليات..."
        }},
        {{
            "id": "pricing",
            "name": "أخصائي استراتيجية التسعير",
            "avatar_icon": "tag",
            "color": "purple",
            "stance": "تعظيم الهوامش / مرونة الطلب",
            "argument": "النص التفصيلي لمداخلة أخصائي التسعير..."
        }}
    ],
    "resolution": {{
        "decision": "القرار الاستراتيجي الموحد المعتمد من مجلس الإدارة...",
        "expected_roi": "+18% نمو متوقع في صافي الأرباح",
        "risk_level": "متوسط (تحت السيطرة)",
        "action_items": [
            "الخطوة التنفيذية الأولى",
            "الخطوة التنفيذية الثانية",
            "الخطوة التنفيذية الثالثة"
        ]
    }}
}}"""

        try:
            response = self.client.models.generate_content(
                model="gemini-flash-lite-latest",
                contents=prompt
            )
            text = response.text.strip()
            if text.startswith("```json"):
                text = text.replace("```json", "", 1).replace("```", "")
            if text.startswith("```"):
                text = text.replace("```", "")
            return json.loads(text.strip())
        except Exception as e:
            print(f"Error in generate_boardroom_debate: {e}")
            return {
                "topic": topic,
                "speakers": [
                    {
                        "id": "financial",
                        "name": "المحلل المالي (CFO)",
                        "avatar_icon": "line-chart",
                        "color": "emerald",
                        "stance": "حذر مالي",
                        "argument": f"بناءً على المعطيات المالية، أي تحرك بخصوص '{topic}' يجب أن يضمن الحفاظ على السيولة النقدية وهامش أمان 20% على الأقل لتغطية تكاليف التشغيل."
                    },
                    {
                        "id": "supply_chain",
                        "name": "مدير العمليات والإمداد (COO)",
                        "avatar_icon": "truck",
                        "color": "blue",
                        "stance": "جاهزية تشغيلية",
                        "argument": "نؤكد على ضرورة تأمين المخزون والمواد الأولية مقدماً لضمان عدم حدوث أي انقطاع في تلبية طلبات العملاء."
                    },
                    {
                        "id": "pricing",
                        "name": "أخصائي استراتيجية التسعير",
                        "avatar_icon": "tag",
                        "color": "purple",
                        "stance": "حماية الهامش",
                        "argument": "نقترح اعتماد هيكل تسعير تفاضلي يراعي أعلى الأصناف مبيعاً لضمان عدم تأثر العائد الصافي لكل وحدة."
                    }
                ],
                "resolution": {
                    "decision": f"الموافقة المشروطة على تنفيذ مبادرة '{topic}' بتدرج مرحلي يبدأ بتجربة أولية لمدة أسبوعين.",
                    "expected_roi": "+15% إلى +22% تحسن في الأداء التجاري",
                    "risk_level": "منخفض إلى متوسط",
                    "action_items": [
                        "إعادة التفاوض مع الموردين على خصومات الكميات",
                        "إعادة التفاوض مع الموردين على خصومات الكميات",
                        "مراجعة نتائج التجربة بعد 14 يوماً وتعديل الأسعار حسب الطلب"
                    ]
                }
            }

    def extract_structured_data_from_file(self, file_path):
        """
        Universal method to extract tabular/structured data from ANY file (PDF, Image, TXT)
        using Gemini File API.
        """
        import json
        import os
        
        prompt = """
        You are an expert data analyst and accountant. Analyze this document (it could be a bank statement, receipt, invoice, or unstructured text).
        Extract all transactional or tabular data into a clean JSON array of objects.
        Dynamically infer the best column names based on the content (e.g., "Date", "Description", "Amount", "Category", "Type").
        Return ONLY a JSON array, for example:
        [
            {"Date": "2024-05-12", "Description": "Purchase", "Amount": 150.00},
            {"Date": "2024-05-13", "Description": "Refund", "Amount": -20.00}
        ]
        Do not include markdown fences or any other text outside the JSON array.
        """
        
        uploaded_file = None
        try:
            if not hasattr(self, 'client') or not self.client:
                return None

            # Upload to Gemini File API
            uploaded_file = self.client.files.upload(file=file_path)
            
            response = self.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[uploaded_file, prompt]
            )
            
            text = response.text.strip()
            if text.startswith("```json"):
                text = text.replace("```json", "", 1).replace("```", "")
            elif text.startswith("```"):
                text = text.replace("```", "", 1).replace("```", "")
                
            data = json.loads(text.strip())
            return data
        except Exception as e:
            print(f"Notice: AI document extraction fallback triggered: {e}")
            return None
        finally:
            # Always clean up the file from Gemini servers
            if uploaded_file and hasattr(self, 'client') and self.client:
                try:
                    self.client.files.delete(name=uploaded_file.name)
                except Exception as cleanup_err:
                    pass

    def extract_receipt_data(self, file_path):
        """
        Extracts structured data from a receipt/invoice image using Gemini Multimodal.
        """
        import PIL.Image
        import json
        
        prompt = """
        You are an expert accountant. Analyze this receipt or invoice.
        Extract the following data into a clean JSON object ONLY (no markdown fences, no other text):
        {
            "merchant_name": "Name of the store or company",
            "date": "Date of transaction (YYYY-MM-DD)",
            "total_amount": 0.0,
            "tax_amount": 0.0,
            "currency": "Currency code or symbol",
            "items": [
                {"description": "Item 1", "quantity": 1, "price": 0.0, "total": 0.0}
            ]
        }
        """
        try:
            if not hasattr(self, 'client') or not self.client:
                return None
            img = PIL.Image.open(file_path)
            response = self.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[img, prompt]
            )
            text = response.text.strip()
            if text.startswith("```json"):
                text = text.replace("```json", "", 1).replace("```", "")
            if text.startswith("```"):
                text = text.replace("```", "")
            return json.loads(text.strip())
        except Exception as e:
            print(f"Receipt extraction notice: {e}")
            return None

    def generate_weekly_digest_for_user(self, df_summary, user):
        """
        Generates a dynamic WeeklyDigest (Business Pulse / Decision Report) based on the user's uploaded data.
        """
        import json
        import datetime
        from dashboard.models import WeeklyDigest

        now = datetime.datetime.now()
        week_label = f"تقرير بيانات {now.strftime('%d-%m-%Y')} ({now.strftime('%H:%M')})"

        prompt = f"""
        You are an executive business analyst and financial AI for the "Baseera" (بصيرة) Decision Platform.
        A business owner just uploaded their dataset. Here is a summary of the records:
        
        {df_summary}
        
        Analyze this data and generate a clear, highly actionable Executive Decision & Risk Report (تقرير نبض الأعمال والقرارات).
        Return ONLY a valid JSON object with the exact keys:
        - "summary_text": A 2-3 sentence executive summary of performance and revenue health in Arabic.
        - "top_risks": Array of 1 to 3 specific financial or operational risks detected from the data in Arabic.
        - "top_opportunities": Array of 1 to 3 growth opportunities based on the high-performing categories/items in Arabic.
        - "action_plan": Array of 1 to 3 prioritized executive decisions and action points for the owner in Arabic.
        """
        
        try:
            if hasattr(self, 'client') and self.client:
                response = self.client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=prompt
                )
                
                text = response.text.strip()
                if text.startswith("```json"):
                    text = text.replace("```json", "", 1)
                if text.endswith("```"):
                    text = text[:-3]
                    
                data = json.loads(text.strip())
                
                WeeklyDigest.objects.filter(user=user).delete()
                digest = WeeklyDigest.objects.create(
                    user=user,
                    week_label=week_label,
                    summary_text=data.get("summary_text", "تم فحص بيانات أعمالك بنجاح وتحليل مؤشرات الأداء الرئيسية."),
                    top_risks=data.get("top_risks", []),
                    top_opportunities=data.get("top_opportunities", []),
                    action_plan=data.get("action_plan", [])
                )
                return digest
        except Exception as e:
            print(f"AI generation fallback triggered: {e}")

        # Smart Data-Driven Fallback Digest
        try:
            summary_info = ""
            records_count = 0
            total_revenue = 0.0
            categories_set = set()
            
            if isinstance(df_summary, str) and df_summary.strip().startswith("["):
                try:
                    records = json.loads(df_summary)
                    records_count = len(records)
                    for r in records:
                        for k, v in r.items():
                            k_low = str(k).lower()
                            if any(w in k_low for w in ['category', 'قسم', 'فئة', 'نوع', 'item', 'product']):
                                categories_set.add(str(v))
                            if any(w in k_low for w in ['sales', 'revenue', 'مبيعات', 'إيراد', 'مبلغ', 'total', 'price']):
                                try:
                                    num = float(str(v).replace(',', '').replace('OMR', '').replace('ر.ع.', '').strip())
                                    if num > 0:
                                        total_revenue += num
                                except (ValueError, TypeError):
                                    pass
                except Exception:
                    pass

            if records_count > 0:
                rev_str = f" بقيمة إجمالية مفحوصة {total_revenue:,.2f} ر.ع." if total_revenue > 0 else ""
                summary_info = f"أظهر تحليل {records_count} سجلاً تشغيلياً{rev_str} استقراراً في مؤشرات الأداء الأساسية مع جاهزية كاملة لاتخاذ القرارات الاستراتيجية."
            else:
                summary_info = "تم تنظيف وفحص بيانات المستند المرفوع بنجاح، ومؤشرات الأداء تظهر استقراراً عاماً في التدفقات التشغيلية."

            cats_preview = f" في قطاعات ({', '.join(list(categories_set)[:2])})" if categories_set else ""
            
            WeeklyDigest.objects.filter(user=user).delete()
            digest = WeeklyDigest.objects.create(
                user=user,
                week_label=week_label,
                summary_text=summary_info,
                top_risks=[
                    f"مراقبة تركز الإيرادات{cats_preview} لتجنب أي تذبذب في سلاسل التوريد والطلب.",
                    "المتابعة الدورية لفواتير التحصيل والائتمان لحماية السيولة النقدية اليومية."
                ],
                top_opportunities=[
                    "زيادة التركيز على المنتجات والخدمات الأعلى طلباً لتعظيم هامش الربح الإجمالي.",
                    "تحسين شروط الشراء مع الموردين الرئيسيين بناءً على حجم المبيعات الفعلي."
                ],
                action_plan=[
                    "اعتماد تقرير تسعير ديناميكي للأصناف الأكثر رواجاً لرفع العائد الاستثماري.",
                    "مراجعة دورة التخزين والتدفقات النقدية أسبوعياً لتفادي أي عجز غير متوقع."
                ]
            )
            return digest
        except Exception as fallback_err:
            print(f"Fallback digest error: {fallback_err}")
            return None
            print(f"Error in fallback digest creation: {fallback_err}")
            return None
