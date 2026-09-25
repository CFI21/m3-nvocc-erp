import pytest
from fastapi import HTTPException
from app import admin


class Result:
    def __init__(self,row=None,rowcount=0):
        self._row=row
        self.rowcount=rowcount
    def fetchone(self):
        return self._row


def test_session_rejects_suspended_user_and_revokes_session():
    calls=[]
    class C:
        def execute(self,sql,args=None):
            calls.append((sql,args))
            if 'FROM iam_sessions' in sql:
                return Result({
                    'id':11,'user_id':3,'status':'ACTIVE','revoked_at':None,
                    'expires_at':'2999-01-01T00:00:00+00:00','user_status':'SUSPENDED',
                    'user_ref':'USR-003','username':'finance.dxb','home_office_id':2,'office_code':'DXB'
                })
            if "UPDATE iam_sessions SET status='REVOKED'" in sql:
                return Result(rowcount=1)
            return Result()
    with pytest.raises(HTTPException) as exc:
        admin.session(C(),'token')
    assert exc.value.status_code==401
    assert exc.value.detail=={'code':'USER_NOT_ACTIVE'}
    assert any("status='REVOKED'" in sql for sql,args in calls)


def test_session_allows_active_user_and_updates_last_seen():
    calls=[]
    class C:
        def execute(self,sql,args=None):
            calls.append((sql,args))
            if 'FROM iam_sessions' in sql:
                return Result({
                    'id':12,'user_id':2,'status':'ACTIVE','revoked_at':None,
                    'expires_at':'2999-01-01T00:00:00+00:00','user_status':'ACTIVE',
                    'user_ref':'USR-002','username':'ops.rtm','home_office_id':1,'office_code':'RTM'
                })
            return Result(rowcount=1)
    s=admin.session(C(),'token')
    assert s['user_status']=='ACTIVE'
    assert any('last_seen_at' in sql for sql,args in calls)


def test_status_change_to_suspended_revokes_active_sessions(monkeypatch):
    calls=[]
    class C:
        def execute(self,sql,args=None):
            calls.append((sql,args))
            if 'FROM iam_sessions' in sql:
                return Result({
                    'id':1,'user_id':1,'status':'ACTIVE','revoked_at':None,
                    'expires_at':'2999-01-01T00:00:00+00:00','user_status':'ACTIVE',
                    'user_ref':'USR-001','username':'admin','home_office_id':1,'office_code':'RTM'
                })
            if 'SELECT * FROM iam_users WHERE username=' in sql:
                return Result({'id':2,'user_ref':'USR-002','status':'ACTIVE','version':1})
            if "UPDATE iam_sessions SET status='REVOKED'" in sql:
                return Result(rowcount=3)
            return Result(rowcount=1)
        def close(self): pass
    monkeypatch.setattr(admin,'connect',lambda:C())
    monkeypatch.setattr(admin,'permission',lambda *args,**kwargs:True)
    monkeypatch.setattr(admin,'audit',lambda *args,**kwargs:None)
    out=admin.set_user_status('ops.rtm',admin.UserStatus(status='SUSPENDED'),'admin-session')
    assert out['status']=='SUSPENDED'
    assert out['sessions_revoked']==3


def test_status_change_to_active_does_not_revoke_sessions(monkeypatch):
    calls=[]
    class C:
        def execute(self,sql,args=None):
            calls.append((sql,args))
            if 'FROM iam_sessions' in sql:
                return Result({
                    'id':1,'user_id':1,'status':'ACTIVE','revoked_at':None,
                    'expires_at':'2999-01-01T00:00:00+00:00','user_status':'ACTIVE',
                    'user_ref':'USR-001','username':'admin','home_office_id':1,'office_code':'RTM'
                })
            if 'SELECT * FROM iam_users WHERE username=' in sql:
                return Result({'id':2,'user_ref':'USR-002','status':'SUSPENDED','version':2})
            return Result(rowcount=1)
        def close(self): pass
    monkeypatch.setattr(admin,'connect',lambda:C())
    monkeypatch.setattr(admin,'permission',lambda *args,**kwargs:True)
    monkeypatch.setattr(admin,'audit',lambda *args,**kwargs:None)
    out=admin.set_user_status('ops.rtm',admin.UserStatus(status='ACTIVE'),'admin-session')
    assert out['sessions_revoked']==0
    assert not any("status='REVOKED'" in sql and 'user_id=?' in sql for sql,args in calls)
