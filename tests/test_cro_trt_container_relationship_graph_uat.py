"""Executable isolated CRO/TRT/Container relationship consistency tests.

All tables and rows exist only in a shared in-memory SQLite fixture.
"""
import sqlite3
import uuid

import pytest
from fastapi import HTTPException
from app import operational_grouping as links


@pytest.fixture
def graph(monkeypatch):
    uri=f"file:uat_graph_{uuid.uuid4().hex}?mode=memory&cache=shared"
    keeper=sqlite3.connect(uri,uri=True)
    keeper.row_factory=sqlite3.Row
    keeper.executescript("""
    CREATE TABLE transaction_records(id INTEGER PRIMARY KEY,job_id INTEGER,module TEXT);
    CREATE TABLE containers(id INTEGER PRIMARY KEY,job_id INTEGER);
    CREATE TABLE cro_trt_links(cro_transaction_id INTEGER,trt_transaction_id INTEGER,
      relationship_status TEXT,created_by TEXT,created_at TEXT,
      UNIQUE(cro_transaction_id,trt_transaction_id));
    CREATE TABLE trt_container_links(trt_transaction_id INTEGER,container_id INTEGER,
      relationship_status TEXT,created_by TEXT,created_at TEXT,
      UNIQUE(trt_transaction_id,container_id));
    INSERT INTO transaction_records VALUES(10,100,'cro'),(11,100,'trt'),
      (12,200,'trt'),(13,100,'booking');
    INSERT INTO containers VALUES(20,100),(21,200);
    """)
    def open_request():
        c=sqlite3.connect(uri,uri=True)
        c.row_factory=sqlite3.Row
        return c
    monkeypatch.setattr(links,"enabled",lambda:True)
    monkeypatch.setattr(links,"connect",open_request)
    monkeypatch.setattr(links,"tx",lambda c:c.execute("BEGIN IMMEDIATE"))
    yield keeper
    keeper.close()


def test_cro_trt_replay_and_cross_job_rejection(graph):
    for _ in range(2):
        result=links.link_cro_trt(cro_id=10,trt_id=11,actor_id="uat")
        assert result["status"]=="LINKED"
    assert graph.execute("SELECT COUNT(*) FROM cro_trt_links").fetchone()[0]==1
    with pytest.raises(HTTPException) as exc:
        links.link_cro_trt(cro_id=10,trt_id=12,actor_id="uat")
    assert exc.value.status_code==409
    assert exc.value.detail["code"]=="CRO_TRT_CROSS_JOB_DATA_MERGE_FORBIDDEN"
    assert graph.execute("SELECT COUNT(*) FROM cro_trt_links").fetchone()[0]==1


def test_trt_container_replay_and_cross_job_rejection(graph):
    for _ in range(2):
        result=links.link_trt_container(trt_id=11,container_id=20,actor_id="uat")
        assert result["status"]=="LINKED"
    assert graph.execute("SELECT COUNT(*) FROM trt_container_links").fetchone()[0]==1
    with pytest.raises(HTTPException) as exc:
        links.link_trt_container(trt_id=11,container_id=21,actor_id="uat")
    assert exc.value.status_code==409
    assert exc.value.detail["code"]=="TRT_CONTAINER_CROSS_JOB_DATA_MERGE_FORBIDDEN"
    assert graph.execute("SELECT COUNT(*) FROM trt_container_links").fetchone()[0]==1


def test_incorrect_transaction_type_never_links(graph):
    with pytest.raises(HTTPException) as exc:
        links.link_cro_trt(cro_id=10,trt_id=13,actor_id="uat")
    assert exc.value.status_code==404
    assert graph.execute("SELECT COUNT(*) FROM cro_trt_links").fetchone()[0]==0
