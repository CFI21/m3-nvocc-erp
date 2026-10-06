import datetime

from app.operations_workbench import _priority, _parse_dt, _age, PRIORITY_ORDER


def test_priority_model():
    assert _priority("PAYMENT_RELEASE_BLOCK")=="HIGH"
    assert _priority("FAILED_INTEGRATION")=="HIGH"
    assert _priority("DOCUMENT_GAP")=="MEDIUM"
    assert PRIORITY_ORDER["CRITICAL"] < PRIORITY_ORDER["HIGH"] < PRIORITY_ORDER["MEDIUM"]


def test_cutoff_datetime_parser():
    d=_parse_dt("2026-09-26T10:00:00+00:00")
    assert d is not None
    assert d.tzinfo is not None
    assert _parse_dt("not-a-date") is None


def test_age_is_never_negative():
    future=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=5)).isoformat()
    assert _age(future)==0


def test_workbench_source_preserves_underlying_contract():
    from app.operations_workbench import BulkAction, READ_ROLES, WRITE_ROLES
    body=BulkAction(exception_keys=["JOB:1:DOC"],owner="ops-1")
    assert body.exception_keys==["JOB:1:DOC"]
    assert "AGENT" in READ_ROLES
    assert "AGENT" not in WRITE_ROLES
    assert {"ADMIN","OPS","DOCS","FINANCE"} <= WRITE_ROLES


def test_sync_items_tolerates_concurrent_create_without_duplicate_history():
    from app.operations_workbench import _sync_items

    key="CUT:69:SI Cut-off"

    class Cursor:
        def __init__(self,rows=None,rowcount=0):
            self._rows=rows or []
            self.rowcount=rowcount
        def fetchall(self):
            return self._rows

    class Connection:
        def __init__(self):
            self.conflict_update=False
            self.history_writes=0
        def execute(self,sql,params=()):
            compact=" ".join(sql.split())
            if compact=="SELECT * FROM operations_work_items":
                return Cursor([])
            if compact.startswith("INSERT INTO operations_work_items"):
                assert "ON CONFLICT(exception_key) DO NOTHING" in compact
                return Cursor(rowcount=0)
            if compact.startswith("UPDATE operations_work_items SET team=COALESCE(team,?)"):
                self.conflict_update=True
                return Cursor(rowcount=1)
            if compact.startswith("INSERT INTO operations_work_history"):
                self.history_writes+=1
                return Cursor(rowcount=1)
            if compact.startswith("SELECT * FROM operations_work_items WHERE source_active=1"):
                return Cursor([{"exception_key":key,"work_status":"OPEN","owner":None,"team":"OPS"}])
            raise AssertionError(compact)

    c=Connection()
    _sync_items(c,[{
        "exception_key":key,
        "category":"CUTOFF_RISK",
        "job_id":69,
        "job_ref":"26469",
        "sla_hours":4,
    }],"OPS","ops-user")

    assert c.conflict_update is True
    assert c.history_writes==0
