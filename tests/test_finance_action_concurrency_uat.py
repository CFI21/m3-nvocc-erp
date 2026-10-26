"""Isolated concurrency regression for GL posting and Treasury release version gates.

Exercises the same compare-and-swap database mutation used in operational
handlers, under simultaneous SQLite writers. No connected database is used.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import sqlite3
import pytest

@pytest.mark.parametrize("table,action",[
    ("gl_records","post"),
    ("treasury_records","release"),
])
def test_two_simultaneous_action_writers_only_one_can_advance_version(tmp_path,table,action):
    filename=tmp_path/"action_race.sqlite"
    with sqlite3.connect(filename) as conn:
        conn.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY,status TEXT,version INTEGER)")
        conn.execute(f"INSERT INTO {table} VALUES(1,'Approved',1)")
    barrier=Barrier(2)
    def worker():
        conn=sqlite3.connect(filename,timeout=15,isolation_level=None)
        try:
            barrier.wait(timeout=15)
            conn.execute("BEGIN IMMEDIATE")
            cur=conn.execute(f"UPDATE {table} SET status=?,version=version+1 WHERE id=1 AND version=1",("Posted" if action=="post" else "Released",))
            conn.execute("COMMIT")
            return cur.rowcount
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert sorted(results)==[0,1],results
    with sqlite3.connect(filename) as conn:
        assert conn.execute(f"SELECT version FROM {table} WHERE id=1").fetchone()[0]==2

def test_operational_action_handlers_reject_stale_versions_before_side_effects():
    base=Path(__file__).resolve().parents[1]/"app"
    gl=(base/"gl.py").read_text()
    treasury=(base/"treasury.py").read_text()
    gl_action=gl[gl.index("def action(module:"):]
    treasury_action=treasury[treasury.index("def action(module:"):]
    for code in (gl_action,treasury_action):
        assert "OPTIMISTIC_LOCK_CONFLICT" in code
        assert "tx(" in code
        assert code.index("OPTIMISTIC_LOCK_CONFLICT")<code.index("if action=='release'" if "if action=='release'" in code else "if action=='post'")
    assert "VOUCHER_ALREADY_POSTED" in gl_action
    assert "DUPLICATE_SOURCE_POSTING" in gl_action
    assert "WHERE id=? AND version=?" in treasury_action
