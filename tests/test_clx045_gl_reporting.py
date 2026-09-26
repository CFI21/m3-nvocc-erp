import json
from pathlib import Path
import pytest

from app import db
from app.seed import run as seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin_seed import run as admin_seed_run
from app.screen_catalog import build_catalog
from app.screen_integration import quick_actions
from app.clx044_gl_exact_flow import GL_SETUP_ORDER,GL_TRANSACTION_ORDER
from app.clx045_gl_reporting import (
    report_rows,trial_balance_rows,profit_loss_rows,balance_sheet_rows,
    gl_detail_rows,subledger_reconciliation_rows,drill_account,drill_voucher,
    control_summary
)

HTML=Path("web/index.html").read_text()

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx045.db')
    seed_run(True)
    masterdata_seed_run()
    admin_seed_run()
    return db.DB_PATH

def test_frozen_clx044_navigation_and_196_screen_baseline():
    c=build_catalog()
    assert c['screen_count']==196
    assert [x['domain'] for x in c['menu']]==[
        'Agent Tasks','HO Tasks','Treasury / AR-AP',
        'Integration & Security','General / Administration','Master Data'
    ]
    ga=next(x for x in c['menu'] if x['domain']=='General / Administration')
    by={x['name']:x for x in ga['submenus']}
    assert [x.split('::')[1] for x in by['Finance & Accounting Setup · Setup']['screens']]==GL_SETUP_ORDER
    assert [x.split('::')[1] for x in by['Finance & Accounting Setup · Transaction']['screens']]==GL_TRANSACTION_ORDER

def test_all_five_existing_gl_reports_are_live_and_read_only(isolated):
    keys=['trial-balance-report','profit-loss','balance-sheet','gl-detail','subledger-gl-reconciliation']
    for key in keys:
        rows=report_rows(key,status='Posted')
        assert isinstance(rows,list)
        a=quick_actions('gl-accounts::'+key,'AUDITOR')['visible_actions']
        assert set(a) <= {'quick-view','related-records','print','export','audit-history'}
        assert not ({'create','edit','approve','release','reverse'} & set(a))

def test_trial_balance_includes_balanced_opening_and_posted_movements(isolated):
    rows=trial_balance_rows(status='Posted')
    debit=round(sum(r['Debit VC'] for r in rows),2)
    credit=round(sum(r['Credit VC'] for r in rows),2)
    debit_lc=round(sum(r['Debit LC'] for r in rows),2)
    credit_lc=round(sum(r['Credit LC'] for r in rows),2)
    assert debit==credit
    assert debit_lc==credit_lc
    equity=next(r for r in rows if r['Account Code']=='3000')
    assert equity['Account Type']=='CAPITAL'
    assert equity['Opening Credit']==40000

def test_profit_loss_and_balance_sheet_classify_from_chart_of_accounts(isolated):
    pl=profit_loss_rows(status='Posted')
    assert pl and {r['Section'] for r in pl} <= {'Revenue','Expense'}
    bs=balance_sheet_rows(status='Posted')
    assert bs and {r['Section'] for r in bs} <= {'ASSET','LIABILITY','CAPITAL','EQUITY'}
    assert any(r['Section']=='CAPITAL' for r in bs)

def test_gl_detail_has_vc_lc_source_trace_and_filters(isolated):
    rows=gl_detail_rows(period='2026-09',account='1200',currency='USD',status='Posted')
    assert rows
    assert all(r['Account Code']=='1200' for r in rows)
    assert all(r['Currency']=='USD' for r in rows)
    assert all('Debit VC' in r and 'Debit LC' in r and 'Source Ref' in r for r in rows)

def test_subledger_invoice_bill_receipt_payment_reconcile_to_posted_gl(isolated):
    rows=subledger_reconciliation_rows(period='2026-09')
    core=[r for r in rows if r['Module'] in {'invoice','bills','receipt','payment'}]
    assert core
    assert all(r['Reconciliation Status']=='MATCHED' for r in core)
    assert all(abs(r['Difference'])<0.01 for r in core)
    assert any(r['Module']=='wht-deposits' for r in rows)

def test_drilldown_account_to_voucher_to_source(isolated):
    account=drill_account('1200',period='2026-09',x_role='AUDITOR',x_m3_session=None)
    assert account['count']>0 and account['next']=='voucher'
    voucher_no=account['rows'][0]['Voucher No']
    voucher=drill_voucher(voucher_no,x_role='AUDITOR',x_m3_session=None)
    assert voucher['lines']
    assert voucher['next']=='source'

def test_control_summary_balances_trial_balance(isolated):
    s=control_summary(x_role='AUDITOR',x_m3_session=None)
    assert s['trial_balance_balanced_vc'] is True
    assert s['trial_balance_balanced_lc'] is True
    assert s['live_providers'] is False
    assert s['real_money'] is False

def test_web_has_financial_report_filters_and_drilldown():
    for marker in ['glCompany','glPeriod','glDateFrom','glDateTo','glAccount','glCostCenter','glCurrency','glReportStatus']:
        assert marker in HTML
    assert "GL_REPORT_KEYS" in HTML
    assert "drillGlReportRow" in HTML
    assert "/api/clx045/reports/" in HTML
    assert "--nav:#4b5563" in HTML
