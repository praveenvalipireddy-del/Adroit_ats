"""Regression test: a consultant deleted from the bench must NOT come back after an app restart.

Root cause this guards against: init_db() used to call ensure_default_consultants() on every
startup (every Render deploy), which re-inserted a hardcoded list of consultants - including
"Valipireddy Praveen" - whenever their row was missing, undoing the recruiter's delete.

Standalone script: runs against a throwaway SQLite file, never the real DB.
Run: python test_deleted_consultant_stays_deleted.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_ats.db")
os.environ["DATABASE_URL"] = ""  # force SQLite even if a Postgres URL is set in .env

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import models  # noqa: E402


def candidate_ids_named(name):
    conn = models.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM candidates WHERE name = ?", (name,))
    ids = [r[0] for r in cur.fetchall()]
    conn.close()
    return ids


# First startup on an empty DB.
models.init_db()

# Put the previously-hardcoded names on the bench (as a recruiter would have them), then delete
# them all, like a recruiter cleaning up the bench.
for n in ("Valipireddy Praveen", "Sai Teja", "Karun", "Thirupathi", "Vikas Reddy"):
    if not candidate_ids_named(n):
        models.create_candidate(name=n, email=n.lower().replace(" ", ".") + "@example.invalid")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT id, name FROM candidates")
seeded = [(r[0], r[1]) for r in cur.fetchall()]
conn.close()
for cand_id, _ in seeded:
    models.delete_candidate(cand_id, is_admin=True)
assert all(not candidate_ids_named(n) for _, n in seeded), "delete_candidate did not delete"

# Simulate restarts / redeploys.
models.init_db()
models.init_db()

resurrected = [n for _, n in seeded if candidate_ids_named(n)]
assert not resurrected, f"deleted consultants came back after restart: {resurrected}"
assert not candidate_ids_named("Valipireddy Praveen"), "Valipireddy Praveen came back after restart"

# A consultant added by a recruiter must still survive a restart (the fix must not wipe data).
models.create_candidate(name="Restart Survivor Check", email="restart.check@example.invalid")
models.init_db()
assert candidate_ids_named("Restart Survivor Check"), "a real consultant was lost on restart"

# No Gmail App Password may be hardcoded in the source (one was, and it is public in git history).
import re  # noqa: E402
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.py"), encoding="utf-8") as f:
    assert not re.search(r'"gmail_app_password"\s*:\s*"[a-z]{16}"', f.read()), "hardcoded Gmail App Password in models.py"

print(f"PASS: {len(seeded)} deleted consultant(s) stayed deleted across 3 restarts; added consultant kept.")
