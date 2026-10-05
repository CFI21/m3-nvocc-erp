from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def test_single_shell_and_master_pattern():
    assert 'CLX-078 UI Consolidation Review' in HTML
    assert 'clx78Workspace' in HTML
    assert 'Commercial Ownership / Financial Parties' in HTML
    assert "['Equipment','Agreed Charges','Revenue','Cost','Financial Auto','Preview']" in HTML
    assert 'FULL FLOW' in HTML
    assert 'Operational Control' in HTML

def test_booking_uses_existing_authoritative_apis():
    assert "/api/v1/'+module+'/" in HTML or "/api/v1/booking/" in HTML
    assert '/api/clx070/container-control/booking-context/' in HTML
    assert '/api/gl/job/' in HTML
    assert '/api/clx046/job/' in HTML
    assert 'no parallel Booking model' in HTML

def test_pot_is_conditional():
    assert "POT Agent" in HTML
    assert "pot.disabled=!(via||trans)" in HTML

def test_global_equipment_tower_is_ho_native():
    assert 'HO Tasks / Equipment Control' in HTML
    assert 'Global Equipment Tower' in HTML
    for route in [
        '/api/clx070/container-control/kpis',
        '/api/clx071/journey/kpis',
        '/api/clx072/network/kpis?horizon_days=14',
        '/api/clx073/optimization/kpis',
        '/api/clx074/mr/kpis',
        '/api/clx075/lease/kpis'
    ]:
        assert route in HTML

def test_no_real_money_or_provider_activation_change():
    acc=json.loads((ROOT/'CLX078_LIVE_UI_ACCEPTANCE.json').read_text())
    assert acc['REAL_MONEY_READY'] is False
    assert acc['REAL_PROVIDER_READY'] is False

def test_canonical_196_contract_preserved():
    matrix=json.loads((ROOT/'CLX078_UI_SCREEN_MATRIX.json').read_text())
    assert matrix['canonical_screen_count']==196
    assert matrix['canonical_ids_preserved'] is True
    assert matrix['canonical_routes_preserved'] is True
