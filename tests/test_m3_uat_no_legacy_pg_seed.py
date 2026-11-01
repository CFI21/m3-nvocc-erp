"""PostgreSQL may never run legacy destructive or synthetic seed routines."""
import pytest
from app import seed, admin_seed, masterdata_seed


@pytest.mark.parametrize("entry", [seed.run, admin_seed.run, masterdata_seed.run])
def test_postgres_legacy_seed_fails_closed(monkeypatch, entry):
    from app import db
    monkeypatch.setattr(db, "DATABASE_URL", "postgresql://no-network.invalid/uat")
    with pytest.raises(RuntimeError, match="POSTGRES_.*SEED_FORBIDDEN"):
        entry()


def test_sqlite_seed_requires_preservation_guard(monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DATABASE_URL", "")
    monkeypatch.setenv("M3_UAT_PRESERVE_DATA", "1")
    with pytest.raises(RuntimeError, match="UAT_DATA_PROTECTION"):
        seed.run(reset=True)
