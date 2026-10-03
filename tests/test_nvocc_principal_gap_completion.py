import json
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.screen_catalog import build_catalog, BUSINESS_NAV_ALIASES
from app.nvocc_principal_extensions import (
    WorkspaceWrite, meta, list_workspace, upsert_workspace,
    release_prerequisites, bl_linkage, branch_pnl, WORKSPACES, SCHEMAS,
)

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'principal-gaps.db')
    seed_run(True)
    admin_seed_run()
    masterdata_seed_run()
    return db.DB_PATH

def test_196_authoritative_screens_preserved_with_real_extension_workspaces(isolated):
    c=build_catalog()
    assert c['screen_count']==196
    assert c['baseline_screen_count']==196
    assert c['extension_count']==0
    m=meta()
    assert m['screen_baseline']==196
    assert m['screen_count_changed'] is False
    assert set(WORKSPACES)=={
      'branch-profile','legal-entity-profile','depot-master','port-config',
      'transfer-pricing','ts-branch-ops','release-control','interbranch-settlement'
    }
    assert m['real_money'] is False and m['providers_active'] is False and m['ancline_touched'] is False

def test_business_navigation_exposes_all_gap_workspaces_without_duplicate_screens():
    items=[x for group in BUSINESS_NAV_ALIASES.values() for x in group]
    keys={x.get('workspace_key') for x in items if x.get('workspace_key')}
    assert {
      'branch-profile','legal-entity-profile','depot-master','port-config','transfer-pricing',
      'ts-branch-ops','release-control','bl-linkage','interbranch-settlement','branch-pnl'
    } <= keys
    assert next(x for x in items if x.get('workspace_key')=='depot-master')['target']=='master-data::locations'
    assert next(x for x in items if x.get('workspace_key')=='release-control')['target']=='agent-tasks::delivery-order'

def test_depot_master_is_persisted_and_audited(isolated):
    out=upsert_workspace('depot-master',WorkspaceWrite(data={
      'depot_code':'RTM-D01','depot_name':'Rotterdam Depot','depot_type':'Own Depot','ownership':'Owned',
      'branch_code':'RTM-HQ','port_code':'NLRTM','address':'Test Yard','free_time_days':5,'storage_rate':25,
      'mr_capability':True,'reefer_plugs':20,'dangerous_goods':True,'customs_bonded':True,'contact':'Ops','active':True
    }),x_role='ADMIN',x_branch_scope=None)
    assert out['record']['depot_code']=='RTM-D01'
    rows=list_workspace('depot-master',x_role='ADMIN',x_branch_scope=None)['rows']
    assert rows[0]['storage_rate']==25
    c=db.connect()
    assert c.execute("SELECT COUNT(*) n FROM nvocc_extension_audit WHERE workspace_key='depot-master'").fetchone()['n']==1
    c.close()

def test_port_config_and_transfer_pricing_validation(isolated):
    upsert_workspace('port-config',WorkspaceWrite(data={
      'config_id':'PC-NLRTM-POL','port_code':'NLRTM','port_role':'POL','agent_code':'RTM-HQ','agent_type':'Own Branch',
      'depot_code':'RTM-D01','depot_type':'Own','empty_return_depot':'RTM-CAR','carrier_depot':'RTM-CAR',
      'cost_allocation':'Internal','effective_from':'2026-10-03','priority':1,'active':True
    }),x_role='ADMIN',x_branch_scope=None)
    with pytest.raises(HTTPException) as e:
        upsert_workspace('transfer-pricing',WorkspaceWrite(data={
          'rule_id':'TP-1','from_branch_type':'Own Branch','to_branch_type':'Own Subsidiary',
          'service_type':'TS Handling','pricing_method':'Cost + Markup','currency':'EUR','effective_from':'2026-10-03','active':True
        }),x_role='FINANCE',x_branch_scope=None)
    assert e.value.status_code==422
    ok=upsert_workspace('transfer-pricing',WorkspaceWrite(data={
      'rule_id':'TP-1','from_branch_type':'Own Branch','to_branch_type':'Own Subsidiary',
      'service_type':'TS Handling','pricing_method':'Cost + Markup','markup_pct':8,
      'currency':'EUR','effective_from':'2026-10-03','active':True
    }),x_role='FINANCE',x_branch_scope=None)
    assert ok['record']['markup_pct']==8

def test_ts_branch_operations_has_full_stage_fields_and_branch_scope(isolated):
    names={x[0] for x in SCHEMAS['ts-branch-ops']}
    for field in {
      'task_ref','job_ref','container_no','ts_port','ts_branch','branch_manager','cost_center','profit_center','legal_entity_code',
      'connecting_vessel','connecting_voyage','eta_ts','etd_ts','acknowledged_by','discharge_at','yard_location','storage_start',
      'storage_cost','reload_at','stowage_position','departure_at','cost_lines_json','documents_json','exceptions_json','status'
    }:
        assert field in names
    upsert_workspace('ts-branch-ops',WorkspaceWrite(data={
      'task_ref':'TS-50001-1','job_ref':'50001','container_no':'M3CU500001','ts_port':'SGSIN','ts_branch':'SIN-MAIN','status':'Draft'
    }),x_role='ADMIN',x_branch_scope=None)
    assert len(list_workspace('ts-branch-ops',x_role='OPS',x_branch_scope='SIN-MAIN')['rows'])==1
    assert len(list_workspace('ts-branch-ops',x_role='OPS',x_branch_scope='RTM-HQ')['rows'])==0

def test_release_gate_derives_authoritative_prerequisites(isolated):
    r=release_prerequisites('50001',x_role='AUDITOR')
    assert set(r['checks'])=={'hbl_issued','customs_cleared','finance_cleared','surrender_or_telex'}
    assert r['authoritative_sources']==['bills','workflow_states','finance_states','delivery-order transaction']
    assert isinstance(r['release_ready'],bool)

def test_mbl_hbl_linkage_reuses_existing_bills_and_gl(isolated):
    d=bl_linkage('50001',x_role='DOCS')
    kinds={x['kind'] for x in d['bills']}
    assert 'HBL' in kinds and 'MBL' in kinds
    assert d['parallel_tracks'] is True
    assert isinstance(d['carrier_customer_finance'],list)

def test_interbranch_settlement_and_branch_pnl_component(isolated):
    upsert_workspace('interbranch-settlement',WorkspaceWrite(data={
      'settlement_ref':'IBS-50001-1','from_branch':'SIN-MAIN','to_branch':'RTM-HQ',
      'legal_entity_from':'M3-AE','legal_entity_to':'M3-NL','job_ref':'50001','container_no':'M3CU500001',
      'charges_json':'[{"code":"TS-THC","amount":100}]','total_amount':100,'currency':'EUR',
      'exchange_rate':1,'status':'APPROVED','elimination_flag':True
    }),x_role='FINANCE',x_branch_scope=None)
    p=branch_pnl(None,x_role='AUDITOR')
    by={x['branch']:x for x in p['rows']}
    assert by['SIN-MAIN']['internal_revenue']==100
    assert by['RTM-HQ']['internal_cost']==100
    assert p['scope']=='INTER_BRANCH_COMPONENT'

def test_existing_forms_are_extended_in_place_not_duplicated():
    meta=json.load(open('app/module_meta.json',encoding='utf-8'))
    by={x['key']:x for x in meta['modules']}
    assert {'Origin Branch','TS Branch','Destination Branch'} <= set(by['booking']['fields'])
    assert {'Release Depot','Release Depot Type','Empty Pickup Depot','POL Agent Type','Cost Allocation'} <= set(by['cro']['fields'])
    assert {'Delivery From','Empty Return To','POD Agent Type','D&D Free Time End','Cost Allocation'} <= set(by['delivery-order']['fields'])
    assert {'MBL-HBL Link Status','Carrier AP Ref','Shipper AR Ref'} <= set(by['bl']['fields'])

def test_web_surfaces_principal_workspaces_and_dense_extension_forms():
    html=open('web/index.html',encoding='utf-8').read()
    catalog=open('app/screen_catalog.py',encoding='utf-8').read()
    for marker in [
      'openPrincipalWorkspace','TS Branch Operations','Release Control','MBL ↔ HBL Link Control',
      'Origin Branch','Destination Branch','Delivery From','Empty Return To','D&D Free Time End'
    ]:
        assert marker in html,marker
    for marker in [
      'Depot Master','Port Agent / Depot Configuration','Inter-Branch Transfer Pricing',
      'Inter-Branch Settlement','Branch P&L / Consolidation'
    ]:
        assert marker in catalog,marker
    assert 'No records yet. Use New to create the first governed record.' in html
    assert '/api/nvocc-principal/release-prerequisites/' in html

def test_schema_and_migration_are_additive():
    schema=open('app/schema.sql',encoding='utf-8').read()
    migration=open('migrations/NVOCC_PRINCIPAL_GAPS_001.sql',encoding='utf-8').read()
    for table in [
      'nvocc_branch_profiles','nvocc_legal_entity_profiles','nvocc_depots','nvocc_port_agent_depot_config',
      'nvocc_transfer_pricing','nvocc_ts_branch_operations','nvocc_release_controls','nvocc_interbranch_settlements','nvocc_extension_audit'
    ]:
        assert 'CREATE TABLE IF NOT EXISTS '+table in schema
        assert 'CREATE TABLE IF NOT EXISTS '+table in migration
    assert 'DROP TABLE' not in migration.upper()
    assert 'ALTER TABLE' not in migration.upper()
