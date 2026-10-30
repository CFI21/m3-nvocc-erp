"""Executable, isolated SQLite regression for CRO/Equipment link idempotency.

No external API, Neon database, Render service or production state is used.
"""
import sqlite3

import pytest
from fastapi import HTTPException

from app import operational_grouping as grouping


@pytest.fixture
def isolated_links(monkeypatch):
    uri="file:m3_cro_link_uat?mode=memory&cache=shared"
    conn=sqlite3.connect(uri,uri=True)
    conn.row_factory=sqlite3.Row
    conn.executescript("""
        CREATE TABLE transaction_records(id INTEGER PRIMARY KEY, job_id INTEGER, module TEXT);
        CREATE TABLE containers(id INTEGER PRIMARY KEY, job_id INTEGER);
        CREATE TABLE cro_container_links(
            cro_transaction_id INTEGER NOT NULL,
            container_id INTEGER NOT NULL,
            relationship_status TEXT,
            created_by TEXT,
            created_at TEXT,
            UNIQUE(cro_transaction_id, container_id)
        );
        INSERT INTO transaction_records(id,job_id,module) VALUES(11,100,'cro');
        INSERT INTO containers(id,job_id) VALUES(21,100),(22,200);
    """)
    monkeypatch.setattr(grouping,"enabled",lambda: True)
    def connection_per_request():
        c=sqlite3.connect(uri,uri=True)
        c.row_factory=sqlite3.Row
        return c
    monkeypatch.setattr(grouping,"connect",connection_per_request)
    monkeypatch.setattr(grouping,"tx",lambda c: c.execute("BEGIN IMMEDIATE"))
    yield conn
    conn.close()


def test_repeated_cro_container_link_creates_only_one_record(isolated_links):
    first=grouping.link_cro_container(cro_id=11,container_id=21,actor_id="uat-maker")
    second=grouping.link_cro_container(cro_id=11,container_id=21,actor_id="uat-maker")
    assert first["status"]==second["status"]=="LINKED"
    count=isolated_links.execute(
        "SELECT COUNT(*) FROM cro_container_links WHERE cro_transaction_id=11 AND container_id=21"
    ).fetchone()[0]
    assert count==1


def test_cross_job_link_is_rejected_without_insertion(isolated_links):
    with pytest.raises(HTTPException) as error:
        grouping.link_cro_container(cro_id=11,container_id=22,actor_id="uat-maker")
    assert error.value.status_code==409
    assert error.value.detail["code"]=="CRO_CONTAINER_CROSS_JOB_DATA_MERGE_FORBIDDEN"
    assert isolated_links.execute("SELECT COUNT(*) FROM cro_container_links").fetchone()[0]==0
