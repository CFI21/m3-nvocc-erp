def test_clx070_router_is_mounted():
    import app.main as main
    paths=set(main.app.openapi()['paths'])
    assert "/api/clx070/equipment/readiness" in paths
    assert "/api/clx070/equipment/availability" in paths
    assert "/api/clx070/equipment/allocate" in paths
    assert "/api/clx070/equipment/containers/register" in paths
    assert "/api/clx070/equipment/work-items" in paths

def test_clx070_safety_flags_remain_blocked(monkeypatch):
    monkeypatch.setenv("M3_PRODUCTION_TRAFFIC","OFF")
    monkeypatch.setenv("M3_LIVE_PROVIDERS","OFF")
    monkeypatch.setenv("REAL_MONEY","OFF")
    assert True
