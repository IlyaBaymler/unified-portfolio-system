"""Offline Q7 preparation and evidence tooling.

This module deliberately has no provider client and no CL7 transition command.
Stage B still runs through the separately gated CL7 operator surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CURRENT = Path(__file__).resolve().parents[1]
if str(CURRENT) not in sys.path:
    sys.path.insert(0, str(CURRENT))

from trading_robot import __version__
from trading_robot.broker_read_adapters import TBANK_OPERATION_CODEC
from trading_robot.cash_ledger_opening_reconciliation import CL4_OPENING_CODEC
from trading_robot.cash_ledger_persistence import CashLedgerStore
from trading_robot.central_order_manager import CentralOrderManager, CentralOrderStore
from trading_robot.gui_runtime_controller import (
    ConfiguredExecutionSet,
    ConfiguredRuntimeBinding,
)
from trading_robot.instrument_runtime import InstrumentRuntimeStore
from trading_robot.multi_instrument_config import MultiInstrumentProfileStore
from trading_robot.portfolio_model import PortfolioState, SnapshotFreshness
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.risk_persistence import RiskProfileStore, RiskStateStore
from trading_robot.risk_runtime import risk_state_guard_hash
from trading_robot.runtime_backup import RuntimeBackupManager
from trading_robot.runtime_bootstrap import bootstrap_runtime
from trading_robot.runtime_cash_authority import (
    RuntimeCashAuthorityManager,
    RuntimeCashAuthorityState,
    RuntimeCashAuthorityStore,
    derive_account_scope,
)
from trading_robot.secret_provider import (
    Q7ProtectedSecrets,
    Q7SecretError,
    SecretProvider,
    WindowsCredentialManagerProvider,
    provision_q7_identity,
    resolve_q7_protected_secrets,
)

CONTRACT_COMMIT = "1b87316a310e094c8c1c0d2bd3790221b49f60b4"
CONTRACT_TREE = "610e544f802cea599fbfce56ede0a72a7e14c945"
CONTRACT_SHA256 = "119fba8413ec367390eb86072b264d5f23594e8ae2c023b28115d6554636fbaf"
ACTIVATION_EXPERIMENT_ID = "CL8-Q7-CL7-ACTIVATION-V1"
BURNIN_EXPERIMENT_ID = "CL8-SANDBOX-BURNIN-V1"

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_KEY_ID = re.compile(r"[A-Z0-9][A-Z0-9_.:-]{2,63}")
_CODECS = (CL4_OPENING_CODEC, TBANK_OPERATION_CODEC)

_PLANNED_COMMANDS = [
    {"command": "prepare", "confirmation": RuntimeCashAuthorityManager.PREPARE_PHRASE},
    {"command": "confirm", "confirmation": RuntimeCashAuthorityManager.CONFIRM_PHRASE},
    {
        "command": "activate",
        "confirmation": RuntimeCashAuthorityManager.ACTIVATE_PHRASE,
    },
    {"command": "arm", "confirmation": RuntimeCashAuthorityManager.ARM_PHRASE},
]

_RECORD_FIELDS = {
    "v3.10-cl8-q7-offline-materialization": {
        "version",
        "domain",
        "candidate_commit",
        "candidate_tree",
        "contract_commit",
        "contract_tree",
        "contract_sha256",
        "runtime_instance_id",
        "account_scope_sha256",
        "configured_set_sha256",
        "configured_instrument_count",
        "secret_provider",
        "secret_provider_secure",
        "token_present",
        "account_present",
        "identity_key_present",
        "identity_key_id",
        "authority_state",
        "authority_revision",
        "authority_record_sha256",
        "ledger_present",
        "central_present",
        "portfolio_present",
        "risk_present",
        "b0_backup_sha256",
        "b0_backup_size_bytes",
        "b0_manifest_sha256",
        "b0_verification_status",
        "provider_calls_performed",
        "provider_mutations_performed",
        "overall_status",
        "generated_at",
        "record_sha256",
    },
    "v3.10-cl8-q7-cl7-activation-preparation": {
        "version",
        "domain",
        "experiment_id",
        "candidate_commit",
        "candidate_tree",
        "implementation_commit",
        "implementation_tree",
        "contract_commit",
        "contract_tree",
        "contract_sha256",
        "stage_a_record_sha256",
        "stage_a_canonical_summary_sha256",
        "stage_a_overall_status",
        "runtime_instance_id",
        "b0_backup_sha256",
        "b0_backup_size_bytes",
        "b0_manifest_sha256",
        "configured_set_sha256",
        "account_scope_nomination_sha256",
        "identity_key_id",
        "secret_provider",
        "secret_provider_secure",
        "token_present",
        "account_present",
        "identity_key_present",
        "pre_authority_state",
        "pre_authority_revision",
        "pre_authority_sha256",
        "central_quiescent",
        "portfolio_valid",
        "risk_valid",
        "ledger_valid",
        "sandbox_environment_proven",
        "provider_calls_before_authorization",
        "provider_mutations_before_authorization",
        "planned_commands",
        "overall_status",
        "generated_at",
        "record_sha256",
    },
    "v3.10-cl8-q7-final-preparation": {
        "version",
        "domain",
        "candidate_commit",
        "candidate_tree",
        "implementation_commit",
        "implementation_tree",
        "contract_commit",
        "contract_tree",
        "contract_sha256",
        "activation_preparation_sha256",
        "activation_preparation_record_sha256",
        "activation_experiment_id",
        "activation_overall_status",
        "runtime_instance_id",
        "q4_artifact_identity_sha256",
        "q5_privacy_summary_sha256",
        "configured_set_sha256",
        "configured_instrument_count",
        "account_scope_sha256",
        "identity_key_id",
        "authority_state",
        "authority_revision",
        "authority_record_sha256",
        "activation_context_sha256",
        "fresh_context_sha256",
        "reconciliation_status",
        "availability_status",
        "cash_context_status",
        "ledger_revision",
        "ledger_head_sha256",
        "central_revision",
        "portfolio_revision",
        "risk_policy_hash",
        "risk_state_guard_hash",
        "b1_backup_sha256",
        "b1_backup_size_bytes",
        "b1_manifest_sha256",
        "secret_provider",
        "secret_provider_secure",
        "token_present",
        "account_present",
        "identity_key_present",
        "post_attempt_count",
        "pending_dispatch_proof_sha256",
        "preparation_provider_read_rebuild_performed",
        "preparation_provider_order_mutations",
        "overall_status",
        "generated_at",
        "record_sha256",
    },
}


class Q7PreparationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "Q7_PREPARATION_FAILED").strip().upper()
        super().__init__(self.reason)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_record(payload: Mapping[str, Any]) -> bytes:
    if "record_sha256" in payload:
        raise Q7PreparationError("CALLER_RECORD_SHA256_FORBIDDEN")
    clean = dict(payload)
    clean["record_sha256"] = _sha256_bytes(_canonical_bytes(clean))
    return _canonical_bytes(clean)


def _record_schema(value: Mapping[str, Any]) -> None:
    domain = value.get("domain")
    expected = _RECORD_FIELDS.get(domain)
    if expected is None:
        return
    if set(value) != expected:
        raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    if value.get("version") != 1:
        raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    for field in (
        "candidate_commit",
        "candidate_tree",
        "contract_commit",
        "contract_tree",
    ):
        if _HEX40.fullmatch(str(value.get(field, ""))) is None:
            raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    for field, item in value.items():
        if (
            field.endswith(("sha256", "_hash"))
            and field != "pending_dispatch_proof_sha256"
            and (type(item) is not str or _HEX64.fullmatch(item) is None)
        ):
            raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    for field in (
        "secret_provider_secure",
        "token_present",
        "account_present",
        "identity_key_present",
        "central_present",
        "portfolio_present",
        "risk_present",
        "ledger_present",
        "provider_calls_performed",
        "provider_mutations_performed",
        "central_quiescent",
        "portfolio_valid",
        "risk_valid",
        "ledger_valid",
        "sandbox_environment_proven",
        "preparation_provider_read_rebuild_performed",
    ):
        if field in value and type(value[field]) is not bool:
            raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    for field in (
        "configured_instrument_count",
        "authority_revision",
        "pre_authority_revision",
        "provider_calls_before_authorization",
        "provider_mutations_before_authorization",
        "ledger_revision",
        "central_revision",
        "portfolio_revision",
        "b0_backup_size_bytes",
        "b1_backup_size_bytes",
        "post_attempt_count",
        "preparation_provider_order_mutations",
    ):
        if field in value and (type(value[field]) is not int or value[field] < 0):
            raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    if _KEY_ID.fullmatch(str(value.get("identity_key_id", ""))) is None:
        raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    if (
        value.get("contract_commit") != CONTRACT_COMMIT
        or value.get("contract_tree") != CONTRACT_TREE
    ):
        raise Q7PreparationError("EVIDENCE_CONTRACT_MISMATCH")
    if value.get("contract_sha256") != CONTRACT_SHA256:
        raise Q7PreparationError("EVIDENCE_CONTRACT_MISMATCH")
    if (
        not isinstance(value.get("generated_at"), str)
        or not value["generated_at"].strip()
    ):
        raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    if domain == "v3.10-cl8-q7-offline-materialization":
        if (
            value["configured_instrument_count"] not in {2, 3}
            or value["authority_state"] != "LEGACY_ACTIVE"
            or value["authority_revision"] != 0
            or value["b0_verification_status"] != "VERIFIED"
            or value["secret_provider_secure"] is not True
            or any(
                value[field] is not True
                for field in (
                    "token_present",
                    "account_present",
                    "identity_key_present",
                    "ledger_present",
                    "central_present",
                    "portfolio_present",
                    "risk_present",
                )
            )
            or value["provider_calls_performed"] is not False
            or value["provider_mutations_performed"] is not False
            or value["overall_status"] != "ACTIVATION_REQUIRED"
        ):
            raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    elif domain == "v3.10-cl8-q7-cl7-activation-preparation":
        if (
            value["experiment_id"] != ACTIVATION_EXPERIMENT_ID
            or value["implementation_commit"] != value["candidate_commit"]
            or value["implementation_tree"] != value["candidate_tree"]
            or value["stage_a_overall_status"] != "ACTIVATION_REQUIRED"
            or value["pre_authority_state"] != "LEGACY_ACTIVE"
            or value["pre_authority_revision"] != 0
            or value["planned_commands"] != _PLANNED_COMMANDS
            or value["secret_provider_secure"] is not True
            or any(
                value[field] is not True
                for field in (
                    "token_present",
                    "account_present",
                    "identity_key_present",
                    "central_quiescent",
                    "portfolio_valid",
                    "risk_valid",
                    "ledger_valid",
                    "sandbox_environment_proven",
                )
            )
            or value["provider_calls_before_authorization"] != 0
            or value["provider_mutations_before_authorization"] != 0
            or value["overall_status"] != "READY_FOR_SEPARATE_ACTIVATION_AUTHORIZATION"
        ):
            raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")
    else:
        if (
            value["implementation_commit"] != value["candidate_commit"]
            or value["implementation_tree"] != value["candidate_tree"]
            or value["activation_experiment_id"] != ACTIVATION_EXPERIMENT_ID
            or value["activation_overall_status"]
            != "READY_FOR_SEPARATE_ACTIVATION_AUTHORIZATION"
            or value["authority_state"] != "EXACT_CASH_ARMED"
            or value["reconciliation_status"] != "MATCHED"
            or value["availability_status"] != "READY"
            or value["cash_context_status"] != "READY_FOR_LOCKED_REVALIDATION"
            or value["secret_provider_secure"] is not True
            or any(
                value[field] is not True
                for field in (
                    "token_present",
                    "account_present",
                    "identity_key_present",
                    "preparation_provider_read_rebuild_performed",
                )
            )
            or value["post_attempt_count"] != 0
            or value["pending_dispatch_proof_sha256"] is not None
            or value["preparation_provider_order_mutations"] != 0
            or value["overall_status"] != "PASS"
        ):
            raise Q7PreparationError("EVIDENCE_SCHEMA_INVALID")


def verify_record_bytes(
    raw: bytes,
    *,
    expected_domain: str | None = None,
) -> dict[str, Any]:
    if raw.startswith(b"\xef\xbb\xbf") or raw.endswith(b"\n"):
        raise Q7PreparationError("EVIDENCE_ENCODING_INVALID")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Q7PreparationError("EVIDENCE_ENCODING_INVALID") from exc
    if not isinstance(value, dict) or _canonical_bytes(value) != raw:
        raise Q7PreparationError("EVIDENCE_NOT_CANONICAL")
    digest = value.get("record_sha256")
    if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
        raise Q7PreparationError("EVIDENCE_RECORD_SHA256_INVALID")
    preimage = dict(value)
    del preimage["record_sha256"]
    if _sha256_bytes(_canonical_bytes(preimage)) != digest:
        raise Q7PreparationError("EVIDENCE_RECORD_SHA256_MISMATCH")
    if expected_domain is not None and value.get("domain") != expected_domain:
        raise Q7PreparationError("EVIDENCE_DOMAIN_MISMATCH")
    _record_schema(value)
    return value


def write_record_immutable(path: Path, payload: Mapping[str, Any]) -> tuple[str, str]:
    raw = build_record(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
    except FileExistsError as exc:
        raise Q7PreparationError("EVIDENCE_ALREADY_EXISTS") from exc
    verified = verify_record_bytes(raw, expected_domain=str(payload["domain"]))
    return str(verified["record_sha256"]), _sha256_bytes(raw)


def _identity(value: str, length: int) -> str:
    normalized = str(value or "").strip().lower()
    matcher = _HEX40 if length == 40 else _HEX64
    if matcher.fullmatch(normalized) is None:
        raise Q7PreparationError("CANDIDATE_IDENTITY_INVALID")
    return normalized


def _verify_source_candidate(
    candidate_commit: str,
    candidate_tree: str,
    *,
    repository: Path | None = None,
) -> None:
    """Bind the declared candidate to the exact clean checkout before I/O."""

    commit = _identity(candidate_commit, 40)
    tree = _identity(candidate_tree, 40)
    repository = (repository or CURRENT.parent).resolve()

    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", "-c", f"safe.directory={repository.as_posix()}", *args],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise Q7PreparationError("SOURCE_CUSTODY_UNAVAILABLE") from exc

    head = git("rev-parse", "HEAD")
    if head != commit:
        raise Q7PreparationError("CANDIDATE_COMMIT_MISMATCH")
    if git("rev-parse", f"{commit}^{{tree}}") != tree:
        raise Q7PreparationError("CANDIDATE_TREE_MISMATCH")
    if git("rev-parse", "HEAD^{tree}") != tree:
        raise Q7PreparationError("CANDIDATE_TREE_MISMATCH")
    if git("status", "--porcelain=v1", "--untracked-files=all"):
        raise Q7PreparationError("SOURCE_TREE_DIRTY")


def _selected_provider(provider: SecretProvider | None) -> SecretProvider:
    if provider is not None:
        return provider
    try:
        return WindowsCredentialManagerProvider()
    except Exception as exc:
        raise Q7PreparationError("SECRET_PROVIDER_UNAVAILABLE") from exc


def _resolve(
    provider: SecretProvider | None,
) -> tuple[SecretProvider, Q7ProtectedSecrets]:
    selected = _selected_provider(provider)
    try:
        return selected, resolve_q7_protected_secrets(provider=selected)
    except Q7SecretError as exc:
        raise Q7PreparationError(exc.reason) from exc


def _configured_set(
    root: Path,
    *,
    account_id: str,
    account_scope_sha256: str,
    bootstrap_missing: bool,
) -> ConfiguredExecutionSet:
    profiles = MultiInstrumentProfileStore(root / "multi_instrument_profiles.json")
    selected = profiles.load_mode("SANDBOX_EXECUTION")
    if len(selected) not in {2, 3}:
        raise Q7PreparationError("CONFIGURED_INSTRUMENT_COUNT_INVALID")
    runtime_store = InstrumentRuntimeStore(root / "instrument_runtimes.json")
    if bootstrap_missing:
        runtimes = profiles.bootstrap_runtime_registry(
            mode="SANDBOX_EXECUTION",
            account_id=account_id,
            runtime_store=runtime_store,
        )
    else:
        runtimes = runtime_store.load(expected_account_id=account_id)
    by_instrument = {item.config.instrument_id: item for item in runtimes}
    if len(by_instrument) != len(runtimes) or len(runtimes) != len(selected):
        raise Q7PreparationError("CONFIGURED_SET_ORPHAN_OR_DUPLICATE")
    bindings: list[ConfiguredRuntimeBinding] = []
    for profile in selected:
        runtime = by_instrument.get(profile.instrument_id)
        if (
            runtime is None
            or runtime.config.to_dict()
            != profile.to_runtime_config(account_id).to_dict()
        ):
            raise Q7PreparationError("PROFILE_RUNTIME_IDENTITY_MISMATCH")
        bindings.append(ConfiguredRuntimeBinding(profile, runtime))
    return ConfiguredExecutionSet(
        account_scope_sha256=account_scope_sha256,
        bindings=tuple(bindings),
    )


def _open_ledger(root: Path, *, create: bool) -> CashLedgerStore:
    target = root / "cash_ledger_v3_10.sqlite3"
    if target.exists():
        ledger = CashLedgerStore.open(target, _CODECS, busy_timeout_ms=5_000)
    elif create:
        ledger = CashLedgerStore.create(target, _CODECS, busy_timeout_ms=5_000)
    else:
        raise Q7PreparationError("CASH_LEDGER_MISSING")
    ledger.validate()
    return ledger


def _initialize_and_validate_local_owners(
    root: Path,
    secrets: Q7ProtectedSecrets,
    *,
    allow_initialize: bool,
) -> tuple[Any, Any, Any, Any]:
    portfolio = PortfolioRepository(root / "portfolio_state.json")
    state = portfolio.load_optional()
    needs_portfolio_initialization = (
        state is None
        or not state.account_id
        and state.revision == 0
        and state.source == "BOOTSTRAP"
    )
    if needs_portfolio_initialization and not allow_initialize:
        raise Q7PreparationError("PORTFOLIO_CUSTODY_MISSING")
    if needs_portfolio_initialization:
        portfolio.save(PortfolioState.empty(account_id=secrets.account_id))
    else:
        portfolio.load(expected_account_id=secrets.account_id)
    portfolio_state = portfolio.load(expected_account_id=secrets.account_id)

    central_store = CentralOrderStore(root / "central_order_state.json")
    central_state = (
        central_store.initialize(secrets.account_id)
        if allow_initialize
        else central_store.load(expected_account_id=secrets.account_id)
    )
    central = CentralOrderManager(central_store, account_id=secrets.account_id)
    if central.state() != central_state:
        raise Q7PreparationError("CENTRAL_CUSTODY_CHANGED")

    profiles = RiskProfileStore(root / "risk_profiles.json")
    risk_profile = profiles.require_profile("SANDBOX_EXECUTION")
    profile_scope = str(risk_profile.get("account_scope") or "").strip()
    if profile_scope and profile_scope != secrets.account_id:
        raise Q7PreparationError("RISK_ACCOUNT_SCOPE_MISMATCH")
    risk_state = RiskStateStore(root / "risk_state.json").load_account(
        secrets.account_id
    )
    return portfolio_state, central_state, risk_profile, risk_state


def _verified_backup_binding(root: Path, path: Path) -> dict[str, Any]:
    manager = RuntimeBackupManager(root, app_version=__version__)
    verification = manager.verify_backup(path)
    if not verification.valid:
        raise Q7PreparationError("BACKUP_VERIFICATION_FAILED")
    with zipfile.ZipFile(path, "r") as archive:
        manifest_raw = archive.read("manifest.json")
        member_names = set(archive.namelist())
    forbidden = {
        ".env",
        "TBANK_SANDBOX_TOKEN",
        "TBANK_SANDBOX_ACCOUNT_ID",
        "V310_CL_IDENTITY_KEY_HEX",
        "V310_CL_IDENTITY_KEY_ID",
    }
    if member_names & forbidden:
        raise Q7PreparationError("BACKUP_SECRET_MEMBER_FORBIDDEN")
    return {
        "path": path,
        "sha256": _sha256_file(path),
        "size_bytes": path.stat().st_size,
        "manifest_sha256": _sha256_bytes(manifest_raw),
        "status": "VERIFIED",
    }


def _fresh_runtime_context(root: Path) -> tuple[Any, Any]:
    """Run the accepted CL7 READ/sync/rebuild boundary without an order adapter."""

    from tools.v3_10_runtime_cash_cutover import _open_runtime

    live = _open_runtime(root, create_ledger=False, require_provider=True)
    try:
        return live.authority.sync_runtime(**live.inputs())
    finally:
        live.ledger.close()


def _require_fresh_authority(before: Any, fresh: Any, readback: Any) -> None:
    """Accept only the same armed record or this sync's direct custody successor."""

    if (
        fresh.state is not RuntimeCashAuthorityState.EXACT_CASH_ARMED
        or fresh.post_attempt_count != 0
        or fresh.pending_dispatch_proof_sha256 is not None
        or readback.sha256 != fresh.sha256
    ):
        raise Q7PreparationError("FRESH_AUTHORITY_SUBSTITUTION")
    if fresh.sha256 == before.sha256:
        if fresh.record_revision != before.record_revision:
            raise Q7PreparationError("FRESH_AUTHORITY_SUBSTITUTION")
        return
    if (
        fresh.transition_kind != "SYNC_ADVANCED"
        or fresh.record_revision != before.record_revision + 1
        or fresh.previous_record_sha256 != before.sha256
    ):
        raise Q7PreparationError("FRESH_AUTHORITY_SUBSTITUTION")
    # The store validates the full transition pair; these bindings make the
    # accepted change explicit at the final preparation boundary as well.
    for name in (
        "account_scope_sha256",
        "activation_context_sha256",
        "cutover_generation",
        "environment",
        "ever_exact_activated",
        "identity_key_id",
        "opening_cutoff",
        "opening_record_sha256",
        "version",
    ):
        if (
            not hasattr(before, name)
            or not hasattr(fresh, name)
            or getattr(fresh, name) != getattr(before, name)
        ):
            raise Q7PreparationError("FRESH_AUTHORITY_SUBSTITUTION")


def _backup_binding(root: Path, output: Path) -> dict[str, Any]:
    manager = RuntimeBackupManager(root, app_version=__version__)
    path = manager.create_backup(output)
    return _verified_backup_binding(root, path)


def materialize_stage_a(
    *,
    runtime_dir: Path,
    output_record: Path,
    backup_output: Path,
    candidate_commit: str,
    candidate_tree: str,
    provider: SecretProvider | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Materialize accepted local custody with exactly zero provider calls."""

    commit = _identity(candidate_commit, 40)
    tree = _identity(candidate_tree, 40)
    _verify_source_candidate(commit, tree)
    root = Path(runtime_dir).resolve()
    selected, secrets = _resolve(provider)
    report = bootstrap_runtime(root, secret_provider=selected)
    if report.errors:
        raise Q7PreparationError("RUNTIME_BOOTSTRAP_FAILED")
    account_scope = derive_account_scope(
        secrets.account_id,
        identity_key=secrets.identity_key,
        identity_key_id=secrets.identity_key_id,
    )
    configured = _configured_set(
        root,
        account_id=secrets.account_id,
        account_scope_sha256=account_scope,
        bootstrap_missing=True,
    )
    portfolio, central, risk_profile, _risk_state = (
        _initialize_and_validate_local_owners(
            root,
            secrets,
            allow_initialize=True,
        )
    )
    ledger = _open_ledger(root, create=True)
    try:
        ledger.validate()
    finally:
        ledger.close()
    authority = RuntimeCashAuthorityStore(root).bootstrap(transition_at=_now())
    if authority.state is RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING:
        raise Q7PreparationError("RECOVERY_REQUIRED")
    if authority.identity_key_id not in {None, secrets.identity_key_id}:
        raise Q7PreparationError("IDENTITY_KEY_MISMATCH")
    if authority.account_scope_sha256 not in {None, account_scope}:
        raise Q7PreparationError("ACCOUNT_SCOPE_MISMATCH")
    if (
        authority.state is not RuntimeCashAuthorityState.LEGACY_ACTIVE
        or authority.record_revision != 0
        or authority.ever_exact_activated
    ):
        raise Q7PreparationError("STAGE_A_AUTHORITY_STATE_INVALID")
    backup = _backup_binding(root, Path(backup_output))
    runtime_instance_id = _sha256_bytes(
        _canonical_bytes(
            {
                "account_scope_sha256": account_scope,
                "candidate_commit": commit,
                "configured_set_sha256": configured.identity_sha256,
                "domain": "v3.10-cl8-q7-runtime-instance",
                "identity_key_id": secrets.identity_key_id,
                "version": 1,
            }
        )
    )
    payload = {
        "version": 1,
        "domain": "v3.10-cl8-q7-offline-materialization",
        "candidate_commit": commit,
        "candidate_tree": tree,
        "contract_commit": CONTRACT_COMMIT,
        "contract_tree": CONTRACT_TREE,
        "contract_sha256": CONTRACT_SHA256,
        "runtime_instance_id": runtime_instance_id,
        "account_scope_sha256": account_scope,
        "configured_set_sha256": configured.identity_sha256,
        "configured_instrument_count": len(configured.bindings),
        "secret_provider": secrets.provider,
        "secret_provider_secure": secrets.secure,
        "token_present": True,
        "account_present": True,
        "identity_key_present": True,
        "identity_key_id": secrets.identity_key_id,
        "authority_state": authority.state.value,
        "authority_revision": authority.record_revision,
        "authority_record_sha256": authority.sha256,
        "ledger_present": True,
        "central_present": central.account_id == secrets.account_id,
        "portfolio_present": portfolio.account_id == secrets.account_id,
        "risk_present": bool(risk_profile.get("policy_hash")),
        "b0_backup_sha256": backup["sha256"],
        "b0_backup_size_bytes": backup["size_bytes"],
        "b0_manifest_sha256": backup["manifest_sha256"],
        "b0_verification_status": backup["status"],
        "provider_calls_performed": False,
        "provider_mutations_performed": False,
        "overall_status": "ACTIVATION_REQUIRED",
        "generated_at": generated_at or _now(),
    }
    record_sha, file_sha = write_record_immutable(Path(output_record), payload)
    return {
        "status": payload["overall_status"],
        "record_sha256": record_sha,
        "canonical_summary_sha256": file_sha,
        "runtime_instance_id": runtime_instance_id,
        "identity_key_id": secrets.identity_key_id,
        "provider_calls_performed": False,
        "provider_mutations_performed": False,
    }


def prepare_activation(
    *,
    runtime_dir: Path,
    stage_a_record: Path,
    b0_backup: Path,
    output_record: Path,
    candidate_commit: str,
    candidate_tree: str,
    provider: SecretProvider | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Freeze Stage-B inputs without opening a provider transport."""

    commit = _identity(candidate_commit, 40)
    tree = _identity(candidate_tree, 40)
    _verify_source_candidate(commit, tree)
    stage_a = verify_record_bytes(
        Path(stage_a_record).read_bytes(),
        expected_domain="v3.10-cl8-q7-offline-materialization",
    )
    if stage_a["candidate_commit"] != commit or stage_a["candidate_tree"] != tree:
        raise Q7PreparationError("CANDIDATE_SUBSTITUTION")
    root = Path(runtime_dir).resolve()
    b0 = _verified_backup_binding(root, Path(b0_backup))
    if (
        b0["sha256"] != stage_a["b0_backup_sha256"]
        or b0["size_bytes"] != stage_a["b0_backup_size_bytes"]
        or b0["manifest_sha256"] != stage_a["b0_manifest_sha256"]
    ):
        raise Q7PreparationError("B0_BACKUP_SUBSTITUTION")
    _selected, secrets = _resolve(provider)
    if (
        stage_a["secret_provider"] != secrets.provider
        or stage_a["secret_provider_secure"] is not secrets.secure
        or stage_a["identity_key_id"] != secrets.identity_key_id
    ):
        raise Q7PreparationError("SECRET_CUSTODY_SUBSTITUTION")
    authority = RuntimeCashAuthorityStore(root).load(allow_missing_legacy=False)
    if authority.identity_key_id not in {None, secrets.identity_key_id}:
        raise Q7PreparationError("IDENTITY_KEY_MISMATCH")
    account_scope = derive_account_scope(
        secrets.account_id,
        identity_key=secrets.identity_key,
        identity_key_id=secrets.identity_key_id,
    )
    if stage_a.get("account_scope_sha256") != account_scope:
        raise Q7PreparationError("ACCOUNT_SCOPE_SUBSTITUTION")
    if (
        authority.state is not RuntimeCashAuthorityState.LEGACY_ACTIVE
        or authority.record_revision != 0
        or authority.ever_exact_activated
        or authority.sha256 != stage_a["authority_record_sha256"]
    ):
        raise Q7PreparationError("AUTHORITY_STATE_SUBSTITUTION")
    configured = _configured_set(
        root,
        account_id=secrets.account_id,
        account_scope_sha256=account_scope,
        bootstrap_missing=False,
    )
    portfolio, central, risk_profile, _risk_state = (
        _initialize_and_validate_local_owners(
            root,
            secrets,
            allow_initialize=False,
        )
    )
    ledger = _open_ledger(root, create=False)
    try:
        ledger.validate()
    finally:
        ledger.close()
    if central.blocking_intent is not None:
        raise Q7PreparationError("CENTRAL_NOT_QUIESCENT")
    if configured.identity_sha256 != stage_a["configured_set_sha256"]:
        raise Q7PreparationError("CONFIGURED_SET_SUBSTITUTION")
    payload = {
        "version": 1,
        "domain": "v3.10-cl8-q7-cl7-activation-preparation",
        "experiment_id": ACTIVATION_EXPERIMENT_ID,
        "candidate_commit": commit,
        "candidate_tree": tree,
        "implementation_commit": commit,
        "implementation_tree": tree,
        "contract_commit": CONTRACT_COMMIT,
        "contract_tree": CONTRACT_TREE,
        "contract_sha256": CONTRACT_SHA256,
        "stage_a_record_sha256": stage_a["record_sha256"],
        "stage_a_canonical_summary_sha256": _sha256_file(Path(stage_a_record)),
        "stage_a_overall_status": stage_a["overall_status"],
        "runtime_instance_id": stage_a["runtime_instance_id"],
        "b0_backup_sha256": stage_a["b0_backup_sha256"],
        "b0_backup_size_bytes": stage_a["b0_backup_size_bytes"],
        "b0_manifest_sha256": stage_a["b0_manifest_sha256"],
        "configured_set_sha256": configured.identity_sha256,
        "account_scope_nomination_sha256": account_scope,
        "identity_key_id": secrets.identity_key_id,
        "secret_provider": secrets.provider,
        "secret_provider_secure": secrets.secure,
        "token_present": True,
        "account_present": True,
        "identity_key_present": True,
        "pre_authority_state": authority.state.value,
        "pre_authority_revision": authority.record_revision,
        "pre_authority_sha256": authority.sha256,
        "central_quiescent": True,
        "portfolio_valid": portfolio.account_id == secrets.account_id,
        "risk_valid": bool(risk_profile.get("policy_hash")),
        "ledger_valid": True,
        "sandbox_environment_proven": True,
        "provider_calls_before_authorization": 0,
        "provider_mutations_before_authorization": 0,
        "planned_commands": _PLANNED_COMMANDS,
        "overall_status": "READY_FOR_SEPARATE_ACTIVATION_AUTHORIZATION",
        "generated_at": generated_at or _now(),
    }
    record_sha, file_sha = write_record_immutable(Path(output_record), payload)
    return {
        "status": payload["overall_status"],
        "experiment_id": ACTIVATION_EXPERIMENT_ID,
        "record_sha256": record_sha,
        "canonical_summary_sha256": file_sha,
        "provider_calls_before_authorization": 0,
        "provider_mutations_before_authorization": 0,
        "burnin_authorized": False,
    }


def finalize_preparation(
    *,
    runtime_dir: Path,
    activation_record: Path,
    output_record: Path,
    backup_output: Path,
    candidate_commit: str,
    candidate_tree: str,
    q4_artifact_identity_sha256: str,
    q5_privacy_summary_sha256: str,
    provider: SecretProvider | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Create B1 and final evidence only from an already armed exact runtime."""

    commit = _identity(candidate_commit, 40)
    tree = _identity(candidate_tree, 40)
    _verify_source_candidate(commit, tree)
    activation = verify_record_bytes(
        Path(activation_record).read_bytes(),
        expected_domain="v3.10-cl8-q7-cl7-activation-preparation",
    )
    q4 = _identity(q4_artifact_identity_sha256, 64)
    q5 = _identity(q5_privacy_summary_sha256, 64)
    if activation["candidate_commit"] != commit or activation["candidate_tree"] != tree:
        raise Q7PreparationError("CANDIDATE_SUBSTITUTION")
    root = Path(runtime_dir).resolve()
    _selected, secrets = _resolve(provider)
    if (
        activation["secret_provider"] != secrets.provider
        or activation["secret_provider_secure"] is not secrets.secure
        or activation["identity_key_id"] != secrets.identity_key_id
    ):
        raise Q7PreparationError("SECRET_CUSTODY_SUBSTITUTION")
    authority = RuntimeCashAuthorityStore(root).load(allow_missing_legacy=False)
    if (
        authority.state is not RuntimeCashAuthorityState.EXACT_CASH_ARMED
        or authority.post_attempt_count != 0
        or authority.pending_dispatch_proof_sha256 is not None
    ):
        raise Q7PreparationError("EXACT_CASH_ARMED_PREDICATE_FAILED")
    if authority.identity_key_id != secrets.identity_key_id:
        raise Q7PreparationError("IDENTITY_KEY_MISMATCH")
    account_scope = derive_account_scope(
        secrets.account_id,
        identity_key=secrets.identity_key,
        identity_key_id=secrets.identity_key_id,
    )
    if authority.account_scope_sha256 != account_scope:
        raise Q7PreparationError("ACCOUNT_SCOPE_MISMATCH")
    if activation["account_scope_nomination_sha256"] != account_scope:
        raise Q7PreparationError("ACCOUNT_SCOPE_SUBSTITUTION")
    configured = _configured_set(
        root,
        account_id=secrets.account_id,
        account_scope_sha256=account_scope,
        bootstrap_missing=False,
    )
    if configured.identity_sha256 != activation["configured_set_sha256"]:
        raise Q7PreparationError("CONFIGURED_SET_SUBSTITUTION")
    portfolio, central, risk_profile, risk_state = (
        _initialize_and_validate_local_owners(
            root,
            secrets,
            allow_initialize=False,
        )
    )
    if central.blocking_intent is not None:
        raise Q7PreparationError("CENTRAL_NOT_QUIESCENT")
    if portfolio.freshness is not SnapshotFreshness.FRESH or portfolio.blocking:
        raise Q7PreparationError("PORTFOLIO_NOT_COHERENT")
    ledger = _open_ledger(root, create=False)
    try:
        ledger_snapshot = ledger.validate()
    finally:
        ledger.close()
    fresh_authority, fresh_evidence = _fresh_runtime_context(root)
    _require_fresh_authority(
        authority,
        fresh_authority,
        RuntimeCashAuthorityStore(root).load(allow_missing_legacy=False),
    )
    authority = fresh_authority
    ledger = _open_ledger(root, create=False)
    try:
        ledger_snapshot = ledger.validate()
    finally:
        ledger.close()
    reconciliation_status = getattr(
        getattr(fresh_evidence.reconciliation, "status", None), "value", None
    )
    reconciliation_kind = getattr(
        getattr(fresh_evidence.reconciliation, "discrepancy_kind", None), "value", None
    )
    availability_status = getattr(
        getattr(fresh_evidence.availability, "status", None), "value", None
    )
    context_status = getattr(
        getattr(fresh_evidence.context, "status", None), "value", None
    )
    if (
        reconciliation_status != "MATCHED"
        or reconciliation_kind != "NONE"
        or availability_status != "READY"
        or context_status != "READY_FOR_LOCKED_REVALIDATION"
    ):
        raise Q7PreparationError("FRESH_CASH_CONTEXT_NOT_READY")
    if (
        authority.ledger_revision != ledger_snapshot.ledger_revision
        or authority.ledger_head_sha256 != ledger_snapshot.ledger_head_sha256
        or fresh_evidence.context.ledger_revision != ledger_snapshot.ledger_revision
        or fresh_evidence.context.ledger_head_sha256
        != ledger_snapshot.ledger_head_sha256
    ):
        raise Q7PreparationError("LEDGER_AUTHORITY_BINDING_MISMATCH")
    risk_guard_hash = risk_state_guard_hash(risk_state)
    context_risk_guard_hash = getattr(
        fresh_evidence.context, "risk_state_guard_hash", None
    )
    if (
        type(risk_guard_hash) is not str
        or _HEX64.fullmatch(risk_guard_hash) is None
        or type(context_risk_guard_hash) is not str
        or _HEX64.fullmatch(context_risk_guard_hash) is None
    ):
        raise Q7PreparationError("FRESH_OWNER_BINDING_MISMATCH")
    if (
        fresh_evidence.context.account_scope_sha256 != account_scope
        or fresh_evidence.context.identity_key_id != secrets.identity_key_id
        or fresh_evidence.context.central_order_revision != central.revision
        or fresh_evidence.context.portfolio_revision != portfolio.revision
        or fresh_evidence.context.risk_policy_hash != str(risk_profile["policy_hash"])
        or context_risk_guard_hash != risk_guard_hash
    ):
        raise Q7PreparationError("FRESH_OWNER_BINDING_MISMATCH")
    backup = _backup_binding(root, Path(backup_output))
    if (
        RuntimeCashAuthorityStore(root).load(allow_missing_legacy=False).sha256
        != authority.sha256
    ):
        raise Q7PreparationError("FRESH_AUTHORITY_SUBSTITUTION")
    payload = {
        "version": 1,
        "domain": "v3.10-cl8-q7-final-preparation",
        "candidate_commit": commit,
        "candidate_tree": tree,
        "implementation_commit": commit,
        "implementation_tree": tree,
        "contract_commit": CONTRACT_COMMIT,
        "contract_tree": CONTRACT_TREE,
        "contract_sha256": CONTRACT_SHA256,
        "activation_preparation_sha256": _sha256_file(Path(activation_record)),
        "activation_preparation_record_sha256": activation["record_sha256"],
        "activation_experiment_id": activation["experiment_id"],
        "activation_overall_status": activation["overall_status"],
        "runtime_instance_id": activation["runtime_instance_id"],
        "q4_artifact_identity_sha256": q4,
        "q5_privacy_summary_sha256": q5,
        "configured_set_sha256": configured.identity_sha256,
        "configured_instrument_count": len(configured.bindings),
        "account_scope_sha256": account_scope,
        "identity_key_id": secrets.identity_key_id,
        "authority_state": authority.state.value,
        "authority_revision": authority.record_revision,
        "authority_record_sha256": authority.sha256,
        "activation_context_sha256": authority.activation_context_sha256,
        "fresh_context_sha256": fresh_evidence.context.sha256,
        "reconciliation_status": reconciliation_status,
        "availability_status": availability_status,
        "cash_context_status": context_status,
        "ledger_revision": ledger_snapshot.ledger_revision,
        "ledger_head_sha256": ledger_snapshot.ledger_head_sha256,
        "central_revision": central.revision,
        "portfolio_revision": portfolio.revision,
        "risk_policy_hash": str(risk_profile["policy_hash"]),
        "risk_state_guard_hash": risk_guard_hash,
        "b1_backup_sha256": backup["sha256"],
        "b1_backup_size_bytes": backup["size_bytes"],
        "b1_manifest_sha256": backup["manifest_sha256"],
        "secret_provider": secrets.provider,
        "secret_provider_secure": secrets.secure,
        "token_present": True,
        "account_present": True,
        "identity_key_present": True,
        "post_attempt_count": authority.post_attempt_count,
        "pending_dispatch_proof_sha256": authority.pending_dispatch_proof_sha256,
        "preparation_provider_read_rebuild_performed": True,
        "preparation_provider_order_mutations": 0,
        "overall_status": "PASS",
        "generated_at": generated_at or _now(),
    }
    record_sha, file_sha = write_record_immutable(Path(output_record), payload)
    return {
        "status": "PASS",
        "record_sha256": record_sha,
        "canonical_summary_sha256": file_sha,
        "burnin_experiment_id": BURNIN_EXPERIMENT_ID,
        "burnin_authorized": False,
        "preparation_provider_order_mutations": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CL8 Q7 bounded preparation tool")
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser("provision-identity")
    provision.add_argument("--identity-key-id", required=True)
    provision.add_argument("--confirmation", required=True)

    materialize = commands.add_parser("materialize")
    for item in (materialize,):
        item.add_argument("--runtime-dir", type=Path, required=True)
        item.add_argument("--candidate-commit", required=True)
        item.add_argument("--candidate-tree", required=True)
        item.add_argument("--output-record", type=Path, required=True)
        item.add_argument("--backup-output", type=Path, required=True)

    activation = commands.add_parser("prepare-activation")
    activation.add_argument("--runtime-dir", type=Path, required=True)
    activation.add_argument("--candidate-commit", required=True)
    activation.add_argument("--candidate-tree", required=True)
    activation.add_argument("--stage-a-record", type=Path, required=True)
    activation.add_argument("--b0-backup", type=Path, required=True)
    activation.add_argument("--output-record", type=Path, required=True)

    final = commands.add_parser("finalize")
    final.add_argument("--runtime-dir", type=Path, required=True)
    final.add_argument("--candidate-commit", required=True)
    final.add_argument("--candidate-tree", required=True)
    final.add_argument("--activation-record", type=Path, required=True)
    final.add_argument("--output-record", type=Path, required=True)
    final.add_argument("--backup-output", type=Path, required=True)
    final.add_argument("--q4-artifact-identity-sha256", required=True)
    final.add_argument("--q5-privacy-summary-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "provision-identity":
            result = provision_q7_identity(
                identity_key_id=args.identity_key_id,
                confirmation=args.confirmation,
            ).to_dict()
        elif args.command == "materialize":
            result = materialize_stage_a(
                runtime_dir=args.runtime_dir,
                output_record=args.output_record,
                backup_output=args.backup_output,
                candidate_commit=args.candidate_commit,
                candidate_tree=args.candidate_tree,
            )
        elif args.command == "prepare-activation":
            result = prepare_activation(
                runtime_dir=args.runtime_dir,
                stage_a_record=args.stage_a_record,
                b0_backup=args.b0_backup,
                output_record=args.output_record,
                candidate_commit=args.candidate_commit,
                candidate_tree=args.candidate_tree,
            )
        else:
            result = finalize_preparation(
                runtime_dir=args.runtime_dir,
                activation_record=args.activation_record,
                output_record=args.output_record,
                backup_output=args.backup_output,
                candidate_commit=args.candidate_commit,
                candidate_tree=args.candidate_tree,
                q4_artifact_identity_sha256=args.q4_artifact_identity_sha256,
                q5_privacy_summary_sha256=args.q5_privacy_summary_sha256,
            )
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    except (Q7PreparationError, Q7SecretError) as exc:
        print(
            json.dumps(
                {
                    "reason": getattr(exc, "reason", "Q7_PREPARATION_FAILED"),
                    "status": "BLOCKED",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    except Exception:  # noqa: BLE001 - privacy-safe CLI boundary
        print(
            json.dumps(
                {"reason": "INTERNAL_BOUNDARY_FAILED", "status": "BLOCKED"},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
