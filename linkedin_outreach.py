"""LinkedIn message to a sourced candidate: the AI draft (Sourcing tracker card > Message on LinkedIn).

The recruiter sends the message from their own LinkedIn; the Chrome extension only types it into LinkedIn's box.
Two kinds, because LinkedIn decides what can be sent:
- "note": a connection-request note (candidate not connected) - LinkedIn allows NOTE_LIMIT characters.
- "message": a normal message (1st-degree connection) or an InMail (Premium / Recruiter).
The draft uses only what Job Hunt knows about the candidate (name, title, company, skills on the card) and the
role the recruiter typed - nothing invented (no rates, no years, no company names that weren't given).
"""
import json
import re
from typing import Dict

import resume_bot

NOTE_LIMIT = 300
MESSAGE_LIMIT = 1200

SYSTEM = """You write a short first LinkedIn message from a recruiter to a candidate they found.
Use ONLY the facts given (candidate name, title, company, skills, the role, the recruiter's name and company).
Never invent a salary, rate, years of experience, location, client name or anything else not given.
Friendly, specific, no buzzwords, no "I hope this finds you well", no emoji, no hashtags, no links.
Address the candidate by first name. Mention why they fit (their title or a skill that matches the role) and
ask for a quick chat. Sign off with the recruiter's first name (and company if given).
Return JSON: {"message": "..."}"""


def first_name(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[0].strip(",.") if parts else ""


def limit_for(mode: str) -> int:
    return NOTE_LIMIT if mode == "note" else MESSAGE_LIMIT


def trim(text: str, limit: int) -> str:
    """Fit LinkedIn's limit: cut at the last full sentence (else the last word) that fits."""
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if end > 0:   # a shorter message that ends cleanly beats one cut mid-sentence
        return cut[:end + 1].strip()
    return cut[:cut.rfind(" ")].rstrip(" ,;:-—") if " " in cut else cut


def facts(card: Dict, role: str, recruiter: str, company: str) -> Dict:
    f = card.get("fields") or {}
    return {"candidate_name": card.get("name") or "", "candidate_first_name": first_name(card.get("name") or ""),
            "candidate_title": card.get("headline") or "", "candidate_company": card.get("company") or "",
            "candidate_skills": f.get("primary_skills") or "", "role": role, "recruiter_name": recruiter,
            "recruiter_company": company}


def ai_draft(card: Dict, role: str, mode: str, recruiter: str, company: str = "") -> str:
    """The AI's message for this candidate, already fitted to LinkedIn's limit. Raises on AI failure."""
    limit = limit_for(mode)
    kind = (f"a connection-request note - at most {NOTE_LIMIT} characters including spaces, 2-3 sentences"
            if mode == "note" else "a LinkedIn message - 3-5 short sentences, under 700 characters")
    prompt = f"Write {kind}.\nFACTS:\n{json.dumps(facts(card, role, recruiter, company), ensure_ascii=False, indent=1)}"
    raw, _model = resume_bot.generate_chat(SYSTEM, prompt, max_tokens=600)
    try:
        text = (json.loads(raw) or {}).get("message") or ""
    except (TypeError, ValueError):
        m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', raw or "")
        text = json.loads('"' + m.group(1) + '"') if m else ""
    text = trim(text, limit)
    if not text:
        raise ValueError("The AI returned no message.")
    return text
