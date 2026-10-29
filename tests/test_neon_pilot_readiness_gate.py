"""Regression for CLX-016 host-pinned Neon UAT readiness gate."""
from unittest.mock import patch
from app import main


class _RowResult:
    def fetchone(self):
        return {"n": 5}


class _Conn:
    def execute(self, sql):
        assert "COUNT(*)" in sql and "jobs" in sql
        return _RowResult()

    def close(self):
        pass


def _check(uat: bool, project_ref: str):
    with (
        patch.object(main, "connect", return_value=_Conn()),
        patch.object(main, "database_health", return_value={"status": "ok"}),
        patch.object(main, "approved_database_target", return_value=True),
        patch.object(main, "_uat_target_approved", return_value=uat),
        patch.dict(main.os.environ, {
            "M3_SUPABASE_PROJECT_REF": project_ref,
            "M3_PRODUCTION_TRAFFIC": "OFF",
            "M3_LIVE_PROVIDERS": "OFF",
            "REAL_MONEY": "OFF",
        }),
    ):
        return main.clx016_readiness()


def test_pinned_neon_uat_does_not_require_supabase_ref():
    assert _check(True, "")["ready"] is True


def test_other_target_does_not_bypass_supabase_ref():
    assert _check(False, "")["ready"] is False


def test_existing_production_reference_check_remains():
    assert _check(False, main.APPROVED_PROJECT_REF)["ready"] is True


def test_pinned_neon_uat_still_requires_db_target_approval():
    with (
        patch.object(main, "connect", return_value=_Conn()),
        patch.object(main, "database_health", return_value={"status": "ok"}),
        patch.object(main, "approved_database_target", return_value=False),
        patch.object(main, "_uat_target_approved", return_value=True),
        patch.dict(main.os.environ, {
            "M3_SUPABASE_PROJECT_REF": "",
            "M3_PRODUCTION_TRAFFIC": "OFF",
            "M3_LIVE_PROVIDERS": "OFF",
            "REAL_MONEY": "OFF",
        }),
    ):
        assert main.clx016_readiness()["ready"] is False
