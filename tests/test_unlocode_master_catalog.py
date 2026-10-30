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


@pytest.mark.parametrize("code",["DDBRA","XYZ99"])
def test_catalog_does_not_stage_missing_or_quarantined_codes(code,monkeypatch):
    monkeypatch.setattr(masterdata,"create_change",
                        lambda *args,**kwargs:pytest.fail("Unauthorized catalog staging"))
    with pytest.raises(HTTPException) as e:
        masterdata.unlocode_stage(code,x_m3_session="maker-session")
    assert e.value.status_code==404


def test_master_data_ports_screen_is_wired_to_catalog_and_approval_api():
    ui=(ROOT/"web"/"index.html").read_text(encoding="utf-8")
    assert "renderUnlocodeMasterScreen(id,navContext,historyMode)" in ui
    assert "id==='master-data::ports'" in ui
    assert "/api/masterdata/unlocode/catalog?" in ui
    assert "/api/masterdata/unlocode/stage/" in ui
    assert "unlocodeMasterSearch()" in ui
    assert "unlocodeMasterStage(code)" in ui


def test_catalog_not_automatically_activated_in_port_master():
    import inspect
    source=inspect.getsource(masterdata.unlocode_catalog)
    assert "INSERT INTO md_records" not in source
    assert "UPDATE md_records" not in source
    stage=inspect.getsource(masterdata.unlocode_stage)
    assert "create_change(" in stage
    assert "operation='CREATE'" in stage


@pytest.mark.parametrize("code",["NL","NLRTMM","12345","NL RTM","N!RTM",""])
def test_unlocode_stage_rejects_malformed_codes_before_source_lookup(monkeypatch,code):
    monkeypatch.setattr(masterdata,"_unlocode_catalog",
                        lambda:pytest.fail("Invalid key must not load catalog"))
    with pytest.raises(HTTPException) as error:
        masterdata.unlocode_stage(code,x_m3_session="maker-session")
    assert error.value.status_code==422
    assert error.value.detail["code"]=="UNLOCODE_INVALID"


def test_unlocode_deleted_release_code_cannot_be_staged(monkeypatch):
    monkeypatch.setattr(masterdata,"_unlocode_catalog",lambda:[{
        "unlocode":"NLRTM","country_code":"NL","country_name":"Netherlands",
        "port_code":"RTM","port_name":"Rotterdam","eligible_for_activation":False,
    }])
    monkeypatch.setattr(masterdata,"create_change",
                        lambda *args,**kwargs:pytest.fail("Deleted code must not enter maker/checker"))
    with pytest.raises(HTTPException) as error:
        masterdata.unlocode_stage("NLRTM",x_m3_session="maker-session")
    assert error.value.status_code==409
    assert error.value.detail["code"]=="UNLOCODE_DELETED_NOT_ACTIVATABLE"


def test_unlocode_stage_retains_official_release_metadata(monkeypatch):
    monkeypatch.setattr(masterdata,"_unlocode_catalog",lambda:[{
        "unlocode":"NLRTM","country_code":"NL","country_name":"",
        "port_code":"RTM","port_name":"Rotterdam","source":"unece_release",
        "subdivision_code":"ZH","function":"1-------","unlocode_status":"AA",
        "iata_code":"RTM","coordinates":"5155N 00430E",
        "eligible_for_activation":True,
    }])
    captured=[]
    monkeypatch.setattr(masterdata,"create_change",
                        lambda change,x_m3_session=None:captured.append(change) or {"status":"PENDING"})
    result=masterdata.unlocode_stage("nlrtm",x_m3_session="maker-session")
    assert result["status"]=="PENDING"
    change=captured[0]
    assert change.operation=="CREATE"
    assert change.domain=="port"
    assert change.record_key=="NLRTM"
    assert {k:change.payload[k] for k in (
        "source","subdivision_code","function","unlocode_status","iata_code","coordinates"
    )}=={
        "source":"unece_release","subdivision_code":"ZH","function":"1-------",
        "unlocode_status":"AA","iata_code":"RTM","coordinates":"5155N 00430E",
    }


def test_catalog_identifies_actual_unece_source_and_no_booking_grant(monkeypatch):
    class FakeDB:
        def close(self): pass
    monkeypatch.setattr(masterdata,"connect",lambda:FakeDB())
    monkeypatch.setattr(masterdata,"using_postgres",lambda:True)
    monkeypatch.setattr(masterdata,"authenticated_actor",lambda c,token:({},{"MASTER_DATA"}))
    monkeypatch.setattr(masterdata,"_unlocode_catalog",lambda:[{
        "unlocode":"NLRTM","country_code":"NL","port_code":"RTM",
        "port_name":"Rotterdam","source":"unece_release","eligible_for_activation":True,
    }])
    result=masterdata.unlocode_catalog(country="NL",x_m3_session="token")
    assert result["source"]=="unece_release"
    assert result["total"]==1
    assert result["booking_grant"] is False


def test_official_catalog_search_supports_boolean_eligibility(monkeypatch):
    class FakeDB:
        def close(self): pass
    monkeypatch.setattr(masterdata,"connect",lambda:FakeDB())
    monkeypatch.setattr(masterdata,"using_postgres",lambda:True)
    monkeypatch.setattr(masterdata,"authenticated_actor",lambda c,token:({},{"MASTER_DATA"}))
    monkeypatch.setattr(masterdata,"_unlocode_catalog",lambda:[
        {"unlocode":"NLRTM","country_code":"NL","port_code":"RTM",
         "port_name":"Rotterdam","source":"unece_release",
         "eligible_for_activation":True},
        {"unlocode":"DEHAM","country_code":"DE","port_code":"HAM",
         "port_name":"Hamburg","source":"unece_release",
         "eligible_for_activation":False},
    ])
    result=masterdata.unlocode_catalog(q="ROTTERDAM",x_m3_session="token")
    assert [item["unlocode"] for item in result["items"]]==["NLRTM"]
    assert result["source"]=="unece_release"
    result=masterdata.unlocode_catalog(q="HAMBURG",x_m3_session="token")
    assert result["items"][0]["eligible_for_activation"] is False
    assert result["booking_grant"] is False
