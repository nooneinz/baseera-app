"""
Orchestrator / Router — section 4 of the platform architecture spec.

Classifies every incoming chat message into exactly one of three routes
BEFORE any agent/LLM call is made, so:

  * Route 1 (off_topic): a general question with no financial/business
    relevance gets a direct, canned refusal — no file lookup, no LLM call,
    no chance of hallucinating a financial analysis for a geography question.
  * Route 2 (single_file): a simple query answerable by one specialist agent
    against (at most) one matched file/sheet — resolved through the
    Retrieval Layer (dashboard.services.retrieval_service), with an explicit
    confirmation step whenever the match is ambiguous or missing.
  * Route 3 (multi_agent): a complex strategic decision gets a dynamically
    selected committee of specialist agents (never a fixed/static list).

Also implements the hard-constraint gate (spec section 4.1, rules 5 & 6):
any structured recommendation that busts max_investment_limit or would push
liquidity under cash_reserve_floor is rejected outright, never surfaced as
an option, regardless of its return.
"""
import re
import logging

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Route classification
# --------------------------------------------------------------------------

ROUTE_OFF_TOPIC = "off_topic"
ROUTE_SINGLE_FILE = "single_file"
ROUTE_MULTI_AGENT = "multi_agent"
ROUTE_GREETING = "greeting"

# Business/financial domain signal — presence of ANY of these takes a message
# out of "off_topic" contention regardless of how the rest of the sentence
# reads. This is intentionally broad (not just narrow accounting terms) since
# rule 2 in the system prompt scopes "بصيرة" to finance AND business
# management generally.
BUSINESS_DOMAIN_KEYWORDS = [
    'مبيعات', 'إيراد', 'ايراد', 'مصروف', 'مصاريف', 'ربح', 'خسارة', 'فاتورة', 'فواتير',
    'تكلفة', 'تكاليف', 'ميزانية', 'استثمار', 'تمويل', 'سيولة', 'تدفق نقدي', 'مخزون',
    'عميل', 'عملاء', 'مورد', 'موردين', 'سعر', 'تسعير', 'هامش', 'ضريبة', 'رأس مال',
    'شركة', 'مشروع', 'تقرير', 'ملف', 'بيانات', 'قرار', 'استراتيجية', 'توسع', 'نمو',
    'موظف', 'رواتب', 'أصول', 'خصوم', 'ديون', 'قرض', 'توقعات', 'تحليل', 'مقارنة',
    'خطة', 'خطه', 'خطط', 'خطتنا', 'هدر', 'أعمال', 'عمل', 'وكيل', 'وكلاء', 'مساعدة', 'مساعدني',
    'sales', 'revenue', 'expense', 'expenses', 'profit', 'loss', 'invoice', 'invoices',
    'cost', 'costs', 'budget', 'invest', 'investment', 'finance', 'financial', 'cash',
    'liquidity', 'inventory', 'stock', 'customer', 'supplier', 'vendor', 'price', 'pricing',
    'margin', 'tax', 'capital', 'company', 'business', 'report', 'file', 'data', 'decision',
    'strategy', 'expand', 'expansion', 'growth', 'employee', 'payroll', 'asset', 'liability',
    'debt', 'loan', 'forecast', 'analysis', 'analyze', 'compare', 'roi', 'kpi', 'p&l',
    'plan', 'plans', 'waste', 'help', 'agent', 'agents',
]

# Pure greetings/small-talk openers -- a message that is ONLY one of these
# (optionally with a couple of trailing filler words like "وكيفك") must
# never be treated as off-topic, and must never even reach the LLM
# secondary classifier: its job description ("no relevance to
# finance/business at all") technically fits a bare "hi", but refusing a
# greeting on a business platform is wrong regardless of what the model
# infers. A greeting WITH a real request attached (e.g. "السلام عليكم
# احتاج منك خطة") is intentionally excluded here -- that has real content
# and must go through normal routing instead of a canned hello.
GREETING_OPENERS = [
    'هلا', 'أهلا', 'اهلا', 'يا هلا', 'مرحبا', 'مرحباً', 'السلام عليكم', 'صباح الخير',
    'مساء الخير', 'تحياتي', 'حياك', 'حياك الله',
    'hi', 'hello', 'hey', 'good morning', 'good evening', 'greetings', 'howdy',
]

# Small-talk fillers tolerated right after a greeting opener (still "just a
# greeting", not a real request) -- deliberately a fixed, explicit list
# rather than "any short remainder": a real request can also be just a few
# words ("احتاج منك خطة" is 3 words), so length alone can't distinguish it
# from small talk.
_SMALL_TALK_FILLERS = {
    'كيف حالك', 'كيفك', 'شخبارك', 'شلونك', 'ايش اخبارك', 'كيف الحال',
    'how are you', "how's it going", 'whats up', "what's up",
}

# Terms describing a computed/INFERRED financial insight rather than a
# specific product, item, or category. This list gates the active-file
# fallback below (route_message): a query about "هدر" (waste) is asking
# for an analysis of whatever data the user already has -- waste is never
# a literal column, it's inferred by waste_analyzer.py from price/cost/qty
# patterns, so no file was ever going to match it by keyword, and there is
# nothing product-specific to get wrong by defaulting to the active file.
# A query naming an actual product/subject (e.g. "دجاج" / chicken) is
# deliberately NOT covered here: guessing the wrong file for that could
# silently answer about the wrong product, which is exactly what the
# Retrieval Layer's ambiguity check exists to prevent (the "chicken vs
# meat file" scenario) -- keep this list narrow, don't add generic terms.
INFERRED_METRIC_TERMS = [
    'هدر', 'الهدر', 'مهدر', 'تسرب', 'تسريب',
    'ربح', 'الربح', 'خسارة', 'الخسارة', 'هامش', 'الهامش',
    'وضع مالي', 'الوضع المالي', 'تدفق نقدي', 'التدفق النقدي', 'سيولة', 'السيولة',
    'waste', 'leakage', 'profit', 'loss', 'margin', 'cash flow', 'liquidity',
]

# Generic trivia/factual question markers — necessary but not sufficient on
# their own (a business question can also start with "what is/كم"), they only
# push toward off_topic when NO business-domain keyword is also present.
TRIVIA_PATTERNS = [
    r'\bعاصمة\b', r'\bمن هو\b', r'\bمن هي\b', r'\bمتى\b', r'\bأين تقع\b', r'\bكم عدد سكان\b',
    r'\bما هي عاصمة\b', r'\bطقس\b', r'\bوصفة\b', r'\bكرة القدم\b', r'\bفيلم\b', r'\bمباراة\b',
    r'\bcapital of\b', r'\bwho is\b', r'\bweather\b', r'\brecipe\b', r'\bfootball\b',
    r'\bmovie\b', r'\bpopulation of\b', r'\bwho won\b',
]

# Signals that a message wants a complex, multi-perspective strategic
# decision rather than a single lookup — this is what triggers the committee
# route (Route 3) instead of a single general agent (Route 2).
STRATEGIC_DECISION_KEYWORDS = [
    'قرار', 'استراتيجية', 'استراتيجي', 'خطة', 'خطه', 'خطتنا', 'توسع', 'نطلق', 'إطلاق', 'دمج',
    'استحواذ', 'إعادة هيكلة', 'نستثمر', 'استثمار', 'مقارنة بين', 'أيهما أفضل', 'قرار مصيري',
    'decision', 'strategy', 'strategic', 'plan', 'expand', 'expansion', 'launch', 'merger',
    'acquisition', 'restructur', 'should we', 'which is better', 'trade-off', 'tradeoff',
]

# Maps a detected specialist domain to the agent_id used by GeminiAIService.get_agent_meta.
DOMAIN_AGENT_MAP = {
    'financial': ['مالي', 'ربح', 'تكلفة', 'ميزانية', 'تدفق نقدي', 'هامش', 'financial', 'profit',
                  'cost', 'budget', 'cash flow', 'margin', 'roi'],
    'supply_chain': ['مخزون', 'مورد', 'توريد', 'شحن', 'لوجستي', 'مستودع', 'inventory', 'supplier',
                      'supply chain', 'logistics', 'warehouse', 'shipping', 'stock'],
    'pricing': ['تسعير', 'سعر', 'خصم', 'عرض', 'حزمة', 'pricing', 'price', 'discount', 'bundle'],
    'audit': ['تدقيق', 'احتيال', 'هدر', 'شذوذ', 'مطابقة', 'audit', 'fraud', 'anomaly',
              'reconcil', 'waste'],
    'retention': ['ولاء', 'عملاء', 'انسحاب', 'استبقاء', 'loyalty', 'churn', 'retention',
                  'customer lifetime'],
}


def _normalize_arabic_letters(text):
    """
    Arabic تاء مربوطة (ة) and هاء (ه) look near-identical and are routinely
    typed interchangeably, especially on mobile keyboards -- e.g.
    "استراتيجيه" vs "استراتيجية". Without normalizing this, a keyword-list
    entry spelled one way silently fails to match a message spelled the
    other way (reported bug: "استراتيجيه للمستقبل" fell through to a
    "couldn't find the document" refusal instead of routing to the
    strategic multi-agent committee, purely because BUSINESS_DOMAIN_
    KEYWORDS only had the "ة" spelling). Applied to both sides of every
    keyword-list comparison below so this class of typo can never break a
    match.
    """
    return (text or "").replace("ة", "ه")


def _matches_any(patterns, text_lower):
    normalized = _normalize_arabic_letters(text_lower)
    return any(re.search(_normalize_arabic_letters(p), normalized) for p in patterns)


def _has_business_domain_signal(text_lower):
    normalized = _normalize_arabic_letters(text_lower)
    return any(_normalize_arabic_letters(kw) in normalized for kw in BUSINESS_DOMAIN_KEYWORDS)


def _is_pure_greeting(text_lower):
    """
    True only when the ENTIRE message is a greeting opener, optionally
    followed by nothing but small talk (see _SMALL_TALK_FILLERS). A
    greeting with a real request attached (e.g. "السلام عليكم احتاج منك
    خطة") returns False -- that has to go through normal routing, not a
    canned hello.
    """
    stripped = text_lower.strip(" ,.!؟?")
    if not stripped:
        return False
    if stripped in GREETING_OPENERS:
        return True
    for opener in GREETING_OPENERS:
        if stripped.startswith(opener):
            remainder = stripped[len(opener):].strip(" ,.!؟?")
            if not remainder or remainder in _SMALL_TALK_FILLERS:
                return True
    return False


def greeting_reply(lang="ar"):
    if lang == "ar":
        return "أهلاً بك! أنا بصيرة، مساعدك المالي. كيف يمكنني مساعدتك اليوم في إدارة أعمالك أو تحليل بياناتك؟"
    return "Hello! I'm Baseera, your financial assistant. How can I help you with your business or data today?"


def classify_route(message, has_uploaded_files=False):
    """
    Rule-based intent classification (deterministic, testable, and always
    available offline). This is the primary/fallback classifier; when a live
    LLM client is available, callers may additionally use
    llm_classify_route() for a second opinion on ambiguous cases — but the
    heuristic below is authoritative for the 3 mandatory acceptance
    scenarios (greeting/off-topic vs simple query vs strategic decision).
    """
    text = (message or "").strip()
    text_lower = text.lower()

    if not text:
        return ROUTE_SINGLE_FILE

    # Route 0: a pure greeting ("هلا", "hello") is answered directly and
    # confidently here -- it never reaches the off-topic checks below, and
    # (in route_message) never reaches the LLM secondary classifier either.
    # That LLM's job description ("no relevance to finance/business at
    # all") technically fits a bare "hi" -- refusing a greeting on this
    # platform would be wrong regardless of what the model infers, so this
    # has to be decided before either heuristic or LLM off-topic logic runs.
    if _is_pure_greeting(text_lower):
        return ROUTE_GREETING

    has_business_signal = _has_business_domain_signal(text_lower)
    looks_like_trivia = _matches_any(TRIVIA_PATTERNS, text_lower)

    # Route 1: off-topic — a factual/trivia question with no business signal
    # at all.
    if looks_like_trivia and not has_business_signal:
        return ROUTE_OFF_TOPIC

    # A message with neither a business signal nor any reference to
    # uploaded data, and that reads as a short general-knowledge question
    # (ends with '?' / '؟', no numbers, no data-ish words) is also off-topic.
    if not has_business_signal and not has_uploaded_files:
        looks_like_question = text.endswith('?') or text.endswith('؟')
        has_digits = any(ch.isdigit() for ch in text)
        if looks_like_question and not has_digits and len(text.split()) <= 12:
            return ROUTE_OFF_TOPIC

    if has_business_signal and _matches_any(STRATEGIC_DECISION_KEYWORDS, text_lower):
        return ROUTE_MULTI_AGENT

    return ROUTE_SINGLE_FILE


def llm_classify_route(ai_service, message, lang="ar"):
    """
    Optional secondary classifier using the live LLM for a genuine intent
    read (as opposed to keyword matching alone), used only to *upgrade* an
    ambiguous heuristic result. Returns one of the ROUTE_* constants, or None
    if the LLM is unavailable / the call fails (callers must fall back to
    classify_route()).
    """
    if not getattr(ai_service, "client", None):
        return None
    try:
        prompt = (
            "You are the intent router for Baseera, a chat assistant used EXCLUSIVELY inside a "
            "business/financial management platform -- every user of this chat is a business owner "
            "or manager, and greetings, small talk, and vaguely-worded requests for help/advice/a "
            "plan are normal here and are NOT off-topic. "
            "Classify the following user message into exactly one label: "
            "OFF_TOPIC (ONLY for messages clearly and entirely about something with no plausible "
            "connection to business/finance/strategy at all, e.g. weather, sports scores, recipes, "
            "general trivia -- when in doubt, do NOT pick this label), "
            "SINGLE_FILE (a simple question answerable from one data file or a direct lookup, "
            "including greetings and vague requests for help), "
            "or MULTI_AGENT (a complex strategic decision needing multiple expert perspectives). "
            "Respond with ONLY the label.\n\nMessage: " + message
        )
        response = ai_service.client.models.generate_content(
            model="gemini-flash-lite-latest", contents=prompt
        )
        label = (response.text or "").strip().upper()
        return {
            "OFF_TOPIC": ROUTE_OFF_TOPIC,
            "SINGLE_FILE": ROUTE_SINGLE_FILE,
            "MULTI_AGENT": ROUTE_MULTI_AGENT,
        }.get(label)
    except Exception as e:
        logger.info("llm_classify_route skipped: %s", e)
        return None


def off_topic_reply(lang="ar"):
    if lang == "ar":
        return "أنا بصيرة، مساعدك المالي. أعتذر، لا يمكنني الإجابة على هذا السؤال لأنه خارج اختصاصي."
    return "I am Baseera, your financial assistant. I'm sorry, I cannot answer this question as it is outside my area of expertise."


def missing_file_reply(lang="ar"):
    if lang == "ar":
        return "لم أتمكن من العثور على المستند المطلوب للإجابة. هل تقصد ملفاً آخر أو ترغب برفع ملف جديد؟"
    return "I could not find the document needed to answer this. Did you mean a different file, or would you like to upload a new one?"


def select_committee_agents(message, max_agents=3):
    """
    Dynamically selects 2-3 specialist agent_ids based on the message
    content (never a fixed/static list — spec section 4.3). Always includes
    'financial' as a baseline domain plus whichever other domains the
    message signals; falls back to a sane default trio if nothing matches.
    """
    text_lower = (message or "").lower()
    matched = []
    for domain, keywords in DOMAIN_AGENT_MAP.items():
        if any(kw in text_lower for kw in keywords):
            matched.append(domain)

    if 'financial' not in matched:
        matched.insert(0, 'financial')

    # De-dup while preserving order, cap to max_agents
    seen = set()
    ordered = []
    for d in matched:
        if d not in seen:
            seen.add(d)
            ordered.append(d)

    if len(ordered) < 2:
        for fallback in ('supply_chain', 'pricing'):
            if fallback not in ordered:
                ordered.append(fallback)
            if len(ordered) >= 2:
                break

    return ordered[:max_agents]


def build_file_confirmation_actions(candidates, lang="ar"):
    """
    Builds the suggested_actions payload (spec section 4.3) for an ambiguous
    or uncertain file match, asking the user to confirm before any
    calculation happens (spec section 5).
    """
    actions = []
    for c in candidates:
        file_label = c["file_name"] or f"file #{c['project_file_id']}"
        sheet_label = c["sheet_name"]
        if lang == "ar":
            label = f"تأكيد استخدام: {file_label} — {sheet_label}"
        else:
            label = f"Use: {file_label} — {sheet_label}"
        actions.append({
            "label": label,
            "action_id": f"confirm_file_{c['project_file_id']}_{sheet_label}",
        })
    actions.append({
        "label": "استخدام ملف آخر" if lang == "ar" else "Use a different file",
        "action_id": "switch_file",
    })
    actions.append({
        "label": "رفع ملف جديد" if lang == "ar" else "Upload a new file",
        "action_id": "upload_new_file",
    })
    return actions


def _latest_active_sheet_metadata(user_id):
    """
    The user's single "active" file context: the most recently uploaded
    file's sheet with the most rows (a reasonable default when a file has
    more than one accepted sheet). Used only as a fallback when the
    Retrieval Layer's keyword search finds literally nothing -- e.g. an
    analytical question about an INFERRED metric like waste, which is
    essentially never a literal column header a sheet gets indexed under.
    Returns None if the account has no indexed sheet metadata at all.
    """
    from dashboard.models import FileSheetMetadata

    # status is 'accept' or 'warning' here -- rejected sheets are never
    # represented in this table at all (see FileSheetMetadata's own
    # docstring), so both statuses present are real, usable sheets.
    return (
        FileSheetMetadata.objects.filter(project_file__user_id=user_id)
        .select_related("project_file")
        .order_by("-project_file__uploaded_at", "-row_count")
        .first()
    )


def route_message(user_id, message, lang="ar", ai_service=None, confirmed_sheet=None):
    """
    Main entry point. Returns a dict:
    {
        "route": ROUTE_GREETING | ROUTE_OFF_TOPIC | ROUTE_SINGLE_FILE | ROUTE_MULTI_AGENT,
        "direct_reply": str | None,       # set for ROUTE_GREETING and ROUTE_OFF_TOPIC
        "agent_ids": [str, ...],
        "needs_confirmation": bool,
        "suggested_actions": [ {label, action_id}, ... ],
        "matched_sheet_note": str,        # human-readable note to inject into file_context
    }

    confirmed_sheet: optional {"project_file_id": int, "sheet_name": str} the
    frontend echoes back after the user clicked a confirm_file_* suggested
    action (spec section 5: "لا تبدأ الحساب مباشرة... أرجع سؤال تأكيد
    صريح"). When present, the Retrieval Layer lookup/ambiguity check is
    skipped entirely and this exact sheet is used.
    """
    from dashboard.services.retrieval_service import search_relevant_sheets
    from dashboard.models import ProjectFile, FileSheetMetadata

    has_files = ProjectFile.objects.filter(user_id=user_id).exists() if user_id else False

    result = {
        "route": ROUTE_SINGLE_FILE,
        "direct_reply": None,
        "agent_ids": ["general"],
        "needs_confirmation": False,
        "suggested_actions": [],
        "matched_sheet_note": "",
    }

    # An explicit user confirmation (they clicked a confirm_file_* suggested
    # action for THIS exact conversation) is authoritative and is honored
    # before any route classification runs at all -- including before the
    # LLM secondary classifier gets a chance to run. That classifier makes a
    # live model call with no guarantee of being deterministic call to call;
    # letting it run first previously meant a user-confirmed file selection
    # could occasionally still get discarded by the classifier mislabeling
    # the very question the user already told us how to answer as
    # off-topic/multi-agent. There is nothing left to classify once the user
    # has told us exactly which file/sheet they mean.
    if user_id and confirmed_sheet and confirmed_sheet.get("project_file_id") and confirmed_sheet.get("sheet_name"):
        meta = FileSheetMetadata.objects.filter(
            project_file_id=confirmed_sheet["project_file_id"],
            project_file__user_id=user_id,
            sheet_name=confirmed_sheet["sheet_name"],
        ).select_related("project_file").first()
        if meta:
            file_name = meta.project_file.excel_file.name.split('/')[-1] if meta.project_file.excel_file else ""
            result["matched_sheet_note"] = (
                f"[Retrieval Layer match — user-confirmed] file={file_name} sheet={meta.sheet_name} "
                f"category={meta.category} columns={meta.columns}"
            )
            return result
        # Confirmed reference no longer resolves (e.g. file deleted) -> fall
        # through to normal classification + a fresh search instead of
        # silently guessing.

    route = classify_route(message, has_uploaded_files=has_files)

    # Give the LLM classifier a chance to upgrade an uncertain heuristic call
    # (never downgrades a confident OFF_TOPIC/MULTI_AGENT heuristic hit).
    #
    # Latency: this is a real extra live API round trip before the actual
    # answer even starts streaming, paid on every single-file message --
    # skipped when the message already has a clear business-domain signal.
    # classify_route()'s own OFF_TOPIC branches both require
    # "not has_business_signal" to fire at all, so when that signal IS
    # present, OFF_TOPIC is structurally unreachable regardless of what a
    # live second opinion would say -- the only thing this check could
    # still catch in that case is an upgrade to MULTI_AGENT, a quality
    # nicety (a strategic question answered by the general single agent
    # instead of the committee), never a hallucination-adjacent safety
    # miss. Genuinely ambiguous messages (no business-domain keyword at
    # all) still get the live second opinion exactly as before.
    if ai_service is not None and route == ROUTE_SINGLE_FILE and not _has_business_domain_signal(message.lower()):
        llm_route = llm_classify_route(ai_service, message, lang=lang)
        if llm_route in (ROUTE_OFF_TOPIC, ROUTE_MULTI_AGENT):
            route = llm_route

    result["route"] = route

    if route == ROUTE_GREETING:
        result["direct_reply"] = greeting_reply(lang)
        return result

    if route == ROUTE_OFF_TOPIC:
        result["direct_reply"] = off_topic_reply(lang)
        return result

    if route == ROUTE_MULTI_AGENT:
        result["agent_ids"] = select_committee_agents(message)
        return result

    # ROUTE_SINGLE_FILE: consult the Retrieval Layer before proceeding.
    if not user_id:
        return result

    candidates = search_relevant_sheets(user_id, message, top_k=5)

    if not candidates:
        # No sheet's indexed keywords/columns/category lexically match this
        # query. That's expected for an ANALYTICAL question about a metric
        # the Retrieval Layer was never going to find as a literal keyword
        # (e.g. "ما سبب ارتفاع الهدر؟" -- "هدر" is inferred by
        # waste_analyzer from price/cost/qty patterns, it is essentially
        # never a real column header a file gets indexed under). Asking
        # "did you mean a different file?" in that case is wrong: there is
        # nothing ambiguous about which file a user with one active
        # dataset is asking about. Fall back to that dataset instead of
        # guessing a DIFFERENT one -- silently picking between multiple
        # real candidates is still never done (that's the is_ambiguous
        # branch below, untouched by this fallback).
        asks_about_inferred_metric = _matches_any(
            [re.escape(t) for t in INFERRED_METRIC_TERMS], message.lower()
        )
        active_meta = (
            _latest_active_sheet_metadata(user_id)
            if (has_files and asks_about_inferred_metric) else None
        )
        if active_meta:
            file_name = (
                active_meta.project_file.excel_file.name.split('/')[-1]
                if active_meta.project_file.excel_file else ""
            )
            result["matched_sheet_note"] = (
                f"[Retrieval Layer match — active file fallback, no literal keyword hit] "
                f"file={file_name} sheet={active_meta.sheet_name} category={active_meta.category} "
                f"columns={active_meta.columns}"
            )
            return result

        # Genuinely nothing to fall back to (zero files, or indexing never
        # produced any sheet metadata for this account): ask instead of
        # guessing, same as before.
        result["needs_confirmation"] = True
        result["direct_reply"] = missing_file_reply(lang)
        actions = []
        if has_files:
            actions.append({"label": "استخدام ملف آخر" if lang == "ar" else "Use a different file", "action_id": "switch_file"})
        actions.append({"label": "رفع ملف جديد" if lang == "ar" else "Upload a new file", "action_id": "upload_new_file"})
        result["suggested_actions"] = actions
        return result

    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None

    # Ambiguous when the top-2 scores are close (within 15%) — e.g. the
    # "chicken vs meat" case where a related-but-different file could be
    # mistaken for the right one. Never silently pick in that case.
    is_ambiguous = bool(
        runner_up and top["score"] > 0 and
        (top["score"] - runner_up["score"]) / top["score"] < 0.15
    )

    if is_ambiguous:
        result["needs_confirmation"] = True
        result["suggested_actions"] = build_file_confirmation_actions(candidates[:3], lang=lang)
        return result

    meta = top["sheet_metadata"]
    result["matched_sheet_note"] = (
        f"[Retrieval Layer match] file={top['file_name']} sheet={top['sheet_name']} "
        f"category={meta.category} columns={meta.columns}"
    )
    return result


# --------------------------------------------------------------------------
# Hard constraint gate (spec section 4.1, rules 5 & 6)
# --------------------------------------------------------------------------

def filter_options_by_hard_constraints(options, profile):
    """
    options: list of dicts, each with at minimum:
        {"label": str, "required_investment": Decimal|float|None,
         "cash_after": Decimal|float|None, ...}
    profile: a CompanyStrategicProfile instance (or None).

    Returns (kept, rejected) where `kept` preserves the original relative
    order (best-return-first ordering from the caller is untouched) and
    `rejected` lists the same option dicts annotated with a "rejection_reason".

    Any option is rejected — regardless of its return — if it:
      * requires more than profile.max_investment_limit, or
      * would leave cash below profile.cash_reserve_floor.
    Both checks are skipped (constraint absent) when the corresponding
    profile field is None, matching the model's own null=True semantics.
    """
    if not profile:
        return list(options), []

    kept, rejected = [], []
    for opt in options:
        reason = None
        required = opt.get("required_investment")
        cash_after = opt.get("cash_after")

        if profile.max_investment_limit is not None and required is not None:
            if required > profile.max_investment_limit:
                reason = "exceeds max_investment_limit"

        if reason is None and profile.cash_reserve_floor is not None and cash_after is not None:
            if cash_after < profile.cash_reserve_floor:
                reason = "would breach cash_reserve_floor"

        if reason:
            rejected_opt = dict(opt)
            rejected_opt["rejection_reason"] = reason
            rejected.append(rejected_opt)
        else:
            kept.append(opt)

    return kept, rejected
