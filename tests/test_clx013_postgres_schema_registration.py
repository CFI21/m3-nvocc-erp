"""PostgreSQL CLX013 initialization must be additive and idempotent."""
from unittest.mock import patch
from app import preprod

class Conn:
    def __init__(self):
        self.statements=[]
    def execute(self,sql,params=()):
        self.statements.append(sql)
        assert "DROP " not in sql.upper()
        assert "ON CONFLICT" in sql.upper()

def test_postgres_init_checks_tables_and_initializes_metadata():
    conn=Conn()
    with patch.object(preprod,"using_postgres",return_value=True),patch.object(
        preprod,"db_table_exists",return_value=True
    ):
        preprod.init_schema(conn)
        preprod.init_schema(conn)
    assert len(conn.statements)==4
    assert all("INSERT INTO clx013_" in q for q in conn.statements)

def test_postgres_missing_schema_fails_closed():
    conn=Conn()
    with patch.object(preprod,"using_postgres",return_value=True),patch.object(
        preprod,"db_table_exists",side_effect=lambda c,t: t!="clx013_schema_versions"
    ):
        import pytest
        with pytest.raises(RuntimeError,match="CLX013_POSTGRES_MIGRATION_REQUIRED"):
            preprod.init_schema(conn)
    assert conn.statements==[]
