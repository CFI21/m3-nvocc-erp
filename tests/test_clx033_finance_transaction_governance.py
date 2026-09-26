import pytest
from fastapi import HTTPException

from app import db
from app.admin_seed import run as admin_seed_run, phash
from app.admin import Login, login
from app.clx032_finance_controls import ApprovalLimitRequest, apply_limit
from app.clx033_finance_transaction_governance import (
    Decision, ReopenBody, enforce_execution, decide, reopen, workbench
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'clx033.db')
    admin_seed_run()
    c=db.connect()
    r_view=c.execute("SELECT id FROM iam_roles WHERE role_code='VIEWER'").fetchone()['id']
    office_rtm=c.execute("SELECT id FROM iam_offices WHERE office_code='RTM'").fetchone()['id']
    for idx,name in enumerate(['checker1','checker2','checker3'],1):
        c.execute(
            '''INSERT INTO iam_users(user_ref,username,display_name,email,password_hash,home_office_id,mfa_required)
               VALUES(?,?,?,?,?,?,1)''',
            (f'USR-C33-{idx}',name,f'Checker {idx}',f'{name}@m3.test',phash('Checker123!'),office_rtm),
        )
        uid=c.execute('SELECT id FROM iam_users WHERE username=?',(name,)).fetchone()['id']
        c.execute(
            '''INSERT INTO iam_user_roles(user_id,role_id,office_id,valid_from,status,assigned_by)
               VALUES(?,?,?,?,?,?)''',(uid,r_view,office_rtm,'2026-09-26T00:00:00+00:00','ACTIVE','clx033-test')
        )
        c.execute(
            '''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
               VALUES(?,?,?,?,?,?,?,?)''',
            (f'FENT-C33-{idx}',uid,'JOURNAL_APPROVE','2026-09-01T00:00:00+00:00','2027-09-01T00:00:00+00:00',
             'CLX033 checker','ACTIVE','test')
        )
    finance=c.execute("SELECT id FROM iam_users WHERE username='finance.dxb'").fetchone()['id']
    c.execute(
        '''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
           VALUES(?,?,?,?,?,?,?,?)''',
        ('FENT-C33-POST',finance,'GL_POST','2026-09-01T00:00:00+00:00','2027-09-01T00:00:00+00:00',
         'CLX033 posting','ACTIVE','test')
    )
    c.close()
    return db.DB_PATH


def token(username,password):
    return login(Login(username=username,password=password,mfa_code='123456'))['session_token']


def add_limit(username,level,amount=100000,action='POST',tx='JOURNAL',office='*',country='*'):
    c=db.connect()
    b=ApprovalLimitRequest(
        subject_type='USER',subject_code=username,currency='USD',amount_limit=amount,
        transaction_type=tx,action=action,level=level,
        office_code=None if office=='*' else office,country_code=None if country=='*' else country,
        valid_from='2026-09-01T00:00:00+00:00',valid_to='2027-09-01T00:00:00+00:00',
        reason='CLX033 test authority'
    )
    apply_limit(c,b.model_dump(),'TEST-SEED')
    c.close()


def pending_call(session_token,amount=5000,version=1,action='POST',maker='USR-MAKER',office='DXB',country='UAE'):
    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','voucher',77,action,session_token,version,amount,'USD','JOURNAL',office,country,maker,
                          f'/api/v1/gl/voucher/77/actions/{action.lower()}')
    return exc.value


def test_three_level_chain_and_final_execution_separation(isolated_db):
    poster=token('finance.dxb','Fin123!')
    c1=token('checker1','Checker123!')
    c2=token('checker2','Checker123!')
    c3=token('checker3','Checker123!')
    add_limit('finance.dxb',3)
    add_limit('checker1',1)
    add_limit('checker2',2)
    add_limit('checker3',3)

    e=pending_call(poster)
    assert e.status_code==409
    assert e.detail['code']=='FINANCE_APPROVAL_PENDING'
    assert e.detail['required_levels']==3
    chain=e.detail['chain_id']

    wb=workbench(x_m3_session=poster)
    item=next(x for x in wb['items'] if x['chain_id']==chain)
    r1=item['next_pending']
    assert decide(r1,Decision(decision='APPROVE',comment='L1'),c1)['status']=='NEXT_LEVEL_REQUIRED'
    item=next(x for x in workbench(x_m3_session=poster)['items'] if x['chain_id']==chain)
    assert decide(item['next_pending'],Decision(decision='APPROVE',comment='L2'),c2)['status']=='NEXT_LEVEL_REQUIRED'
    item=next(x for x in workbench(x_m3_session=poster)['items'] if x['chain_id']==chain)
    assert decide(item['next_pending'],Decision(decision='APPROVE',comment='L3'),c3)['status']=='APPROVE'

    ok=enforce_execution('GL','voucher',77,'POST',poster,1,5000,'USD','JOURNAL','DXB','UAE','USR-MAKER',
                         '/api/v1/gl/voucher/77/actions/post')
    assert ok['approved'] is True
    assert ok['required_levels']==3

    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','voucher',77,'POST',c3,1,5000,'USD','JOURNAL','DXB','UAE','USR-MAKER',
                          '/api/v1/gl/voucher/77/actions/post')
    assert exc.value.detail['code']=='EXECUTOR_CANNOT_BE_FINAL_APPROVER'


def test_over_limit_escalates_before_execution(isolated_db):
    poster=token('finance.dxb','Fin123!')
    add_limit('finance.dxb',1,amount=1000)
    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','voucher',88,'POST',poster,1,5000,'USD','JOURNAL','DXB','UAE','USR-MAKER',
                          '/api/v1/gl/voucher/88/actions/post')
    assert exc.value.detail['code']=='APPROVAL_LIMIT_EXCEEDED'


def test_wrong_office_blocks_direct_entitlement(isolated_db):
    poster=token('finance.dxb','Fin123!')
    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','voucher',89,'POST',poster,1,500,'USD','JOURNAL','RTM','NL','USR-MAKER',
                          '/api/v1/gl/voucher/89/actions/post')
    assert exc.value.detail['code']=='FINANCE_ENTITLEMENT_REQUIRED'


def test_expired_delegation_does_not_authorize(isolated_db):
    c=db.connect()
    finance=c.execute("SELECT id FROM iam_users WHERE username='finance.dxb'").fetchone()['id']
    c.execute("UPDATE iam_temporary_access SET status='REVOKED' WHERE user_id=? AND permission_code='GL_POST'",(finance,))
    admin=c.execute("SELECT id FROM iam_users WHERE username='admin'").fetchone()['id']
    c.execute(
        '''INSERT INTO iam_delegations(delegation_ref,from_user_id,to_user_id,permission_code,valid_from,valid_to,status,approved_by)
           VALUES(?,?,?,?,?,?,?,?)''',
        ('DEL-EXPIRED-C33',admin,finance,'GL_POST;TX=JOURNAL;OFFICE=DXB;COUNTRY=UAE',
         '2026-08-01T00:00:00+00:00','2026-08-31T23:59:59+00:00','ACTIVE','admin')
    )
    c.close()
    poster=token('finance.dxb','Fin123!')
    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','voucher',90,'POST',poster,1,500,'USD','JOURNAL','DXB','UAE','USR-MAKER',
                          '/api/v1/gl/voucher/90/actions/post')
    assert exc.value.detail['code']=='FINANCE_ENTITLEMENT_REQUIRED'


def test_journal_maker_cannot_request_own_approval(isolated_db):
    maker=token('checker1','Checker123!')
    add_limit('checker1',1,action='APPROVE')
    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','voucher',91,'APPROVE',maker,1,500,'USD','JOURNAL','RTM','NL','USR-C33-1',
                          '/api/v1/gl/voucher/91/actions/approve')
    assert exc.value.detail['code']=='MAKER_SELF_ACTION_BLOCKED'


def test_sod_conflict_blocks_checker(isolated_db):
    poster=token('finance.dxb','Fin123!')
    c1=token('checker1','Checker123!')
    add_limit('finance.dxb',1)
    add_limit('checker1',1)
    e=pending_call(poster,version=2)
    c=db.connect()
    uid=c.execute("SELECT id FROM iam_users WHERE username='checker1'").fetchone()['id']
    c.execute(
        '''INSERT INTO iam_temporary_access(temp_ref,user_id,permission_code,valid_from,valid_to,reason,status,approved_by)
           VALUES(?,?,?,?,?,?,?,?)''',
        ('FENT-C33-CONFLICT',uid,'GL_POST','2026-09-01T00:00:00+00:00','2027-09-01T00:00:00+00:00',
         'force SoD','ACTIVE','test')
    )
    c.close()
    item=next(x for x in workbench(x_m3_session=poster)['items'] if x['chain_id']==e.detail['chain_id'])
    with pytest.raises(HTTPException) as exc:
        decide(item['next_pending'],Decision(decision='APPROVE'),c1)
    assert exc.value.detail['code']=='SOD_CONFLICT'


def test_return_for_correction_reopen_and_idempotent_retry(isolated_db):
    poster=token('finance.dxb','Fin123!')
    c1=token('checker1','Checker123!')
    add_limit('finance.dxb',1)
    add_limit('checker1',1)
    e=pending_call(poster,version=5)
    chain=e.detail['chain_id']
    # Same retry must reuse the existing chain.
    e2=pending_call(poster,version=5)
    assert e2.detail['chain_id']==chain
    item=next(x for x in workbench(x_m3_session=poster)['items'] if x['chain_id']==chain)
    assert len(item['review_refs'])==1
    out=decide(item['next_pending'],Decision(decision='RETURN_FOR_CORRECTION',comment='Fix coding',reason_code='CODING_ERROR'),c1)
    assert out['status']=='RETURN_FOR_CORRECTION'
    reopened=reopen(chain,ReopenBody(comment='Coding corrected'),poster)
    assert reopened['status']=='REOPENED'
    assert reopened['chain_id']!=chain


def test_reject_blocks_chain(isolated_db):
    poster=token('finance.dxb','Fin123!')
    c1=token('checker1','Checker123!')
    add_limit('finance.dxb',1)
    add_limit('checker1',1)
    e=pending_call(poster,version=6)
    item=next(x for x in workbench(x_m3_session=poster)['items'] if x['chain_id']==e.detail['chain_id'])
    assert decide(item['next_pending'],Decision(decision='REJECT',reason_code='POLICY'),c1)['status']=='REJECT'
    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','voucher',77,'POST',poster,6,5000,'USD','JOURNAL','DXB','UAE','USR-MAKER',
                          '/api/v1/gl/voucher/77/actions/post')
    assert exc.value.detail['code']=='FINANCE_APPROVAL_REJECTED'


def test_period_close_is_governed(isolated_db):
    admin=token('admin','Admin123!')
    with pytest.raises(HTTPException) as exc:
        enforce_execution('GL','accounting-periods',9,'CLOSE',admin,1,0,'USD','GL','RTM','NL','USR-OTHER',
                          '/api/v1/gl/accounting-periods/9/actions/close')
    assert exc.value.detail['code']=='FINANCE_APPROVAL_PENDING'
    assert exc.value.detail['required_levels']==1
