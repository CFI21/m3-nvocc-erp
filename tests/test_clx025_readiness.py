from types import SimpleNamespace
from app import main


class _Row(dict):
    def __getattr__(self, name):
        return self[name]


class _Conn:
    def execute(self, sql, params=None):
        return SimpleNamespace(fetchone=lambda: _Row(n=5))
    def close(self):
        pass


def test_readiness_accepts_controlled_live_traffic_when_external_execution_is_off(monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    monkeypatch.setenv('M3_LIVE_PROVIDERS','OFF')
    monkeypatch.setenv('REAL_MONEY','OFF')
    monkeypatch.setenv('M3_SUPABASE_PROJECT_REF',main.APPROVED_PROJECT_REF)
    monkeypatch.setattr(main,'connect',lambda:_Conn())
    monkeypatch.setattr(main,'database_health',lambda conn:{'status':'ok'})
    monkeypatch.setattr(main,'approved_database_target',lambda:True)
    r=main.clx016_readiness()
    assert r['production_traffic']=='ON'
    assert r['traffic_mode']=='LIVE'
    assert r['infrastructure_ready'] is True
    assert r['external_execution_safe'] is True
    assert r['ready'] is True


def test_readiness_accepts_locked_traffic_as_ready_when_core_is_healthy(monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','OFF')
    monkeypatch.setenv('M3_LIVE_PROVIDERS','OFF')
    monkeypatch.setenv('REAL_MONEY','OFF')
    monkeypatch.setenv('M3_SUPABASE_PROJECT_REF',main.APPROVED_PROJECT_REF)
    monkeypatch.setattr(main,'connect',lambda:_Conn())
    monkeypatch.setattr(main,'database_health',lambda conn:{'status':'ok'})
    monkeypatch.setattr(main,'approved_database_target',lambda:True)
    r=main.clx016_readiness()
    assert r['traffic_mode']=='LOCKED'
    assert r['ready'] is True


def test_readiness_fails_safe_when_external_execution_is_enabled(monkeypatch):
    monkeypatch.setenv('M3_PRODUCTION_TRAFFIC','ON')
    monkeypatch.setenv('M3_LIVE_PROVIDERS','ON')
    monkeypatch.setenv('REAL_MONEY','OFF')
    monkeypatch.setenv('M3_SUPABASE_PROJECT_REF',main.APPROVED_PROJECT_REF)
    monkeypatch.setattr(main,'connect',lambda:_Conn())
    monkeypatch.setattr(main,'database_health',lambda conn:{'status':'ok'})
    monkeypatch.setattr(main,'approved_database_target',lambda:True)
    r=main.clx016_readiness()
    assert r['infrastructure_ready'] is True
    assert r['external_execution_safe'] is False
    assert r['ready'] is False
