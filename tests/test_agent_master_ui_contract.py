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
    assert "['SUPER_ADMIN','MASTER_DATA','MASTER_DATA_MANAGER'].includes(state.role)" in segment
    assert "['SUPER_ADMIN','MASTER_DATA','MASTER_DATA_MANAGER','OPS','FINANCE'].includes(state.role)" not in segment
