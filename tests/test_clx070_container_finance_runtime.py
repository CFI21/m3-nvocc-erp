import pytest
from app import db
from app.seed import run as seed_run
from app.clx070_equipment_runtime import (
    ContainerRegisterBody, FinanceEntryBody, FinanceDecisionBody, MovementBody, ShareRuleBody,
    register_container, add_finance_entry, finance_decision, add_share_rule, post_movement, container_profile
)

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'clx070-global.db')
    seed_run(True)
    c=db.connect()
    try:
        now='2026-10-01T00:00:00+00:00'
        rules=[
          ('T-MASTER','MASTER_EDIT','OPS','GLOBAL'),
          ('T-VIEW','VIEW','AUDITOR','GLOBAL'),
          ('T-MOVE','MOVE','OPS','GLOBAL'),
          ('T-FIN','FINANCE_CREATE','FINANCE','GLOBAL'),
          ('T-FIN-ADMIN','FINANCE_APPROVE','ADMIN','GLOBAL'),
        ]
        for code,action,role,scope in rules:
            c.execute("""INSERT INTO equipment_policy_rules(rule_code,action_code,subject_role,scope_type,effect,four_eyes,config_json,active,created_at,updated_at)
                         VALUES(?,?,?,?, 'ALLOW',1,'{}',1,?,?)""",(code,action,role,scope,now,now))
        c.commit()
    finally:c.close()
    return db.DB_PATH

def test_register_principal_container_and_auto_acquisition_cost(isolated):
    out=register_container(ContainerRegisterBody(
      container_no='M3TEST001',size_type='40HC',ownership='OWNED',owner_type='PRINCIPAL',
      owner_party_code='PR-M3',principal_owner_code='PR-M3',principal_code='M3 NVOCC',
      branch_code='KHI',current_port='Karachi',agent_code='AG-KHI',depot_code='KHI-DEPOT',
      acquisition_type='PURCHASE',purchase_order_ref='PO-TEST-1',unit_cost=3000,currency='USD'
    ),x_role='OPS',x_agent_scope=None,x_branch_scope=None)
    assert out['record']['owner_type']=='PRINCIPAL'
    assert out['record']['owner_party_code']=='PR-M3'
    c=db.connect()
    try:
        links=[dict(x) for x in c.execute("select * from container_party_links where container_id=?",(out['record']['id'],))]
        costs=[dict(x) for x in c.execute("select * from container_financial_entries where container_id=?",(out['record']['id'],))]
    finally:c.close()
    assert any(x['is_owner'] for x in links)
    assert any(x['party_role']=='AGENT_CUSTODIAN' for x in links)
    assert costs[0]['entry_category']=='COST'
    assert costs[0]['charge_code']=='PURCHASE_ACQUISITION'

def test_finance_revenue_cost_share_and_movement_are_same_container(isolated):
    register_container(ContainerRegisterBody(
      container_no='M3TEST002',size_type='20GP',ownership='LEASED',owner_type='LEASING_COMPANY',
      owner_party_code='LESSOR-1',leasing_company_code='LESSOR-1',principal_code='M3 NVOCC',
      branch_code='DXB',current_port='Jebel Ali',agent_code='AG-DXB',depot_code='JAFZA',
      acquisition_type='LEASE',lease_contract_ref='L-001',unit_cost=0
    ),x_role='OPS',x_agent_scope=None,x_branch_scope=None)
    e=add_finance_entry('M3TEST002',FinanceEntryBody(
      entry_category='REVENUE',charge_code='FREIGHT_REVENUE',amount=1500,currency='USD',
      party_type='CUSTOMER',party_code='CUST-1',source_type='BOOKING',source_ref='BK-TEST'
    ),x_role='FINANCE',x_agent_scope=None,x_branch_scope=None)
    finance_decision('M3TEST002',e['entry_ref'],FinanceDecisionBody(decision='APPROVE',note='checker'),x_role='ADMIN',x_agent_scope=None,x_branch_scope=None)
    add_share_rule('M3TEST002',ShareRuleBody(party_type='LEASING_COMPANY',party_code='LESSOR-1',rate_percent=10),x_role='FINANCE',x_agent_scope=None,x_branch_scope=None)
    post_movement('M3TEST002',MovementBody(event_type='REPOSITION',port='Dubai',branch_code='DXB',agent_code='AG-DXB',depot_code='DXB-YARD',detail={'actual_cost':200,'currency':'USD','charge_code':'REPOSITION_COST'}),x_role='OPS',x_agent_scope=None,x_branch_scope=None)
    p=container_profile('M3TEST002',x_role='AUDITOR',x_agent_scope=None,x_branch_scope=None)
    assert p['container']['current_port']=='Dubai'
    assert any(x['entry_category']=='REVENUE' for x in p['financial_entries'])
    assert any(x['charge_code']=='REPOSITION_COST' for x in p['financial_entries'])
    assert any(x['party_code']=='LESSOR-1' for x in p['share_rules'])
