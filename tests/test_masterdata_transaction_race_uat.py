"""Test PostgreSQL-style transaction advisory-lock discipline without live DB writes.

A SQLite test adapter simulates transaction-scoped advisory locks on each
connection. Concurrent makers and checkers exercise the production branches.
"""
import concurrent.futures
import sqlite3
import threading

import pytest
from fastapi import HTTPException

from app import masterdata


@pytest.fixture
def transaction_pooler_contract(tmp_path, monkeypatch):
    dbfile = tmp_path / "isolated_concurrency_contract.db"
    first = sqlite3.connect(dbfile, isolation_level=None)
    masterdata.init_schema(first)
    first.close()
    registry = {}
    registry_mutex = threading.Lock()

    class Connection:
        def __init__(self):
            self.raw = sqlite3.connect(dbfile, timeout=20, isolation_level=None)
            self.raw.row_factory = sqlite3.Row
            self.held = []

        def execute(self, query, params=()):
            if "pg_advisory_xact_lock" in query:
                assert query.startswith("SELECT pg_advisory_xact_lock")
                key = tuple(params)
                with registry_mutex:
                    lock = registry.setdefault(key, threading.Lock())
                lock.acquire(timeout=18) or pytest.fail("Advisory lock timed out")
                self.held.append(lock)
                return self.raw.execute("SELECT 1")
            return self.raw.execute(query, params)

        def _release(self):
            while self.held:
                self.held.pop().release()

        def commit(self):
            self.raw.commit()
            self._release()

        def rollback(self):
            self.raw.rollback()
            self._release()

        def close(self):
            self.raw.close()
            self._release()

    def actor(connection, token):
        if token in ("makerA", "makerB"):
            return {"user_ref": token, "username": token}, {"MASTER_DATA"}
        if token in ("checkerA", "checkerB"):
            return {"user_ref": token, "username": token}, {"MASTER_DATA_MANAGER"}
        raise HTTPException(401, {"code": "UNAUTHORIZED"})

    monkeypatch.setattr(masterdata, "connect", Connection)
    monkeypatch.setattr(masterdata, "authenticated_actor", actor)
    monkeypatch.setattr(masterdata, "using_postgres", lambda: True)
    return Connection


def _change(key, name):
    return masterdata.Change(
        domain="customer", record_key=key, operation="CREATE",
        payload={"name": name}, reason="Isolated race-condition regression",
    )


def _parallel(*jobs):
    gate = threading.Barrier(len(jobs))

    def run(job):
        gate.wait(timeout=15)
        try:
            return ("success", job())
        except HTTPException as exc:
            return ("denied", exc.status_code, exc.detail["code"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(run, jobs))


def test_parallel_casefold_makers_create_one_pending_change(transaction_pooler_contract):
    jobs = (
        lambda: masterdata.create_change(
            _change("RACE-PENDING-001", "Concurrency Subject"),
            x_m3_session="makerA",
        ),
        lambda: masterdata.create_change(
            _change("race-pending-001", "Concurrency Subject"),
            x_m3_session="makerB",
        ),
    )
    results = _parallel(*jobs)
    assert [r[0] for r in results].count("success") == 1
    assert any(r == ("denied", 409, "PENDING_CHANGE_EXISTS") for r in results)
    c = transaction_pooler_contract()
    assert c.execute(
        "SELECT COUNT(*) FROM md_change_requests WHERE status='PENDING'"
    ).fetchone()[0] == 1
    assert c.execute(
        "SELECT COUNT(*) FROM md_audit WHERE action='CHANGE_REQUEST_CREATE'"
    ).fetchone()[0] == 1
    c.close()


def test_parallel_checkers_only_one_approval_applies(transaction_pooler_contract):
    submission = masterdata.create_change(
        _change("RACE-DECISION-001", "Approval Subject"), x_m3_session="makerA"
    )
    ref = submission["change_ref"]
    results = _parallel(
        lambda: masterdata.decide(
            ref, masterdata.Decision(decision="APPROVE"), x_m3_session="checkerA"
        ),
        lambda: masterdata.decide(
            ref, masterdata.Decision(decision="APPROVE"), x_m3_session="checkerB"
        ),
    )
    assert [r[0] for r in results].count("success") == 1
    assert ("denied", 409, "CHANGE_ALREADY_DECIDED") in results
    c = transaction_pooler_contract()
    assert c.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM md_versions").fetchone()[0] == 1
    assert c.execute(
        "SELECT COUNT(*) FROM md_audit WHERE action='CHANGE_APPROVE_APPLY'"
    ).fetchone()[0] == 1
    c.close()


def test_pending_withdraw_competes_safely_with_checker(transaction_pooler_contract):
    ref = masterdata.create_change(
        _change("RACE-WITHDRAW-001", "Withdraw Subject"),
        x_m3_session="makerA",
    )["change_ref"]
    results = _parallel(
        lambda: masterdata.withdraw_change(
            ref, masterdata.Withdrawal(reason="Maker cancels"),
            x_m3_session="makerA",
        ),
        lambda: masterdata.decide(
            ref, masterdata.Decision(decision="APPROVE"),
            x_m3_session="checkerA",
        ),
    )
    assert [r[0] for r in results].count("success") == 1
    assert ("denied", 409, "CHANGE_ALREADY_DECIDED") in results
    c = transaction_pooler_contract()
    status = c.execute(
        "SELECT status FROM md_change_requests WHERE change_ref=?", (ref,)
    ).fetchone()["status"]
    assert status in ("APPROVED", "WITHDRAWN")
    assert c.execute("SELECT COUNT(*) FROM md_records").fetchone()[0] == (
        1 if status == "APPROVED" else 0
    )
    c.close()
