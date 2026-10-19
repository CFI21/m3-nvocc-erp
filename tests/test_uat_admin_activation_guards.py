"""Fail-closed checks for the one-time isolated UAT Admin activation."""
from unittest.mock import patch
import pytest

from scripts import activate_uat_admin as activate

def test_rejects_production_traffic_before_database_connect():
    with patch.dict("os.environ", {"M3_PRODUCTION_TRAFFIC": "ON"}), \
         patch.object(activate.db, "connect") as connect:
        with pytest.raises(RuntimeError, match="Production traffic enabled"):
            activate.main()
        connect.assert_not_called()

def test_rejects_non_postgresql_before_database_connect():
    with patch.dict("os.environ", {"M3_PRODUCTION_TRAFFIC": "OFF"}), \
         patch.object(activate.db, "using_postgres", return_value=False), \
         patch.object(activate.db, "connect") as connect:
        with pytest.raises(RuntimeError, match="Host-pinned"):
            activate.main()
        connect.assert_not_called()

def test_rejects_unapproved_database_before_database_connect():
    with patch.dict("os.environ", {"M3_PRODUCTION_TRAFFIC": "OFF"}), \
         patch.object(activate.db, "using_postgres", return_value=True), \
         patch.object(activate.db, "_uat_target_approved", return_value=False), \
         patch.object(activate.db, "connect") as connect:
        with pytest.raises(RuntimeError, match="Host-pinned"):
            activate.main()
        connect.assert_not_called()

def test_rejects_wrong_neon_endpoint_before_database_connect():
    url="postgresql://neondb_owner:dummy@ep-other.c-6.us-east-2.aws.neon.tech/neondb"
    with patch.dict("os.environ", {"M3_PRODUCTION_TRAFFIC": "OFF"}), \
         patch.object(activate.db, "using_postgres", return_value=True), \
         patch.object(activate.db, "_uat_target_approved", return_value=True), \
         patch.object(activate.db, "DATABASE_URL", url), \
         patch.object(activate.db, "connect") as connect:
        with pytest.raises(RuntimeError, match="Incorrect UAT Neon endpoint"):
            activate.main()
        connect.assert_not_called()
