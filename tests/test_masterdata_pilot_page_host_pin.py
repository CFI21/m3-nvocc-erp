"""Host-pinned Neon UAT page access regression."""
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from app import uat_approval_page as page

def _env(host):
    return {"M3_PRODUCTION_TRAFFIC":"OFF","M3_UAT_PRESERVE_DATA":"1"}

def test_masterdata_page_accepts_only_pinned_approved_neon():
    db=page.db
    with patch.dict(page.os.environ,_env("ignored")), patch.object(db,"using_postgres",return_value=True), patch.object(db,"_uat_target_approved",return_value=True), patch.object(db,"DATABASE_URL","postgresql://user:pass@ep-pilot-pooler.c-6.us-east-2.aws.neon.tech/neondb"), patch.object(db,"M3_UAT_DB_HOST","ep-pilot-pooler.c-6.us-east-2.aws.neon.tech"):
        assert "Isolated Master Data approval UAT" in page.uat_approval_page().body.decode()

def test_masterdata_page_denies_other_host_even_if_other_checks_pass():
    db=page.db
    with patch.dict(page.os.environ,_env("ignored")), patch.object(db,"using_postgres",return_value=True), patch.object(db,"_uat_target_approved",return_value=True), patch.object(db,"DATABASE_URL","postgresql://user:pass@ep-other-pooler.c-6.us-east-2.aws.neon.tech/neondb"), patch.object(db,"M3_UAT_DB_HOST","ep-pilot-pooler.c-6.us-east-2.aws.neon.tech"):
        with pytest.raises(HTTPException) as exc:
            page.uat_approval_page()
        assert exc.value.status_code==404

def test_masterdata_page_requires_approved_target():
    db=page.db
    with patch.dict(page.os.environ,_env("ignored")), patch.object(db,"using_postgres",return_value=True), patch.object(db,"_uat_target_approved",return_value=False), patch.object(db,"DATABASE_URL","postgresql://user:pass@ep-pilot-pooler.c-6.us-east-2.aws.neon.tech/neondb"), patch.object(db,"M3_UAT_DB_HOST","ep-pilot-pooler.c-6.us-east-2.aws.neon.tech"):
        with pytest.raises(HTTPException) as exc:
            page.uat_approval_page()
        assert exc.value.status_code==404
