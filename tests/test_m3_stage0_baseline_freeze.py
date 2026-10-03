import json
from pathlib import Path

from app.screen_catalog import BUSINESS_NAV_ALIASES, build_catalog


def test_stage0_screen_baseline_is_governed_not_hard_capped():
    catalog = build_catalog()
    assert catalog["baseline_screen_count"] == 196
    assert catalog["screen_count"] >= 196
    assert catalog["extension_count"] == max(0, catalog["screen_count"] - 196)
    assert "not a permanent hard limit" in catalog["screen_count_policy"]


def test_stage0_screen_ids_and_routes_are_unique():
    screens = build_catalog()["screens"]
    ids = [s["screen_id"] for s in screens]
    routes = [s["route"] for s in screens]
    assert len(ids) == len(set(ids))
    assert len(routes) == len(set(routes))


def test_stage0_business_navigation_targets_are_authoritative():
    catalog = build_catalog()
    ids = {s["screen_id"] for s in catalog["screens"]}
    for items in BUSINESS_NAV_ALIASES.values():
        for item in items:
            assert item["target"] in ids


def test_stage0_extension_governance_remains_strict():
    governance = build_catalog()["extension_governance"]
    assert governance["placeholder_screens_forbidden"] is True
    assert governance["duplicate_models_forbidden"] is True
    assert "existing screen first" in governance["field"]
    assert "verified distinct business workspace" in governance["screen"]


def test_stage0_safety_acceptance_flags_are_non_production():
    payload = json.loads(Path("M3_NVOCC_PRINCIPAL_GAP_ACCEPTANCE.json").read_text(encoding="utf-8"))
    assert payload["real_money"] is False
    assert payload["dummy_bank"] == "SIMULATION_ONLY"
    assert payload["providers"] == {"TRUE_LAYER": False, "OXR": False, "AVALARA": False}
    assert payload["ancline_touched"] is False


def test_stage0_no_ai_approval_identity_is_present_in_catalog_roles():
    roles = {
        role
        for screen in build_catalog()["screens"]
        for role in screen.get("roles", [])
    }
    forbidden = {"AI", "AI_APPROVER", "AI_SERVICE", "AUTONOMOUS_AI"}
    assert roles.isdisjoint(forbidden)
