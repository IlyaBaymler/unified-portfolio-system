from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_robot.portfolio_model import PortfolioState
from trading_robot.portfolio_repository import (
    PortfolioAccountScopeError,
    PortfolioChecksumError,
    PortfolioRepository,
    PortfolioRepositoryError,
)


def test_repository_initializes_checksum_managed_empty_state(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    repository = PortfolioRepository(path)
    result = repository.initialize_empty()
    assert result is not None
    assert path.exists()
    assert path.with_name(path.name + ".sha256").exists()
    assert repository.load().state_status == "EMPTY"


def test_repository_save_load_and_status(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    repository = PortfolioRepository(path)
    repository.save(PortfolioState.empty(account_id="account-1"))
    loaded = repository.load(expected_account_id="account-1")
    status = repository.status()
    assert loaded.account_id == "account-1"
    assert status.valid is True
    assert status.version == 2
    assert status.account_id == "account-1"


def test_repository_rejects_checksum_mismatch(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    repository = PortfolioRepository(path)
    repository.save(PortfolioState.empty(account_id="a"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["state_status"] = "TAMPERED"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(PortfolioChecksumError, match="checksum mismatch"):
        repository.load()


def test_repository_rejects_account_scope_change(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(PortfolioState.empty(account_id="account-1"))
    with pytest.raises(PortfolioAccountScopeError, match="different account"):
        repository.save(PortfolioState.empty(account_id="account-2"))


def test_repository_expected_account_mismatch_is_fail_closed(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(PortfolioState.empty(account_id="account-1"))
    with pytest.raises(PortfolioAccountScopeError, match="account mismatch"):
        repository.load(expected_account_id="account-2")


def test_repository_preserves_last_good_on_second_save(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    first = PortfolioState.empty(account_id="account-1")
    repository.save(first)
    second = first.with_freshness(first.freshness, warning="second")
    repository.save(second)
    restored = repository.load_last_good(expected_account_id="account-1")
    assert restored == first


def test_repository_corrupt_json_is_not_silently_reset(tmp_path: Path):
    path = tmp_path / "portfolio_state.json"
    path.write_text("{broken", encoding="utf-8")
    repository = PortfolioRepository(path)
    with pytest.raises(PortfolioRepositoryError):
        repository.initialize_empty()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_repository_inspection_detects_valid_schema(tmp_path: Path):
    repository = PortfolioRepository(tmp_path / "portfolio_state.json")
    repository.save(PortfolioState.empty())
    report = repository.inspect()
    assert report.valid is True
    assert report.schema_version == 2
