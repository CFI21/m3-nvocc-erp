"""Readiness must fail safely when isolated PostgreSQL UAT fixtures are absent."""
from unittest.mock import patch
from fastapi import HTTPException
import pytest
from app import preprod

def test_missing_canonical_jobs_returns_actionable_503():
    with patch.object(preprod, "using_postgres", return_value=True), patch.object(
        preprod, "readiness_status", side_effect=HTTPException(404, "Unknown job")
    ):
        with pytest.raises(HTTPException) as caught:
            preprod.readiness()
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "UAT_CANONICAL_JOBS_NOT_SEEDED"

def test_other_http_404_is_not_rewritten():
    with patch.object(preprod, "using_postgres", return_value=True), patch.object(
        preprod, "readiness_status", side_effect=HTTPException(404, "Other resource")
    ):
        with pytest.raises(HTTPException) as caught:
            preprod.readiness()
    assert caught.value.status_code == 404
    assert caught.value.detail == "Other resource"

def test_sqlite_missing_jobs_remains_configuration_error():
    import sqlite3
    with patch.object(preprod, "using_postgres", return_value=False), patch.object(
        preprod, "readiness_status", side_effect=sqlite3.OperationalError("no such table: jobs")
    ):
        with pytest.raises(HTTPException) as caught:
            preprod.readiness()
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "ISOLATED_UAT_DATABASE_NOT_CONFIGURED"
