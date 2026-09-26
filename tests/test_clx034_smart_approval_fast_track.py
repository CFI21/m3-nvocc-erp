import pytest
from fastapi import HTTPException

from app.clx034_smart_approval_fast_track import enforce_execution, policy
from tests.test_clx033_finance_transaction_governance import isolated_db, token, add_limit


def test_low_risk_level1_post_goes_straight_through(isolated_db):
    poster=token('finance.dxb','Fin123!')
    add_limit('finance.dxb',1,amount=100000,action='POST',tx='JOURNAL')
    out=enforce_execution(
        'GL','voucher',701,'POST',poster,1,5000,'USD','JOURNAL',
        'DXB','UAE','USR-MAKER','/api/v1/gl/voucher/701/actions/post'
    )
    assert out['approved'] is True
    assert out['auto_approved'] is True
    assert out['approval_required'] is False
    assert out['phase']=='CLX-034'


def test_multilevel_authority_routes_to_manual_approval(isolated_db):
    poster=token('finance.dxb','Fin123!')
    add_limit('finance.dxb',3,amount=100000,action='POST',tx='JOURNAL')
    with pytest.raises(HTTPException) as exc:
        enforce_execution(
            'GL','voucher',702,'POST',poster,1,5000,'USD','JOURNAL',
            'DXB','UAE','USR-MAKER','/api/v1/gl/voucher/702/actions/post'
        )
    assert exc.value.status_code==409
    assert exc.value.detail['code']=='FINANCE_APPROVAL_PENDING'
    assert exc.value.detail['phase']=='CLX-034'
    assert 'HIGH_VALUE_OR_MULTI_LEVEL_AUTHORITY' in exc.value.detail['approval_reasons']


def test_writeoff_and_reverse_always_require_manual_approval(isolated_db):
    poster=token('finance.dxb','Fin123!')
    add_limit('finance.dxb',1,amount=100000,action='REVERSE',tx='JOURNAL')
    with pytest.raises(HTTPException) as exc:
        enforce_execution(
            'GL','voucher',703,'REVERSE',poster,1,500,'USD','JOURNAL',
            'DXB','UAE','USR-MAKER','/api/v1/gl/voucher/703/actions/reverse'
        )
    assert exc.value.detail['smart_route']=='MANUAL_APPROVAL'
    assert 'REVERSE_REQUIRES_APPROVAL' in exc.value.detail['approval_reasons']


def test_treasury_payment_release_stays_four_eyes(isolated_db):
    treasury=token('admin','Admin123!')
    with pytest.raises(HTTPException) as exc:
        enforce_execution(
            'TREASURY','bank-payment',704,'RELEASE',treasury,1,2500,'USD','TREASURY',
            'DXB','UAE','USR-OTHER','/api/v1/treasury/bank-payment/704/actions/release'
        )
    assert exc.value.detail['smart_route']=='MANUAL_APPROVAL'
    assert 'PAYMENT_RELEASE_REQUIRES_APPROVAL' in exc.value.detail['approval_reasons']


def test_explicit_exception_flag_prevents_straight_through(isolated_db):
    poster=token('finance.dxb','Fin123!')
    add_limit('finance.dxb',1,amount=100000,action='POST',tx='JOURNAL')
    with pytest.raises(HTTPException) as exc:
        enforce_execution(
            'GL','voucher',705,'POST',poster,1,500,'USD','JOURNAL',
            'DXB','UAE','USR-MAKER','/api/v1/gl/voucher/705/actions/post',
            risk_flags=['OVERRIDE']
        )
    assert exc.value.detail['smart_route']=='MANUAL_APPROVAL'
    assert 'OVERRIDE' in exc.value.detail['approval_reasons']


def test_clx034_policy_keeps_live_money_off():
    p=policy()
    assert p['mode']=='EXCEPTION_BASED_GOVERNANCE'
    assert p['live_providers'] is False
    assert p['real_money'] is False
