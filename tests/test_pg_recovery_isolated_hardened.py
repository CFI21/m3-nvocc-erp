"""M3 recovery transaction MODEL tests. Disposable localhost PostgreSQL only.
No M3 application login, secrets, Neon, providers, payments, or production.
"""
import hashlib
import hmac
import os
import secrets
import threading
import unittest
from urllib.parse import urlsplit


def safe_dsn():
    raw = os.environ.get("M3_DISPOSABLE_PG_DSN", "")
    try:
        u = urlsplit(raw)
        safe = (u.scheme == "postgresql" and u.hostname == "127.0.0.1"
                and u.port == 5432 and u.path == "/m3_disposable_test"
                and u.username == "m3_disposable_runner" and bool(u.password)
                and not u.query and not u.fragment)
    except (ValueError, TypeError):
        safe = False
    if not safe:
        raise RuntimeError("REFUSE_NONLOCAL_DATABASE_TARGET")
    return raw


def connect():
    import psycopg
    return psycopg.connect(safe_dsn(), connect_timeout=5)


def start(c, attempt, user, capability):
    with c.transaction():
        c.execute("INSERT INTO attempts(id,user_id,cap_hash,state) VALUES (%s,%s,%s,'PENDING')",
                  (attempt, user, hashlib.sha256(capability.encode()).digest()))


def login(c, attempt, user, fail=False):
    with c.transaction():
        row = c.execute("SELECT user_id,state FROM attempts WHERE id=%s FOR UPDATE",
                        (attempt,)).fetchone()
        if row != (user, "PENDING"):
            return "NOT_PENDING"
        c.execute("INSERT INTO sessions(attempt_id,user_id,status) VALUES (%s,%s,'ACTIVE')",
                  (attempt, user))
        if fail:
            raise RuntimeError("LOGIN_FAILURE_INJECTED")
        c.execute("INSERT INTO audit(attempt_id,event) VALUES (%s,'LOGIN')", (attempt,))
        c.execute("UPDATE attempts SET state='ISSUED' WHERE id=%s", (attempt,))
        return "ISSUED"


def recover(c, attempt, user, capability, fail=False):
    cap_hash = hashlib.sha256(capability.encode()).digest()
    with c.transaction():
        row = c.execute("SELECT user_id,cap_hash,state FROM attempts WHERE id=%s FOR UPDATE",
                        (attempt,)).fetchone()
        if not row or row[0] != user or not hmac.compare_digest(bytes(row[1]), cap_hash):
            return "DENIED"
        if row[2] == "CANCELLED":
            return "ALREADY_CANCELLED"
        if row[2] == "REVOKED":
            return "ALREADY_REVOKED"
        if row[2] == "PENDING":
            c.execute("UPDATE attempts SET state='CANCELLED' WHERE id=%s", (attempt,))
            event, outcome = "CANCEL_PENDING", "CANCELLED"
        elif row[2] == "ISSUED":
            n = c.execute("""UPDATE sessions SET status='REVOKED'
                             WHERE attempt_id=%s AND user_id=%s AND status='ACTIVE'""",
                          (attempt, user)).rowcount
            if n != 1:
                raise RuntimeError("SESSION_INVARIANT")
            c.execute("UPDATE attempts SET state='REVOKED' WHERE id=%s", (attempt,))
            event, outcome = "RECOVER_REVOKE", "REVOKED"
        else:
            raise RuntimeError("INVALID_STATE")
        if fail:
            raise RuntimeError("RECOVERY_FAILURE_INJECTED")
        c.execute("INSERT INTO audit(attempt_id,event) VALUES (%s,%s)", (attempt,event))
        return outcome


class PostgreSQLRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        safe_dsn()
        cls.schema = "m3_disposable_" + secrets.token_hex(8)
        cls.admin = connect()
        try:
            cls.admin.execute("CREATE SCHEMA " + cls.schema)
            cls.admin.commit()
            cls.admin.execute("SET search_path TO " + cls.schema)
            cls.admin.commit()
            cls.admin.execute("""CREATE TABLE attempts(
                id text PRIMARY KEY,user_id integer NOT NULL,cap_hash bytea NOT NULL,
                state text NOT NULL CHECK(state IN ('PENDING','CANCELLED','ISSUED','REVOKED')))""")
            cls.admin.execute("""CREATE TABLE sessions(
                attempt_id text PRIMARY KEY REFERENCES attempts(id),
                user_id integer NOT NULL,status text NOT NULL)""")
            cls.admin.execute("""CREATE TABLE audit(
                id bigserial PRIMARY KEY,attempt_id text NOT NULL,event text NOT NULL)""")
            cls.admin.commit()
        except BaseException:
            cls.admin.rollback()
            try:
                cls.admin.execute("DROP SCHEMA IF EXISTS " + cls.schema + " CASCADE")
                cls.admin.commit()
            finally:
                cls.admin.close()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.rollback()
            cls.admin.execute("DROP SCHEMA IF EXISTS " + cls.schema + " CASCADE")
            cls.admin.commit()
        finally:
            cls.admin.close()

    def db(self):
        c = connect()
        c.execute("SET search_path TO " + self.schema)
        c.commit()
        return c

    def setUp(self):
        self.attempt = secrets.token_hex(16)
        self.cap = secrets.token_urlsafe(48)
        with self.db() as c:
            start(c, self.attempt, 101, self.cap)

    def snapshot(self):
        with self.db() as c:
            a = c.execute("SELECT state FROM attempts WHERE id=%s", (self.attempt,)).fetchone()[0]
            s = [r[0] for r in c.execute("SELECT status FROM sessions WHERE attempt_id=%s",
                                      (self.attempt,)).fetchall()]
            e = [r[0] for r in c.execute("SELECT event FROM audit WHERE attempt_id=%s ORDER BY id",
                                      (self.attempt,)).fetchall()]
        return a, s, e

    def test_recover_first(self):
        with self.db() as c:
            self.assertEqual(recover(c, self.attempt, 101, self.cap), "CANCELLED")
        with self.db() as c:
            self.assertEqual(login(c, self.attempt, 101), "NOT_PENDING")
        self.assertEqual(self.snapshot(), ("CANCELLED", [], ["CANCEL_PENDING"]))

    def test_login_first(self):
        with self.db() as c:
            self.assertEqual(login(c, self.attempt, 101), "ISSUED")
        with self.db() as c:
            self.assertEqual(recover(c, self.attempt, 101, self.cap), "REVOKED")
        self.assertEqual(self.snapshot(), ("REVOKED", ["REVOKED"], ["LOGIN", "RECOVER_REVOKE"]))

    def test_failed_login_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with self.db() as c:
                login(c, self.attempt, 101, fail=True)
        self.assertEqual(self.snapshot(), ("PENDING", [], []))

    def test_failed_recovery_rolls_back(self):
        with self.db() as c:
            login(c, self.attempt, 101)
        with self.assertRaises(RuntimeError):
            with self.db() as c:
                recover(c, self.attempt, 101, self.cap, fail=True)
        self.assertEqual(self.snapshot(), ("ISSUED", ["ACTIVE"], ["LOGIN"]))

    def test_wrong_capability(self):
        with self.db() as c:
            self.assertEqual(recover(c, self.attempt, 101, "wrong"), "DENIED")
        self.assertEqual(self.snapshot(), ("PENDING", [], []))

    def test_simultaneous_login_and_recovery(self):
        barrier = threading.Barrier(3, timeout=8)
        outcome = {}
        def runner(key, fn):
            try:
                with self.db() as c:
                    barrier.wait()
                    outcome[key] = fn(c)
            except BaseException as error:
                outcome[key] = "ERROR:" + type(error).__name__
        t1 = threading.Thread(target=runner, args=("login", lambda c: login(c,self.attempt,101)), daemon=True)
        t2 = threading.Thread(target=runner, args=("recover", lambda c: recover(c,self.attempt,101,self.cap)), daemon=True)
        t1.start()
        t2.start()
        barrier.wait()
        t1.join(10)
        t2.join(10)
        self.assertFalse(t1.is_alive() or t2.is_alive(), "CONCURRENCY_TIMEOUT")
        self.assertIn(outcome.get("login"), ("ISSUED", "NOT_PENDING"))
        self.assertIn(outcome.get("recover"), ("REVOKED", "CANCELLED"))
        state, sessions, _ = self.snapshot()
        self.assertIn(state, ("CANCELLED", "REVOKED"))
        self.assertNotIn("ACTIVE", sessions)


if __name__ == "__main__":
    unittest.main()
