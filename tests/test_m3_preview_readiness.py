"""M3 isolated preview readiness validation."""
from scripts import m3_preview_readiness as probe

def test_health_rejects_production_and_real_money():
    healthy={"project":"M3 NVOCC ERP","sandbox_only":True,
             "production_promoted":False,"real_payment_execution":False}
    assert probe.validate_health(healthy)
    for key,value in [("sandbox_only",False),("production_promoted",True),
                      ("real_payment_execution",True)]:
        assert not probe.validate_health({**healthy,key:value})

def test_readiness_requires_approved_db_and_sandbox():
    ready={"ready":True,"database_health":"ok","database_target_approved":True,
           "approved_project_ref":"isolated-test-project",
           "configured_project_ref":"isolated-test-project",
           "jobs_50001_50005":5,
           "production_traffic":"OFF","live_providers":"OFF","real_money":"OFF"}
    assert probe.validate_readiness(ready)
    for key,value in [("ready",False),("database_health","fail"),
                      ("database_target_approved",False),("production_traffic","ON"),
                      ("live_providers","ON"),("real_money","ON"),
                      ("configured_project_ref","wrong-target"),
                      ("jobs_50001_50005",4)]:
        assert not probe.validate_readiness({**ready,key:value})

def test_offline_probe_blocks_unavailable_preview(monkeypatch,capsys):
    def unavailable(url):
        raise OSError("offline")
    monkeypatch.setattr(probe,"fetch",unavailable)
    assert probe.main()==1
    output=capsys.readouterr().out
    assert '"status": "BLOCKED"' in output
