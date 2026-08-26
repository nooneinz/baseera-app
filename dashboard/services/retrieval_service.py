"""
Retrieval Layer (RAG) — section 5 of the platform architecture spec.

Responsibilities implemented in this module:
1. Extract & persist per-sheet metadata for every ACCEPTED sheet of an uploaded
   file (columns, date range, a coarse category classification, and a keyword
   index) — never for rejected sheets.
2. Provide a hybrid (semantic-ish keyword overlap + exact/BM25-style lexical
   match) search over that metadata so a user question can be matched to the
   right file/sheet instead of guessing.

No external vector DB / embeddings service is required: the "semantic" leg is
a lightweight token-overlap + substring score, and the "lexical" leg is exact
keyword containment, weighted so a literal term (e.g. "دجاج") is never
silently replaced by a merely-similar term (e.g. "لحوم").
"""
import os
import re
import logging

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    'sales': ['مبيعات', 'إيراد', 'ايراد', 'sale', 'sales', 'revenue'],
    'expenses': ['مصروف', 'مصاريف', 'expense', 'expenses', 'تكلفة', 'cost'],
    'invoices': ['فاتورة', 'فواتير', 'invoice', 'invoices'],
    'inventory': ['مخزون', 'كمية', 'stock', 'inventory', 'qty', 'quantity'],
    'bank': ['بنك', 'كشف حساب', 'bank', 'statement'],
}

_TOKEN_RE = re.compile(r'[\w؀-ۿ]+', re.UNICODE)


def _tokenize(text):
    return {t for t in _TOKEN_RE.findall(str(text).lower()) if len(t) > 1}


def classify_sheet(sheet_name, columns):
    """Coarse category classification based on sheet name + column headers."""
    haystack = (str(sheet_name) + ' ' + ' '.join(str(c) for c in columns)).lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return category
    return 'other'


def extract_keywords(sheet_name, columns, file_name=""):
    tokens = _tokenize(sheet_name) | _tokenize(file_name)
    for col in columns:
        tokens |= _tokenize(col)
    return sorted(tokens)


def _detect_date_range(df):
    """Best-effort detection of a date column and its min/max range."""
    import pandas as pd
    best_col = None
    best_score = 0
    for col in df.columns:
        series = df[col]
        if str(series.dtype).startswith('datetime'):
            parsed = series
        else:
            parsed = pd.to_datetime(series, errors='coerce')
        score = parsed.notna().sum()
        if score > best_score and score > 0:
            best_score = score
            best_col = parsed
    if best_col is None or best_score < max(1, len(df) * 0.5):
        return None, None
    valid = best_col.dropna()
    if valid.empty:
        return None, None
    return valid.min().date(), valid.max().date()


def index_accepted_sheets(project_file, accepted_sheets):
    """
    Re-reads ONLY the accepted sheets of an already-saved ProjectFile and
    stores FileSheetMetadata rows for the Retrieval Layer. Rejected sheets are
    never opened here, so they can never be indexed or surfaced to search.
    """
    from dashboard.models import FileSheetMetadata
    import pandas as pd

    if not accepted_sheets:
        return []

    file_path = project_file.excel_file.path
    if not os.path.exists(file_path):
        return []

    ext = os.path.splitext(file_path)[1].lower()
    file_name = os.path.basename(file_path)
    created = []

    # Wipe any previous index rows for this file (re-upload / re-validation case)
    FileSheetMetadata.objects.filter(project_file=project_file).delete()

    try:
        if ext == '.pdf':
            for sheet_name in accepted_sheets:
                meta = FileSheetMetadata.objects.create(
                    project_file=project_file,
                    sheet_name=sheet_name,
                    status='accept',
                    columns=[],
                    row_count=0,
                    category=classify_sheet(sheet_name, [file_name]),
                    keywords=extract_keywords(sheet_name, [], file_name=file_name),
                )
                created.append(meta)
            return created

        if ext == '.csv':
            sheets = {'csv_file': pd.read_csv(file_path)}
        else:
            xls = pd.ExcelFile(file_path)
            sheets = {name: xls.parse(name) for name in accepted_sheets if name in xls.sheet_names}

        for sheet_name in accepted_sheets:
            df = sheets.get(sheet_name)
            if df is None:
                continue
            columns = [str(c) for c in df.columns]
            date_start, date_end = _detect_date_range(df)
            meta = FileSheetMetadata.objects.create(
                project_file=project_file,
                sheet_name=sheet_name,
                status='accept',
                columns=columns,
                row_count=len(df),
                date_range_start=date_start,
                date_range_end=date_end,
                category=classify_sheet(sheet_name, columns),
                keywords=extract_keywords(sheet_name, columns, file_name=file_name),
            )
            created.append(meta)
    except Exception:
        logger.exception("Retrieval indexing failed for project_file=%s", project_file.id)

    return created


def search_relevant_sheets(user_id, query, top_k=5):
    """
    Hybrid search over the current user's indexed FileSheetMetadata:
    - Lexical leg: exact keyword/substring containment (heavily weighted, so a
      literal term like "دجاج" wins over a merely related term like "لحوم").
    - Semantic-ish leg: token (Jaccard) overlap between the query and the
      sheet's keyword/category index, as a stand-in for embedding similarity.

    Returns a list of dicts sorted by descending score:
      {sheet_metadata, score, lexical_hit, file_name, sheet_name}
    """
    from dashboard.models import FileSheetMetadata

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    candidates = FileSheetMetadata.objects.filter(
        project_file__user_id=user_id
    ).select_related('project_file')

    scored = []
    for meta in candidates:
        sheet_tokens = set(meta.keywords or [])
        sheet_tokens |= _tokenize(meta.sheet_name)
        sheet_tokens |= _tokenize(meta.get_category_display())
        file_name = os.path.basename(meta.project_file.excel_file.name) if meta.project_file.excel_file else ""
        sheet_tokens |= _tokenize(file_name)

        if not sheet_tokens:
            continue

        overlap = query_tokens & sheet_tokens
        # Semantic-ish score: Jaccard overlap
        union = query_tokens | sheet_tokens
        semantic_score = len(overlap) / len(union) if union else 0.0

        # Lexical leg: exact literal substring match of any query token inside
        # the sheet's own tokens (not just a related/similar word) — this is
        # what keeps "دجاج" from being silently swapped for "لحوم".
        lexical_hit = len(overlap) > 0
        lexical_score = len(overlap) / len(query_tokens) if query_tokens else 0.0

        combined_score = (0.4 * semantic_score) + (0.6 * lexical_score)

        if combined_score > 0:
            scored.append({
                "sheet_metadata": meta,
                "score": combined_score,
                "lexical_hit": lexical_hit,
                "file_name": file_name,
                "sheet_name": meta.sheet_name,
                "project_file_id": meta.project_file_id,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
