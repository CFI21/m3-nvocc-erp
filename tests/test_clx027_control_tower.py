from app.control_tower import _summarize, _team_workload, _risk_jobs


def test_management_summary():
    jobs=[
      {"closed":0,"release_status":"BLOCKED","documentation_status":"SI PENDING","payment_status":"CLEARED","credit_hold":0,"outstanding":0},
      {"closed":1,"release_status":"RELEASED","documentation_status":"COMPLETE","payment_status":"CLEARED","credit_hold":0,"outstanding":0},
    ]
    items=[
      {"status":"OPEN","priority":"CRITICAL","sla_breached":True,"escalation_state":"OVERDUE","owner":None},
      {"status":"CLOSED","priority":"HIGH","sla_breached":False,"escalation_state":"NONE","owner":"x"},
    ]
    s=_summarize(jobs,items)
    assert s["jobs_total"]==2
    assert s["jobs_open"]==1
    assert s["release_blocked"]==1
    assert s["document_gaps"]==1
    assert s["active_work_items"]==1
    assert s["critical"]==1
    assert s["overdue"]==1
    assert s["unassigned"]==1


def test_team_workload():
    rows=[
      {"status":"OPEN","team":"OPS","priority":"HIGH","sla_breached":False,"escalation_state":"NONE","owner":None},
      {"status":"IN_PROGRESS","team":"OPS","priority":"CRITICAL","sla_breached":True,"escalation_state":"OVERDUE","owner":"u"},
      {"status":"CLOSED","team":"OPS","priority":"CRITICAL","sla_breached":True,"escalation_state":"OVERDUE","owner":"u"},
    ]
    t=_team_workload(rows)
    assert len(t)==1
    assert t[0]["total"]==2
    assert t[0]["critical"]==1
    assert t[0]["high"]==1
    assert t[0]["overdue"]==1
    assert t[0]["unassigned"]==1


def test_risk_jobs_sorted():
    jobs=[
      {"job_ref":"50001","customer_code":"C1","agent_code":"A1","operational_status":"JOB","release_status":"BLOCKED"},
      {"job_ref":"50002","customer_code":"C2","agent_code":"A2","operational_status":"BOOKED","release_status":"BLOCKED"},
    ]
    items=[
      {"job_ref":"50001","status":"OPEN","priority":"HIGH","sla_breached":False,"escalation_state":"NONE","owner":"u","category":"DOCUMENT_GAP"},
      {"job_ref":"50002","status":"OPEN","priority":"CRITICAL","sla_breached":True,"escalation_state":"OVERDUE","owner":None,"category":"PAYMENT_RELEASE_BLOCK"},
    ]
    r=_risk_jobs(jobs,items)
    assert r[0]["job_ref"]=="50002"
    assert r[0]["risk_score"]>r[1]["risk_score"]
    assert r[0]["workbench_url"].endswith("50002")
