import datetime

from app.operations_workbench import _default_team, _view_filter, _parse_dt


def test_default_team_routing():
    assert _default_team("DOCUMENT_GAP")=="DOCS"
    assert _default_team("PAYMENT_RELEASE_BLOCK")=="FINANCE"
    assert _default_team("FAILED_INTEGRATION")=="OPS"
    assert _default_team("MILESTONE_DELAY")=="OPS"


def test_governed_views():
    rows=[
      {"owner":"alice","team":"OPS","priority":"CRITICAL","status":"OPEN","escalation_state":"OVERDUE"},
      {"owner":"bob","team":"FINANCE","priority":"HIGH","status":"IN_PROGRESS","escalation_state":"NONE"},
      {"owner":"alice","team":"OPS","priority":"LOW","status":"CLOSED","escalation_state":"NONE"},
    ]
    assert len(_view_filter(rows,"my","alice",None))==2
    assert len(_view_filter(rows,"team",None,"OPS"))==2
    assert len(_view_filter(rows,"overdue",None,None))==1
    assert len(_view_filter(rows,"critical",None,None))==1


def test_iso_sla_due_date_parse():
    d=_parse_dt((datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=4)).isoformat())
    assert d is not None and d.tzinfo is not None
