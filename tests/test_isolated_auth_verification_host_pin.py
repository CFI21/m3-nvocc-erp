"""Fail-closed host checks for isolated login and maker/checker UAT pages."""
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from app import main, db

PILOT = "ep-pilot-pooler.c-6.us-east-2.aws.neon.tech"

@pytest.mark.parametrize("page", [
    main.isolated_uat_login_verify_page,
    main.isolated_uat_maker_checker_page,
])
def test_verification_page_only_on_pinned_approved_neon(page):
    env={"M3_PRODUCTION_TRAFFIC":"OFF","M3_UAT_PRESERVE_DATA":"1"}
    with patch.dict(main.os.environ,env), patch.object(db,"using_postgres",return_value=True), patch.object(db,"_uat_target_approved",return_value=True), patch.object(db,"M3_UAT_DB_HOST",PILOT), patch.object(db,"DATABASE_URL",f"postgresql://x:y@{PILOT}/neondb"):
        response=page()
        assert response.status_code==200
        assert "<html" in response.body.decode().lower()

@pytest.mark.parametrize("page", [
    main.isolated_uat_login_verify_page,
    main.isolated_uat_maker_checker_page,
])
@pytest.mark.parametrize("host,approved", [
    ("ep-unapproved-pooler.c-6.us-east-2.aws.neon.tech",True),
    (PILOT,False),
])
def test_verification_page_fails_closed(page,host,approved):
    env={"M3_PRODUCTION_TRAFFIC":"OFF","M3_UAT_PRESERVE_DATA":"1"}
    with patch.dict(main.os.environ,env), patch.object(db,"using_postgres",return_value=True), patch.object(db,"_uat_target_approved",return_value=approved), patch.object(db,"M3_UAT_DB_HOST",PILOT), patch.object(db,"DATABASE_URL",f"postgresql://x:y@{host}/neondb"):
        with pytest.raises(HTTPException) as exc:
            page()
        assert exc.value.status_code==404
