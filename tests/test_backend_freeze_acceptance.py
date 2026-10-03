import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_backend_freeze_acceptance_contract():
    a=json.loads((ROOT/'M3_BACKEND_FREEZE_ACCEPTANCE.json').read_text())
    assert a['scope']=='M3_ONLY'
    assert a['screen_contract']==196
    assert a['backend_development_ready'] is True
    assert a['frontend_development_can_start'] is True
    assert a['full_live_operational'] is False
    assert a['remaining_infrastructure_gate']=='INDEPENDENT_PRODUCTION_DR_NOT_CERTIFIED'
    assert a['provider_safety']['real_money']=='OFF'
    assert a['ancline_touched'] is False
    assert all(v=='PASS' for v in a['authoritative_models'].values())

def test_booking_party_link_acceptance_closed():
    a=json.loads((ROOT/'BOOKING_PARTY_LINK_ALIGNMENT.json').read_text())
    assert a['BOOKING_PARTY_LINK_READY'] is True
    assert a['status']=='PASS'
    assert a['model']['customer_agent_relationship']=='BOOKING_JOB_ONLY'
    assert a['model']['permanent_customer_agent_link'] is False
    assert a['verification']['booking_job_party_mismatch']==0
