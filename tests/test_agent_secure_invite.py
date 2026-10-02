import json
import secrets
import datetime
import pytest
from fastapi import HTTPException

from app import db
from app.seed import run as seed_run
from app.admin_seed import run as admin_seed_run
from app.masterdata_seed import run as masterdata_seed_run
from app.admin import (
    AgentUserInvite, AgentUserInviteDecision, AgentUserActivate,
    request_agent_user_invite, decide_agent_user_invite, reissue_agent_user_invite_token, activate_agent_user_invite
)
from app.screen_catalog import build_catalog

@pytest.fixture()
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'agent_invite.db')
    seed_run(True); admin_seed_run(); masterdata_seed_run()
    c=db.connect()
    ts='2026-10-02T14:00:00+00:00'
    name='ANC WORLDWIDE CONTAINERS LINE (M) SDN BHD'
    cp={'name':name,'source_type':'Agent'}
    ag={'name':name,'common_party_key':'ANCML','common_party_name':name,'country':'MY','port':'MYPKG','relationship':'AGENT_MASTER_TO_COMMON_PARTY','customer_link':None}
    c.execute("INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",('common-party','ANCML',name,json.dumps(cp),'ACTIVE',1,ts,ts))
    c.execute("INSERT INTO md_records(domain,record_key,display_name,payload_json,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",('agent','220',name,json.dumps(ag),'ACTIVE',1,ts,ts))
    c.close()
    return db.DB_PATH

def session_for(username):
    c=db.connect()
    u=c.execute("SELECT id FROM iam_users WHERE username=?",(username,)).fetchone()
    token='T-'+secrets.token_urlsafe(18)
    now=datetime.datetime.now(datetime.timezone.utc)
    exp=(now+datetime.timedelta(hours=1)).isoformat()
    c.execute("INSERT INTO iam_sessions(session_token,user_id,created_at,expires_at,last_seen_at,mfa_verified,status) VALUES(?,?,?,?,?,1,'ACTIVE')",(token,u['id'],now.isoformat(),exp,now.isoformat()))
    c.close()
    return token

def invite():
    return AgentUserInvite(username='ancml@786',display_name='ANCML',email='minbox@ancline.net',agent_code='220')

def test_agent_role_limited_permissions(isolated):
    c=db.connect()
    role=c.execute("SELECT * FROM iam_roles WHERE role_code='AGENT'").fetchone()
    assert role and role['status']=='ACTIVE' and role['sensitive']==0
    perms={r['permission_code'] for r in c.execute("SELECT p.permission_code FROM iam_role_permissions rp JOIN iam_permissions p ON p.id=rp.permission_id WHERE rp.role_id=?",(role['id'],))}
    assert perms=={'agent-tasks:view','agent-tasks:create','agent-tasks:edit'}
    c.close()

def test_invite_stays_inactive_until_activation(isolated):
    out=request_agent_user_invite(invite(),session_for('md.maker'))
    assert out['status']=='INVITE_PENDING_CHECKER' and out['login_active'] is False
    c=db.connect()
    u=c.execute("SELECT * FROM iam_users WHERE username='ancml@786'").fetchone()
    assert u['status']=='INVITED_PENDING_CHECKER' and u['mfa_required']==1
    assert c.execute("SELECT 1 FROM iam_party_access WHERE user_id=?",(u['id'],)).fetchone() is None
    c.close()

def test_four_eyes_agent_only_scope_and_one_time_activation(isolated):
    maker=session_for('md.maker')
    checker=session_for('md.checker')
    out=request_agent_user_invite(invite(),maker)
    with pytest.raises(HTTPException) as same:
        decide_agent_user_invite(out['review_ref'],AgentUserInviteDecision(decision='APPROVE'),maker)
    assert same.value.detail['code']=='IDENTITY_CHECKER_PERMISSION_DENIED'
    approved=decide_agent_user_invite(out['review_ref'],AgentUserInviteDecision(decision='APPROVE'),checker)
    assert approved['status']=='INVITE_PENDING' and approved['login_active'] is False
    c=db.connect()
    u=c.execute("SELECT * FROM iam_users WHERE username='ancml@786'").fetchone()
    roles={r['role_code'] for r in c.execute("SELECT r.role_code FROM iam_user_roles ur JOIN iam_roles r ON r.id=ur.role_id WHERE ur.user_id=? AND ur.status='ACTIVE'",(u['id'],))}
    access=[dict(r) for r in c.execute("SELECT party_type,party_key,access_level,status FROM iam_party_access WHERE user_id=?",(u['id'],))]
    office=c.execute("SELECT o.office_code,c.country_code FROM iam_offices o JOIN iam_countries c ON c.id=o.country_id WHERE o.id=?",(u['home_office_id'],)).fetchone()
    assert roles=={'AGENT'}
    assert access==[{'party_type':'AGENT','party_key':'220','access_level':'EDIT','status':'ACTIVE'}]
    assert office['office_code']=='AGT-220' and office['country_code']=='MY'
    assert not c.execute("SELECT 1 FROM iam_party_access WHERE user_id=? AND party_type='CUSTOMER'",(u['id'],)).fetchone()
    c.close()
    activated=activate_agent_user_invite(AgentUserActivate(invite_token=approved['invite_token'],new_password='UserSelectedCredential-2026'))
    assert activated['status']=='ACTIVE' and activated['mfa_required'] is True
    with pytest.raises(HTTPException):
        activate_agent_user_invite(AgentUserActivate(invite_token=approved['invite_token'],new_password='DifferentUserSelectedCredential-2026'))

def test_super_admin_self_approval_is_four_eyes_blocked(isolated):
    maker=session_for('admin')
    out=request_agent_user_invite(invite(),maker)
    with pytest.raises(HTTPException) as same:
        decide_agent_user_invite(out['review_ref'],AgentUserInviteDecision(decision='APPROVE'),maker)
    assert same.value.detail['code']=='FOUR_EYES_VIOLATION'

def test_retry_recovers_pre_review_partial_invite(isolated):
    c=db.connect()
    office=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    c.execute("""INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,status,home_office_id,mfa_required)
      VALUES('USR-AGT-PARTIAL','ancml@786','ANCML','minbox@ancline.net','unusable','INVITED_PENDING_CHECKER',?,1)""",(office,))
    c.close()
    out=request_agent_user_invite(invite(),session_for('md.maker'))
    assert out['user_ref']=='USR-AGT-PARTIAL'
    assert out['status']=='INVITE_PENDING_CHECKER'
    c=db.connect()
    assert c.execute("SELECT COUNT(*) n FROM iam_users WHERE username='ancml@786'").fetchone()['n']==1
    assert c.execute("SELECT COUNT(*) n FROM iam_access_reviews WHERE user_id=(SELECT id FROM iam_users WHERE username='ancml@786') AND status='PENDING'").fetchone()['n']==1
    c.close()

def test_reissue_invalidates_old_token_and_enforces_password_policy(isolated):
    maker=session_for('md.maker')
    checker=session_for('md.checker')
    out=request_agent_user_invite(invite(),maker)
    approved=decide_agent_user_invite(out['review_ref'],AgentUserInviteDecision(decision='APPROVE'),checker)
    old_token=approved['invite_token']
    reissued=reissue_agent_user_invite_token(out['review_ref'],checker)
    assert reissued['status']=='INVITE_PENDING'
    assert reissued['display_once'] is True
    assert reissued['invite_token']!=old_token
    with pytest.raises(HTTPException) as old:
        activate_agent_user_invite(AgentUserActivate(invite_token=old_token,new_password='UserSelectedCredential-2026'))
    assert old.value.status_code==401
    with pytest.raises(HTTPException) as short:
        activate_agent_user_invite(AgentUserActivate(invite_token=reissued['invite_token'],new_password='short'))
    assert short.value.detail['code']=='PASSWORD_POLICY_MIN_LENGTH'
    activated=activate_agent_user_invite(AgentUserActivate(invite_token=reissued['invite_token'],new_password='UserSelectedCredential-2026'))
    assert activated['status']=='ACTIVE'
    with pytest.raises(HTTPException):
        activate_agent_user_invite(AgentUserActivate(invite_token=reissued['invite_token'],new_password='AnotherUserSelectedCredential-2026'))

def test_reissue_requires_checker_and_pending_activation(isolated):
    maker=session_for('md.maker')
    checker=session_for('md.checker')
    out=request_agent_user_invite(invite(),maker)
    with pytest.raises(HTTPException) as denied:
        reissue_agent_user_invite_token(out['review_ref'],maker)
    assert denied.value.detail['code']=='INVITE_REISSUE_PERMISSION_DENIED'
    approved=decide_agent_user_invite(out['review_ref'],AgentUserInviteDecision(decision='APPROVE'),checker)
    reissued=reissue_agent_user_invite_token(out['review_ref'],checker)
    c=db.connect()
    ar=c.execute("SELECT scope,status FROM iam_access_reviews WHERE review_ref=?",(out['review_ref'],)).fetchone()
    scope=json.loads(ar['scope'])
    assert ar['status']=='APPROVED_PENDING_ACTIVATION'
    assert scope['invite_token_hash']!=reissued['invite_token']
    assert scope['invite_token_hash']==__import__('hashlib').sha256(reissued['invite_token'].encode()).hexdigest()
    assert scope['token_reissue_count']==1
    c.close()

def test_expired_reissued_token_is_rejected(isolated):
    maker=session_for('md.maker')
    checker=session_for('md.checker')
    out=request_agent_user_invite(invite(),maker)
    decide_agent_user_invite(out['review_ref'],AgentUserInviteDecision(decision='APPROVE'),checker)
    reissued=reissue_agent_user_invite_token(out['review_ref'],checker)
    c=db.connect()
    ar=c.execute("SELECT scope FROM iam_access_reviews WHERE review_ref=?",(out['review_ref'],)).fetchone()
    scope=json.loads(ar['scope'])
    scope['invite_expires_at']='2000-01-01T00:00:00+00:00'
    c.execute("UPDATE iam_access_reviews SET scope=? WHERE review_ref=?",(json.dumps(scope,sort_keys=True),out['review_ref']))
    c.close()
    with pytest.raises(HTTPException) as expired:
        activate_agent_user_invite(AgentUserActivate(invite_token=reissued['invite_token'],new_password='UserSelectedCredential-2026'))
    assert expired.value.status_code==410

def test_duplicate_email_and_196_screens(isolated):
    maker=session_for('admin')
    request_agent_user_invite(invite(),maker)
    with pytest.raises(HTTPException) as dup:
        request_agent_user_invite(AgentUserInvite(username='another.user',display_name='Another',email='minbox@ancline.net',agent_code='220'),maker)
    assert dup.value.detail['code']=='DUPLICATE_USERNAME_OR_EMAIL'
    assert build_catalog()['screen_count']==196
