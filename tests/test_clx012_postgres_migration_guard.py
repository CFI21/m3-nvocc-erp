"""Regression guards for isolated CLX-012 schema initialization."""
import sqlite3
from unittest.mock import patch

import pytest

from app import business_day


class FakePostgres:
    backend = "postgres"


def test_postgres_missing_migration_fails_before_runtime_sql():
    with patch("app.db.table_exists", return_value=False):
        with pytest.raises(RuntimeError, match="CLX012_MIGRATION_REQUIRED"):
            business_day.init_schema(FakePostgres())


def test_postgres_migrated_schema_avoids_sqlite_executescript():
    with patch("app.db.table_exists", return_value=True), patch.object(
        business_day, "trigger_exists", return_value=True
    ):
        business_day.init_schema(FakePostgres())


def test_postgres_missing_immutability_trigger_fails_closed():
    with patch("app.db.table_exists", return_value=True), patch.object(
        business_day, "trigger_exists", return_value=False
    ):
        with pytest.raises(RuntimeError, match="immutable event triggers"):
            business_day.init_schema(FakePostgres())


def test_sqlite_test_runtime_still_bootstraps_business_day_schema():
    conn = sqlite3.connect(":memory:")
    try:
        business_day.init_schema(conn)
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'clx012_%'"
        )}
        assert names == {
            "clx012_runs", "clx012_stage_events", "clx012_exceptions",
            "clx012_action_queue", "clx012_events",
        }
    finally:
        conn.close()
