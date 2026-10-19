"""Safety tests for read-only Neon UAT restore evidence."""
from unittest.mock import patch
import pytest
from scripts.verify_neon_uat_restore import validate_pair, inspect, TABLES

SRC="postgresql://user:password@ep-source.c-6.us-east-2.aws.neon.tech/neondb"
DST="postgresql://user:password@ep-restored.c-6.us-east-2.aws.neon.tech/neondb"

def test_rejects_same_database_and_host():
    with patch.dict("os.environ", {"M3_UAT_PRESERVE_DATA":"1","M3_PRODUCTION_TRAFFIC":"OFF"}):
        with pytest.raises(ValueError, match="distinct"):
            validate_pair(SRC,SRC)

def test_rejects_non_neon_destination():
    with patch.dict("os.environ", {"M3_UAT_PRESERVE_DATA":"1","M3_PRODUCTION_TRAFFIC":"OFF"}):
        with pytest.raises(ValueError, match="isolated Neon"):
            validate_pair(SRC,"postgresql://user:password@database.example.org/neondb")

def test_requires_preservation_and_production_off():
    with patch.dict("os.environ", {"M3_UAT_PRESERVE_DATA":"0","M3_PRODUCTION_TRAFFIC":"OFF"}):
        with pytest.raises(ValueError, match="preservation"):
            validate_pair(SRC,DST)
    with patch.dict("os.environ", {"M3_UAT_PRESERVE_DATA":"1","M3_PRODUCTION_TRAFFIC":"ON"}):
        with pytest.raises(ValueError, match="preservation"):
            validate_pair(SRC,DST)

def test_accepts_distinct_neon_hosts_with_guardrails():
    with patch.dict("os.environ", {"M3_UAT_PRESERVE_DATA":"1","M3_PRODUCTION_TRAFFIC":"OFF"}):
        validate_pair(SRC,DST)
