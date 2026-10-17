"""Imported UN/LOCODE catalog must not bypass approved Port Master and IAM."""
import csv
from pathlib import Path
import pytest
from fastapi import HTTPException
from app import masterdata

ROOT=Path(__file__).resolve().parents[1]


def test_uploaded_catalog_is_unique_and_country_consistent():
    with (ROOT/"data"/"unlocode_source_2026_uploaded.csv").open(encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==7508
    assert len({r["unlocode"] for r in rows})==len(rows)
    for r in rows:
        assert len(r["unlocode"])==5
        assert r["unlocode"][:2]==r["country_code"]
        assert r["unlocode"][2:]==r["port_code"]
        assert r["port_name"].strip()
    with (ROOT/"data"/"unlocode_quarantine_2026_uploaded.txt").open(encoding="utf-8") as f:
        assert len([line for line in f if line.strip()])==60


def test_catalog_search_is_read_only_and_paginated(monkeypatch):
    class FakeDB:
        def close(self): pass
    monkeypatch.setattr(masterdata,"connect",lambda:FakeDB())
    monkeypatch.setattr(masterdata,"using_postgres",lambda:True)
    monkeypatch.setattr(masterdata,"authenticated_actor",lambda c,token:({},{"MASTER_DATA"}))
    result=masterdata.unlocode_catalog(q="KABUL",country="AF",limit=5,x_m3_session="token")
    assert result["total"]>=1
    assert any(x["unlocode"]=="AFKBL" for x in result["items"])
    assert result["booking_grant"] is False


def test_catalog_requires_authorized_master_reader(monkeypatch):
    class FakeDB:
        def close(self): pass
    monkeypatch.setattr(masterdata,"connect",lambda:FakeDB())
    monkeypatch.setattr(masterdata,"using_postgres",lambda:True)
    monkeypatch.setattr(masterdata,"authenticated_actor",lambda c,token:({},{"OPS"}))
    with pytest.raises(HTTPException) as e:
        masterdata.unlocode_catalog(q="NL",limit=1,x_m3_session="token")
    assert e.value.status_code==403


def test_catalog_stages_via_maker_checker_never_directly_activates(monkeypatch):
    called=[]
    def fake_create(change,x_m3_session=None):
        called.append((change,x_m3_session))
        return {"status":"PENDING"}
    monkeypatch.setattr(masterdata,"create_change",fake_create)
    result=masterdata.unlocode_stage("afkbl",x_m3_session="maker-session")
    assert result["status"]=="PENDING"
    change,token=called[0]
    assert token=="maker-session"
    assert change.domain=="port"
    assert change.record_key=="AFKBL"
    assert change.payload["country_code"]=="AF"
    assert change.payload["unlocode"]=="AFKBL"
    assert change.operation=="CREATE"


@pytest.mark.parametrize("code",["INVALID","DDBRA","XYZ99"])
def test_catalog_does_not_stage_missing_or_quarantined_codes(code,monkeypatch):
    monkeypatch.setattr(masterdata,"create_change",
                        lambda *args,**kwargs:pytest.fail("Unauthorized catalog staging"))
    with pytest.raises(HTTPException) as e:
        masterdata.unlocode_stage(code,x_m3_session="maker-session")
    assert e.value.status_code==404
