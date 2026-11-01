"""Isolated UAT regression contracts for duplicate-sensitive shared navigation.

These static checks guard source-level controls. They do not replace runtime
idempotency, financial posting or concurrency UAT against synthetic records.
"""
from pathlib import Path

from app.screen_catalog import build_catalog, HO_TRANSACTION_ALIASES


def test_shared_invoice_links_reuse_authoritative_finance_screens():
    by_label={item["label"]:item["target"] for item in HO_TRANSACTION_ALIASES}
    assert len(by_label)==18
    assert all(by_label[x]=="gl-accounts::bills" for x in
               ("Container Purchase","Purchase Invoice","Purchase Invoice Slot"))
    assert all(by_label[x]=="gl-accounts::invoice" for x in
               ("Container Sale","Sale Invoice","Sale Invoice Import"))
    catalog=build_catalog()
    screens={x["screen_id"] for x in catalog["screens"]}
    assert set(by_label.values())<=screens
    assert catalog["screen_count"]==196


def test_transaction_api_has_duplicate_reference_and_idempotency_guards():
    source=(Path(__file__).resolve().parents[1]/"app"/"main.py").read_text()
    assert "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST" in source
    assert "DUPLICATE_TRANSACTION" in source
    assert "Idempotency-Key" in source
    assert "OPTIMISTIC_LOCK_CONFLICT" in source


def test_cro_container_link_guard_rejects_cross_job_and_replays():
    source=(Path(__file__).resolve().parents[1]/"app"/"operational_grouping.py").read_text()
    assert "CRO_CONTAINER_CROSS_JOB_DATA_MERGE_FORBIDDEN" in source
    assert "ON CONFLICT(cro_transaction_id,container_id) DO NOTHING" in source


def test_agent_defaults_mapping_stays_read_only_and_versioned():
    source=(Path(__file__).resolve().parents[1]/"app"/"cro_agent_defaults.py").read_text()
    assert "CRO_AGENT_BOOKING_MISMATCH" in source
    assert "CRO_AGENT_NOT_ACTIVE" in source
    assert "agent_master_version" in source
    assert "agent_cro_defaults_snapshot" in source
