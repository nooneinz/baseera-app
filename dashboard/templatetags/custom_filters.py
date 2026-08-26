import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from dashboard.utils import translate_digest_item

register = template.Library()

@register.filter(name='translate_digest')
def translate_digest(value, lang_code='ar'):
    if lang_code == 'en':
        if isinstance(value, list):
            return [translate_digest_item(item) for item in value]
        return translate_digest_item(value)
    return value


# Matches a real figure -- comma-grouped thousands ("4,429.02"), a decimal
# ("18.5"), or a plain 2+ digit whole number ("50") -- with an optional
# trailing "%". Deliberately does NOT match a single bare digit, so a
# numbered list item like "1. الخطوة الأولى" keeps its list marker
# unmasked instead of turning "1." into a dot.
_SENSITIVE_NUMBER_RE = re.compile(r'(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{2,})%?')


@register.filter(name='mask_sensitive_numbers')
def mask_sensitive_numbers(value):
    """
    Wraps real numeric figures (revenue, percentages, counts) inside
    AI-generated narrative text -- the Weekly Digest summary, risks, and
    action plan -- in a `sensitive-value` span, so the dashboard's
    privacy/hide toggle actually covers these numbers too, not just the
    KPI cards. Masking the surrounding prose itself isn't done here: a
    whole sentence rendered as dots is unreadable and was exactly what
    caused cards to overflow/overlap when the hide toggle was on -- only
    the number substrings get the mask.

    Returns marked-safe HTML: everything outside the matched numbers is
    escaped here, since Django's own auto-escaping does not apply once a
    filter's result is marked safe.
    """
    if not value:
        return value
    text = str(value)
    chunks = []
    last_end = 0
    for m in _SENSITIVE_NUMBER_RE.finditer(text):
        token = m.group(0)
        chunks.append(escape(text[last_end:m.start()]))
        chunks.append(f'<span class="sensitive-value">{escape(token)}</span>')
        last_end = m.end()
    chunks.append(escape(text[last_end:]))
    return mark_safe(''.join(chunks))
