from __future__ import annotations

import hashlib
import json
from pathlib import Path

from trading_robot.risk import RiskPolicy
from trading_robot.risk_persistence import RiskProfileStore
from trading_robot.runtime_bootstrap import (
    CANONICAL_RISK_PROFILE_NAME,
    RUNTIME_BOOTSTRAP_REPORT_NAME,
    bootstrap_runtime,
)
from trading_robot.secret_provider import EnvFileSecretProvider


class FakeSecretProvider:
    def __init__(
        self,
        *,
        value: str | None,
        error: Exception | None = None,
    ) -> None:
        self.name = "Windows Credential Manager"
        self.secure = True
        self.value = value
        self.error = error

    def get(self, key: str) -> str | None:
        if self.error is not None:
            raise self.error
        return self.value

    def set(self, key: str, value: str) -> None:
        self.value = value

    def delete(self, key: str) -> None:
        self.value = None


EXPECTED_RUNTIME_FILES = {
    ".env",
    "strategy_profiles.json",
    "risk_profiles.json",
    "risk_state.json",
    "robot_state.json",
    "portfolio_state.json",
    "sandbox_diagnostic_state.json",
    "trading_events.db",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_first_run_bootstrap_creates_complete_canonical_runtime_set(tmp_path: Path):
    (tmp_path / ".env.example").write_text(
        "TBANK_SANDBOX_TOKEN=\n"
        "TBANK_SANDBOX_ACCOUNT_ID=\n"
        "ROBOT_TICKER=SBER\n"
        "ROBOT_MAX_ORDER_LOTS=1\n",
        encoding="utf-8",
    )

    report = bootstrap_runtime(tmp_path)

    assert report.ok
    assert report.first_run
    assert report.changed
    assert EXPECTED_RUNTIME_FILES <= {path.name for path in tmp_path.iterdir()}
    assert (tmp_path / RUNTIME_BOOTSTRAP_REPORT_NAME).exists()
    report_json = json.loads(
        (tmp_path / RUNTIME_BOOTSTRAP_REPORT_NAME).read_text(encoding="utf-8")
    )
    assert report_json["canonical_risk_profile"] == CANONICAL_RISK_PROFILE_NAME
    assert report_json["first_run"] is True

    strategy_document = json.loads(
        (tmp_path / "strategy_profiles.json").read_text(encoding="utf-8")
    )
    risk_document = json.loads(
        (tmp_path / "risk_profiles.json").read_text(encoding="utf-8")
    )
    assert set(strategy_document["profiles"]) == {"DRY_RUN", "SANDBOX_EXECUTION"}
    assert set(risk_document["profiles"]) == {"DRY_RUN", "SANDBOX_EXECUTION"}


def test_bootstrap_reports_protected_credential_without_false_missing_warning(
    tmp_path: Path,
):
    provider = FakeSecretProvider(value="TOP-SECRET-CANARY")

    report = bootstrap_runtime(tmp_path, secret_provider=provider)
    payload = report.to_dict()

    assert payload["credential_status"] == "credential_present"
    assert payload["secret_provider"] == "Windows Credential Manager"
    assert payload["secret_provider_secure"] is True
    assert payload["secret_present"] is True
    assert not any("credential отсутствует" in item for item in report.warnings)
    assert "TOP-SECRET-CANARY" not in json.dumps(payload)


def test_bootstrap_distinguishes_absent_and_unavailable_provider(tmp_path: Path):
    absent = bootstrap_runtime(
        tmp_path / "absent",
        secret_provider=FakeSecretProvider(value=None),
    )
    unavailable = bootstrap_runtime(
        tmp_path / "unavailable",
        secret_provider=FakeSecretProvider(value=None, error=OSError("offline")),
    )

    assert absent.to_dict()["credential_status"] == "credential_absent"
    assert any("credential отсутствует" in item for item in absent.warnings)
    assert unavailable.to_dict()["credential_status"] == "provider_unavailable"
    assert any("provider недоступен" in item for item in unavailable.warnings)


def test_bootstrap_reports_env_fallback_separately(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("TBANK_SANDBOX_TOKEN=env-only-token\n", encoding="utf-8")

    report = bootstrap_runtime(
        tmp_path,
        secret_provider=EnvFileSecretProvider(env_path),
    )

    assert report.to_dict()["credential_status"] == ".env_fallback"
    assert any(".env fallback" in item for item in report.warnings)
    assert not any("credential отсутствует" in item for item in report.warnings)


def test_bootstrap_is_idempotent_and_preserves_user_runtime_files(tmp_path: Path):
    first = bootstrap_runtime(tmp_path)
    assert first.ok
    protected = EXPECTED_RUNTIME_FILES - {"trading_events.db"}
    before = {name: _sha(tmp_path / name) for name in protected}

    second = bootstrap_runtime(tmp_path)

    assert second.ok
    assert not second.first_run
    assert not second.changed
    assert {name: _sha(tmp_path / name) for name in protected} == before


def test_bootstrap_migrates_valid_legacy_singular_risk_profile(tmp_path: Path):
    legacy = tmp_path / "risk_profile.json"
    store = RiskProfileStore(legacy)
    store.save_profile("DRY_RUN", RiskPolicy())
    store.save_profile("SANDBOX_EXECUTION", RiskPolicy())

    report = bootstrap_runtime(tmp_path)

    assert report.ok
    assert not legacy.exists()
    canonical = tmp_path / CANONICAL_RISK_PROFILE_NAME
    assert canonical.exists()
    assert RiskProfileStore(canonical).require_profile("SANDBOX_EXECUTION")
    assert any(item.action == "MIGRATED" for item in report.items)


def test_bootstrap_never_overwrites_corrupt_existing_risk_profile(tmp_path: Path):
    path = tmp_path / CANONICAL_RISK_PROFILE_NAME
    path.write_text('{"broken":', encoding="utf-8")
    before = path.read_bytes()

    report = bootstrap_runtime(tmp_path)

    assert not report.ok
    assert report.has_errors
    assert path.read_bytes() == before
    assert any("не был перезаписан" in error for error in report.errors)
