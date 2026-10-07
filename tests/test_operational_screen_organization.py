from pathlib import Path
from app.screen_catalog import build_catalog

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def test_operational_layout_contract_is_present_without_screen_growth():
    c=build_catalog()
    assert c['screen_count']==196
    for marker in [
        'opHeader','opContext','opSectionNav','opFollow','opGridToolbar','opGridWrap',
        'Operational follow-up','BASIC','PARTIES','ROUTING','CARGO / EQUIPMENT',
        'DOCUMENTS','COST / REVENUE','FOLLOW-UP','AUDIT'
    ]:
        assert marker in HTML

def test_grid_is_cleaner_and_limited_by_default():
    assert "keys=keys.slice(0,8)" in HTML
    assert 'Search reference / party / status...' in HTML
    assert 'max-height:58vh' in HTML

def test_dense_forms_are_grouped_not_four_column_newspaper_layout():
    assert '.clx78Fields,.clx78Commercial .clx78Fields{grid-template-columns:repeat(2' in HTML
    assert '.clx82Grid{grid-template-columns:repeat(2' in HTML
    assert '.clx78MainGrid{grid-template-columns:1fr!important}' in HTML

def test_followup_reuses_existing_authoritative_screens():
    for sid in [
        'agent-tasks::booking','agent-tasks::bl','agent-tasks::cro',
        'agent-tasks::trt','agent-tasks::container-activity',
        'agent-tasks::delivery-order','agent-tasks::soa'
    ]:
        assert sid in HTML

def test_visible_reference_setup_flow_is_navigation_only():
    assert "▾ Setup <span class=count>'+visibleSetup.length+'</span>" in HTML
    assert 'Account Setup' in HTML
    assert "gl-accounts::account-integration" in HTML
    assert 'Container Coding' not in HTML
    assert "showEquipmentWorkspace('containers')" in HTML
    assert 'Equipment Size & Type' in HTML
    assert "master-data::equipment-types" in HTML


def test_detention_process_and_collection_are_distinct_existing_frontend_stages():
    assert "function renderDetentionProcess" in HTML
    assert "function renderDetentionCollection" in HTML
    assert "Detention Process" in HTML
    assert "Detention Collection" in HTML
    assert "Sale Invoice Detention" in HTML  # existing catalog alias is presentation-normalized at init
    assert "Create Draft Invoice → Finance" in HTML
    assert "Receivable / follow-up stage for Agent → Customer only." in HTML
    assert "Principal → Agent uses approved COST detention tariffs." in HTML
    assert "Agent → Customer uses approved REVENUE detention tariffs" in HTML
    assert "agent-tasks::detention-collection" in HTML
    assert "gl-accounts::invoice" in HTML
    assert "gl-accounts::receipt" in HTML
    assert "agent-tasks::soa" in HTML
