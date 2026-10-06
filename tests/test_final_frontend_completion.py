from pathlib import Path

from app.screen_catalog import BUSINESS_NAV_ALIASES, build_catalog

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web/index.html").read_text(encoding="utf-8")


def test_final_frontend_preserves_exact_196_authoritative_screens():
    c = build_catalog()
    assert c["screen_count"] == 196
    assert c["baseline_screen_count"] == 196
    assert c["extension_count"] == 0
    ids = [s["screen_id"] for s in c["screens"]]
    routes = [s["route"] for s in c["screens"]]
    assert len(ids) == len(set(ids)) == 196
    assert len(routes) == len(set(routes)) == 196


def test_all_business_navigation_targets_resolve_to_authoritative_screens():
    ids = {s["screen_id"] for s in build_catalog()["screens"]}
    for items in BUSINESS_NAV_ALIASES.values():
        for item in items:
            assert item["target"] in ids


def test_core_operational_flow_is_visible_and_reuses_existing_screens():
    for sid in [
        "agent-tasks::booking",
        "agent-tasks::planning",
        "agent-tasks::bl",
        "agent-tasks::cro",
        "agent-tasks::trt",
        "agent-tasks::container-activity",
        "agent-tasks::delivery-order",
        "agent-tasks::soa",
    ]:
        assert sid in HTML
    assert "const CORE_BUSINESS_FLOW=" in HTML
    assert "Operational follow-up" in HTML


def test_operational_trt_presentation_does_not_relabel_trt_as_crt():
    assert "TRT / Transshipment Master" in HTML
    assert "clx83RO('TRT No.'" in HTML
    assert "'TRT / Transshipment',jr" in HTML
    assert "'TRT / TS',crt.length" in HTML
    assert "'CLX-071 / TRT'" in HTML
    assert "CRT / Transshipment Master" not in HTML
    assert "clx83RO('CRT No.'" not in HTML
    # Legacy CRT data is still accepted as a read fallback; governance CRT remains valid.
    assert "'CRT No.'))" in HTML
    assert "existing governed change / CRT / adjustment route" in HTML


def test_permission_denied_has_dedicated_frontend_state():
    assert "e.status===401||e.status===403" in HTML
    assert "Permission denied" in HTML
    assert "Your current M3 role or data scope does not allow this screen or record." in HTML


def test_loading_empty_error_and_responsive_containment_states_exist():
    assert "Loading active screen" in HTML
    assert "No records for this screen / filter." in HTML
    assert "Error loading screen" in HTML
    assert ".tablewrap{background:#fff;border:1px solid var(--line);overflow:auto" in HTML
    assert ".opGridWrap{background:#fff;border:1px solid var(--line);overflow:auto" in HTML
    assert "@media(max-width:950px)" in HTML
    assert "@media(max-width:1250px)" in HTML


def test_master_ui_and_major_frontend_workspaces_remain_present():
    for marker in [
        "Booking Basic & Routing",
        "B/L Basic Info & Routing",
        "CRO Basic Info / Routing",
        "Delivery Order Master",
        "Operations Workbench",
        "Control Tower",
        "Management KPI",
        "Global Stock",
        "Depot &amp; M&amp;R",
        "Financials",
        "Exceptions",
    ]:
        assert marker in HTML
