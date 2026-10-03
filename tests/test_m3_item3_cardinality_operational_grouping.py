import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.operational_grouping import (
    create_operational_group,
    group_context,
    link_cro_container,
    link_cro_trt,
    link_mbl_hbl,
    link_trt_container,
)


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db,"DB_PATH",tmp_path/"item3.db")
    monkeypatch.setenv("M3_OPERATIONAL_GROUPING_ENABLED","true")
    seed_run(True)
    return db.DB_PATH


def test_operational_group_is_coordination_only_and_multi_job(isolated):
    out=create_operational_group(
        group_ref="OPG-TEST-001",group_type="TS",
        job_refs=["50001","50002"],actor_id="TEST-OPS-001",
    )
    assert out["status"]=="CREATED"
    ctx=group_context("OPG-TEST-001")
    assert ctx["job_refs"]==["50001","50002"]
    assert ctx["authority_scope"]=="OPERATIONAL_COORDINATION_ONLY"
    assert ctx["data_merge_allowed"] is False
    assert ctx["pnl_authority"]=="JOB_ONLY"


def test_operational_group_replay_and_membership_conflict(isolated):
    create_operational_group(group_ref="OPG-TEST-001",group_type="VESSEL",job_refs=["50001","50002"],actor_id="TEST-OPS-001")
    again=create_operational_group(group_ref="OPG-TEST-001",group_type="VESSEL",job_refs=["50002","50001"],actor_id="TEST-OPS-002")
    assert again["status"]=="EXISTS"
    with pytest.raises(HTTPException) as e:
        create_operational_group(group_ref="OPG-TEST-001",group_type="VESSEL",job_refs=["50001","50003"],actor_id="TEST-OPS-003")
    assert e.value.detail["code"]=="OPERATIONAL_GROUP_MEMBERSHIP_CONFLICT"


def test_one_job_supports_multiple_hbl_and_mbl_and_n_to_m_links(isolated):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    c.execute("INSERT INTO bills(bill_no,job_id,kind,status) VALUES('TST-HBL-A',?,'HBL','Draft')",(jid,))
    c.execute("INSERT INTO bills(bill_no,job_id,kind,status) VALUES('TST-HBL-B',?,'HBL','Draft')",(jid,))
    c.execute("INSERT INTO bills(bill_no,job_id,kind,status) VALUES('TST-MBL-A',?,'MBL','Draft')",(jid,))
    c.execute("INSERT INTO bills(bill_no,job_id,kind,status) VALUES('TST-MBL-B',?,'MBL','Draft')",(jid,))
    c.close()
    link_mbl_hbl(mbl_no="TST-MBL-A",hbl_no="TST-HBL-A",job_ref="50001",actor_id="TEST-DOCS-001")
    link_mbl_hbl(mbl_no="TST-MBL-A",hbl_no="TST-HBL-B",job_ref="50001",actor_id="TEST-DOCS-001")
    link_mbl_hbl(mbl_no="TST-MBL-B",hbl_no="TST-HBL-A",job_ref="50001",actor_id="TEST-DOCS-001")
    c=db.connect()
    counts={
      "hbl":c.execute("SELECT COUNT(*) n FROM bills WHERE job_id=? AND kind='HBL'",(jid,)).fetchone()["n"],
      "mbl":c.execute("SELECT COUNT(*) n FROM bills WHERE job_id=? AND kind='MBL'",(jid,)).fetchone()["n"],
      "links":c.execute("SELECT COUNT(*) n FROM mbl_hbl_links WHERE job_id=?",(jid,)).fetchone()["n"],
    }
    c.close()
    assert counts["hbl"]>=3
    assert counts["mbl"]>=3
    assert counts["links"]==3


def test_mbl_hbl_cross_job_link_is_forbidden(isolated):
    c=db.connect()
    j1=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()["id"]
    j2=c.execute("SELECT id FROM jobs WHERE job_ref='50002'").fetchone()["id"]
    c.execute("INSERT INTO bills(bill_no,job_id,kind,status) VALUES('TST-MBL-X',?,'MBL','Draft')",(j1,))
    c.execute("INSERT INTO bills(bill_no,job_id,kind,status) VALUES('TST-HBL-X',?,'HBL','Draft')",(j2,))
    c.close()
    with pytest.raises(HTTPException) as e:
        link_mbl_hbl(mbl_no="TST-MBL-X",hbl_no="TST-HBL-X",job_ref="50001",actor_id="TEST-DOCS")
    assert e.value.detail["code"]=="BL_JOB_SCOPE_MISMATCH"


def ids_for(job_ref):
    c=db.connect()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref=?",(job_ref,)).fetchone()["id"]
    cro=c.execute("SELECT id FROM transaction_records WHERE job_id=? AND module='cro' ORDER BY id LIMIT 1",(jid,)).fetchone()["id"]
    trt=c.execute("SELECT id FROM transaction_records WHERE job_id=? AND module IN ('trt','export-trt','import-trt','transshipment-trt') ORDER BY id LIMIT 1",(jid,)).fetchone()["id"]
    con=c.execute("SELECT id FROM containers WHERE job_id=? ORDER BY id LIMIT 1",(jid,)).fetchone()["id"]
    c.close()
    return cro,trt,con


def test_cro_container_trt_cardinalities_same_job(isolated):
    cro,trt,con=ids_for("50001")
    assert link_cro_container(cro_id=cro,container_id=con,actor_id="TEST-OPS")["status"]=="LINKED"
    assert link_cro_trt(cro_id=cro,trt_id=trt,actor_id="TEST-OPS")["status"]=="LINKED"
    assert link_trt_container(trt_id=trt,container_id=con,actor_id="TEST-OPS")["status"]=="LINKED"


def test_cross_job_data_merge_links_are_forbidden(isolated):
    cro1,trt1,con1=ids_for("50001")
    cro2,trt2,con2=ids_for("50002")
    with pytest.raises(HTTPException) as e1:
        link_cro_container(cro_id=cro1,container_id=con2,actor_id="TEST-OPS")
    assert e1.value.detail["code"]=="CRO_CONTAINER_CROSS_JOB_DATA_MERGE_FORBIDDEN"
    with pytest.raises(HTTPException) as e2:
        link_cro_trt(cro_id=cro1,trt_id=trt2,actor_id="TEST-OPS")
    assert e2.value.detail["code"]=="CRO_TRT_CROSS_JOB_DATA_MERGE_FORBIDDEN"
    with pytest.raises(HTTPException) as e3:
        link_trt_container(trt_id=trt1,container_id=con2,actor_id="TEST-OPS")
    assert e3.value.detail["code"]=="TRT_CONTAINER_CROSS_JOB_DATA_MERGE_FORBIDDEN"


def test_group_schema_has_no_financial_or_customer_authority_columns(isolated):
    c=db.connect()
    cols={x["name"] for x in c.execute("PRAGMA table_info(operational_groups)")}
    c.close()
    forbidden={"customer_id","revenue","cost","margin","invoice_id","bill_id","gl_ref","pnl_owner"}
    assert cols.isdisjoint(forbidden)


def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("M3_OPERATIONAL_GROUPING_ENABLED",raising=False)
    with pytest.raises(HTTPException) as e:
        create_operational_group(group_ref="OPG-X",group_type="TS",job_refs=["50001","50002"],actor_id="TEST")
    assert e.value.detail["code"]=="OPERATIONAL_GROUPING_DISABLED"
