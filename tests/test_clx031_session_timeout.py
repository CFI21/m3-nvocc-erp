import datetime
import pytest
from fastapi import HTTPException
from app import admin


class Result:
    def __init__(self,row=None,rowcount=0):
        self._row=row
        self.rowcount=rowcount
    def fetchone(self):
        return self._row


def _policy_conn(idle='30'):
    class C:
        def execute(self,sql,args=None):
            if 'SELECT policy_value' in sql:
                return Result({'policy_value':idle})
            return Result()
    return C()


def test_session_expiry_reason_detects_absolute_timeout():
    c=_policy_conn('30')
    s={'expires_at':'2026-09-25T20:00:00+00:00','last_seen_at':'2026-09-25T19:50:00+00:00'}
    at=datetime.datetime(2026,9,25,20,1,tzinfo=datetime.timezone.utc)
    assert admin.session_expiry_reason(c,s,at)=='ABSOLUTE_TIMEOUT'


def test_session_expiry_reason_detects_idle_timeout():
    c=_policy_conn('30')
    s={'expires_at':'2026-09-25T22:00:00+00:00','last_seen_at':'2026-09-25T19:00:00+00:00'}
    at=datetime.datetime(2026,9,25,19,31,tzinfo=datetime.timezone.utc)
    assert admin.session_expiry_reason(c,s,at)=='IDLE_TIMEOUT'


def test_session_expiry_reason_allows_active_session():
    c=_policy_conn('30')
    s={'expires_at':'2026-09-25T22:00:00+00:00','last_seen_at':'2026-09-25T19:20:00+00:00'}
    at=datetime.datetime(2026,9,25,19,40,tzinfo=datetime.timezone.utc)
    assert admin.session_expiry_reason(c,s,at) is None


def test_session_marks_expired_token_closed(monkeypatch):
    calls=[]
    class C:
        def execute(self,sql,args=None):
            calls.append((sql,args))
            if 'FROM iam_sessions' in sql:
                return Result({
                    'id':14,'user_id':2,'status':'ACTIVE','revoked_at':None,
                    'expires_at':'2000-01-01T00:00:00+00:00',
                    'last_seen_at':'1999-12-31T23:55:00+00:00',
                    'user_status':'ACTIVE','user_ref':'USR-002','username':'ops.rtm',
                    'home_office_id':1,'office_code':'RTM'
                })
            if 'SELECT policy_value' in sql:
                return Result({'policy_value':'30'})
            return Result(rowcount=1)
    with pytest.raises(HTTPException) as exc:
        admin.session(C(),'expired-token')
    assert exc.value.status_code==401
    assert exc.value.detail['code']=='SESSION_EXPIRED'
    assert exc.value.detail['reason']=='ABSOLUTE_TIMEOUT'
    assert any("status='EXPIRED'" in sql for sql,args in calls)
