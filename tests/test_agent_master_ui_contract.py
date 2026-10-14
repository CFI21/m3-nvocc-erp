"""Regression contract for the existing M3 Agent Master six-tab UI.

These are static source checks, not a replacement for browser acceptance.
"""
from pathlib import Path
import re

SOURCE = Path(__file__).resolve().parents[1] / "web" / "index.html"


def agent_source():
    return SOURCE.read_text(encoding="utf-8")


def test_agent_master_six_tab_contract():
    source = agent_source()
    match = re.search(r"const m3AgentTabs\s*=\s*\[([^\]]+)\]", source)
    assert match, "Agent Master tabs declaration missing"
    tabs = re.findall(r"'([^']+)'", match.group(1))
    assert tabs == [
        "Agent Detail", "Emails", "Countries", "Offices",
        "CRO Detail", "B/L Template",
    ]
    for tab in tabs:
        assert f"'{tab}':[" in source, f"Missing field map for {tab}"


def test_agent_switch_only_prompts_for_real_unsaved_changes():
    source = agent_source()
    assert "function m3AgentHasChanges(row)" in source
    assert "if(i===m3AgentIndex)return" in source
    assert "m3AgentHasChanges(m3AgentRows[m3AgentIndex])" in source
    assert "Object.keys(m3AgentDraft).length&&!confirm" not in source


def test_agent_boolean_compare_applies_to_dirty_and_submit_paths():
    source = agent_source()
    dirty = source.split("function m3AgentHasChanges(row)", 1)[1].split(
        "function m3AgentSelect(i)", 1
    )[0]
    submit = source.split("async function m3AgentSubmit()", 1)[1].split(
        "async function renderHoAgentMasterWorkspace()", 1
    )[0]
    assert "m3AgentBooleanKeys.has(key)" in dirty
    assert "m3AgentBooleanKeys.has(key)" in submit
    assert "payload[key]=" in submit
    assert "operation:'UPDATE'" in submit


def test_new_agent_uses_existing_approval_queue():
    source = agent_source()
    assert "id=\"m3AgentNew\"" in source
    assert "async function m3AgentCreate()" in source
    create = source.split("async function m3AgentCreate()", 1)[1].split(
        "async function m3AgentSubmit()", 1
    )[0]
    assert "operation:'CREATE'" in create
    assert "domain:'agent'" in create
    assert "'/api/masterdata/changes'" in create
    assert "record_key:code" in create

 
def test_pending_new_agents_have_checker_decision_controls():
    source = agent_source()
    assert "New Agents awaiting approval" in source
    assert "data-agent-create-decision" in source
    assert "data-agent-create-ref" in source
    assert "async function m3AgentDecide(decision,changeRef)" in source
    assert "changeRef?x.change_ref===changeRef" in source
    assert "canApprove?" in source


def test_agent_master_ui_roles_align_with_authorized_master_reads():
    source = agent_source()
    segment = source.split('let m3AgentRows=', 1)[1].split(
        'async function renderHoAgentMasterWorkspace()', 1
    )[0]
    assert "['ADMIN','SUPER_ADMIN','MASTER_DATA','MASTER_DATA_MANAGER'].includes(state.role)" in segment
    assert "['SUPER_ADMIN','MASTER_DATA','MASTER_DATA_MANAGER','OPS','FINANCE'].includes(state.role)" not in segment


def test_approved_agent_selected_for_immediate_readback():
    source = agent_source()
    assert "if(decision==='APPROVE')m3AgentFocusCode=change.record_key" in source
    assert "const selected=m3AgentFocusCode||m3AgentRows[m3AgentIndex]?.record_key" in source
    assert "m3AgentFocusCode='';m3AgentDiscardDraft();m3AgentPaint()" in source


def test_new_agent_and_refresh_protect_unsaved_edits():
    source = agent_source()
    assert "Discard unsaved Agent edits and refresh?" in source
    assert "Discard unsaved Agent edits and create a new Agent?" in source
    assert "m3AgentCaptureDraft();if(m3AgentHasChanges(m3AgentRows[m3AgentIndex])" in source


def test_email_and_bl_reference_are_visible_in_six_tab_readback():
    source = agent_source()
    assert "Configured Agent email destinations" in source
    assert "esc(String(p[k]))" in source
    assert "Existing B/L template reference" in source
    assert "esc(String(p.bl_template||'Not assigned'))" in source
    assert "No template is created or assigned automatically" in source


def test_agent_email_fields_editable_in_emails_tab():
    source = agent_source()
    assert "m3AgentTab==='Emails'&&!m3AgentEditing" in source
    assert "['email','export_email','import_email']" in source
    assert "m3AgentField(k,p)" in source


def test_agent_office_and_country_views_use_governed_relations():
    source = agent_source()
    assert "AGENT_OFFICE_ASSIGNMENT" in source
    assert "AGENT_NETWORK" in source
    assert "Configured port coverage" in source
    assert "Countries derive from approved office assignment records" in source
    assert "Office assignment never overrides IAM membership" in source
    assert "No explicit assignments recorded" in source


def test_agent_cro_and_bl_use_existing_master_values():
    source = agent_source()
    assert "'CRO Detail':['bs_code','operator_code','cro_remarks','noc_note']" in source
    assert "CRO values are governed Agent Master fields" in source
    assert "'B/L Template':['bl_template']" in source
    assert "Existing B/L template reference" in source
    assert "No template is created or assigned automatically" in source


def test_agent_relationship_requests_use_governed_change_approval():
    source = agent_source()
    related = source.split("async function m3AgentRelatedCreate()", 1)[1].split(
        "async function m3AgentDecide(", 1
    )[0]
    assert "AGENT_EMAIL" in related
    assert "AGENT_OFFICE_ASSIGNMENT" in related
    assert "/api/masterdata/changes" in related
    assert "operation:'CREATE'" in related


def test_role_switch_keeps_governed_agent_workspace():
    source = agent_source()
    assert "location.hash.startsWith('#ho-setup=')" in source
    assert "await openHoGovernedSetup(workspace,state.navContext?.label||state.current.name,state.current.screen_id)" in source
    assert "history.replaceState(null,'','#ho-setup='+encodeURIComponent(workspace))" in source


def test_admin_ui_alias_shows_agent_master_controls_without_changing_backend_auth():
    source = agent_source()
    assert "['ADMIN','SUPER_ADMIN','MASTER_DATA','MASTER_DATA_MANAGER'].includes(state.role)" in source
    assert "['ADMIN','SUPER_ADMIN','MASTER_DATA_MANAGER'].includes(state.role)" in source
    assert "'/api/masterdata/changes'" in source


def test_preview_switch_user_requires_fresh_iam_login():
    source=agent_source()
    assert '<button onclick="m3SwitchPreviewUser()">Switch M3 User</button>' in source
    switch=source.split('function m3SwitchPreviewUser(){',1)[1].split('async function m3EnsureIamLogin()',1)[0]
    assert "sessionStorage.removeItem('M3_SESSION')" in switch
    assert "sessionStorage.removeItem('M3_SESSION_TOKEN')" in switch
    assert "location.reload()" in switch
    assert "M3_UAT_TOKEN" not in switch
    assert "await m3EnsureIamLogin();" in source
