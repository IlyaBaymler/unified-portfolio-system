"""Rebuilt STEP31: executable lost-update reproductions; no broker IO."""
from dataclasses import replace

import pytest

from trading_robot.portfolio_model import PortfolioState, CompatibilityShadowStatus
from trading_robot.portfolio_repository import PortfolioRepository, PortfolioRevisionConflictError
from trading_robot.portfolio_preflight import PortfolioSnapshotLease
from trading_robot.portfolio_transactions import PortfolioTransactionCoordinator


def checksum(state):
    return PortfolioSnapshotLease.from_state(state).document_checksum


def repository(tmp_path):
    r = PortfolioRepository(tmp_path / 'portfolio.json')
    r.save(PortfolioState.empty(account_id='synthetic-account'))
    return r


def test_concurrent_same_revision_primary_write_is_not_overwritten(tmp_path):
    r=repository(tmp_path); before=r.load(); external=replace(before, warnings=('concurrent snapshot',))
    c=PortfolioTransactionCoordinator(r)
    def transform(old):
        r.save(external)
        return replace(old, warnings=('stale candidate',))
    with pytest.raises(PortfolioRevisionConflictError):
        c.commit('REFRESH',transform,expected_revision=before.revision)
    assert r.load()==external and r.load().revision==before.revision


def test_concurrent_same_revision_shadow_write_is_not_overwritten(tmp_path):
    r=repository(tmp_path); raced=[]
    class Shadow:
        def write(self,candidate):
            external=replace(candidate,warnings=('concurrent after canonical save',))
            r.save(external);raced.append(external)
            return CompatibilityShadowStatus.DEGRADED
    c=PortfolioTransactionCoordinator(r,shadow_writer=Shadow())
    with pytest.raises(PortfolioRevisionConflictError):
        c.commit('REFRESH',lambda current:replace(current,warnings=('first',)))
    assert r.load()==raced[0]


def test_document_cas_success_and_stale_rejection(tmp_path):
    r=repository(tmp_path); old=r.load();new=replace(old,warnings=('новый snapshot',))
    r.save(new,expected_revision=old.revision,expected_document_checksum=checksum(old))
    with pytest.raises(PortfolioRevisionConflictError):
        r.save(old,expected_revision=old.revision,expected_document_checksum=checksum(old))
    assert r.load()==new


@pytest.mark.parametrize('bad',['x'*64,'a'*63,'A'*64,True,42])
def test_bad_checksum_is_rejected_without_write(tmp_path,bad):
    r=repository(tmp_path);data=r.path.read_bytes()
    with pytest.raises(ValueError):r.save(r.load(),expected_document_checksum=bad)
    assert r.path.read_bytes()==data


def test_expected_missing_document_cannot_initialize(tmp_path):
    r=PortfolioRepository(tmp_path/'missing.json')
    with pytest.raises(PortfolioRevisionConflictError):
        r.save(PortfolioState.empty(),expected_document_checksum='0'*64)
    assert not r.path.exists()


def test_transaction_rejects_stale_input_before_transform(tmp_path):
    r=repository(tmp_path);old=r.load();r.save(replace(old,warnings=('new',)))
    called=[]
    with pytest.raises(PortfolioRevisionConflictError):
        PortfolioTransactionCoordinator(r).commit('REFRESH',lambda p:called.append(1),
                                                expected_document_checksum=checksum(old))
    assert called==[]


def test_hash_matches_existing_lease_and_legacy_api_unchanged(tmp_path):
    from trading_robot.portfolio_repository import portfolio_document_checksum
    r=repository(tmp_path);old=replace(r.load(),warnings=('русский текст',))
    assert portfolio_document_checksum(old)==checksum(old)
    r.save(old); r.save(replace(old,warnings=()))
    assert r.load().warnings==()


def test_manager_refuses_same_revision_change_during_broker_read(tmp_path,monkeypatch):
    from tests.test_portfolio_manager_v3_7 import FakePortfolioAPI,make_manager
    api=FakePortfolioAPI(lots=1);manager=make_manager(tmp_path,api)
    before=manager.repository.load();external=replace(before,warnings=('concurrent during provider read',))
    from types import SimpleNamespace
    from trading_robot.portfolio_observation import PortfolioObservationPolicy
    policy=PortfolioObservationPolicy('account-1', {'uid-sber':SimpleNamespace(asset_class='SHARE',lot_size=1,currency='RUB')})
    original=api.get_portfolio
    def read(account):
        raw=original(account)
        raw['accountId']=account
        raw['totalAmountCurrencies']={'currency':'rub','units':'50000','nano':0}
        manager.repository.save(external)
        return raw
    monkeypatch.setattr(api,'get_portfolio',read)
    with pytest.raises(PortfolioRevisionConflictError):manager.refresh(record_event=False,observation_policy=policy)
    assert manager.repository.load()==external and external.revision==before.revision
