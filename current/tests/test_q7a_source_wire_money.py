"""STEP9A provider wire alias; canonical Money and proof custody remain strict."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json

import pytest

from trading_robot import broker_read_adapters as cl3
from trading_robot import cash_ledger_opening_reconciliation as cl4
from trading_robot.cash_ledger_domain import Money, MoneyError

KEY = bytes(range(32))
KEY_ID = 'CL5_TEST_KEY_V1'
T = '2027-01-01T12:00:00.000000000Z'
ACCOUNT_HASH = 'a' * 64


@pytest.mark.parametrize('currency', ['RUB', 'rub'])
@pytest.mark.parametrize('units,nano', [
    ('0', 0), ('0', 1), ('0', -1), ('105', 10), ('-105', -10),
    ('9223372036854775807', 999999999), ('-9223372036854775808', -999999999),
])
def test_wire_alias_preserves_exact_integer_money(currency, units, nano):
    body = {'currency': currency, 'units': units, 'nano': nano}
    before = deepcopy(body)
    actual = cl3.money_value_to_money(body)
    assert actual == Money('RUB', int(units) * 10**9 + nano)
    assert actual.currency == 'RUB' and body == before
    assert actual.canonical_bytes == cl3.money_value_to_money({**body, 'currency': 'RUB'}).canonical_bytes


@pytest.mark.parametrize('currency', ['Rub', 'rUB', 'RUb', 'rub ', ' RUB', 'RUR', 'usd', 'USD',
                                     'ruЬ', 'ＲＵＢ', '', None, True, 1, [], {}])
def test_alias_is_not_unrestricted_casefold_or_default_currency(currency):
    with pytest.raises(cl3.BrokerReadError):
        cl3.money_value_to_money({'currency': currency, 'units': '1', 'nano': 0})


@pytest.mark.parametrize('change', [
    {'units': '00'}, {'units': '-0'}, {'units': '+1'}, {'units': 1}, {'units': True},
    {'units': '1.1'}, {'units': '9223372036854775808'}, {'nano': True},
    {'nano': 1000000000}, {'nano': -1}, {'nano': 0.0}, {'nano': '0'}, {'extra': 1},
])
def test_lowercase_does_not_relax_numeric_or_schema_guards(change):
    with pytest.raises(cl3.BrokerReadError):
        cl3.money_value_to_money({'currency': 'rub', 'units': '1', 'nano': 0, **change})


def test_canonical_domain_still_rejects_lowercase_currency():
    with pytest.raises(MoneyError): Money('rub', 105000000000)


def test_cl4_proof_preserves_raw_response_hash_without_mutating_wire_alias():
    proofs = []
    for alias in ('rub', 'RUB'):
        raw = {'totalAmountCurrencies': {'currency': alias, 'units': '105', 'nano': 1},
               'positions': [], 'accountId': 'SYNTHETIC'}
        before = deepcopy(raw)
        proof = cl4.build_broker_cash_proof(raw, account_scope_sha256=ACCOUNT_HASH,
            environment=cl3.BrokerEnvironment.SANDBOX, as_of=T, evaluated_at=T,
            response_complete=True, identity_key=KEY, identity_key_id=KEY_ID)
        assert raw == before
        wire = json.dumps(raw, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')
        assert proof.response_canonical_sha256 == hashlib.sha256(wire).hexdigest()
        assert proof.cash == Money('RUB', 105000000001)
        proofs.append(proof)
    assert proofs[0].cash == proofs[1].cash
    assert proofs[0].response_canonical_sha256 != proofs[1].response_canonical_sha256
    assert proofs[0].proof_identity_sha256 != proofs[1].proof_identity_sha256
