import datetime
from pathlib import Path

from app.operations_workbench import _target_screen, _view_filter
from app.screen_catalog import build_catalog

ROOT=Path(__file__).resolve().parents[1]

def test_clx055_preserves_frozen_196_screen_baseline():
    catalog=build_catalog()
    assert catalog["screen_count"]==196

def test_clx055_today_view_is_additive_and_governed():
    now=datetime.datetime.now(datetime.timezone.utc)
    rows=[
      {"owner":"ui-user","team":"OPS","priority":"HIGH","status":"OPEN","escalation_state":"DUE_SOON","sla_due_at":now.isoformat()},
      {"owner":"other","team":"OPS","priority":"CRITICAL","status":"CLOSED","escalation_state":"OVERDUE","sla_due_at":now.isoformat()},
      {"owner":"other","team":"FINANCE","priority":"HIGH","status":"IN_PROGRESS","escalation_state":"NONE","sla_due_at":(now+datetime.timedelta(days=2)).isoformat()},
    ]
    today=_view_filter(rows,"today","ui-user","OPS")
    assert len(today)==1
    assert today[0]["owner"]=="ui-user"

def test_clx055_related_screen_routes_are_existing_catalog_screens():
    ids={s["screen_id"] for s in build_catalog()["screens"]}
    cases=[
      ("DOCUMENT_GAP",None),
      ("CUTOFF_RISK",None),
      ("MILESTONE_DELAY",None),
      ("PAYMENT_RELEASE_BLOCK",None),
      ("FAILED_INTEGRATION","edi"),
      ("RECONCILIATION_EXCEPTION",None),
    ]
    for category,module in cases:
        assert _target_screen(category,module) in ids

def test_clx055_ui_has_daily_views_and_same_page_actions():
    html=(ROOT/"web"/"index.html").read_text()
    for marker in [
      "CLX-055 Production Operations Workspace",
      "showOpsWorkbench('today')",
      "Assign to me",
      "Acknowledge",
      "Start",
      "Resolve",
      "openOpsRelated",
    ]:
        assert marker in html
    assert "window.open(M3_API_BASE+'\${x.target_url}'" not in html
