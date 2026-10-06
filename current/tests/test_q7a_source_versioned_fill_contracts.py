"""STEP35 pure economic contracts. Synthetic data; no trading authority."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from test_q7a_source_versioned_dispatch_contracts import queued
from test_q7a_source_provider_refresh import money
from trading_robot import versioned_dispatch as dispatch
from trading_robot import versioned_fill_evidence as fill
from trading_robot.exact_own_funds import OwnFundsEvidence, digest
from trading_robot.runtime_cash_authority import derive_account_scope

KEY = bytes(range(32))
KEY_ID = 'FILL_TEST_KEY'
AT = '2026-08-13T12:00:00.000000000Z'
TRADE = '2026-08-13T12:00:01.000000000Z'
SEEN = '2026-08-13T12:00:02.000000000Z'


def value(tmp_path):
    _, before = queued(tmp_path)
    at = dispatch.cl6._normalize_portfolio_timestamp(before.queued[0].created_at)[0]
    def later(seconds):
        t=datetime.fromisoformat(at.replace('Z','+00:00'))+timedelta(seconds=seconds)
        return t.strftime('%Y-%m-%dT%H:%M:%S.')+f'{t.microsecond:06d}000Z'
    trade_at, seen_at = later(1), later(2)
    state = dispatch._inflight(before, 'a'*64, at)
    state = dispatch._outcome(state, 'SUBMITTED', at, 'exchange-1')
    intent = state.blocking_intent
    c = intent.candidate
    metadata = dict(instrument_id=c.instrument_id, currency='RUB', asset_class='share', lot_size=c.lot_size)
    own = OwnFundsEvidence(request_sha256=digest(dict(domain='CL7_OWN_FUNDS_REQUEST_V1',
            intent_id=intent.intent_id, candidate=c.to_dict(), get_max_lots_price=None)),
        metadata_sha256=digest(metadata), positions_sha256='1'*64, limits_sha256='2'*64,
        direction=c.direction, order_type=c.order_type, time_in_force=c.time_in_force,
        requested_lots=c.requested_lots, lot_size=c.lot_size, estimated_price_kopecks=c.estimated_price_kopecks,
        rub_position_nano=10**15, broker_blocked_nano=0, own_money_nano=10**15, own_max_lots=10,
        all_local_reservations_nano=intent.reserved_cash_kopecks*10**7,
        own_reservation_nano=intent.reserved_cash_kopecks*10**7, started_at=at, completed_at=at)
    raw = dict(orderRequestId=intent.intent_id, orderId='exchange-1', instrumentUid=c.instrument_id,
        accountId=c.account_id, currency='RUB', orderType='ORDER_TYPE_MARKET', direction='ORDER_DIRECTION_BUY',
        lotsRequested=str(c.requested_lots), lotsExecuted=str(c.requested_lots),
        executionReportStatus='EXECUTION_REPORT_STATUS_FILL', executedCommission=money('0.123456789'),
        serviceCommission=money(0), executedOrderPrice=money('9999.999999999'),
        averagePositionPrice=money('105.123456789'), stages=[dict(tradeId='TRADE',executionTime=trade_at,
            price=money('105.123456789'),quantity=str(c.requested_lots))])
    result = dict(domain=fill.DOMAIN, version=1, dispatch_plan_sha256='a'*64, intent=intent.to_dict(),
        instrument=metadata, own_funds=own.to_canonical_dict(), proof_evaluated_at=at,
        observed_at=seen_at, order_state=raw)
    scope = derive_account_scope(c.account_id, identity_key=KEY, identity_key_id=KEY_ID)
    return result, scope


def decode(v, scope):
    return fill.decode_bound_fill(v, key=KEY, key_id=KEY_ID, account_scope=scope)[2]


def test_stage_price_lots_and_explicit_zero_fee_no_aggregate_multiplication(tmp_path):
    v, scope = value(tmp_path)
    receipt = decode(v, scope)
    assert receipt.gross_nano == 1051234567890
    assert receipt.expected_cash_delta_nano == -1051358024679
    v['order_state']['executedOrderPrice'] = money('1')
    assert decode(v, scope).expected_cash_delta_nano == receipt.expected_cash_delta_nano
    v['order_state']['executedCommission'] = money(0)
    assert decode(v, scope).expected_cash_delta_nano == -1051234567890


@pytest.mark.parametrize('field,replacement', [
    ('orderRequestId','foreign'), ('orderId','foreign'), ('accountId','foreign'),
    ('instrumentUid','foreign'), ('orderType','ORDER_TYPE_LIMIT'), ('direction','ORDER_DIRECTION_SELL'),
    ('lotsRequested','2'), ('lotsExecuted','2'), ('lotsExecuted',True),
    ('executedCommission',None), ('serviceCommission',None),
    ('serviceCommission',money('0.01')), ('averagePositionPrice',money('106')),
    ('stages',[]), ('executionReportStatus','EXECUTION_REPORT_STATUS_PARTIALLYFILL'),
])
def test_invalid_receipt_cannot_be_cash_permission(tmp_path,field,replacement):
    v, scope = value(tmp_path)
    assert decode(v, scope).gross_nano == 1051234567890
    v['order_state'][field] = replacement
    with pytest.raises(RuntimeError): decode(v, scope)


@pytest.mark.parametrize('damage',['version','domain','extra','hash','metadata_lot','metadata_type',
    'metadata_currency','metadata_id','own_request','own_metadata','old_proof','interday',
    'duplicate_trade','stage_fraction','stage_date','stage_id_missing','account_scope'])
def test_bad_bound_identity_fails_without_new_source_id(tmp_path,damage):
    v, scope = value(tmp_path)
    assert decode(v, scope).gross_nano == 1051234567890
    if damage=='version': v['version']=True
    elif damage=='domain': v['domain']='other'
    elif damage=='extra': v['permit']=True
    elif damage=='hash': v['dispatch_plan_sha256']='b'*64
    elif damage=='metadata_lot': v['instrument']['lot_size']=1
    elif damage=='metadata_type': v['instrument']['asset_class']=None
    elif damage=='metadata_currency': v['instrument']['currency']='USD'
    elif damage=='metadata_id': v['instrument']['instrument_id']='other'
    elif damage=='own_request': v['own_funds']['request_sha256']='9'*64
    elif damage=='own_metadata': v['own_funds']['metadata_sha256']='9'*64
    elif damage=='old_proof': v['proof_evaluated_at']='2030-01-01T00:00:00.000000000Z'
    elif damage=='interday': v['observed_at']='2030-01-01T00:00:00.000000000Z'
    elif damage=='duplicate_trade': v['order_state']['stages']*=2
    elif damage=='stage_fraction': v['order_state']['stages'][0]['quantity']='0.5'
    elif damage=='stage_date': v['order_state']['stages'][0]['executionTime']='2026-08-13T11:00:00.000000000Z'
    elif damage=='stage_id_missing': v['order_state']['stages'][0].pop('tradeId')
    else: scope='9'*64
    with pytest.raises((RuntimeError,ValueError)) as e: decode(v, scope)
    assert not isinstance(e.value, (AttributeError,KeyError,TypeError))


def test_live_v1_proof_decoder_still_rejects_versioned_marker(tmp_path):
    from trading_robot.exact_order_receipt import decode_exact_order_receipt
    v, scope = value(tmp_path)
    intent = dispatch.CentralOrderIntent.from_dict(v['intent'])
    with pytest.raises(RuntimeError) as exc:
        decode_exact_order_receipt(v['order_state'], intent, account_id=intent.candidate.account_id,
            identity_key=KEY, identity_key_id=KEY_ID, instrument=SimpleNamespace(**v['instrument']), observed_at=v['observed_at'])
    assert not isinstance(exc.value, TypeError)


def test_result_public_summary_cannot_grant_closure_or_leak_money():
    from trading_robot.versioned_fill_cash import VersionedFillCashResult
    pins=SimpleNamespace(export_sha256='7'*64)
    result=VersionedFillCashResult('1'*64,'2'*64,'COMMITTED',pins,None,1051234567890,
        123456789,-1051358024679,('3'*64,),2,False)
    summary=result.public_summary()
    assert summary['cash_components_verified'] is True
    assert summary['settlement_verified'] is summary['authority_clear_allowed'] is False
    assert summary['post_order_called'] is summary['risk_execution_written'] is False
    assert not any(str(x) in str(summary) for x in (1051234567890,123456789,-1051358024679))
