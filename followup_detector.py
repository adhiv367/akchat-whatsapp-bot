"""followup_detector.py

Pure helpers that spot a customer saying they will buy / get back LATER
("I'll buy it next Friday", "naalaikku vaangaren", "will let you know tomorrow").
No database, no network, no imports from the AI bridge, so it is safe to unit test.

detect_future_intent(message, today=None) -> dict or None
    {"kind": "buy" | "contact", "followup_text": "next Friday", "due_date": date | None}

Date rules (kept simple and predictable):
  * plain weekday ("Friday")          -> the next such day after today
  * "next Friday" / "Friday next week" -> that weekday in the following Mon-Sun week
  * "next week"                       -> next Monday;   "this weekend" -> coming Saturday
  * "later", "after salary", ...      -> due_date None (the phrase is still stored)
Questions, negations and delivery talk never count. English and Tanglish only for now.
"""
import re
from datetime import date, timedelta

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_NUMWORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
             "five": 5, "six": 6, "seven": 7}

_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_MODAL = r"(?:'?ll|will|shall|am going to|'m going to|gonna|plan to|planning to|want to|wanna|would like to|might|may|can|could)"
_BUY_VERBS = r"(?:buy|purchase|order|take|book|get\s+(?:it|this|that|one|them|the))"

_EN_BUY = re.compile(
    r"\b(?:i|we)\s*" + _MODAL + r"\s+(?:(?:definitely|surely|probably|also|then|just|maybe)\s+)?" + _BUY_VERBS + r"\b"
    r"|\b(?:i\s+am|i'm|we\s+are|we're)\s+(?:(?:planning|going|thinking)\s+(?:to|of)\s+)?(?:buy|purchase|order|take|buying|purchasing|ordering|taking)\b"
    r"|\bwill\s+(?:(?:definitely|surely|probably|also)\s+)?" + _BUY_VERBS + r"\b"
    r"|\b(?:planning|plan|going)\s+to\s+" + _BUY_VERBS + r"\b",
    re.I)
_TA_BUY = re.compile(
    r"\b(?:vaa?ng(?:aren|uren|ren|uven|en|alam|anum|idalam|idren|iduven|uvom)"
    r"|order\s+pann(?:uren|aren|ren|uven|alam|uvom)"
    r"|edu(?:kkaren|kkiren|ppen|ppom|kalam)"
    r"|book\s+pann(?:uren|aren|ren|uven))\b",
    re.I)
_CONTACT = re.compile(
    r"\b(?:i|we)\s*" + _MODAL + r"\s+(?:(?:definitely|surely|also|then|just)\s+)?"
    r"(?:let\s+(?:you|u)\s+know|inform\s+you|message\s+you|msg\s+you|text\s+you|call\s+you|contact\s+you"
    r"|come|visit|check|decide|confirm|revert|get\s+back|discuss|ask)\b",
    re.I)
_ANY_BUY = re.compile(r"\b(?:buy|buying|purchase|purchasing|order|vaa?ng)", re.I)

_NEG = re.compile(
    r"\b(?:won't|wont|will\s+not|not\s+going\s+to|never|don't|dont|do\s+not|can't|cant|cannot|no\s+need\s+to|not\s+planning\s+to)\b"
    r"[^.?!]{0,25}\b(?:buy|purchase|order|take|book)\b", re.I)
_QUESTION_START = re.compile(
    r"^(?:can|could|do|does|did|is|are|was|when|what|how|where|why|which|who|should|shall|would)\b"
    r"|^will\s+(?:it|this|that|the|you|they|there|my|we|i|u)\b|^may\s+i\b", re.I)
_DELIVERY = re.compile(r"\b(?:deliver\w*|dispatch\w*|courier|parcel|arriv\w*|reach\w*|shipping|shipped)\b", re.I)

_WEEKDAY_RE = re.compile(
    r"\b(?:(next|this|coming|on)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b(?:\s+(next\s+week))?", re.I)
_DM_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?" + _MON + r"\b", re.I)
_MD_RE = re.compile(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_DOM_RE = re.compile(r"\bon\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", re.I)
_VAGUE = re.compile(
    r"\b(?:after\s+some\s+time|later\s+on|later|in\s+a\s+few\s+days|few\s+days|some\s+other\s+time|another\s+time"
    r"|sometime|some\s+time|one\s+of\s+these\s+days|after\s+(?:my\s+)?salary|after\s+payday|payday"
    r"|salary\s+(?:vanthathum|vandhathum)|after\s+(?:diwali|deepavali|pongal|festival|the\s+festival|onam|eid|christmas)"
    r"|apram|aprom|appuram|apuram|pinnadi|pinnaadi|konjam\s+naal\s+(?:kalichu|kazhichu)|innum\s+konjam\s+naal)\b",
    re.I)


def _norm(message):
    t = (message or "").replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", t).strip()


def _monday_next_week(today):
    return today + timedelta(days=7 - today.weekday())


def _first_of_next_month(today):
    return date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)


def _month_end(y, m):
    first_next = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first_next - timedelta(days=1)


def _resolve_day_month(day, mon, today):
    try:
        d = date(today.year, mon, day)
        if d < today:
            d = date(today.year + 1, mon, day)
        return d
    except ValueError:
        return None


def _next_day_of_month(day, today):
    y, m = today.year, today.month
    if day <= today.day:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    try:
        return date(y, m, day)
    except ValueError:
        return None


def _find_time(text, today):
    """Returns (phrase, due_date_or_None) for the first time expression, else None."""
    m = re.search(r"\bday\s+after\s+(?:tomorrow|tmrw|tomorow)\b", text, re.I)
    if m:
        return m.group(0), today + timedelta(days=2)
    m = re.search(r"\b(?:tomorrow|tomorow|tommorow|tmrw|naa?lai(?:kk?u)?)\b", text, re.I)
    if m:
        return m.group(0), today + timedelta(days=1)
    m = re.search(r"\b(?:later\s+today|tonight|this\s+evening|this\s+afternoon|today\s+evening|today\s+night)\b", text, re.I)
    if m:
        return m.group(0), today
    m = re.search(r"\bnext\s+weekend\b", text, re.I)
    if m:
        return m.group(0), _monday_next_week(today) + timedelta(days=5)
    m = re.search(r"\b(?:this\s+|coming\s+)?weekend\b", text, re.I)
    if m:
        return m.group(0), today + timedelta(days=(5 - today.weekday()) % 7)
    m = _WEEKDAY_RE.search(text)
    if m:
        qual = (m.group(1) or "").lower()
        wd = _WEEKDAYS[m.group(2).lower()]
        if qual == "next" or m.group(3):
            due = _monday_next_week(today) + timedelta(days=wd)
        else:
            due = today + timedelta(days=((wd - today.weekday()) % 7) or 7)
        return m.group(0).strip(), due
    m = re.search(r"\b(?:in|after)\s+(\d{1,2}|a|an|one|two|three|four|five|six|seven)\s+(day|days|week|weeks)\b", text, re.I)
    if m:
        tok = m.group(1)
        n = int(tok) if tok.isdigit() else _NUMWORDS[tok.lower()]
        days = n * (7 if m.group(2).lower().startswith("week") else 1)
        return m.group(0), today + timedelta(days=days)
    m = re.search(r"\b(?:(?:next|nxt)\s+week|adutha\s+vaa?ram)\b", text, re.I)
    if m:
        return m.group(0), _monday_next_week(today)
    m = re.search(r"\b(?:(?:next|nxt)\s+month|adutha\s+maa?sam)\b", text, re.I)
    if m:
        return m.group(0), _first_of_next_month(today)
    m = re.search(r"\b(?:end\s+of\s+(?:the\s+|this\s+)?month|month[- ]?end)\b", text, re.I)
    if m:
        due = _month_end(today.year, today.month)
        if due <= today:
            nm = _first_of_next_month(today)
            due = _month_end(nm.year, nm.month)
        return m.group(0), due
    m = _DM_RE.search(text)
    if m:
        return m.group(0), _resolve_day_month(int(m.group(1)), _MONTHS[m.group(2).lower()[:3]], today)
    m = _MD_RE.search(text)
    if m:
        return m.group(0), _resolve_day_month(int(m.group(2)), _MONTHS[m.group(1).lower()[:3]], today)
    m = _DOM_RE.search(text)
    if m:
        return m.group(0), _next_day_of_month(int(m.group(1)), today)
    m = _VAGUE.search(text)
    if m:
        return m.group(0), None
    return None


def detect_future_intent(message, today=None):
    """Returns {"kind", "followup_text", "due_date"} or None. Never raises."""
    try:
        text = _norm(message)
        if not text or len(text) > 400:
            return None
        if _QUESTION_START.search(text) or _NEG.search(text) or _DELIVERY.search(text):
            return None
        buy = _EN_BUY.search(text) or _TA_BUY.search(text)
        contact = _CONTACT.search(text)
        if not (buy or contact):
            return None
        found = _find_time(text, today or date.today())
        if not found:
            return None
        phrase, due = found
        kind = "buy" if (buy or _ANY_BUY.search(text)) else "contact"
        return {"kind": kind, "followup_text": phrase, "due_date": due}
    except Exception:
        return None