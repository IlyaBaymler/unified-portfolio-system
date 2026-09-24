"""One-shot CL8 Q7A live economic-smoke entrypoint.

Import, ``--help`` and preparation validation are offline.  The only live
surface is ``ECONOMIC_SMOKE``.  This module does not create a Strategy, Risk,
Central or execution owner: it binds the accepted owners into one finite
proposal lineage and records privacy-safe custody evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import sys
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

CURRENT = Path(__file__).resolve().parents[1]
if str(CURRENT) not in sys.path:
    sys.path.insert(0, str(CURRENT))

from tools.v3_10_q7a_e2e_smoke import (
    Q7AControlledHooks,
    Q7AControlRecord,
    Q7ASyntheticError,
    _proposal_canonical,
)
from trading_robot.candle_policy import strategy_lookback_days
from trading_robot.central_order_coordinator import (
    CentralOrderCoordinationResult,
    CentralOrderCoordinator,
)
from trading_robot.gui_runtime_controller import (
    ConfiguredExecutionSet,
    GuiCoordinationRequest,
    GuiRuntimeBlockedError,
    _ProductionGuiHooks,
)
from trading_robot.instrument_runtime import InstrumentRuntime
from trading_robot.locking import InterProcessFileLock, LockUnavailableError
from trading_robot.multi_instrument_config import MultiInstrumentProfile
from trading_robot.multi_instrument_strategy import (
    StrategyCandleLoader,
    StrategyProposal,
    build_strategy_proposal,
)
from trading_robot.portfolio_repository import PortfolioRepository
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState
from trading_robot.strategy_runtime import (
    compare_strategy_decisions,
    strategy_suite_from_bot_config,
)
from trading_robot.tbank_sandbox import TBankSandboxClient

LIVE_CONTRACT_COMMIT = "67ce7bb77e506b67326c0e4f3265b3525fa30472"
LIVE_CONTRACT_TREE = "4e52d0298d2cac3b75783da7da11e3c759c56e2f"
Q7A_CONTRACT_COMMIT = "13cf47dbff0b1b1cb4310ee7a49641b580d559e3"
Q7A_CONTRACT_TREE = "44384da47d41cf873bdd7d947804960bb403bb6e"
Q7A_SYNTHETIC_IMPLEMENTATION_COMMIT = "141416eefefcba2c3d90fc721e52282ea0a1ea42"
Q7A_SYNTHETIC_IMPLEMENTATION_TREE = "4e85875b75184d0895e2cdac9cf6852d60f800b6"
PREPARATION_DOMAIN = "CL8_Q7A_LIVE_PREPARATION_V1"
EXPERIMENT_ID = "CL8-Q7-E2E-SMOKE-V1"
LIVE_MODE = "ECONOMIC_SMOKE"
DERIVATION_VERSION = "CL8_Q7A_CONTROLLED_PROPOSAL_V1"

_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z\Z")
_NATIVE_UTC = re.compile(
    r"(?P<prefix>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(?P<fraction>\d{1,9})Z\Z"
)
_QUOTE_UNITS = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_INTERVAL_SECONDS = {
    "CANDLE_INTERVAL_10_MIN": 600,
    "CANDLE_INTERVAL_15_MIN": 900,
    "CANDLE_INTERVAL_30_MIN": 1800,
    "CANDLE_INTERVAL_HOUR": 3600,
    "CANDLE_INTERVAL_DAY": 86400,
}
_PRIMARY_REASONS = frozenset(
    {
        "PREPARATION_INVALID",
        "PREPARATION_SHA_MISMATCH",
        "PREPARATION_ALREADY_CONSUMED",
        "SOURCE_IDENTITY_MISMATCH",
        "RUNTIME_MANIFEST_MISMATCH",
        "ENVIRONMENT_NOT_SANDBOX",
        "ACCOUNT_SCOPE_MISMATCH",
        "CONFIGURED_SET_MISMATCH",
        "CONFIGURED_SET_NOT_ACTIVE",
        "TARGET_NOT_MEMBER",
        "CREDENTIAL_CUSTODY_INVALID",
        "SINGLE_INSTANCE_LOCK_UNAVAILABLE",
        "PROVIDER_READ_FAILED",
        "PROVIDER_READ_SCOPE_INVALID",
        "PROVIDER_READ_INCOMPLETE",
        "LIVE_ACQUISITION_POLICY_MISMATCH",
        "LIVE_ACQUISITION_BUDGET_EXHAUSTED",
        "LIVE_EVIDENCE_EXPIRED_BEFORE_ADMISSION",
        "CL3_SYNC_BLOCKED",
        "PORTFOLIO_REFRESH_BLOCKED",
        "CL4_RECONCILIATION_BLOCKED",
        "CL5_AVAILABILITY_NOT_READY",
        "CL6_CONTEXT_NOT_READY",
        "AUTHORITY_NOT_EXACT_CASH_ARMED",
        "ATTEMPT_BUDGET_EXHAUSTED",
        "PENDING_DISPATCH_PRESENT",
        "CENTRAL_NOT_QUIESCENT",
        "RISK_POLICY_NOT_ENFORCED",
        "RISK_STATE_GUARD_MISMATCH",
        "QUOTE_OR_METADATA_INVALID",
        "CANDLE_ACQUISITION_POLICY_MISMATCH",
        "CANDLE_READ_FAILED",
        "CANDLE_FRAME_INVALID",
        "CANDLE_FRAME_INCOMPLETE",
        "CANDLE_EVIDENCE_STALE",
        "CONTROL_RECORD_INVALID",
        "CONTROLLED_PROPOSAL_DERIVATION_INVALID",
        "PROPOSAL_ADMISSION_BINDING_INVALID",
        "PORTFOLIO_FLAT_POSITION_DRIFT",
        "LOCKED_REVALIDATION_FAILED",
        "POST_ADMISSION_DRIFT",
        "EXISTING_INTENT_REQUIRES_RECOVERY",
        "ATTEMPT_MARKER_FAILED",
        "PROVIDER_SAFE_REJECTED",
        "PROVIDER_OUTCOME_AMBIGUOUS",
        "POSTCONDITION_FAILED",
        "EVIDENCE_WRITE_FAILED",
    }
)
# These are secondary, finite dependency codes. They never become new primary
# terminal reasons and no exception text is copied into shareable evidence.
_POST_MARKER_SYNTHETIC_REASONS = {
    "NON_TARGET_COORDINATION_FORBIDDEN": "TARGET_NOT_MEMBER",
    "SECOND_LIFECYCLE_FORBIDDEN": "PROPOSAL_ADMISSION_BINDING_INVALID",
    "CONTROLLED_PROPOSAL_INVALID": "CONTROLLED_PROPOSAL_DERIVATION_INVALID",
    "PROPOSAL_MARKER_INVALID": "PROPOSAL_ADMISSION_BINDING_INVALID",
    "COORDINATION_REQUEST_INVALID": "PROPOSAL_ADMISSION_BINDING_INVALID",
    "COORDINATION_REQUEST_STALE": "QUOTE_OR_METADATA_INVALID",
    "QUOTE_NOT_FRESH": "QUOTE_OR_METADATA_INVALID",
    "Q7A_ADMISSION_REQUEST_INVALID": "PROPOSAL_ADMISSION_BINDING_INVALID",
    "Q7A_PROPOSAL_DRIFT": "PROPOSAL_ADMISSION_BINDING_INVALID",
    "Q7A_PRIVATE_PROPOSAL_DRIFT": "PROPOSAL_ADMISSION_BINDING_INVALID",
    "Q7A_ADMISSION_BINDING_INVALID": "PROPOSAL_ADMISSION_BINDING_INVALID",
}
_POST_MARKER_GUI_REASONS = {
    "CANDIDATE_QUOTE_READ_FAILED": "PROVIDER_READ_FAILED",
    "CANDIDATE_QUOTE_INVALID": "QUOTE_OR_METADATA_INVALID",
    "CANDIDATE_QUOTE_NOT_FRESH": "QUOTE_OR_METADATA_INVALID",
    "CYCLE_CLOCK_INVALID": "QUOTE_OR_METADATA_INVALID",
    "DECISION_AUDIT_UNAVAILABLE": "PROPOSAL_ADMISSION_BINDING_INVALID",
}
_CANDLE_VALIDATION_REASONS = {
    "FRAME": frozenset(
        {
            "FRAME_TYPE",
            "INDEX_TYPE",
            "INDEX_ORDER",
            "OHLC_TYPE",
            "OHLC_NONFINITE",
            "OHLC_NONPOSITIVE",
            "VOLUME_TYPE",
            "VOLUME_NEGATIVE",
            "OHLC_ORDER",
            "CLOCK_TYPE",
        }
    ),
    "REQUEST_BINDING": frozenset(
        {
            "REQUEST_SHAPE",
            "REQUEST_IDENTITY",
            "REQUEST_TIME_PARSE",
            "REQUEST_RANGE_ORDER",
            "FIRST_BEGIN_BEFORE_FROM",
            "LAST_CLOSE_AFTER_TO",
        }
    ),
}
_PRE_ADMISSION_CHECKPOINTS = frozenset(
    {
        "BEFORE_PROPOSAL_MARKER",
        "PROPOSAL_VERIFICATION",
        "COORDINATION_REQUEST",
        "REQUEST_BINDING",
        "QUOTE_EVIDENCE",
        "LOCAL_CUSTODY_READBACK",
        "LEDGER_READBACK",
        "GATE_A_RECEIPTS",
        "CENTRAL_ADMISSION",
    }
)
_PREPARATION_FIELDS = frozenset(
    {
        "version",
        "domain",
        "experiment_id",
        "candidate_commit",
        "candidate_tree",
        "live_contract_commit",
        "live_contract_tree",
        "q7a_contract_commit",
        "q7a_contract_tree",
        "q7a_synthetic_implementation_commit",
        "q7a_synthetic_implementation_tree",
        "accepted_implementation_commit",
        "accepted_implementation_tree",
        "accepted_q1_summary_sha256",
        "accepted_q4_summary_sha256",
        "accepted_q5_summary_sha256",
        "runtime_manifest_sha256",
        "account_scope_sha256",
        "identity_key_id",
        "environment",
        "configured_set_sha256",
        "target_instrument_sha256",
        "control_record_sha256",
        "risk_policy_sha256",
        "risk_policy_mode",
        "static_metadata_schema_sha256",
        "static_metadata_sha256",
        "currency_evidence_sha256",
        "lot_size_evidence_sha256",
        "account_cash_portfolio_policy",
        "candle_policy",
        "quote_policy",
        "trading_status_policy",
        "live_acquisition_policy_sha256",
        "cl7_final_freshness_seconds",
        "max_provider_post_attempts",
        "automatic_retries",
        "redirect_replays",
        "absolute_deadline_utc",
        "evidence_root_sha256",
        "backup_binding_sha256",
        "pre_admission_stop_rules_sha256",
        "post_admission_same_lineage_rules_sha256",
        "evidence_privacy_policy_sha256",
        "recovery_procedure_sha256",
        "stop_conditions_sha256",
        "created_at_utc",
        "preparation_sha256",
    }
)
_ACCOUNT_POLICY_FIELDS = frozenset(
    {
        "owner",
        "operations_service",
        "operations_method",
        "operations_logical_sessions_per_gate",
        "operations_max_pages",
        "operations_max_items",
        "operations_max_attempts_per_page",
        "operations_policy_sha256",
        "portfolio_service",
        "portfolio_method",
        "portfolio_physical_requests_per_gate",
        "portfolio_logical_reads_gate_a",
        "portfolio_logical_reads_gate_b",
        "withdraw_limits_method",
        "withdraw_limits_requests_per_gate",
        "positions_required",
        "positions_method",
        "orders_service",
        "orders_method",
        "orders_owner",
        "orders_requests_gate_a",
        "orders_requests_gate_b",
        "orders_retries",
        "orders_redirect_replays",
        "orders_automatic_reacquisition",
        "portfolio_owner",
        "owner_graph_sha256",
        "absolute_timeout_policy_sha256",
    }
)
_CANDLE_POLICY_FIELDS = frozenset(
    {
        "owner",
        "service",
        "method",
        "target_instrument_sha256",
        "interval",
        "required_bars",
        "lookback_days",
        "maximum_span_seconds",
        "logical_sessions",
        "physical_requests",
        "retries",
        "redirects_followed",
        "automatic_reacquisition",
        "candle_source_type",
        "maximum_age_seconds",
        "future_skew_seconds",
    }
)
_QUOTE_POLICY_FIELDS = frozenset(
    {
        "owner",
        "service",
        "method",
        "target_instrument_sha256",
        "last_price_type",
        "request_count",
        "retries",
        "redirects_followed",
        "source",
        "maximum_age_seconds",
        "future_skew_seconds",
    }
)
_STATUS_POLICY_FIELDS = frozenset(
    {
        "owner",
        "service",
        "method",
        "target_instrument_sha256",
        "logical_calls",
        "physical_requests",
        "retries",
        "redirect_replays",
        "automatic_reacquisition",
    }
)
_FORBIDDEN_FUTURE_KEYS = frozenset(
    {
        "current_quote_sha256",
        "current_quote",
        "quote_value",
        "quote_timestamp",
        "current_portfolio_revision",
        "current_portfolio_sha256",
        "current_cash",
        "current_watermark",
        "current_cl4_sha256",
        "current_cl5_sha256",
        "current_cl6_sha256",
    }
)


class Q7ALiveError(RuntimeError):
    """Finite, privacy-safe live-entrypoint failure."""

    def __init__(
        self,
        reason: str,
        *,
        dependency_reason: str | None = None,
        candle_validation_stage: str | None = None,
        candle_validation_reason: str | None = None,
    ) -> None:
        normalized = str(reason or "").strip().upper()
        self.reason = (
            normalized if normalized in _PRIMARY_REASONS else "POSTCONDITION_FAILED"
        )
        self.dependency_reason = (
            dependency_reason
            if type(dependency_reason) is str
            and (
                _POST_MARKER_SYNTHETIC_REASONS.get(dependency_reason) == self.reason
                or _POST_MARKER_GUI_REASONS.get(dependency_reason) == self.reason
            )
            else None
        )
        valid_candle_diagnostic = (
            self.reason == "CANDLE_FRAME_INVALID"
            and type(candle_validation_stage) is str
            and type(candle_validation_reason) is str
            and candle_validation_reason
            in _CANDLE_VALIDATION_REASONS.get(candle_validation_stage, ())
        )
        self.candle_validation_stage = (
            candle_validation_stage if valid_candle_diagnostic else None
        )
        self.candle_validation_reason = (
            candle_validation_reason if valid_candle_diagnostic else None
        )
        super().__init__(self.reason)


def _fail(reason: str) -> None:
    raise Q7ALiveError(reason)


def _fail_candle(stage: str, reason: str) -> None:
    raise Q7ALiveError(
        "CANDLE_FRAME_INVALID",
        candle_validation_stage=stage,
        candle_validation_reason=reason,
    )


def _map_gui_blocker(exc: GuiRuntimeBlockedError) -> str:
    reason = getattr(exc, "reason", None)
    return (
        _POST_MARKER_GUI_REASONS.get(reason, "POSTCONDITION_FAILED")
        if type(reason) is str
        else "POSTCONDITION_FAILED"
    )


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("PREPARATION_INVALID")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash(value: object, reason: str = "PREPARATION_INVALID") -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        _fail(reason)
    return value


def _commit(value: object) -> str:
    if type(value) is not str or _HEX40.fullmatch(value) is None:
        _fail("SOURCE_IDENTITY_MISMATCH")
    return value


def _timestamp(value: object, reason: str = "PREPARATION_INVALID") -> datetime:
    if type(value) is not str or _UTC.fullmatch(value) is None:
        _fail(reason)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        _fail(reason)


def _native_timestamp(value: object, reason: str) -> datetime:
    if type(value) is not str:
        _fail(reason)
    matched = _NATIVE_UTC.fullmatch(value)
    if matched is None:
        _fail(reason)
    fraction = matched.group("fraction")
    normalized = matched.group("prefix") + "." + (fraction + "000000")[:6] + "Z"
    return _timestamp(normalized, reason)


def _enum_value(value: object, reason: str) -> str:
    raw = getattr(value, "value", None)
    if type(raw) is not str or not raw:
        _fail(reason)
    return raw


def _gate_a_owner_evidence(authority: Any, evidence: Any) -> dict[str, Any]:
    """Bind privacy-safe CL2-CL7 owner outputs from the exact Gate A rebuild."""

    try:
        ledger_export = evidence.ledger_export_bytes
        cash = evidence.broker_cash_proof
        withdraw = evidence.broker_withdraw_limits_proof
        reconciliation = evidence.reconciliation
        reservations = evidence.reservations
        availability = evidence.availability
        portfolio = evidence.portfolio
        risk_guard = evidence.risk_guard
        context = evidence.context
        values: dict[str, Any] = {
            "operations_complete_through": authority.operations_complete_through,
            "ledger_export_sha256": _sha256(ledger_export),
            "ledger_revision": authority.ledger_revision,
            "ledger_head_sha256": authority.ledger_head_sha256,
            "broker_cash_proof_sha256": cash.sha256,
            "broker_cash_response_sha256": cash.response_canonical_sha256,
            "withdraw_limits_proof_sha256": withdraw.sha256,
            "withdraw_limits_response_sha256": withdraw.response_canonical_sha256,
            "cl4_reconciliation_status": _enum_value(
                reconciliation.status, "CL4_RECONCILIATION_BLOCKED"
            ),
            "cl4_discrepancy_kind": _enum_value(
                reconciliation.discrepancy_kind, "CL4_RECONCILIATION_BLOCKED"
            ),
            "cl4_reconciliation_sha256": reconciliation.sha256,
            "central_projection_sha256": reservations.sha256,
            "central_projection_revision": reservations.central_order_revision,
            "central_queued_count": reservations.queued_count,
            "central_ambiguous_count": reservations.ambiguous_count,
            "cl5_availability_status": _enum_value(
                availability.status, "CL5_AVAILABILITY_NOT_READY"
            ),
            "cl5_availability_reason": _enum_value(
                availability.availability_reason, "CL5_AVAILABILITY_NOT_READY"
            ),
            "cl5_availability_sha256": availability.sha256,
            "portfolio_evidence_sha256": portfolio.sha256,
            "portfolio_revision": portfolio.portfolio_revision,
            "portfolio_decision_checksum": portfolio.portfolio_decision_checksum,
            "portfolio_document_checksum": portfolio.portfolio_document_checksum,
            "portfolio_snapshot_at": portfolio.portfolio_snapshot_at,
            "risk_guard_evidence_sha256": risk_guard.sha256,
            "risk_policy_sha256": risk_guard.risk_policy_hash,
            "risk_state_guard_sha256": risk_guard.risk_state_guard_hash,
            "cl6_status": _enum_value(context.status, "CL6_CONTEXT_NOT_READY"),
            "cl6_reason": _enum_value(context.reason, "CL6_CONTEXT_NOT_READY"),
            "cl6_context_sha256": context.sha256,
            "cl6_evaluated_at": context.evaluated_at,
            "cl7_state": _enum_value(authority.state, "AUTHORITY_NOT_EXACT_CASH_ARMED"),
            "cl7_revision": authority.record_revision,
            "cl7_record_sha256": authority.sha256,
            "post_attempt_count": authority.post_attempt_count,
            "pending_dispatch_proof": authority.pending_dispatch_proof_sha256
            is not None,
        }
    except Q7ALiveError:
        raise
    except (AttributeError, TypeError):
        _fail("POSTCONDITION_FAILED")
    if type(ledger_export) is not bytes:
        _fail("POSTCONDITION_FAILED")
    for key in (
        "ledger_revision",
        "central_projection_revision",
        "central_queued_count",
        "central_ambiguous_count",
        "portfolio_revision",
        "cl7_revision",
        "post_attempt_count",
    ):
        if type(values[key]) is not int or values[key] < 0:
            _fail("POSTCONDITION_FAILED")
    for key in (
        "ledger_export_sha256",
        "ledger_head_sha256",
        "broker_cash_proof_sha256",
        "broker_cash_response_sha256",
        "withdraw_limits_proof_sha256",
        "withdraw_limits_response_sha256",
        "cl4_reconciliation_sha256",
        "central_projection_sha256",
        "cl5_availability_sha256",
        "portfolio_evidence_sha256",
        "portfolio_decision_checksum",
        "portfolio_document_checksum",
        "risk_guard_evidence_sha256",
        "risk_policy_sha256",
        "risk_state_guard_sha256",
        "cl6_context_sha256",
        "cl7_record_sha256",
    ):
        _hash(values[key], "POSTCONDITION_FAILED")
    _native_timestamp(values["operations_complete_through"], "CL3_SYNC_BLOCKED")
    _native_timestamp(values["portfolio_snapshot_at"], "PORTFOLIO_REFRESH_BLOCKED")
    _native_timestamp(values["cl6_evaluated_at"], "CL6_CONTEXT_NOT_READY")
    if (
        values["ledger_export_sha256"] != context.ledger_export_sha256
        or values["ledger_revision"] != context.ledger_revision
        or values["ledger_head_sha256"] != context.ledger_head_sha256
        or values["cl4_reconciliation_sha256"] != context.reconciliation_sha256
        or values["cl5_availability_sha256"] != context.availability_sha256
        or values["portfolio_evidence_sha256"] != context.portfolio_evidence_sha256
        or values["risk_guard_evidence_sha256"] != context.risk_guard_evidence_sha256
        or values["risk_policy_sha256"] != context.risk_policy_hash
        or values["risk_state_guard_sha256"] != context.risk_state_guard_hash
        or values["post_attempt_count"] != 0
        or values["pending_dispatch_proof"]
    ):
        _fail("POSTCONDITION_FAILED")
    values["receipt_set_sha256"] = _sha256(_canonical(values))
    return values


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("PREPARATION_INVALID")
        result[key] = value
    return result


def _contains_future_key(value: object) -> bool:
    if type(value) is dict:
        return bool(set(value) & _FORBIDDEN_FUTURE_KEYS) or any(
            _contains_future_key(child) for child in value.values()
        )
    if type(value) is list:
        return any(_contains_future_key(child) for child in value)
    return False


@dataclass(frozen=True, slots=True)
class LivePreparation:
    raw: bytes
    fields: Mapping[str, Any]

    @property
    def sha256(self) -> str:
        return _sha256(self.raw)

    @property
    def candle_policy(self) -> Mapping[str, Any]:
        return self.fields["candle_policy"]

    @property
    def quote_policy(self) -> Mapping[str, Any]:
        return self.fields["quote_policy"]

    @property
    def trading_status_policy(self) -> Mapping[str, Any]:
        return self.fields["trading_status_policy"]


def verify_preparation(
    raw: bytes,
    *,
    expected_sha256: str,
    candidate_commit: str,
    candidate_tree: str,
) -> LivePreparation:
    """Verify exact canonical durable policy bytes before private/runtime access."""

    expected = _hash(expected_sha256, "PREPARATION_SHA_MISMATCH")
    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf") or raw.endswith(b"\n"):
        _fail("PREPARATION_INVALID")
    if not hmac.compare_digest(_sha256(raw), expected):
        _fail("PREPARATION_SHA_MISMATCH")
    try:
        fields = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError):
        _fail("PREPARATION_INVALID")
    if type(fields) is not dict or frozenset(fields) != _PREPARATION_FIELDS:
        _fail("PREPARATION_INVALID")
    if raw != _canonical(fields) or _contains_future_key(fields):
        _fail("PREPARATION_INVALID")
    digest = _hash(fields["preparation_sha256"], "PREPARATION_INVALID")
    preimage = dict(fields)
    del preimage["preparation_sha256"]
    if not hmac.compare_digest(digest, _sha256(_canonical(preimage))):
        _fail("PREPARATION_INVALID")
    if (
        fields["version"] != 1
        or type(fields["version"]) is not int
        or fields["domain"] != PREPARATION_DOMAIN
        or fields["experiment_id"] != EXPERIMENT_ID
        or fields["live_contract_commit"] != LIVE_CONTRACT_COMMIT
        or fields["live_contract_tree"] != LIVE_CONTRACT_TREE
        or fields["q7a_contract_commit"] != Q7A_CONTRACT_COMMIT
        or fields["q7a_contract_tree"] != Q7A_CONTRACT_TREE
        or fields["q7a_synthetic_implementation_commit"]
        != Q7A_SYNTHETIC_IMPLEMENTATION_COMMIT
        or fields["q7a_synthetic_implementation_tree"]
        != Q7A_SYNTHETIC_IMPLEMENTATION_TREE
        or fields["candidate_commit"] != _commit(candidate_commit)
        or fields["candidate_tree"] != _commit(candidate_tree)
        or fields["accepted_implementation_commit"] != candidate_commit
        or fields["accepted_implementation_tree"] != candidate_tree
    ):
        _fail("SOURCE_IDENTITY_MISMATCH")
    for key in (
        "runtime_manifest_sha256",
        "accepted_q1_summary_sha256",
        "accepted_q4_summary_sha256",
        "accepted_q5_summary_sha256",
        "account_scope_sha256",
        "configured_set_sha256",
        "target_instrument_sha256",
        "control_record_sha256",
        "risk_policy_sha256",
        "static_metadata_schema_sha256",
        "static_metadata_sha256",
        "currency_evidence_sha256",
        "lot_size_evidence_sha256",
        "live_acquisition_policy_sha256",
        "evidence_root_sha256",
        "backup_binding_sha256",
        "pre_admission_stop_rules_sha256",
        "post_admission_same_lineage_rules_sha256",
        "evidence_privacy_policy_sha256",
        "recovery_procedure_sha256",
        "stop_conditions_sha256",
    ):
        _hash(fields[key])
    if fields["environment"] != "SANDBOX" or fields["risk_policy_mode"] != "ENFORCED":
        _fail(
            "ENVIRONMENT_NOT_SANDBOX"
            if fields["environment"] != "SANDBOX"
            else "RISK_POLICY_NOT_ENFORCED"
        )
    if (
        type(fields["identity_key_id"]) is not str
        or not fields["identity_key_id"].strip()
    ):
        _fail("CREDENTIAL_CUSTODY_INVALID")
    if (
        fields["max_provider_post_attempts"] != 1
        or type(fields["max_provider_post_attempts"]) is not int
        or fields["automatic_retries"] != 0
        or type(fields["automatic_retries"]) is not int
        or fields["redirect_replays"] != 0
        or type(fields["redirect_replays"]) is not int
    ):
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    created = _timestamp(fields["created_at_utc"])
    deadline = _timestamp(fields["absolute_deadline_utc"])
    if deadline <= created:
        _fail("PREPARATION_INVALID")
    _verify_candle_policy(fields["candle_policy"], fields["target_instrument_sha256"])
    _verify_quote_policy(fields["quote_policy"], fields["target_instrument_sha256"])
    _verify_status_policy(
        fields["trading_status_policy"], fields["target_instrument_sha256"]
    )
    _verify_account_policy(fields["account_cash_portfolio_policy"])
    if (
        fields["cl7_final_freshness_seconds"] != 10
        or type(fields["cl7_final_freshness_seconds"]) is not int
    ):
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    acquisition_policy = {
        "account_cash_portfolio_policy": fields["account_cash_portfolio_policy"],
        "candle_policy": fields["candle_policy"],
        "quote_policy": fields["quote_policy"],
        "trading_status_policy": fields["trading_status_policy"],
        "cl7_final_freshness_seconds": fields["cl7_final_freshness_seconds"],
        "max_provider_post_attempts": fields["max_provider_post_attempts"],
        "automatic_retries": fields["automatic_retries"],
        "redirect_replays": fields["redirect_replays"],
    }
    if not hmac.compare_digest(
        fields["live_acquisition_policy_sha256"],
        _sha256(_canonical(acquisition_policy)),
    ):
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    return LivePreparation(raw, fields)


def _verify_account_policy(value: object) -> None:
    if type(value) is not dict or frozenset(value) != _ACCOUNT_POLICY_FIELDS:
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    for key in (
        "operations_logical_sessions_per_gate",
        "operations_max_pages",
        "operations_max_items",
        "operations_max_attempts_per_page",
        "portfolio_physical_requests_per_gate",
        "portfolio_logical_reads_gate_a",
        "portfolio_logical_reads_gate_b",
        "withdraw_limits_requests_per_gate",
        "orders_requests_gate_a",
        "orders_requests_gate_b",
        "orders_retries",
        "orders_redirect_replays",
        "orders_automatic_reacquisition",
    ):
        if type(value[key]) is not int:
            _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    if type(value["positions_required"]) is not bool:
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    for key in (
        "operations_policy_sha256",
        "owner_graph_sha256",
        "absolute_timeout_policy_sha256",
    ):
        _hash(value[key], "LIVE_ACQUISITION_POLICY_MISMATCH")
    if value != {
        **value,
        "owner": "RuntimeCashAuthorityManager.sync_runtime",
        "operations_service": "SandboxService",
        "operations_method": "GetSandboxOperationsByCursor",
        "operations_logical_sessions_per_gate": 1,
        "operations_max_pages": 100,
        "operations_max_items": 100_000,
        "operations_max_attempts_per_page": 3,
        "portfolio_service": "SandboxService",
        "portfolio_method": "GetSandboxPortfolio",
        "portfolio_physical_requests_per_gate": 1,
        "portfolio_logical_reads_gate_a": 2,
        "portfolio_logical_reads_gate_b": 1,
        "withdraw_limits_method": "GetSandboxWithdrawLimits",
        "withdraw_limits_requests_per_gate": 1,
        "positions_required": False,
        "positions_method": None,
        "orders_service": "SandboxService",
        "orders_method": "GetSandboxOrders",
        "orders_owner": "CanonicalPortfolioManager.refresh",
        "orders_requests_gate_a": 1,
        "orders_requests_gate_b": 0,
        "orders_retries": 0,
        "orders_redirect_replays": 0,
        "orders_automatic_reacquisition": 0,
        "portfolio_owner": "CanonicalPortfolioManager.refresh",
    }:
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")


def _verify_candle_policy(value: object, target_hash: str) -> None:
    if type(value) is not dict or frozenset(value) != _CANDLE_POLICY_FIELDS:
        _fail("CANDLE_ACQUISITION_POLICY_MISMATCH")
    if (
        value["owner"] != "StrategyCandleLoader"
        or value["service"] != "MarketDataService"
        or value["method"] != "GetCandles"
        or value["target_instrument_sha256"] != target_hash
        or value["interval"] not in _INTERVAL_SECONDS
        or value["candle_source_type"] != "CANDLE_SOURCE_EXCHANGE"
        or any(
            type(value[key]) is not int
            for key in (
                "required_bars",
                "lookback_days",
                "maximum_span_seconds",
                "logical_sessions",
                "physical_requests",
                "retries",
                "redirects_followed",
                "automatic_reacquisition",
                "maximum_age_seconds",
                "future_skew_seconds",
            )
        )
        or value["required_bars"] < 1
        or value["lookback_days"] < 1
        or value["logical_sessions"] != 1
        or value["physical_requests"] != 1
        or value["retries"] != 0
        or value["redirects_followed"] != 0
        or value["automatic_reacquisition"] != 0
        or value["maximum_age_seconds"] != _INTERVAL_SECONDS[value["interval"]] + 300
        or value["future_skew_seconds"] != 5
        or value["lookback_days"] * 86400 > value["maximum_span_seconds"]
    ):
        _fail("CANDLE_ACQUISITION_POLICY_MISMATCH")


def _verify_quote_policy(value: object, target_hash: str) -> None:
    if type(value) is not dict or frozenset(value) != _QUOTE_POLICY_FIELDS:
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    if value != {
        "owner": "_ProductionGuiHooks",
        "service": "MarketDataService",
        "method": "GetLastPrices",
        "target_instrument_sha256": target_hash,
        "last_price_type": "LAST_PRICE_EXCHANGE",
        "request_count": 1,
        "retries": 0,
        "redirects_followed": 0,
        "source": "TBANK_LAST_PRICE_EXCHANGE",
        "maximum_age_seconds": 300,
        "future_skew_seconds": 5,
    }:
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")


def _verify_status_policy(value: object, target_hash: str) -> None:
    if type(value) is not dict or frozenset(value) != _STATUS_POLICY_FIELDS:
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
    if value != {
        "owner": "SandboxExecutionAdapter._market_precheck",
        "service": "MarketDataService",
        "method": "GetTradingStatus",
        "target_instrument_sha256": target_hash,
        "logical_calls": 1,
        "physical_requests": 1,
        "retries": 0,
        "redirect_replays": 0,
        "automatic_reacquisition": 0,
    }:
        _fail("LIVE_ACQUISITION_POLICY_MISMATCH")


@dataclass(frozen=True, slots=True)
class CandleEvidence:
    canonical: bytes
    sha256: str
    row_count: int
    earliest_begin_utc: str
    latest_begin_utc: str
    latest_close_utc: str


def canonical_candle_evidence(
    frame: pd.DataFrame,
    *,
    policy: Mapping[str, Any],
    now: datetime,
) -> CandleEvidence:
    """Validate and canonically bind the exact complete frame owner output."""

    if type(frame) is not pd.DataFrame:
        _fail_candle("FRAME", "FRAME_TYPE")
    if type(frame.index) is not pd.DatetimeIndex:
        _fail_candle("FRAME", "INDEX_TYPE")
    required = ("open", "high", "low", "close", "volume", "is_complete")
    if any(column not in frame.columns for column in required):
        _fail("CANDLE_FRAME_INCOMPLETE")
    if len(frame) < policy["required_bars"] or frame.index.has_duplicates:
        _fail("CANDLE_FRAME_INCOMPLETE")
    normalized = (
        frame.index.tz_localize("UTC")
        if frame.index.tz is None
        else frame.index.tz_convert("UTC")
    )
    seconds = _INTERVAL_SECONDS[policy["interval"]]
    if not normalized.is_monotonic_increasing:
        _fail_candle("FRAME", "INDEX_ORDER")
    rows: list[dict[str, object]] = []
    for timestamp, (_, row) in zip(normalized, frame.iterrows(), strict=True):
        values: dict[str, float] = {}
        for key in ("open", "high", "low", "close"):
            value = row[key]
            if type(value) not in {float, np.float64}:
                _fail_candle("FRAME", "OHLC_TYPE")
            numeric = float(value)
            if not math.isfinite(numeric):
                _fail_candle("FRAME", "OHLC_NONFINITE")
            if numeric <= 0:
                _fail_candle("FRAME", "OHLC_NONPOSITIVE")
            values[key] = numeric
        volume = row["volume"]
        complete = row["is_complete"]
        if type(volume) not in {int, np.int64}:
            _fail_candle("FRAME", "VOLUME_TYPE")
        if int(volume) < 0:
            _fail_candle("FRAME", "VOLUME_NEGATIVE")
        if type(complete) not in {bool, np.bool_} or not bool(complete):
            _fail("CANDLE_FRAME_INCOMPLETE")
        if not (
            values["low"]
            <= min(values["open"], values["close"])
            <= max(values["open"], values["close"])
            <= values["high"]
        ):
            _fail_candle("FRAME", "OHLC_ORDER")
        rows.append(
            {
                "begin": timestamp.to_pydatetime().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "open": values["open"].hex(),
                "high": values["high"].hex(),
                "low": values["low"].hex(),
                "close": values["close"].hex(),
                "volume": int(volume),
                "is_complete": True,
            }
        )
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        _fail_candle("FRAME", "CLOCK_TYPE")
    latest_begin = normalized[-1].to_pydatetime()
    latest_close = latest_begin + timedelta(seconds=seconds)
    age = (now.astimezone(timezone.utc) - latest_close).total_seconds()
    if age < -policy["future_skew_seconds"] or age > policy["maximum_age_seconds"]:
        _fail("CANDLE_EVIDENCE_STALE")
    canonical = _canonical(
        {
            "domain": "CL8_Q7A_CANDLE_FRAME_V1",
            "interval": policy["interval"],
            "rows": rows,
        }
    )
    return CandleEvidence(
        canonical=canonical,
        sha256=_sha256(canonical),
        row_count=len(rows),
        earliest_begin_utc=rows[0]["begin"],
        latest_begin_utc=rows[-1]["begin"],
        latest_close_utc=latest_close.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    )


def verify_candle_request_binding(
    evidence: CandleEvidence,
    request: Mapping[str, Any] | None,
    *,
    expected_interval: str,
    expected_target_sha256: str,
) -> None:
    """Reject frames whose canonical rows escape the one authorized request."""

    if type(request) is not dict or set(request) != {
        "target_instrument_sha256",
        "from_utc",
        "to_utc",
        "interval",
        "limit",
        "candle_source_type",
    }:
        _fail_candle("REQUEST_BINDING", "REQUEST_SHAPE")
    if (
        request["target_instrument_sha256"] != expected_target_sha256
        or request["interval"] != expected_interval
        or request["limit"] is not None
        or request["candle_source_type"] != "CANDLE_SOURCE_EXCHANGE"
    ):
        _fail_candle("REQUEST_BINDING", "REQUEST_IDENTITY")
    try:
        start = _timestamp(request["from_utc"], "CANDLE_FRAME_INVALID")
        stop = _timestamp(request["to_utc"], "CANDLE_FRAME_INVALID")
        earliest = _timestamp(evidence.earliest_begin_utc, "CANDLE_FRAME_INVALID")
        latest_close = _timestamp(evidence.latest_close_utc, "CANDLE_FRAME_INVALID")
    except Q7ALiveError:
        _fail_candle("REQUEST_BINDING", "REQUEST_TIME_PARSE")
    if start >= stop:
        _fail_candle("REQUEST_BINDING", "REQUEST_RANGE_ORDER")
    if earliest < start:
        _fail_candle("REQUEST_BINDING", "FIRST_BEGIN_BEFORE_FROM")
    if latest_close > stop:
        _fail_candle("REQUEST_BINDING", "LAST_CLOSE_AFTER_TO")


@dataclass(frozen=True, slots=True)
class ControlledProposalEvidence:
    base: StrategyProposal
    controlled: StrategyProposal
    base_sha256: str
    controlled_sha256: str
    frame_sha256: str


def derive_controlled_proposal(
    *,
    runtime: InstrumentRuntime,
    profile: MultiInstrumentProfile,
    frame: pd.DataFrame,
    frame_evidence: CandleEvidence,
    now: datetime,
) -> ControlledProposalEvidence:
    """Invoke the existing Strategy owner once, then apply the frozen derivation."""

    try:
        base = build_strategy_proposal(runtime, profile, frame, now=now)
        primary = base.decisions[base.primary_strategy]
        controlled_primary = replace(primary, signal=1, target_lots=1)
        decisions = dict(base.decisions)
        decisions[base.primary_strategy] = controlled_primary
        comparison = compare_strategy_decisions(decisions, base.primary_strategy)
        controlled = replace(
            base,
            primary_target_lots=1,
            decisions=decisions,
            comparison=comparison,
        )
    except Exception:  # noqa: BLE001 - finite Strategy owner boundary
        _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    if type(base) is not StrategyProposal or type(controlled) is not StrategyProposal:
        _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    base_fields = base.to_dict()
    controlled_fields = controlled.to_dict()
    base_primary = base_fields["decisions"][base.primary_strategy]
    controlled_primary_fields = controlled_fields["decisions"][base.primary_strategy]
    for key in base_primary:
        if (
            key not in {"signal", "target_lots"}
            and base_primary[key] != controlled_primary_fields[key]
        ):
            _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    if (
        controlled_primary_fields["signal"] != 1
        or controlled_primary_fields["target_lots"] != 1
    ):
        _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    for key in base_fields:
        if (
            key not in {"primary_target_lots", "decisions", "comparison"}
            and base_fields[key] != controlled_fields[key]
        ):
            _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    for key, decision in base_fields["decisions"].items():
        if (
            key != base.primary_strategy
            and decision != controlled_fields["decisions"][key]
        ):
            _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    if (
        controlled.primary_target_lots != 1
        or controlled.execution_authorized is not False
    ):
        _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    return ControlledProposalEvidence(
        base=base,
        controlled=controlled,
        base_sha256=_sha256(_proposal_canonical(base)),
        controlled_sha256=_sha256(_proposal_canonical(controlled)),
        frame_sha256=frame_evidence.sha256,
    )


def verify_candle_policy_for_profile(
    *,
    preparation: LivePreparation,
    runtime: InstrumentRuntime,
    profile: MultiInstrumentProfile,
) -> None:
    """Recompute the one-request candle budget before the first provider IO."""

    policy = preparation.candle_policy
    try:
        desired = profile.to_runtime_config(runtime.config.account_id)
        config = profile.to_bot_config(
            mode="DRY_RUN",
            state_file="robot_state.json",
            journal_file="trading_events.db",
        )
        suite = strategy_suite_from_bot_config(config)
        required_bars = suite.required_bars_for_suite()
        lookback_days = strategy_lookback_days(
            runtime.config.candle_interval,
            required_bars=required_bars,
            requested_days=config.lookback_days,
        )
        max_span = TBankSandboxClient.CANDLE_MAX_SPAN[runtime.config.candle_interval]
    except Exception:  # noqa: BLE001 - finite profile/policy owner boundary
        _fail("CANDLE_ACQUISITION_POLICY_MISMATCH")
    if (
        desired.to_dict() != runtime.config.to_dict()
        or policy["interval"] != runtime.config.candle_interval
        or policy["required_bars"] != required_bars
        or policy["lookback_days"] != lookback_days
        or policy["maximum_span_seconds"] != int(max_span.total_seconds())
        or timedelta(days=lookback_days) > max_span
    ):
        _fail("CANDLE_ACQUISITION_POLICY_MISMATCH")


@dataclass(frozen=True, slots=True)
class PortfolioFlatBinding:
    revision: int
    decision_sha256: str
    state_sha256: str
    current_lots: int


def bind_flat_position(
    repository: PortfolioRepository,
    *,
    account_id: str,
    target_instrument_id: str,
) -> PortfolioFlatBinding:
    try:
        state = repository.load(expected_account_id=account_id)
        position = state.position(target_instrument_id)
        current = 0 if position is None else position.actual_lots
        raw = _canonical(state.to_dict())
    except Exception:  # noqa: BLE001 - finite persisted Portfolio boundary
        _fail("PORTFOLIO_REFRESH_BLOCKED")
    if type(current) is not int or current != 0:
        _fail("PORTFOLIO_FLAT_POSITION_DRIFT")
    return PortfolioFlatBinding(
        revision=state.revision,
        decision_sha256=state.decision_sha256,
        state_sha256=_sha256(raw),
        current_lots=current,
    )


def recheck_flat_position(
    repository: PortfolioRepository,
    *,
    account_id: str,
    target_instrument_id: str,
    expected: PortfolioFlatBinding,
) -> PortfolioFlatBinding:
    actual = bind_flat_position(
        repository,
        account_id=account_id,
        target_instrument_id=target_instrument_id,
    )
    if actual != expected:
        _fail("PORTFOLIO_FLAT_POSITION_DRIFT")
    return actual


class ProviderEvidenceAdapter:
    """Transparent one-call market-read/POST budget and evidence observer."""

    def __init__(
        self,
        delegate: Any,
        *,
        target_instrument_id: str,
        account_id: str | None = None,
        account_policy: Mapping[str, Any] | None = None,
        candle_interval: str | None = None,
        candle_lookback_days: int | None = None,
    ) -> None:
        self._delegate = delegate
        self._target = target_instrument_id
        self._target_hash = _sha256(target_instrument_id.encode("utf-8"))
        self._account_id = account_id
        self._account_policy = account_policy
        self._candle_interval = candle_interval
        self._candle_lookback_days = candle_lookback_days
        self._gate = "A"
        self._gate_b_telemetry_start: int | None = None
        self.candle_calls = 0
        self.quote_calls = 0
        self.status_calls = 0
        self.post_calls = 0
        self.operation_calls = {"A": 0, "B": 0}
        self.portfolio_logical_calls = {"A": 0, "B": 0}
        self.portfolio_physical_calls = {"A": 0, "B": 0}
        self.withdraw_calls = {"A": 0, "B": 0}
        self.order_calls = {"A": 0, "B": 0}
        self._portfolio_cache: dict[str, Any] | None = None
        self.orders_response_canonical_sha256: str | None = None
        self.quote_raw: dict[str, Any] | None = None
        self.telemetry: list[dict[str, Any]] = []
        self.candle_request: dict[str, Any] | None = None
        self.trading_status_response_sha256: str | None = None
        self.gate_b_precheck_evidence: dict[str, Any] | None = None
        self._telemetry_required = False
        self._install_transport_guards()

    @property
    def target_sha256(self) -> str:
        return self._target_hash

    def _transport(self) -> Any:
        current = self._delegate
        seen: set[int] = set()
        while id(current) not in seen:
            seen.add(id(current))
            if callable(getattr(current, "set_telemetry_callback", None)):
                return current
            current = getattr(current, "_delegate", None)
            if current is None:
                break
        return None

    def _install_transport_guards(self) -> None:
        transport = self._transport()
        if transport is None:
            return
        self._telemetry_required = True
        if getattr(transport, "max_retries", None) != 0:
            _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
        session = getattr(transport, "_session", None)
        if session is None:
            _fail("LIVE_ACQUISITION_POLICY_MISMATCH")
        session.max_redirects = 0
        prior = getattr(transport, "telemetry_callback", None)

        def observe(event: dict[str, Any]) -> None:
            self.telemetry.append(_sanitize_receipt(event))
            if callable(prior):
                prior(event)

        transport.set_telemetry_callback(observe)

    def get_candles(
        self,
        instrument_id: str,
        from_time: datetime,
        to_time: datetime,
        *,
        interval: str,
        limit: int | None,
    ) -> pd.DataFrame:
        if (
            self._gate != "A"
            or instrument_id != self._target
            or self.candle_calls != 0
            or limit is not None
            or type(from_time) is not datetime
            or type(to_time) is not datetime
            or from_time.tzinfo is None
            or to_time.tzinfo is None
            or from_time.utcoffset() is None
            or to_time.utcoffset() is None
            or from_time >= to_time
            or (self._candle_interval is not None and interval != self._candle_interval)
            or (
                self._candle_lookback_days is not None
                and to_time - from_time != timedelta(days=self._candle_lookback_days)
            )
        ):
            _fail("PROVIDER_READ_SCOPE_INVALID")
        self.candle_calls += 1
        self.candle_request = {
            "target_instrument_sha256": self._target_hash,
            "from_utc": from_time.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "to_utc": to_time.astimezone(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            ),
            "interval": interval,
            "limit": None,
            "candle_source_type": "CANDLE_SOURCE_EXCHANGE",
        }
        try:
            return self._delegate.get_candles(
                instrument_id, from_time, to_time, interval=interval, limit=limit
            )
        except Q7ALiveError:
            raise
        except Exception:  # noqa: BLE001 - finite provider boundary
            _fail("CANDLE_READ_FAILED")

    def get_last_prices(self, instrument_ids: list[str]) -> list[dict[str, Any]]:
        if (
            self._gate != "A"
            or type(instrument_ids) is not list
            or instrument_ids != [self._target]
            or self.quote_calls != 0
        ):
            _fail("PROVIDER_READ_SCOPE_INVALID")
        self.quote_calls += 1
        try:
            result = self._delegate.get_last_prices(instrument_ids)
        except Q7ALiveError:
            raise
        except Exception:  # noqa: BLE001 - finite provider boundary
            _fail("PROVIDER_READ_FAILED")
        if type(result) is not list or len(result) != 1 or type(result[0]) is not dict:
            _fail("QUOTE_OR_METADATA_INVALID")
        raw = result[0]
        price = raw.get("price")
        if (
            type(raw.get("instrumentUid")) is not str
            or raw["instrumentUid"] != self._target
            or type(raw.get("time")) is not str
            or type(price) is not dict
            or type(price.get("units")) is not str
            or _QUOTE_UNITS.fullmatch(price["units"]) is None
            or type(price.get("nano")) is not int
            or not 0 <= price["nano"] < 1_000_000_000
        ):
            _fail("QUOTE_OR_METADATA_INVALID")
        try:
            detached = json.loads(
                json.dumps(result, ensure_ascii=False, allow_nan=False)
            )
        except (TypeError, ValueError):
            _fail("QUOTE_OR_METADATA_INVALID")
        if detached != result or detached is result:
            _fail("QUOTE_OR_METADATA_INVALID")
        self.quote_raw = deepcopy(detached[0])
        return detached

    def get_operations_by_cursor_once(self, *args: Any, **kwargs: Any) -> Any:
        gate = self._gate
        maximum = (
            self._account_policy["operations_max_pages"]
            if self._account_policy is not None
            else 100
        )
        if gate not in {"A", "B"} or self.operation_calls[gate] >= maximum:
            _fail("PROVIDER_READ_SCOPE_INVALID")
        self.operation_calls[gate] += 1
        return self._delegate.get_operations_by_cursor_once(*args, **kwargs)

    def get_portfolio(self, account_id: str) -> dict[str, Any]:
        gate = self._gate
        if (
            gate not in {"A", "B"}
            or self._account_id is None
            or account_id != self._account_id
        ):
            _fail("PROVIDER_READ_SCOPE_INVALID")
        self.portfolio_logical_calls[gate] += 1
        maximum = 2 if gate == "A" else 1
        if self.portfolio_logical_calls[gate] > maximum:
            _fail("LIVE_ACQUISITION_BUDGET_EXHAUSTED")
        if self._portfolio_cache is None:
            try:
                observed = self._delegate.get_portfolio(account_id)
                detached = json.loads(
                    json.dumps(observed, ensure_ascii=False, allow_nan=False)
                )
            except Q7ALiveError:
                raise
            except Exception:  # noqa: BLE001 - finite provider boundary
                _fail("PROVIDER_READ_FAILED")
            if type(detached) is not dict:
                _fail("PROVIDER_READ_INCOMPLETE")
            self._portfolio_cache = detached
            self.portfolio_physical_calls[gate] += 1
        return deepcopy(self._portfolio_cache)

    def get_withdraw_limits(self, account_id: str) -> Any:
        gate = self._gate
        if (
            gate not in {"A", "B"}
            or self._account_id is None
            or account_id != self._account_id
            or self.withdraw_calls[gate] != 0
        ):
            _fail("PROVIDER_READ_SCOPE_INVALID")
        self.withdraw_calls[gate] += 1
        return self._delegate.get_withdraw_limits(account_id)

    def get_orders(self, account_id: str) -> list[dict[str, Any]]:
        gate = self._gate
        if (
            gate != "A"
            or self._account_id is None
            or account_id != self._account_id
            or self.order_calls["A"] != 0
        ):
            _fail("PROVIDER_READ_SCOPE_INVALID")
        self.order_calls["A"] += 1
        try:
            observed = self._delegate.get_orders(account_id)
            if type(observed) is not list or any(
                type(item) is not dict for item in observed
            ):
                _fail("PROVIDER_READ_INCOMPLETE")
            raw = json.dumps(
                observed,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            detached = json.loads(raw)
        except Q7ALiveError:
            raise
        except Exception:  # noqa: BLE001 - finite provider/order custody boundary
            _fail("PROVIDER_READ_FAILED")
        if type(detached) is not list or detached != observed or detached is observed:
            _fail("PROVIDER_READ_INCOMPLETE")
        self.orders_response_canonical_sha256 = _sha256(raw)
        return detached

    def begin_gate_b(self) -> None:
        if self._gate != "A" or self.candle_calls != 1 or self.quote_calls != 1:
            _fail("LIVE_ACQUISITION_BUDGET_EXHAUSTED")
        self.verify_gate_a_receipts()
        self._gate = "B"
        self._portfolio_cache = None
        self._gate_b_telemetry_start = len(self.telemetry)

    def get_trading_status(self, instrument_id: str) -> Mapping[str, Any]:
        if self._gate != "B" or instrument_id != self._target or self.status_calls != 0:
            _fail("PROVIDER_READ_SCOPE_INVALID")
        self.status_calls += 1
        result = self._delegate.get_trading_status(instrument_id)
        if isinstance(result, Mapping):
            try:
                raw = json.dumps(
                    dict(result),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, ValueError, UnicodeError):
                raw = _canonical({"response_shape": "INVALID"})
        else:
            raw = _canonical({"response_shape": "NON_MAPPING"})
        self.trading_status_response_sha256 = _sha256(raw)
        return result

    def post_order_once(self, *args: Any, **kwargs: Any) -> Any:
        if self._gate != "B" or self.status_calls != 1 or self.post_calls != 0:
            _fail("ATTEMPT_BUDGET_EXHAUSTED")
        self.post_calls += 1
        return self._delegate.post_order_once(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name in {
            "get_candles",
            "get_last_prices",
            "get_trading_status",
            "post_order_once",
            "post_order",
            "get_positions",
        }:
            raise AttributeError(name)
        return getattr(self._delegate, name)

    def verify_gate_a_receipts(self) -> None:
        stop = self._gate_b_telemetry_start
        self._verify_gate_receipts(
            "A", self.telemetry if stop is None else self.telemetry[:stop]
        )

    def verify_gate_b_receipts(self, *, dispatch_status: str) -> None:
        if self._gate_b_telemetry_start is None:
            _fail("PROVIDER_READ_INCOMPLETE")
        normalized = str(dispatch_status or "").strip().upper()
        self._verify_gate_receipts(
            "B",
            self.telemetry[self._gate_b_telemetry_start :],
            dispatch_status=normalized,
        )
        self.gate_b_precheck_evidence = {
            "dispatch_status": normalized,
            "trading_status_response_sha256": self.trading_status_response_sha256,
        }

    def _verify_gate_receipts(
        self,
        gate: str,
        receipts: list[dict[str, Any]],
        *,
        dispatch_status: str | None = None,
    ) -> None:
        if not self._telemetry_required:
            return
        counts: dict[tuple[str, str], int] = {}
        allowed = {
            ("SandboxService", "GetSandboxOperationsByCursor"),
            ("SandboxService", "GetSandboxPortfolio"),
            ("SandboxService", "GetSandboxWithdrawLimits"),
            (
                "MarketDataService",
                "GetCandles" if gate == "A" else "GetTradingStatus",
            ),
        }
        if gate == "A":
            allowed.add(("MarketDataService", "GetLastPrices"))
            allowed.add(("SandboxService", "GetSandboxOrders"))
        if gate == "B" and self.post_calls == 1:
            allowed.add(("SandboxService", "PostSandboxOrder"))
        for receipt in receipts:
            pair = (receipt.get("service"), receipt.get("method"))
            if pair not in allowed:
                _fail("PROVIDER_READ_SCOPE_INVALID")
            maximum_attempts = (
                self._account_policy["operations_max_attempts_per_page"]
                if pair == ("SandboxService", "GetSandboxOperationsByCursor")
                and self._account_policy is not None
                else 1
            )
            attempts = receipt.get("attempt_count")
            retries = receipt.get("retry_count")
            if (
                type(attempts) is not int
                or not 1 <= attempts <= maximum_attempts
                or retries != attempts - 1
            ):
                _fail("LIVE_ACQUISITION_BUDGET_EXHAUSTED")
            counts[pair] = counts.get(pair, 0) + 1
        market = (
            {
                ("MarketDataService", "GetCandles"): 1,
                ("MarketDataService", "GetLastPrices"): 1,
            }
            if gate == "A"
            else {("MarketDataService", "GetTradingStatus"): 1}
        )
        early_market_terminal = gate == "B" and dispatch_status in {
            "MARKET_IDLE",
            "MARKET_STATUS_UNAVAILABLE",
            "MARKET_STATUS_UNCERTAIN",
        }
        expected = dict(market)
        if not early_market_terminal:
            expected.update(
                {
                    ("SandboxService", "GetSandboxPortfolio"): 1,
                    ("SandboxService", "GetSandboxWithdrawLimits"): 1,
                }
            )
            if gate == "A":
                expected[("SandboxService", "GetSandboxOrders")] = 1
            if gate == "B" and self.post_calls == 1:
                expected[("SandboxService", "PostSandboxOrder")] = 1
        operations = counts.pop(("SandboxService", "GetSandboxOperationsByCursor"), 0)
        operations_valid = (
            operations == 0
            if early_market_terminal
            else 1
            <= operations
            <= (
                self._account_policy["operations_max_pages"]
                if self._account_policy is not None
                else 100
            )
        )
        if counts != expected or not operations_valid:
            _fail("PROVIDER_READ_INCOMPLETE")
        expected_portfolio_physical = 0 if early_market_terminal else 1
        expected_portfolio_logical = (
            0 if early_market_terminal else (2 if gate == "A" else 1)
        )
        expected_withdraw = 0 if early_market_terminal else 1
        expected_orders = 1 if gate == "A" else 0
        if (
            self.operation_calls[gate] != operations
            or self.portfolio_physical_calls[gate] != expected_portfolio_physical
            or self.portfolio_logical_calls[gate] != expected_portfolio_logical
            or self.withdraw_calls[gate] != expected_withdraw
            or self.order_calls[gate] != expected_orders
            or (
                gate == "A"
                and (
                    type(self.orders_response_canonical_sha256) is not str
                    or _HEX64.fullmatch(self.orders_response_canonical_sha256) is None
                )
            )
            or (early_market_terminal and self.post_calls != 0)
        ):
            _fail("PROVIDER_READ_INCOMPLETE")


def _sanitize_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in (
        "service",
        "method",
        "status_code",
        "error_class",
        "transient",
        "attempt_count",
        "retry_count",
        "request_started_at",
        "request_completed_at",
    ):
        item = value.get(key)
        if type(item) in {str, int, bool} or item is None:
            result[key] = item
    tracking = value.get("tracking_id")
    if type(tracking) is str and tracking:
        result["tracking_id_sha256"] = _sha256(tracking.encode("utf-8"))
    return result


def quote_evidence(
    adapter: ProviderEvidenceAdapter, request: GuiCoordinationRequest, *, now: datetime
) -> dict[str, Any]:
    raw = adapter.quote_raw
    if type(raw) is not dict or request.portfolio_risk_candidate_quote is None:
        _fail("QUOTE_OR_METADATA_INVALID")
    price = raw["price"]
    try:
        amount = Decimal(price["units"]) + Decimal(price["nano"]) / Decimal(
            1_000_000_000
        )
        provider_time = datetime.fromisoformat(
            raw["time"].replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (InvalidOperation, ValueError, TypeError):
        _fail("QUOTE_OR_METADATA_INVALID")
    quote = request.portfolio_risk_candidate_quote
    age = (now.astimezone(timezone.utc) - provider_time).total_seconds()
    if (
        quote.source != "TBANK_LAST_PRICE_EXCHANGE"
        or quote.price_at != provider_time
        or quote.unit_price_rub != float(amount)
        or age < -5
        or age > 300
    ):
        _fail("QUOTE_OR_METADATA_INVALID")
    safe = {
        "units": price["units"],
        "nano": price["nano"],
        "time": provider_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "source": quote.source,
        "target_instrument_sha256": adapter.target_sha256,
    }
    raw_canonical = _canonical(safe)
    return {"canonical_sha256": _sha256(raw_canonical), **safe}


def validate_admission(
    result: CentralOrderCoordinationResult,
    *,
    coordinator: CentralOrderCoordinator,
    before_state: Any,
) -> Any:
    after = coordinator.manager.state()
    if (
        result.status != "QUEUED"
        or result.current_lots != 0
        or result.proposed_target_lots != 1
        or result.approved_target_lots != 1
        or type(result.intent_id) is not str
        or result.cancelled_intent_id is not None
        or len(before_state.intents) != 0
        or len(after.intents) != 1
        or len(after.queued) != 1
    ):
        _fail("PROPOSAL_ADMISSION_BINDING_INVALID")
    intent = after.queued[0]
    candidate = intent.candidate
    if (
        intent.intent_id != result.intent_id
        or candidate.current_lots != 0
        or candidate.target_lots != 1
        or candidate.requested_lots != 1
        or str(candidate.direction) != "BUY"
        or after.revision <= before_state.revision
    ):
        _fail("PROPOSAL_ADMISSION_BINDING_INVALID")
    return intent


def verify_pre_admission_ledger(
    execution_adapter: Any,
    *,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    """Read back the exact CL2 head/export immediately before Central admission."""

    ledger = getattr(execution_adapter, "cl7_ledger_store", None)
    snapshot = getattr(ledger, "snapshot", None)
    export_bytes = getattr(ledger, "export_bytes", None)
    if not callable(snapshot) or not callable(export_bytes):
        _fail("CL6_CONTEXT_NOT_READY")
    try:
        first = snapshot()
        exported = export_bytes()
        second = snapshot()
        first_store_revision = first.store_revision
        first_revision = first.ledger_revision
        first_head = first.ledger_head_sha256
        second_store_revision = second.store_revision
        second_revision = second.ledger_revision
        second_head = second.ledger_head_sha256
    except Q7ALiveError:
        raise
    except Exception:  # noqa: BLE001 - finite CL2 read-back boundary
        _fail("CL6_CONTEXT_NOT_READY")
    if (
        type(first_store_revision) is not int
        or first_store_revision < 0
        or type(second_store_revision) is not int
        or second_store_revision < 0
        or type(first_revision) is not int
        or first_revision < 0
        or type(second_revision) is not int
        or second_revision < 0
        or type(first_head) is not str
        or _HEX64.fullmatch(first_head) is None
        or type(second_head) is not str
        or _HEX64.fullmatch(second_head) is None
        or type(exported) is not bytes
    ):
        _fail("CL6_CONTEXT_NOT_READY")
    export_sha256 = _sha256(exported)
    if (
        first_store_revision != second_store_revision
        or first_revision != second_revision
        or first_head != second_head
        or first_revision != expected.get("ledger_revision")
        or first_head != expected.get("ledger_head_sha256")
        or export_sha256 != expected.get("ledger_export_sha256")
    ):
        _fail("CL6_CONTEXT_NOT_READY")
    return {
        "store_revision": first_store_revision,
        "ledger_revision": first_revision,
        "ledger_head_sha256": first_head,
        "ledger_export_sha256": export_sha256,
    }


def verify_configured_active_runtime(
    runtime_file: Path,
    *,
    manifest: Mapping[str, Any],
    configured_loader: Callable[[], ConfiguredExecutionSet],
    expected_configured_set_sha256: str,
) -> ConfiguredExecutionSet:
    """Read back the exact prepared account-wide ACTIVE set without starting it."""

    expected_file_sha256 = _hash(
        manifest.get("instrument_runtimes_sha256"), "RUNTIME_MANIFEST_MISMATCH"
    )
    count = manifest.get("configured_runtime_count")
    statuses = manifest.get("configured_runtime_statuses")
    if (
        type(count) is not int
        or count not in {2, 3}
        or type(statuses) is not list
        or statuses != ["ACTIVE"] * count
        or any(type(item) is not str for item in statuses)
        or manifest.get("configured_set_sha256") != expected_configured_set_sha256
    ):
        _fail("RUNTIME_MANIFEST_MISMATCH")
    before = _read_exact(runtime_file, "CONFIGURED_SET_MISMATCH")
    try:
        configured = configured_loader()
    except Q7ALiveError:
        raise
    except Exception:  # noqa: BLE001 - finite persisted-owner read-back boundary
        _fail("CONFIGURED_SET_MISMATCH")
    after = _read_exact(runtime_file, "CONFIGURED_SET_MISMATCH")
    try:
        bindings = configured.bindings
        observed = [item.runtime.status for item in bindings]
        identity = configured.identity_sha256
    except (AttributeError, TypeError):
        _fail("CONFIGURED_SET_MISMATCH")
    if len(bindings) != count:
        _fail("CONFIGURED_SET_MISMATCH")
    if any(type(status) is not str or status != "ACTIVE" for status in observed):
        _fail("CONFIGURED_SET_NOT_ACTIVE")
    if (
        before != after
        or _sha256(before) != expected_file_sha256
        or identity != expected_configured_set_sha256
    ):
        _fail("CONFIGURED_SET_MISMATCH")
    return configured


@dataclass(slots=True)
class LiveOwners:
    preparation: LivePreparation
    configured: ConfiguredExecutionSet
    runtime: InstrumentRuntime
    profile: MultiInstrumentProfile
    provider: ProviderEvidenceAdapter
    portfolio_repository: PortfolioRepository
    coordinator: CentralOrderCoordinator
    execution_adapter: Any
    q7a_hooks: Q7AControlledHooks
    gui_hooks: _ProductionGuiHooks
    sync_gate_a: Callable[[], Any]
    clock: Callable[[], datetime]
    controlled_proposal_box: dict[str, StrategyProposal]
    verify_active_runtime: Callable[[], ConfiguredExecutionSet]
    pre_admission_checkpoint: str = "BEFORE_PROPOSAL_MARKER"


def execute_economic_smoke(owners: LiveOwners) -> dict[str, Any]:
    """Execute the one-shot accepted owner chain; no automatic retry exists."""

    prep = owners.preparation
    now = owners.clock()
    if now > _timestamp(
        prep.fields["absolute_deadline_utc"], "LIVE_EVIDENCE_EXPIRED_BEFORE_ADMISSION"
    ):
        _fail("LIVE_EVIDENCE_EXPIRED_BEFORE_ADMISSION")
    if owners.configured.identity_sha256 != prep.fields["configured_set_sha256"]:
        _fail("CONFIGURED_SET_MISMATCH")
    owners.verify_active_runtime()
    if owners.provider.target_sha256 != prep.fields["target_instrument_sha256"]:
        _fail("TARGET_NOT_MEMBER")
    risk_runtime = getattr(owners.execution_adapter, "risk_runtime", None)
    if (
        risk_runtime is None
        or str(getattr(risk_runtime, "mode", "")).upper() != "SANDBOX_EXECUTION"
        or risk_runtime.current_policy_hash() != prep.fields["risk_policy_sha256"]
    ):
        _fail("RISK_POLICY_NOT_ENFORCED")
    authority_manager = getattr(
        owners.execution_adapter, "cash_authority_manager", None
    )
    try:
        initial_authority = authority_manager.status()
    except Exception:  # noqa: BLE001 - finite CL7 custody boundary
        _fail("AUTHORITY_NOT_EXACT_CASH_ARMED")
    if initial_authority.state is not RuntimeCashAuthorityState.EXACT_CASH_ARMED:
        _fail("AUTHORITY_NOT_EXACT_CASH_ARMED")
    if initial_authority.post_attempt_count != 0:
        _fail("ATTEMPT_BUDGET_EXHAUSTED")
    if initial_authority.pending_dispatch_proof_sha256 is not None:
        _fail("PENDING_DISPATCH_PRESENT")
    before = owners.coordinator.manager.state()
    if len(before.intents) != 0 or before.reserved_cash_kopecks != 0:
        _fail("CENTRAL_NOT_QUIESCENT")
    try:
        sync_result = owners.sync_gate_a()
    except Q7ALiveError:
        raise
    except Exception:  # noqa: BLE001 - finite CL3/CL7 owner boundary
        _fail("CL3_SYNC_BLOCKED")
    if type(sync_result) is not tuple or len(sync_result) != 2:
        _fail("CL3_SYNC_BLOCKED")
    synced_authority, sync_evidence = sync_result
    context = getattr(sync_evidence, "context", None)
    context_status = str(getattr(getattr(context, "status", None), "value", ""))
    if (
        getattr(synced_authority, "state", None)
        is not RuntimeCashAuthorityState.EXACT_CASH_ARMED
        or getattr(synced_authority, "post_attempt_count", None) != 0
        or getattr(synced_authority, "pending_dispatch_proof_sha256", "INVALID")
        is not None
        or context_status != "READY_FOR_LOCKED_REVALIDATION"
        or type(getattr(context, "sha256", None)) is not str
    ):
        _fail("CL6_CONTEXT_NOT_READY")
    try:
        context_evaluated_at = _native_timestamp(
            context.evaluated_at, "CL6_CONTEXT_NOT_READY"
        )
    except AttributeError:
        _fail("CL6_CONTEXT_NOT_READY")
    owner_evidence = _gate_a_owner_evidence(synced_authority, sync_evidence)
    if owner_evidence["risk_policy_sha256"] != prep.fields["risk_policy_sha256"]:
        _fail("RISK_POLICY_NOT_ENFORCED")
    try:
        risk_state = risk_runtime.state_store.load_account(risk_runtime.account_id)
        risk_state_raw = _canonical(risk_state.to_dict())
    except Exception:  # noqa: BLE001 - finite RiskState boundary
        _fail("RISK_STATE_GUARD_MISMATCH")
    if (
        getattr(risk_state, "kill_switch_active", None) is not False
        or getattr(risk_state, "risk_resync_required", None) is not False
    ):
        _fail("RISK_STATE_GUARD_MISMATCH")
    risk_state_sha256 = _sha256(risk_state_raw)
    gate_a_deadline = min(
        _timestamp(
            prep.fields["absolute_deadline_utc"],
            "LIVE_EVIDENCE_EXPIRED_BEFORE_ADMISSION",
        ),
        context_evaluated_at + timedelta(seconds=120),
    )
    flat = bind_flat_position(
        owners.portfolio_repository,
        account_id=owners.runtime.config.account_id,
        target_instrument_id=owners.runtime.config.instrument_id,
    )
    acquire_at = owners.clock()
    verify_candle_policy_for_profile(
        preparation=prep,
        runtime=owners.runtime,
        profile=owners.profile,
    )
    try:
        frame = StrategyCandleLoader(owners.provider).load(
            owners.runtime, owners.profile, now=acquire_at
        )
    except Q7ALiveError:
        raise
    except Exception:  # noqa: BLE001 - finite candle owner boundary
        _fail("CANDLE_READ_FAILED")
    frame_evidence = canonical_candle_evidence(
        frame, policy=prep.candle_policy, now=acquire_at
    )
    verify_candle_request_binding(
        frame_evidence,
        owners.provider.candle_request,
        expected_interval=prep.candle_policy["interval"],
        expected_target_sha256=owners.provider.target_sha256,
    )
    proposal_evidence = derive_controlled_proposal(
        runtime=owners.runtime,
        profile=owners.profile,
        frame=frame,
        frame_evidence=frame_evidence,
        now=acquire_at,
    )
    owners.controlled_proposal_box["proposal"] = proposal_evidence.controlled
    owners.gui_hooks.frames[owners.runtime.config.instrument_id] = frame
    issued = owners.q7a_hooks.evaluate_closed_candle(
        owners.runtime,
        datetime.fromisoformat(proposal_evidence.controlled.candle_time),
        acquire_at,
    )
    owners.pre_admission_checkpoint = "PROPOSAL_VERIFICATION"
    if _proposal_canonical(issued) != _proposal_canonical(proposal_evidence.controlled):
        _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    owners.pre_admission_checkpoint = "COORDINATION_REQUEST"
    try:
        request = owners.q7a_hooks.coordination_request(
            owners.runtime,
            issued,
            datetime.fromisoformat(issued.candle_time),
            owners.clock(),
        )
    except GuiRuntimeBlockedError as exc:
        dependency_reason = getattr(exc, "reason", None)
        raise Q7ALiveError(
            _map_gui_blocker(exc), dependency_reason=dependency_reason
        ) from None
    owners.pre_admission_checkpoint = "REQUEST_BINDING"
    request_evaluated_at = getattr(request, "evaluated_at", None)
    expected_lot_size = getattr(owners.gui_hooks, "lot_sizes", {}).get(
        owners.runtime.config.instrument_id
    )
    if (
        request.candles is not frame
        or request.proposal is not issued
        or request.profile is not owners.profile
        or type(request.lot_size) is not int
        or request.lot_size <= 0
        or request.lot_size != expected_lot_size
        or type(request.cash_buffer_bps) is not int
        or request.cash_buffer_bps < 0
        or type(request_evaluated_at) is not datetime
        or request_evaluated_at.tzinfo is None
    ):
        _fail("PROPOSAL_ADMISSION_BINDING_INVALID")
    owners.pre_admission_checkpoint = "QUOTE_EVIDENCE"
    quote = quote_evidence(owners.provider, request, now=owners.clock())
    request_binding = {
        "account_scope_sha256": prep.fields["account_scope_sha256"],
        "configured_set_sha256": prep.fields["configured_set_sha256"],
        "target_instrument_sha256": owners.provider.target_sha256,
        "candle_frame_sha256": frame_evidence.sha256,
        "controlled_proposal_sha256": proposal_evidence.controlled_sha256,
        "quote_canonical_sha256": quote["canonical_sha256"],
        "risk_policy_sha256": prep.fields["risk_policy_sha256"],
        "lot_size": request.lot_size,
        "cash_buffer_bps": request.cash_buffer_bps,
        "evaluated_at_utc": request_evaluated_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
    }
    request_binding_sha256 = _sha256(_canonical(request_binding))
    gate_a_deadline = min(
        gate_a_deadline,
        _timestamp(quote["time"], "QUOTE_OR_METADATA_INVALID")
        + timedelta(seconds=prep.quote_policy["maximum_age_seconds"]),
        _timestamp(frame_evidence.latest_close_utc, "CANDLE_FRAME_INVALID")
        + timedelta(seconds=prep.candle_policy["maximum_age_seconds"]),
    )
    if (
        canonical_candle_evidence(
            frame, policy=prep.candle_policy, now=owners.clock()
        ).sha256
        != frame_evidence.sha256
    ):
        _fail("CANDLE_FRAME_INVALID")
    if _sha256(_proposal_canonical(issued)) != proposal_evidence.controlled_sha256:
        _fail("CONTROLLED_PROPOSAL_DERIVATION_INVALID")
    owners.pre_admission_checkpoint = "LOCAL_CUSTODY_READBACK"
    recheck_flat_position(
        owners.portfolio_repository,
        account_id=owners.runtime.config.account_id,
        target_instrument_id=owners.runtime.config.instrument_id,
        expected=flat,
    )
    try:
        risk_state_recheck = risk_runtime.state_store.load_account(
            risk_runtime.account_id
        )
        authority_recheck = authority_manager.status()
        central_recheck = owners.coordinator.manager.state()
    except Exception:  # noqa: BLE001 - finite local owner read-back boundary
        _fail("RISK_STATE_GUARD_MISMATCH")
    if _sha256(_canonical(risk_state_recheck.to_dict())) != risk_state_sha256:
        _fail("RISK_STATE_GUARD_MISMATCH")
    if authority_recheck.canonical_bytes != synced_authority.canonical_bytes:
        _fail("AUTHORITY_NOT_EXACT_CASH_ARMED")
    if (
        central_recheck.revision != before.revision
        or tuple(central_recheck.intents) != tuple(before.intents)
        or central_recheck.reserved_cash_kopecks != before.reserved_cash_kopecks
    ):
        _fail("CENTRAL_NOT_QUIESCENT")
    owners.pre_admission_checkpoint = "LEDGER_READBACK"
    ledger_readback = verify_pre_admission_ledger(
        owners.execution_adapter,
        expected=owner_evidence,
    )
    owners.pre_admission_checkpoint = "GATE_A_RECEIPTS"
    owners.provider.verify_gate_a_receipts()
    if owners.clock() > gate_a_deadline:
        _fail("LIVE_EVIDENCE_EXPIRED_BEFORE_ADMISSION")
    owners.pre_admission_checkpoint = "CENTRAL_ADMISSION"
    owners.verify_active_runtime()
    result = owners.q7a_hooks.coordinate_marked(
        coordinator=owners.coordinator,
        runtime=owners.runtime,
        request=request,
    )
    intent = validate_admission(
        result, coordinator=owners.coordinator, before_state=before
    )
    owners.provider.begin_gate_b()
    dispatch = owners.execution_adapter.dispatch_next(
        owners.portfolio_repository,
        expected_intent_id=intent.intent_id,
    )
    status = str(getattr(dispatch, "status", "UNKNOWN"))
    owners.provider.verify_gate_b_receipts(dispatch_status=status)
    if owners.provider.status_calls != 1 or owners.provider.post_calls > 1:
        _fail("POSTCONDITION_FAILED")
    outcome_class = classify_dispatch_outcome(
        dispatch, provider_post_attempts=owners.provider.post_calls
    )
    after_dispatch = owners.coordinator.manager.state()
    matching = tuple(
        item for item in after_dispatch.intents if item.intent_id == intent.intent_id
    )
    try:
        terminal_authority = authority_manager.status()
    except Exception:  # noqa: BLE001 - finite CL7 custody boundary
        _fail("POSTCONDITION_FAILED")
    if (
        len(matching) != 1
        or terminal_authority.post_attempt_count != owners.provider.post_calls
        or terminal_authority.post_attempt_count not in {0, 1}
        or (
            outcome_class == "PROVIDER_OUTCOME_AMBIGUOUS"
            and terminal_authority.pending_dispatch_proof_sha256 is None
        )
    ):
        _fail("POSTCONDITION_FAILED")
    return {
        "domain": "CL8_Q7A_LIVE_RESULT_V1",
        "version": 1,
        "experiment_id": EXPERIMENT_ID,
        "preparation_sha256": prep.sha256,
        "candidate_commit": prep.fields["candidate_commit"],
        "candidate_tree": prep.fields["candidate_tree"],
        "account_scope_sha256": prep.fields["account_scope_sha256"],
        "configured_set_sha256": prep.fields["configured_set_sha256"],
        "target_instrument_sha256": owners.provider.target_sha256,
        "candle_frame_sha256": frame_evidence.sha256,
        "candle_row_count": frame_evidence.row_count,
        "base_proposal_sha256": proposal_evidence.base_sha256,
        "controlled_proposal_sha256": proposal_evidence.controlled_sha256,
        "derivation_version": DERIVATION_VERSION,
        "quote_evidence": quote,
        "portfolio_revision": flat.revision,
        "portfolio_decision_sha256": flat.decision_sha256,
        "cl6_context_sha256": context.sha256,
        "cl6_context_status": context_status,
        "risk_policy_sha256": prep.fields["risk_policy_sha256"],
        "risk_state_sha256": risk_state_sha256,
        "gate_a_owner_evidence": owner_evidence,
        "gate_a_orders_response_canonical_sha256": (
            owners.provider.orders_response_canonical_sha256
        ),
        "pre_admission_ledger_readback": ledger_readback,
        "request_binding_sha256": request_binding_sha256,
        "gate_a_freshness_deadline_utc": gate_a_deadline.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "candle_request_set_sha256": _sha256(
            _canonical(owners.provider.candle_request)
        ),
        "admission_binding_sha256": _sha256(
            owners.q7a_hooks.admission_binding_raw or b""
        ),
        "central_revision": owners.coordinator.manager.state().revision,
        "intent_id_sha256": _sha256(intent.intent_id.encode("utf-8")),
        "provider_read_counts": {
            "GetCandles": owners.provider.candle_calls,
            "GetLastPrices": owners.provider.quote_calls,
            "GetSandboxOrders": owners.provider.order_calls["A"],
            "GetTradingStatus": owners.provider.status_calls,
        },
        "provider_post_attempts": owners.provider.post_calls,
        "dispatch_status": status,
        "gate_b_precheck_evidence": owners.provider.gate_b_precheck_evidence,
        "outcome_class": outcome_class,
        "terminal_authority_record_sha256": terminal_authority.sha256,
        "terminal_authority_revision": terminal_authority.record_revision,
        "terminal_pending_dispatch_proof": (
            terminal_authority.pending_dispatch_proof_sha256 is not None
        ),
        "telemetry": owners.provider.telemetry,
        "completed_at_utc": owners.clock()
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }


def classify_dispatch_outcome(dispatch: Any, *, provider_post_attempts: int) -> str:
    status = str(getattr(dispatch, "status", "")).strip().upper()
    may_have_been_sent = getattr(dispatch, "order_may_have_been_sent", None)
    if type(provider_post_attempts) is not int or provider_post_attempts not in {0, 1}:
        _fail("POSTCONDITION_FAILED")
    if status in {"SUBMISSION_UNCERTAIN", "STATE_COMMIT_UNCERTAIN"}:
        if provider_post_attempts != 1 or may_have_been_sent is not True:
            _fail("POSTCONDITION_FAILED")
        return "PROVIDER_OUTCOME_AMBIGUOUS"
    if status in {"SUBMITTED", "ORDER_OBSERVED"}:
        if provider_post_attempts != 1:
            _fail("POSTCONDITION_FAILED")
        return "POSTED_AWAITING_TERMINAL_RECONCILIATION"
    if status == "SUBMISSION_REJECTED":
        if provider_post_attempts != 1:
            _fail("POSTCONDITION_FAILED")
        return "PROVIDER_SAFE_REJECTED"
    if status in {
        "MARKET_IDLE",
        "MARKET_STATUS_UNAVAILABLE",
        "MARKET_STATUS_UNCERTAIN",
    }:
        if provider_post_attempts != 0:
            _fail("POSTCONDITION_FAILED")
        return "PROVIDER_SAFE_REJECTED"
    if status and provider_post_attempts == 0:
        return "POST_ADMISSION_RECOVERY_REQUIRED"
    _fail("POSTCONDITION_FAILED")


def write_evidence_once(path: Path, value: Mapping[str, Any]) -> str:
    raw = _canonical(dict(value))
    manifest_path = path.with_name(path.name + ".manifest.json")
    if (
        path.exists()
        or path.is_symlink()
        or manifest_path.exists()
        or manifest_path.is_symlink()
    ):
        _fail("EVIDENCE_WRITE_FAILED")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != raw:
            _fail("EVIDENCE_WRITE_FAILED")
        digest = _sha256(raw)
        manifest = _canonical(
            {
                "domain": "CL8_Q7A_EVIDENCE_FILE_MANIFEST_V1",
                "version": 1,
                "file_name": path.name,
                "size": len(raw),
                "sha256": digest,
            }
        )
        with manifest_path.open("xb") as stream:
            stream.write(manifest)
            stream.flush()
            os.fsync(stream.fileno())
        if manifest_path.read_bytes() != manifest:
            _fail("EVIDENCE_WRITE_FAILED")
    except Q7ALiveError:
        raise
    except OSError:
        _fail("EVIDENCE_WRITE_FAILED")
    return digest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CL8 Q7A one-shot live economic smoke")
    parser.add_argument("mode", choices=(LIVE_MODE, "VALIDATE_PREPARATION"))
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--expected-preparation-sha256", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--candidate-tree", required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--runtime-manifest", type=Path)
    parser.add_argument("--control-record", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    return parser


def _read_exact(path: Path, reason: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            _fail(reason)
        return path.read_bytes()
    except Q7ALiveError:
        raise
    except OSError:
        _fail(reason)


def evidence_root_identity(path: Path) -> str:
    """Hash the normalized private root without exporting its absolute path."""

    if not isinstance(path, Path) or not path.is_absolute():
        _fail("EVIDENCE_WRITE_FAILED")
    try:
        normalized = os.path.normcase(str(path.resolve(strict=False)))
    except OSError:
        _fail("EVIDENCE_WRITE_FAILED")
    return _sha256(
        _canonical(
            {
                "domain": "CL8_Q7A_EVIDENCE_ROOT_V1",
                "normalized_private_path": normalized,
            }
        )
    )


def _verify_evidence_root(path: Path, expected_sha256: str) -> None:
    if not hmac.compare_digest(evidence_root_identity(path), expected_sha256):
        _fail("EVIDENCE_WRITE_FAILED")
    current = path
    while True:
        if current.exists():
            is_junction = getattr(current, "is_junction", None)
            if current.is_symlink() or (callable(is_junction) and is_junction()):
                _fail("EVIDENCE_WRITE_FAILED")
        parent = current.parent
        if parent == current:
            break
        current = parent


def consume_preparation_once(path: Path, prep: LivePreparation) -> str:
    marker = {
        "domain": "CL8_Q7A_PREPARATION_CONSUMPTION_V1",
        "version": 1,
        "experiment_id": EXPERIMENT_ID,
        "preparation_sha256": prep.sha256,
        "candidate_commit": prep.fields["candidate_commit"],
        "candidate_tree": prep.fields["candidate_tree"],
    }
    if path.exists() or path.is_symlink():
        _fail("PREPARATION_ALREADY_CONSUMED")
    try:
        return write_evidence_once(path, marker)
    except Q7ALiveError:
        if path.exists():
            _fail("PREPARATION_ALREADY_CONSUMED")
        raise


def _compose_live_owners(args: argparse.Namespace, prep: LivePreparation) -> LiveOwners:
    """Materialize the accepted existing owner graph after all public checks."""

    required_paths = (
        args.runtime_dir,
        args.runtime_manifest,
        args.control_record,
        args.metadata,
        args.evidence_dir,
    )
    if any(value is None for value in required_paths):
        _fail("PREPARATION_INVALID")
    _verify_evidence_root(args.evidence_dir, prep.fields["evidence_root_sha256"])
    manifest_raw = _read_exact(args.runtime_manifest, "RUNTIME_MANIFEST_MISMATCH")
    if _sha256(manifest_raw) != prep.fields["runtime_manifest_sha256"]:
        _fail("RUNTIME_MANIFEST_MISMATCH")
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeError, json.JSONDecodeError):
        _fail("RUNTIME_MANIFEST_MISMATCH")
    expected_manifest = {
        "candidate_commit": prep.fields["candidate_commit"],
        "candidate_tree": prep.fields["candidate_tree"],
        "environment": "SANDBOX",
        "account_scope_sha256": prep.fields["account_scope_sha256"],
        "configured_set_sha256": prep.fields["configured_set_sha256"],
        "identity_key_id": prep.fields["identity_key_id"],
        "backup_binding_sha256": prep.fields["backup_binding_sha256"],
    }
    if type(manifest) is not dict or any(
        manifest.get(key) != value for key, value in expected_manifest.items()
    ):
        _fail("RUNTIME_MANIFEST_MISMATCH")
    metadata_raw = _read_exact(args.metadata, "CONTROL_RECORD_INVALID")
    if _sha256(metadata_raw) != prep.fields["static_metadata_sha256"]:
        _fail("CONTROL_RECORD_INVALID")
    raw_control = _read_exact(args.control_record, "CONTROL_RECORD_INVALID")
    if _sha256(raw_control) != prep.fields["control_record_sha256"]:
        _fail("CONTROL_RECORD_INVALID")
    control = Q7AControlRecord(raw_control)
    try:
        control_fields = control.fields
    except (UnicodeError, json.JSONDecodeError, TypeError):
        _fail("CONTROL_RECORD_INVALID")
    if control_fields.get("metadata_sha256") != prep.fields["static_metadata_sha256"]:
        _fail("CONTROL_RECORD_INVALID")

    from tools.v3_10_q7_prepare_runtime import _configured_set
    from tools.v3_10_runtime_cash_cutover import _open_runtime
    from trading_robot.central_order_coordinator import CentralOrderCoordinator
    from trading_robot.runtime_cash_authority import derive_account_scope

    live = _open_runtime(
        args.runtime_dir,
        create_ledger=False,
        require_provider=True,
        allow_environment_secrets=False,
    )
    account_scope = derive_account_scope(
        live.raw_account,
        identity_key=live.identity_key,
        identity_key_id=live.identity_key_id,
    )
    if account_scope != prep.fields["account_scope_sha256"]:
        _fail("ACCOUNT_SCOPE_MISMATCH")
    if live.identity_key_id != prep.fields["identity_key_id"]:
        _fail("CREDENTIAL_CUSTODY_INVALID")
    runtime_file = args.runtime_dir / "instrument_runtimes.json"

    def verify_active_runtime() -> ConfiguredExecutionSet:
        return verify_configured_active_runtime(
            runtime_file,
            manifest=manifest,
            configured_loader=lambda: _configured_set(
                args.runtime_dir,
                account_id=live.raw_account,
                account_scope_sha256=account_scope,
                bootstrap_missing=False,
            ),
            expected_configured_set_sha256=prep.fields["configured_set_sha256"],
        )

    configured = verify_active_runtime()
    target = control.fields.get("target_instrument_id")
    if (
        type(target) is not str
        or _sha256(target.encode("utf-8")) != prep.fields["target_instrument_sha256"]
    ):
        _fail("TARGET_NOT_MEMBER")
    binding = next(
        (
            item
            for item in configured.bindings
            if item.runtime.config.instrument_id == target
        ),
        None,
    )
    if binding is None:
        _fail("TARGET_NOT_MEMBER")
    provider = ProviderEvidenceAdapter(
        live.provider,
        target_instrument_id=target,
        account_id=live.raw_account,
        account_policy=prep.fields["account_cash_portfolio_policy"],
        candle_interval=prep.candle_policy["interval"],
        candle_lookback_days=prep.candle_policy["lookback_days"],
    )
    live.provider = provider
    live.portfolio_manager.api = provider
    adapter = live.adapter()
    coordinator = CentralOrderCoordinator(
        live.central,
        live.portfolio,
        adapter.risk_runtime,
        portfolio_risk_runtime=adapter.portfolio_risk_runtime,
    )
    frames: dict[str, pd.DataFrame] = {}
    clock = lambda: datetime.now(timezone.utc)
    gui_hooks = _ProductionGuiHooks(
        provider=provider,
        risk_runtime=adapter.risk_runtime,
        portfolio_refresher=lambda: live.portfolio_manager.refresh(record_event=False),
        profiles={target: binding.profile},
        frames=frames,
        lot_sizes={target: int(control.fields["target_lot_size"])},
        clock=clock,
    )

    proposal_box: dict[str, StrategyProposal] = {}
    q7a_hooks = Q7AControlledHooks(
        delegate=gui_hooks,
        configured=configured,
        record=control,
        candidate_commit=prep.fields["candidate_commit"],
        candidate_tree=prep.fields["candidate_tree"],
        metadata_path=args.metadata,
        proposal_marker_path=args.evidence_dir / "proposal-marker.json",
        controlled_proposal=lambda _runtime, _candle, _now: proposal_box["proposal"],
    )
    owners = LiveOwners(
        preparation=prep,
        configured=configured,
        runtime=binding.runtime,
        profile=binding.profile,
        provider=provider,
        portfolio_repository=live.portfolio,
        coordinator=coordinator,
        execution_adapter=adapter,
        q7a_hooks=q7a_hooks,
        gui_hooks=gui_hooks,
        sync_gate_a=lambda: live.authority.sync_runtime(**live.inputs()),
        clock=clock,
        controlled_proposal_box=proposal_box,
        verify_active_runtime=verify_active_runtime,
    )
    return owners


def _write_terminal_blocked(
    *,
    args: argparse.Namespace,
    prep: LivePreparation,
    reason: str,
    owners: LiveOwners | None,
    dependency_reason: str | None = None,
    candle_validation_stage: str | None = None,
    candle_validation_reason: str | None = None,
) -> str:
    normalized = reason if reason in _PRIMARY_REASONS else "POSTCONDITION_FAILED"
    terminal: dict[str, Any] = {
        "domain": "CL8_Q7A_LIVE_TERMINAL_V1",
        "version": 1,
        "experiment_id": EXPERIMENT_ID,
        "preparation_sha256": prep.sha256,
        "candidate_commit": prep.fields["candidate_commit"],
        "candidate_tree": prep.fields["candidate_tree"],
        "status": "BLOCKED",
        "reason": normalized,
        "completed_at_utc": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
    }
    if type(dependency_reason) is str and (
        _POST_MARKER_SYNTHETIC_REASONS.get(dependency_reason) == normalized
        or _POST_MARKER_GUI_REASONS.get(dependency_reason) == normalized
    ):
        terminal["dependency_reason"] = dependency_reason
    if owners is not None:
        checkpoint = getattr(owners, "pre_admission_checkpoint", None)
        try:
            central = owners.coordinator.manager.state()
            intents = tuple(central.intents)
            if (
                not intents
                and type(checkpoint) is str
                and checkpoint in _PRE_ADMISSION_CHECKPOINTS
            ):
                terminal["pre_admission_checkpoint"] = checkpoint
            authority_manager = getattr(
                owners.execution_adapter, "cash_authority_manager", None
            )
            authority = authority_manager.status()
            if intents:
                terminal.update(
                    {
                        "status": "RECOVERY_REQUIRED",
                        "reason": "EXISTING_INTENT_REQUIRES_RECOVERY",
                        "trigger_reason": normalized,
                        "central_revision": central.revision,
                        "central_intent_count": len(intents),
                        "central_lineage_sha256": _sha256(
                            _canonical(
                                sorted(
                                    _sha256(item.intent_id.encode("utf-8"))
                                    for item in intents
                                )
                            )
                        ),
                        "authority_record_sha256": authority.sha256,
                        "authority_revision": authority.record_revision,
                        "post_attempt_count": authority.post_attempt_count,
                        "pending_dispatch_proof": (
                            authority.pending_dispatch_proof_sha256 is not None
                        ),
                    }
                )
        except Exception:  # noqa: BLE001 - best-effort privacy-safe read-back
            terminal["custody_readback"] = "UNAVAILABLE"
    if (
        terminal["reason"] == "CANDLE_FRAME_INVALID"
        and "pre_admission_checkpoint" in terminal
        and type(candle_validation_stage) is str
        and type(candle_validation_reason) is str
        and candle_validation_reason
        in _CANDLE_VALIDATION_REASONS.get(candle_validation_stage, ())
    ):
        terminal["candle_validation_stage"] = candle_validation_stage
        terminal["candle_validation_reason"] = candle_validation_reason
    try:
        write_evidence_once(args.evidence_dir / "terminal-blocked.json", terminal)
    except Exception:  # noqa: BLE001 - terminal evidence failure is itself finite
        return "EVIDENCE_WRITE_FAILED"
    return str(terminal["reason"])


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    prep: LivePreparation | None = None
    owners: LiveOwners | None = None
    consumed = False
    try:
        raw = _read_exact(args.preparation, "PREPARATION_INVALID")
        prep = verify_preparation(
            raw,
            expected_sha256=args.expected_preparation_sha256,
            candidate_commit=args.candidate_commit,
            candidate_tree=args.candidate_tree,
        )
        if args.mode == "VALIDATE_PREPARATION":
            print(
                _canonical(
                    {"status": "PASS", "preparation_sha256": prep.sha256}
                ).decode("utf-8")
            )
            return 0
        if args.runtime_dir is None or args.evidence_dir is None:
            _fail("PREPARATION_INVALID")
        lock_path = (
            args.runtime_dir / f".q7a-{prep.fields['account_scope_sha256']}.lock"
        )
        try:
            with InterProcessFileLock(lock_path, timeout_seconds=1.0):
                _verify_evidence_root(
                    args.evidence_dir, prep.fields["evidence_root_sha256"]
                )
                consume_preparation_once(
                    args.evidence_dir / "preparation-consumed.json", prep
                )
                consumed = True
                owners = _compose_live_owners(args, prep)
                result = execute_economic_smoke(owners)
                result_sha = write_evidence_once(
                    args.evidence_dir / "live-result.json", result
                )
        except LockUnavailableError:
            _fail("SINGLE_INSTANCE_LOCK_UNAVAILABLE")
        print(
            _canonical({"status": "TERMINAL", "result_sha256": result_sha}).decode(
                "utf-8"
            )
        )
        return 0
    except (Q7ALiveError, Q7ASyntheticError) as exc:
        if isinstance(exc, Q7ASyntheticError):
            dependency_reason = getattr(exc, "reason", None)
            reason = (
                _POST_MARKER_SYNTHETIC_REASONS.get(
                    dependency_reason, "POSTCONDITION_FAILED"
                )
                if type(dependency_reason) is str
                else "POSTCONDITION_FAILED"
            )
            candle_validation_stage = None
            candle_validation_reason = None
        else:
            reason = exc.reason
            dependency_reason = exc.dependency_reason
            candle_validation_stage = exc.candle_validation_stage
            candle_validation_reason = exc.candle_validation_reason
        if consumed and prep is not None and args.evidence_dir is not None:
            reason = _write_terminal_blocked(
                args=args,
                prep=prep,
                reason=reason,
                owners=owners,
                dependency_reason=dependency_reason,
                candle_validation_stage=candle_validation_stage,
                candle_validation_reason=candle_validation_reason,
            )
        print(_canonical({"status": "BLOCKED", "reason": reason}).decode("utf-8"))
        return 2
    except Exception:  # noqa: BLE001 - never export raw owner/provider exceptions
        reason = "POSTCONDITION_FAILED"
        if consumed and prep is not None and args.evidence_dir is not None:
            reason = _write_terminal_blocked(
                args=args,
                prep=prep,
                reason=reason,
                owners=owners,
            )
        print(_canonical({"status": "BLOCKED", "reason": reason}).decode("utf-8"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
