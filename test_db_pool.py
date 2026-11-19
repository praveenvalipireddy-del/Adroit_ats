"""Postgres connections are reused (models._pg_take / _pg_give) instead of opened for every lookup.

Opening a connection costs ~1 s when the database is far from the server (AWS Sydney -> Supabase Oregon);
one extension click opened 5-6. Checks (with a stand-in for psycopg2 - no database needed):
1. Ten lookups in a row open ONE connection.
2. A connection handed back mid-transaction is rolled back first (nothing uncommitted carries over).
3. A broken / closed connection is never handed out again; one idle too long is checked first and replaced
   when the check fails.
4. The pool keeps at most PG_POOL_MAX idle connections; a closed wrapper can't be used again.
5. Threads at the same time each get their own connection.
Run: python test_db_pool.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import models  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extensions as ext  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


opened = []


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description = conn, [("x",)]

    def execute(self, sql, params=None):
        if self.conn.broken:
            raise psycopg2.OperationalError("server closed the connection")
        self.conn.status = ext.TRANSACTION_STATUS_INTRANS
        self.conn.sql.append(sql)

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(1,)]

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.closed, self.broken, self.status, self.sql, self.rollbacks = 0, False, ext.TRANSACTION_STATUS_IDLE, [], 0
        opened.append(self)

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.status = ext.TRANSACTION_STATUS_IDLE

    def rollback(self):
        self.rollbacks += 1
        self.status = ext.TRANSACTION_STATUS_IDLE

    def get_transaction_status(self):
        return self.status

    def close(self):
        self.closed = 1


psycopg2.connect = lambda *a, **k: FakeConn()
config.DATABASE_URL = "postgresql://pool-test.invalid/db"

# 1. reuse
for _ in range(10):
    c = models.get_db_connection()
    c.cursor().execute("SELECT 1")
    c.close()
check(len(opened) == 1, f"ten lookups opened {len(opened)} connections (should be 1)")

# 2. rolled back when handed back mid-transaction
c = models.get_db_connection()
raw = c._conn
c.cursor().execute("UPDATE users SET name = 'x' WHERE id = 1")      # never committed
before = raw.rollbacks
c.close()
check(raw.rollbacks == before + 1 and raw.status == ext.TRANSACTION_STATUS_IDLE, "an uncommitted change is rolled back on close")
c.close()                                                            # closing twice is harmless
check(len(models._PG_IDLE) == 1, f"a double close doesn't put it in the pool twice: {len(models._PG_IDLE)}")
try:
    c.cursor()
    check(False, "a closed wrapper can't be used")
except psycopg2.InterfaceError:
    pass

# 3. broken connections are dropped
models._PG_IDLE[0][0].closed = 1
c = models.get_db_connection()
check(len(opened) == 2 and c._conn is opened[-1], "a closed pooled connection is replaced with a new one")
c.close()
idle_conn = models._PG_IDLE[0][0]
models._PG_IDLE[0] = (idle_conn, models._pool_time.monotonic() - models.PG_PING_AFTER_SECONDS - 5)
idle_conn.broken = True                                              # the server dropped it while idle
c = models.get_db_connection()
check(idle_conn.closed and c._conn is not idle_conn and len(opened) == 3, "an idle connection that fails its check is replaced")
c.close()
fresh = models._PG_IDLE[0][0]
models._PG_IDLE[0] = (fresh, models._pool_time.monotonic() - models.PG_PING_AFTER_SECONDS - 5)
c = models.get_db_connection()
check(c._conn is fresh and "SELECT 1" in fresh.sql and len(opened) == 3, "an idle connection that passes its check is reused")
c.close()

# 4. at most PG_POOL_MAX idle
conns = [models.get_db_connection() for _ in range(models.PG_POOL_MAX + 3)]
for x in conns:
    x.close()
check(len(models._PG_IDLE) == models.PG_POOL_MAX and sum(1 for o in opened if o.closed) >= 3,
      f"pool capped at {models.PG_POOL_MAX}: {len(models._PG_IDLE)} idle")

# 5. threads get their own connections
seen, lock = [], threading.Lock()


def worker():
    x = models.get_db_connection()
    with lock:
        seen.append(id(x._conn))
    threading.Event().wait(0.05)
    x.close()


ts = [threading.Thread(target=worker) for _ in range(6)]
[t.start() for t in ts]
[t.join() for t in ts]
check(len(set(seen)) == 6, f"six threads at once got six different connections: {len(set(seen))}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Postgres connections reused - one connection for ten lookups, rolled back on hand-back, broken/idle ones "
      "checked and replaced, pool capped, one connection per thread")
