from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_clx014_runtime_migration_exists_in_repo():
    migration = ROOT / "migrations" / "CLX014_001_production_prep.sql"
    assert migration.exists()
    text = migration.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS clx014_cert_runs" in text


def test_docker_image_packages_runtime_migrations():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY app ./app" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
