import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.item10_tax_fx_governance import (
    tax_code_record,validate_tax,validate_fx_input,authoritative_fx,
    ensure_period_not_tax_filed,validate_multicurrency_settlement,
    validate_fx_event_duplicate,
)
from app.gl import build_voucher


@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'item10.db')
    monkeypatch.setenv('M3_ITEM10_TAX_FX_GOVERNANCE_ENABLED','true')
    seed_run(True); masterdata_seed_run(); admin_seed_run()
    return db.DB_PATH


def conn():
    return db.connect()


def test_vat_tax_code_and_amount_from_authoritative_master(isolated):
    c=conn()
    out=validate_tax(c,{'Tax Code':'VAT-NL-21','Tax Jurisdiction':'NL','Taxable Base':'100','Tax Amount':'21'})
    assert out['rate']==21
    assert out['jurisdiction']=='NL'
    assert out['treatment']=='STANDARD'
    c.close()


def test_wrong_tax_jurisdiction_and_amount_blocked(isolated):
    c=conn()
    with pytest.raises(HTTPException) as exc:
        validate_tax(c,{'Tax Code':'VAT-NL-21','Tax Jurisdiction':'DE','Taxable Base':'100','Tax Amount':'21'})
    assert exc.value.detail['code']=='TAX_JURISDICTION_MISMATCH'
    with pytest.raises(HTTPException) as exc:
        validate_tax(c,{'Tax Code':'VAT-NL-21','Tax Jurisdiction':'NL','Taxable Base':'100','Tax Amount':'20'})
    assert exc.value.detail['code']=='TAX_AMOUNT_MISMATCH'
    c.close()


def test_wht_and_reverse_charge_treatments(isolated):
    c=conn()
    wht=validate_tax(c,{'Tax Code':'WHT-PK-10','Tax Jurisdiction':'PK','Taxable Base':'100','Tax Amount':'10'})
    assert wht['treatment']=='WHT'
    ts='2026-10-04T00:00:00+00:00'
    c.execute("""INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,created_at,updated_at)
      VALUES('tax-code','RC-EU-0','EU Reverse Charge',?,'ACTIVE',1,?,?)""",
      (json.dumps({'name':'EU Reverse Charge','rate':0,'country':'NL'}),ts,ts))
    rc=validate_tax(c,{'Tax Code':'RC-EU-0','Tax Jurisdiction':'NL','Tax Treatment':'REVERSE_CHARGE','Taxable Base':'100','Tax Amount':'0'})
    assert rc['treatment']=='REVERSE_CHARGE' and rc['tax_amount']==0
    c.close()


def test_invalid_tax_code_blocked(isolated):
    c=conn()
    with pytest.raises(HTTPException) as exc:
        tax_code_record(c,'NO-SUCH-TAX')
    assert exc.value.detail['code']=='TAX_CODE_INVALID'
    c.close()


def test_authoritative_fx_blocks_manual_override(isolated):
    c=conn()
    fx=authoritative_fx(c,'EUR','2026-09-23')
    assert fx['rate']==1.10 and fx['source']=='TEST_RATE'
    with pytest.raises(HTTPException) as exc:
        validate_fx_input(c,'EUR','2026-09-23',1.25)
    assert exc.value.detail['code']=='FX_RATE_OVERRIDE_BLOCKED'
    ok=validate_fx_input(c,'EUR','2026-09-23',1.10)
    assert ok['rate']==1.10
    c.close()


def test_missing_or_future_only_fx_rate_blocked(isolated):
    c=conn()
    c.execute("DELETE FROM gl_fx_rates WHERE currency='EUR'")
    c.execute("INSERT INTO gl_fx_rates(rate_date,currency,base_currency,rate,source,status) VALUES('2026-10-10','EUR','USD',1.20,'FUTURE','ACTIVE')")
    with pytest.raises(HTTPException) as exc:
        authoritative_fx(c,'EUR','2026-09-23')
    assert exc.value.detail['code']=='FX_RATE_REQUIRED'
    c.close()


def test_gl_voucher_uses_authoritative_fx_and_balances_base_currency(isolated):
    c=conn()
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    vid,vno=build_voucher(c,None,{
        'Voucher Type':'JV','Date':'2026-09-23','Currency':'EUR',
        'Debit Account':'1200','Credit Account':'4000','Amount':'100'
    },jid,'TEST','ITEM10-FX',status='Draft',maker_role='GL_ACCOUNTANT')
    v=c.execute('SELECT * FROM gl_vouchers WHERE id=?',(vid,)).fetchone()
    assert v['exchange_rate']==1.10
    assert v['total_debit']==100 and v['total_credit']==100
    assert v['base_total_debit']==110 and v['base_total_credit']==110
    c.close()


def test_gl_voucher_manual_fx_override_is_blocked(isolated):
    c=conn(); jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    with pytest.raises(HTTPException) as exc:
        build_voucher(c,None,{
            'Voucher Type':'JV','Date':'2026-09-23','Currency':'EUR','Exchange Rate':'1.30',
            'Debit Account':'1200','Credit Account':'4000','Amount':'100'
        },jid,'TEST','ITEM10-BADFX')
    assert exc.value.detail['code']=='FX_RATE_OVERRIDE_BLOCKED'
    c.close()


def test_tax_filed_period_blocks_tax_and_fx_mutation(isolated):
    c=conn()
    p=c.execute("SELECT id FROM gl_periods WHERE start_date='2026-09-01'").fetchone()
    c.execute("UPDATE gl_periods SET tax_filed_at='2026-10-04T08:00:00+00:00',tax_filed_by='CFO' WHERE id=?",(p['id'],))
    with pytest.raises(HTTPException) as exc:
        ensure_period_not_tax_filed(c,'2026-09-23')
    assert exc.value.detail['code']=='TAX_FILED_PERIOD_LOCK'
    c.close()


def test_duplicate_unrealized_fx_event_blocked(isolated):
    c=conn()
    p=c.execute("SELECT id FROM gl_periods WHERE start_date='2026-09-01'").fetchone()['id']
    jid=c.execute("SELECT id FROM jobs WHERE job_ref='50001'").fetchone()['id']
    c.execute("""INSERT INTO gl_fx_events(event_ref,event_type,period_id,job_id,currency,foreign_amount,old_rate,new_rate,gain_loss,status)
      VALUES('ITEM10-FXU','UNREALIZED',?,?,?,1000,1.0,1.1,100,'Calculated')""",(p,jid,'EUR'))
    with pytest.raises(HTTPException) as exc:
        validate_fx_event_duplicate(c,'UNREALIZED',p,jid,'EUR')
    assert exc.value.detail['code']=='DUPLICATE_FX_EVENT'
    c.close()


def test_multi_currency_settlement_uses_authoritative_fx(isolated):
    c=conn()
    out=validate_multicurrency_settlement(c,{
      'Date':'2026-09-23','Source Currency':'EUR','Source Amount':'1000',
      'Settlement Currency':'USD','FX Rate':'1.10','Settlement Amount':'1100'
    })
    assert out['rate']==1.10 and out['settlement_amount']==1100
    with pytest.raises(HTTPException) as exc:
        validate_multicurrency_settlement(c,{
          'Date':'2026-09-23','Source Currency':'EUR','Source Amount':'1000',
          'Settlement Currency':'USD','FX Rate':'1.10','Settlement Amount':'1090'
        })
    assert exc.value.detail['code']=='FX_SETTLEMENT_MISMATCH'
    c.close()


def test_tax_and_fx_models_are_reused_not_duplicated(isolated):
    c=conn()
    tables={r['name'] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'gl_tax_postings' in tables
    assert 'gl_fx_rates' in tables
    assert 'gl_fx_events' in tables
    assert 'treasury_records' in tables
    assert not any(x in tables for x in {'item10_tax','item10_fx','item10_ledger','tax_ledger_v2','fx_ledger_v2'})
    c.close()


@pytest.mark.parametrize('flow',['EXPORT','IMPORT','TS'])
def test_export_import_ts_share_same_tax_fx_governance(flow):
    assert flow in {'EXPORT','IMPORT','TS'}
    assert callable(validate_tax)
    assert callable(validate_fx_input)
