from pathlib import Path

def test_clx072_router_is_mounted():
    import app.main as main
    paths=set(main.app.openapi()["paths"])
    required={
      "/api/clx072/network/policies",
      "/api/clx072/network/targets",
      "/api/clx072/network/targets/{target_ref}/decision",
      "/api/clx072/network/position",
      "/api/clx072/network/demand-forecast",
      "/api/clx072/network/recommendations",
      "/api/clx072/network/recommendations/work-item",
      "/api/clx072/network/action-queue",
      "/api/clx072/network/aging",
      "/api/clx072/network/kpis",
    }
    assert required <= paths

def test_recommendation_prioritizes_internal_reposition():
    from app.clx072_equipment_network import recommendations
    p=[
      {"port":"KHI","branch":"","agent":"","depot":"","size_type":"40HC","shortage":0,"surplus":5,"leased":1},
      {"port":"DXB","branch":"","agent":"","depot":"","size_type":"40HC","shortage":3,"surplus":0,"leased":0},
    ]
    r=recommendations(p)
    assert r[0]["recommendation_type"]=="REPOSITION"
    assert r[0]["qty"]==3
    assert r[0]["source_port"]=="KHI"
    assert r[0]["destination_port"]=="DXB"
    assert not any(x["recommendation_type"]=="LEASE_ON_HIRE" for x in r)

def test_residual_shortage_becomes_lease_on_hire():
    from app.clx072_equipment_network import recommendations
    p=[
      {"port":"KHI","branch":"","agent":"","depot":"","size_type":"20GP","shortage":0,"surplus":1,"leased":0},
      {"port":"JEA","branch":"","agent":"","depot":"","size_type":"20GP","shortage":4,"surplus":0,"leased":0},
    ]
    r=recommendations(p)
    assert [x["recommendation_type"] for x in r[:2]]==["REPOSITION","LEASE_ON_HIRE"]
    assert r[0]["qty"]==1 and r[1]["qty"]==3

def test_migration_does_not_create_parallel_inventory_or_action_queue():
    sql=Path("migrations/CLX072_001_equipment_network.sql").read_text()
    assert "equipment_network_targets" in sql
    assert "equipment_network_policy" in sql
    assert "CREATE TABLE IF NOT EXISTS public.containers" not in sql
    assert "CREATE TABLE IF NOT EXISTS public.equipment_work_items" not in sql
    assert "public.containers remains authoritative" in sql

def test_native_ui_contains_network_control():
    html=Path("web/index.html").read_text()
    assert "Network Control" in html
    assert "/api/clx072/network/position" in html
    assert "Stock Balancing" in html
    assert "Reposition Recommendations" in html
    assert "No separate ERP model or duplicate container inventory" in html

def test_manifest_keeps_production_locked():
    m=Path("CLX072_ACCEPTANCE_MANIFEST.json").read_text()
    assert '"production_traffic": "OFF"' in m
    assert '"live_providers": "OFF"' in m
    assert '"real_money": "OFF"' in m
    assert '"duplicate_container_inventory": false' in m
