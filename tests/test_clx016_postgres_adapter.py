import pytest

from app import db


M3_URL = "postgresql://postgres.ozupgknqaqgvprliewxe:secret@aws-0-eu-west-2.pooler.supabase.com:6543/postgres"
ANCLINE_URL = "postgresql://postgres.wgxbvbcttxkdqmtdngmq:secret@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"


def test_m3_target_lock_accepts_only_approved_project(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", M3_URL)
    assert db.approved_database_target() is True
    db.assert_approved_database_target()

    monkeypatch.setattr(db, "DATABASE_URL", ANCLINE_URL)
    assert db.approved_database_target() is False
    with pytest.raises(RuntimeError, match="DATABASE_URL_TARGET_REJECTED"):
        db.assert_approved_database_target()


def test_qmark_and_insert_ignore_translation():
    sql, last = db._translate_sql(
        "INSERT OR IGNORE INTO gl_bank_matches(statement_item_id,voucher_id) VALUES(?,?)"
    )
    assert "ON CONFLICT DO NOTHING" in sql
    assert "%s" in sql
    assert "?" not in sql
    assert last is False


def test_round_real_scale_translation():
    sql, _ = db._translate_sql(
        "SELECT ROUND(SUM(outstanding),2) total FROM gl_ap_open_items"
    )
    assert "ROUND(CAST((SUM(outstanding)) AS numeric),2)" in sql


def test_julianday_translation():
    sql, _ = db._translate_sql(
        "SELECT ROUND(SUM(CASE WHEN julianday('2026-09-23')-julianday(due_date)<=0 THEN outstanding ELSE 0 END),2) current FROM gl_ap_open_items"
    )
    assert "CAST('2026-09-23' AS date)-CAST(due_date AS date)" in sql
    assert "AS numeric" in sql


def test_json_set_translation():
    sql, _ = db._translate_sql(
        "UPDATE gl_records SET payload_json=json_set(payload_json,'$.\"Voucher No.\"',?) WHERE id=?"
    )
    assert "jsonb_set" in sql
    assert "to_jsonb(%s::text)" in sql
    assert "WHERE id=%s" in sql


def test_lastrowid_translation_for_transaction_and_treasury_batch():
    sql, last = db._translate_sql(
        "INSERT INTO transaction_records(module,external_ref) VALUES(?,?)"
    )
    assert sql.endswith("RETURNING id")
    assert last is True

    sql, last = db._translate_sql(
        "INSERT INTO treasury_payment_batches(batch_ref,status) VALUES(?,?)"
    )
    assert sql.endswith("RETURNING id")
    assert last is True
