from app.management_kpi import _match


def test_alert_threshold_operators():
    assert _match(3,"GTE",3)
    assert _match(4,"GT",3)
    assert _match(2,"LTE",3)
    assert _match(2,"LT",3)
    assert _match(3,"EQ",3)
    assert not _match(2,"GT",3)


def test_snapshot_contract_static():
    from app.management_kpi import DEFAULT_RULES
    keys={x[0] for x in DEFAULT_RULES}
    assert {"CRITICAL_WORK","OVERDUE_WORK","UNASSIGNED_WORK","RELEASE_BLOCKS","PAYMENT_BLOCKS"} <= keys
