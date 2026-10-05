from pathlib import Path
import json

def test_agent_setup_routes_and_no_duplicate_models():
    import app.main as main
    paths=set(main.app.openapi()['paths'])
    for p in [
        '/api/agent-setup/relations',
        '/api/agent-setup/volume-restrictions',
        '/api/agent-setup/network',
        '/api/agent-setup/booking/{job_ref}/eligibility',
    ]:
        assert p in paths
    src=Path('app/agent_setup.py').read_text()
    assert "md_records WHERE domain='configuration'" in src
    assert 'CREATE TABLE' not in src
    assert 'BOOKING_COUNT' in src
    assert 'CONTAINER_COUNT' in src
    assert 'TEU' in src
    assert 'server_side_enforcement' in src

def test_booking_server_side_agent_rule_enforcement_hook():
    src=Path('app/main.py').read_text()
    assert "from .agent_setup import router as agent_setup_router, assert_booking_rules" in src
    assert "if module=='booking': assert_booking_rules(conn,ctx,body.fields,None)" in src
    assert "if module=='booking': assert_booking_rules(conn,ctx,payload,tid)" in src
    assert "'booking','trt'" in src
    assert 'CRT_REQUIRED_AFTER_APPROVAL' in src

def test_agent_setup_ui_uses_existing_masters_only():
    html=Path('web/index.html').read_text()
    for x in [
        "label:'Agent Relation'",
        "label:'Agent Volume Restriction'",
        "label:'Agent Network'",
        "workspace:'agent-relation'",
        "workspace:'agent-volume-restriction'",
        "workspace:'agent-network'",
        "master-data::agents",
        "master-data::configurations",
        "Equipment Network remains separate and unchanged",
    ]:
        assert x in html,x
    assert 'pa_parties' not in html.lower()
    assert 'CREATE TABLE' not in html
