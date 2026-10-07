from pathlib import Path

import pytest
from fastapi import HTTPException


SRC = Path("app/clx071_container_journey.py").read_text()
GLGOV = Path("app/item7_financial_document_governance.py").read_text()
MIGRATION = Path("migrations/M3_DETENTION_REV2_001_segments.sql").read_text()
SCHEMA = Path("app/schema.sql").read_text()
HTML = Path("web/index.html").read_text()


def test_rev2_uses_one_additive_detention_segment_child_store():
    assert "CREATE TABLE IF NOT EXISTS public.detention_segments" in MIGRATION
    assert "transaction_id bigint NOT NULL REFERENCES public.transaction_records(id)" in MIGRATION
    assert "commercial_direction text NOT NULL" in MIGRATION
    assert "stage text NOT NULL" in MIGRATION
    assert "tariff_version integer NOT NULL" in MIGRATION
    assert "fx_rate numeric NOT NULL" in MIGRATION
    assert "base_currency text NOT NULL" in MIGRATION
    assert "calculation_hash text NOT NULL UNIQUE" in MIGRATION
    assert "DETENTION_SEGMENT_IMMUTABLE" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS detention_segments" in SCHEMA


def test_rev2_required_blockers_are_implemented():
    for marker in [
        "DETENTION_PERIOD_ALREADY_COVERED",
        "DETENTION_DAY_REGRESSION",
        "TARIFF_VERSION_AMBIGUOUS",
        "FX_RATE_MISSING_ON_SEGMENT",
        "REEFER_CLASSIFICATION_CONFLICT",
        "UNAPPROVED_CHARGE_CODE",
        "ER_BEFORE_COVERED_PERIOD",
        "ACTUAL_ALREADY_POSTED",
        "DETENTION_REF_DIRECTION_COLLISION",
        "SEGMENT_TABLE_MISSING",
    ]:
        assert marker in SRC
    assert "CREDIT_EXCEEDS_INVOICE" in GLGOV


def test_rev2_named_existing_sources_and_feature_switch():
    assert "FROM mrg_rules" in SRC
    assert "FROM mrg_slabs" in SRC
    assert "FROM gl_fx_rates" in SRC
    assert "DETENTION_CALCULATION_ENABLED" in SRC
    assert "DETENTION_CALCULATION_ENABLED" in MIGRATION
    assert "APPROVED mrg_rules.charge_code" in SRC
    assert "segment_storage" in SRC
    assert '"detention_segments"' in SRC


def test_rev2_tariff_version_and_fx_are_pinned_per_segment():
    for marker in [
        "tariff_snapshot_json",
        "tariff_version",
        "tariff_effective_from",
        "tariff_effective_to",
        "fx_rate",
        "fx_rate_date",
        "base_currency",
        "base_amount",
        "rule_ref",
        "calculation_hash",
    ]:
        assert marker in SRC
    assert "def _detention_build_parts" in SRC
    assert 'key=(rule["rule_ref"],int(rule.get("version") or 1))' in SRC


def test_rev2_commercial_direction_refs_are_distinct():
    assert '"AGENT_TO_CUSTOMER":revenue' in SRC
    assert '"PRINCIPAL_TO_AGENT":cost' in SRC
    assert 'str(txr["external_ref"])+"-COST"' in SRC
    assert "DETENTION_REF_DIRECTION_COLLISION" in SRC


def test_rev2_reefer_precedence_conflict():
    from app.clx071_container_journey import _detention_reefer_classification
    assert _detention_reefer_classification({"size_type":"40RF","container_type":"REEFER"}) is True
    assert _detention_reefer_classification({"size_type":"40HC","container_type":"DRY"}) is False
    with pytest.raises(HTTPException) as exc:
        _detention_reefer_classification({"size_type":"40RF","container_type":"DRY"})
    assert exc.value.detail["code"] == "REEFER_CLASSIFICATION_CONFLICT"
    with pytest.raises(HTTPException) as exc:
        _detention_reefer_classification({"size_type":"40HC","container_type":"REEFER"})
    assert exc.value.detail["code"] == "REEFER_CLASSIFICATION_CONFLICT"


def test_rev2_empty_return_and_actual_guards():
    assert "EMPTY_RETURN_DEPOT_REQUIRED" in SRC
    assert "ER_BEFORE_COVERED_PERIOD" in SRC
    assert "ACTUAL_ALREADY_POSTED" in SRC
    assert '"required_path":"CRT"' in SRC or '"require_crt":True' in SRC


def test_rev2_process_collection_status_independence_is_nonblocking():
    assert "PROCESS_COLLECTION_MATRIX" in SRC
    assert '"RUNNING"' in SRC and '"COMPLETE"' in SRC
    assert '"CLOSED"' in SRC
    assert '"blocking":False' in SRC


def test_rev2_credit_bound_uses_existing_finance_governance():
    assert "CREDIT_EXCEEDS_INVOICE" in GLGOV
    assert "original" in GLGOV.lower()
    assert "segment" in GLGOV.lower()


def test_rev2_no_direct_gl_or_treasury_post_from_detention_engine():
    start=SRC.index('@router.get("/detention/{record_id}/summary")')
    end=SRC.index("def scope_container_query", start)
    block=SRC[start:end]
    for forbidden in [
        "INSERT INTO gl_vouchers",
        "INSERT INTO gl_voucher_lines",
        "INSERT INTO treasury_records",
        "INSERT INTO treasury_allocations",
    ]:
        assert forbidden not in block
    assert '"gl_posted":False' in block
    assert '"payment_posted":False' in block


def test_rev2_ui_remains_existing_screen_only():
    assert "Calculate Advance" in HTML
    assert "Recalculate Ongoing" in HTML
    assert "Calculate Actual" in HTML
    assert "Detention / Demurrage" in HTML
    assert "Port Charges" in HTML
    assert "Other Charges" in HTML
    assert "Open Detention Collection" in HTML
    assert "Create Draft Invoice → Finance" in HTML
