"""Memory of the emails a recruiter approved, per consultant - so the AI learns their style.

Table email_examples (models.init_db): one row per application email.
- remember_ai(): when the AI writes an email (Optimize / Paste window), its version is kept (final_body empty).
- remember_final(): when the recruiter clicks Save & Create Draft, the email they actually saved is stored on that
  row (or a new row when the AI didn't write it).
- prompt_block(): the consultant's last few approved emails, for the writers (application_brief, the email chat):
  where the recruiter changed the AI's version, both are shown, so their changes ("always removes X", "adds the
  notice period") are repeated next time. Job-specific facts from an example are never copied.
Never raises into a caller: a memory problem must not stop a draft.
"""
import logging
from typing import Dict, List, Optional

import models

logger = logging.getLogger("email_memory")

EXAMPLES = 3
EXAMPLE_CHARS = 1500
KEEP_UNSENT = 20          # AI versions never saved as a draft: only the newest few per consultant are kept


def remember_ai(candidate_id: int, job_id: Optional[int], user_id: Optional[int], job_title: str, ai_body: str) -> None:
    if not candidate_id or not (ai_body or "").strip():
        return
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO email_examples (candidate_id, user_id, job_id, job_title, ai_body) VALUES (?, ?, ?, ?, ?)",
                    (candidate_id, user_id, job_id, (job_title or "")[:200], ai_body.strip()[:8000]))
        cur.execute("""DELETE FROM email_examples WHERE candidate_id = ? AND final_body IS NULL AND id NOT IN
                       (SELECT id FROM email_examples WHERE candidate_id = ? AND final_body IS NULL ORDER BY id DESC LIMIT ?)""",
                    (candidate_id, candidate_id, KEEP_UNSENT))
        conn.commit()
    except Exception as ex:
        logger.warning(f"Could not keep the AI email for consultant {candidate_id}: {ex}")
    finally:
        conn.close()


def remember_final(candidate_id: int, job_id: Optional[int], user_id: Optional[int], job_title: str, final_body: str,
                   pasted: bool = False) -> None:
    """The email the recruiter saved as a Gmail draft. `pasted`: the Paste window, where the job didn't exist yet
    while the AI wrote (its AI version was kept without a job)."""
    if not candidate_id or not (final_body or "").strip():
        return
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        if pasted:
            cur.execute("SELECT id FROM email_examples WHERE candidate_id = ? AND final_body IS NULL AND job_id IS NULL ORDER BY id DESC LIMIT 1",
                        (candidate_id,))
        else:
            cur.execute("SELECT id FROM email_examples WHERE candidate_id = ? AND final_body IS NULL AND job_id = ? ORDER BY id DESC LIMIT 1",
                        (candidate_id, job_id))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE email_examples SET final_body = ?, drafted_at = CURRENT_TIMESTAMP, job_id = ?, job_title = ?, user_id = ? WHERE id = ?",
                        (final_body.strip()[:8000], job_id, (job_title or "")[:200], user_id, row[0]))
        else:
            cur.execute("""INSERT INTO email_examples (candidate_id, user_id, job_id, job_title, final_body, drafted_at)
                           VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                        (candidate_id, user_id, job_id, (job_title or "")[:200], final_body.strip()[:8000]))
        conn.commit()
    except Exception as ex:
        logger.warning(f"Could not remember the approved email for consultant {candidate_id}: {ex}")
    finally:
        conn.close()


def examples(candidate_id: int, limit: int = EXAMPLES) -> List[Dict]:
    if not candidate_id:
        return []
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT job_title, ai_body, final_body FROM email_examples WHERE candidate_id = ? AND final_body IS NOT NULL
                       ORDER BY id DESC LIMIT ?""", (candidate_id, limit))
        return [{"job_title": r[0] or "", "ai_body": r[1] or "", "final_body": r[2] or ""} for r in cur.fetchall()]
    except Exception as ex:
        logger.warning(f"Could not read approved emails for consultant {candidate_id}: {ex}")
        return []
    finally:
        conn.close()


def prompt_block(candidate_id: int) -> str:
    ex = examples(candidate_id)
    if not ex:
        return ""
    parts = []
    for i, e in enumerate(ex, 1):
        changed = e["ai_body"].strip() and e["ai_body"].strip() != e["final_body"].strip()
        parts.append(f"--- Example {i} (job: {e['job_title'] or 'unknown'})\n"
                     + (f"AI DRAFT:\n{e['ai_body'][:EXAMPLE_CHARS]}\nWHAT THE RECRUITER SENT INSTEAD:\n" if changed else "SENT:\n")
                     + e["final_body"][:EXAMPLE_CHARS])
    return ("EMAILS THE RECRUITER APPROVED FOR THIS CONSULTANT (most recent first). Learn their style - tone, length, structure, "
            "how they open and close, what they add or remove. Where an AI DRAFT is shown, the recruiter changed it into what they "
            "sent: make the same kind of changes now. Never copy job-specific facts (company, job title, that job's skills) from "
            "these examples.\n" + "\n".join(parts) + "\n\n")
