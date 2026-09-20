"""Offline Q7A control boundary and fake Sandbox transport.

This module has no credential or network entry point.  A controlled proposal
still goes through the existing GUI coordination hook, Central, Risk and CL7
adapter.  The fake transport is deliberately incapable of proving live cash or
operation completeness; those gates belong to a later, separately authorized
Preparation and experiment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_robot.cash_ledger_domain import LedgerClassification
from trading_robot.cash_ledger_persistence import LedgerHead
from trading_robot.central_order_manager import CentralOrderIntent
from trading_robot.gui_runtime_controller import (
    ConfiguredExecutionSet,
    GuiCoordinationRequest,
)
from trading_robot.instrument_runtime import InstrumentRuntime
from trading_robot.multi_instrument_strategy import StrategyProposal
from trading_robot.portfolio_risk_read_service import load_portfolio_risk_metadata
from trading_robot.portfolio_risk_shadow import PortfolioRiskCandidateQuote
from trading_robot.tbank_sandbox import TBankAPIError

CONTRACT_COMMIT = "13cf47dbff0b1b1cb4310ee7a49641b580d559e3"
CONTRACT_TREE = "44384da47d41cf873bdd7d947804960bb403bb6e"
CONTROL_DOMAIN = "CL8_Q7A_CONTROL_RECORD_V1"
EXPERIMENT_ID = "CL8-Q7-E2E-SMOKE-V1"
CONTROL_MODE = "CONTROLLED_Q7A_ONLY"
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_UTC = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z\Z")
_CONTROL_FIELDS = frozenset(
    {
        "version",
        "domain",
        "experiment_id",
        "contract_commit",
        "contract_tree",
        "candidate_commit",
        "candidate_tree",
        "account_scope_sha256",
        "configured_set_sha256",
        "target_instrument_id",
        "target_runtime_key_sha256",
        "target_lot_size",
        "metadata_sha256",
        "requested_target_lots",
        "max_provider_post_attempts",
        "automatic_retries",
        "control_mode",
        "created_at",
        "record_sha256",
    }
)


class Q7ASyntheticError(RuntimeError):
    """Finite, privacy-safe Q7A offline boundary failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _fail(reason: str) -> None:
    raise Q7ASyntheticError(reason)


def _canonical(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("CONTROL_NOT_CANONICAL")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class _FrozenDict(dict):
    """Prevent the controlled proposal's nested maps changing after issuance."""

    def _deny(self, *_args: object, **_kwargs: object) -> None:
        _fail("PROPOSAL_MARKER_INVALID")

    __setitem__ = _deny
    __delitem__ = _deny
    clear = _deny
    pop = _deny
    popitem = _deny
    setdefault = _deny
    update = _deny
    __ior__ = _deny

    def __deepcopy__(self, memo: dict[int, object]) -> dict[object, object]:
        # StrategyDecision.to_dict uses dataclasses.asdict; expose ordinary
        # JSON data there while keeping the issued object itself sealed.
        return {
            deepcopy(key, memo): deepcopy(value, memo) for key, value in self.items()
        }


def _seal_json(value: object) -> object:
    if isinstance(value, Mapping):
        return _FrozenDict({key: _seal_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_seal_json(item) for item in value)
    return value


def _seal_proposal(proposal: StrategyProposal) -> StrategyProposal:
    return replace(
        proposal,
        decisions=_FrozenDict(
            {
                key: replace(decision, indicators=_seal_json(decision.indicators))
                for key, decision in proposal.decisions.items()
            }
        ),
        comparison=_seal_json(proposal.comparison),
    )


def _hex(value: object, pattern: re.Pattern[str], reason: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(reason)
    return value


def _timestamp(value: object) -> str:
    if type(value) is not str or _UTC.fullmatch(value) is None:
        _fail("CONTROL_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("CONTROL_TIME_INVALID")
    if parsed.tzinfo != timezone.utc or parsed.isoformat(timespec="microseconds") != (
        value[:-1] + "+00:00"
    ):
        _fail("CONTROL_TIME_INVALID")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("CONTROL_DUPLICATE_FIELD")
        result[key] = value
    return result


def _members(configured: ConfiguredExecutionSet) -> dict[str, InstrumentRuntime]:
    if type(configured) is not ConfiguredExecutionSet or len(
        configured.bindings
    ) not in {
        2,
        3,
    }:
        _fail("CONFIGURED_SET_INVALID")
    _hex(configured.account_scope_sha256, _HEX64, "ACCOUNT_SCOPE_INVALID")
    members: dict[str, InstrumentRuntime] = {}
    keys: set[str] = set()
    account_ids: set[str] = set()
    for binding in configured.bindings:
        runtime = binding.runtime
        instrument = runtime.config.instrument_id
        if (
            instrument in members
            or runtime.runtime_key in keys
            or binding.profile.instrument_id != instrument
            or binding.profile.to_runtime_config(runtime.config.account_id).to_dict()
            != runtime.config.to_dict()
        ):
            _fail("CONFIGURED_SET_INVALID")
        members[instrument] = runtime
        keys.add(runtime.runtime_key)
        account_ids.add(runtime.config.account_id)
    if len(account_ids) != 1:
        _fail("CONFIGURED_SET_INVALID")
    return members


def _target_metadata(path: Path, target: str) -> tuple[int, str]:
    try:
        before = path.read_bytes()
        metadata = load_portfolio_risk_metadata(path)
        selected = metadata.get(target)
        after = path.read_bytes()
    except (OSError, RuntimeError, TypeError, ValueError):
        _fail("METADATA_UNVERIFIED")
    if before != after:
        _fail("METADATA_UNVERIFIED")
    if selected is None or selected.currency != "RUB":
        _fail("TARGET_CURRENCY_NOT_RUB")
    if type(selected.lot_size) is not int or selected.lot_size < 1:
        _fail("TARGET_LOT_SIZE_INVALID")
    return selected.lot_size, _sha256(after)


@dataclass(frozen=True, slots=True)
class Q7AControlRecord:
    """Exact original canonical bytes, never a mutable decoded dictionary."""

    raw: bytes

    @property
    def fields(self) -> dict[str, object]:
        return json.loads(self.raw)

    @property
    def record_sha256(self) -> str:
        return str(self.fields["record_sha256"])


def build_control_record(
    *,
    candidate_commit: str,
    candidate_tree: str,
    configured: ConfiguredExecutionSet,
    target_instrument_id: str,
    metadata_path: Path,
    created_at: str,
) -> Q7AControlRecord:
    """Build one immutable, account-bound control record without provider IO."""

    members = _members(configured)
    if type(target_instrument_id) is not str or target_instrument_id not in members:
        _fail("TARGET_NOT_CONFIGURED")
    lot_size, metadata_sha = _target_metadata(metadata_path, target_instrument_id)
    fields: dict[str, object] = {
        "version": 1,
        "domain": CONTROL_DOMAIN,
        "experiment_id": EXPERIMENT_ID,
        "contract_commit": CONTRACT_COMMIT,
        "contract_tree": CONTRACT_TREE,
        "candidate_commit": _hex(candidate_commit, _HEX40, "CANDIDATE_INVALID"),
        "candidate_tree": _hex(candidate_tree, _HEX40, "CANDIDATE_INVALID"),
        "account_scope_sha256": configured.account_scope_sha256,
        "configured_set_sha256": configured.identity_sha256,
        "target_instrument_id": target_instrument_id,
        "target_runtime_key_sha256": _sha256(
            members[target_instrument_id].runtime_key.encode("utf-8")
        ),
        "target_lot_size": lot_size,
        "metadata_sha256": metadata_sha,
        "requested_target_lots": 1,
        "max_provider_post_attempts": 1,
        "automatic_retries": 0,
        "control_mode": CONTROL_MODE,
        "created_at": _timestamp(created_at),
    }
    fields["record_sha256"] = _sha256(_canonical(fields))
    return Q7AControlRecord(_canonical(fields))


def verify_control_record(
    raw: bytes,
    *,
    candidate_commit: str,
    candidate_tree: str,
    configured: ConfiguredExecutionSet,
    metadata_path: Path,
) -> Q7AControlRecord:
    """Reject altered, noncanonical or context-substituted control bytes."""

    if type(raw) is not bytes or raw.startswith(b"\xef\xbb\xbf") or raw.endswith(b"\n"):
        _fail("CONTROL_ENCODING_INVALID")
    try:
        fields = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError):
        _fail("CONTROL_ENCODING_INVALID")
    if type(fields) is not dict or frozenset(fields) != _CONTROL_FIELDS:
        _fail("CONTROL_SCHEMA_INVALID")
    if _canonical(fields) != raw:
        _fail("CONTROL_NOT_CANONICAL")
    digest = _hex(fields["record_sha256"], _HEX64, "CONTROL_HASH_INVALID")
    preimage = dict(fields)
    del preimage["record_sha256"]
    if not hmac.compare_digest(_sha256(_canonical(preimage)), digest):
        _fail("CONTROL_HASH_MISMATCH")
    if (
        type(fields["version"]) is not int
        or fields["version"] != 1
        or fields["domain"] != CONTROL_DOMAIN
        or fields["experiment_id"] != EXPERIMENT_ID
        or fields["contract_commit"] != CONTRACT_COMMIT
        or fields["contract_tree"] != CONTRACT_TREE
        or fields["control_mode"] != CONTROL_MODE
        or fields["candidate_commit"]
        != _hex(candidate_commit, _HEX40, "CANDIDATE_INVALID")
        or fields["candidate_tree"] != _hex(candidate_tree, _HEX40, "CANDIDATE_INVALID")
    ):
        _fail("CONTROL_IDENTITY_MISMATCH")
    _timestamp(fields["created_at"])
    for key, expected in (
        ("requested_target_lots", 1),
        ("max_provider_post_attempts", 1),
        ("automatic_retries", 0),
    ):
        if type(fields[key]) is not int or fields[key] != expected:
            _fail("CONTROL_BUDGET_INVALID")
    members = _members(configured)
    target = fields["target_instrument_id"]
    if type(target) is not str or target not in members:
        _fail("TARGET_NOT_CONFIGURED")
    lot_size, metadata_sha = _target_metadata(metadata_path, target)
    if (
        fields["account_scope_sha256"] != configured.account_scope_sha256
        or fields["configured_set_sha256"] != configured.identity_sha256
        or fields["target_runtime_key_sha256"]
        != _sha256(members[target].runtime_key.encode("utf-8"))
        or type(fields["target_lot_size"]) is not int
        or fields["target_lot_size"] != lot_size
        or fields["metadata_sha256"] != metadata_sha
    ):
        _fail("CONTROL_BINDING_MISMATCH")
    return Q7AControlRecord(raw)


def write_control_record_once(path: Path, record: Q7AControlRecord) -> str:
    """Create once; an existing evidence file is never overwritten."""

    return _write_once(path, record.raw, "CONTROL_ALREADY_EXISTS")


def _write_once(path: Path, raw: bytes, existing_reason: str) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        _fail(existing_reason)
    except OSError:
        _fail("CONTROL_PERSISTENCE_FAILED")
    try:
        readback = path.read_bytes()
    except OSError:
        _fail("CONTROL_POSTCONDITION_FAILED")
    if readback != raw:
        _fail("CONTROL_READBACK_MISMATCH")
    return _sha256(raw)


class Q7AControlledHooks:
    """Intercept synthetic proposals before the existing Central hook.

    The delegate still owns market/Risk/Portfolio refresh and construction of
    the normal coordination request.  This wrapper owns only an experiment
    budget and evidence binding; it never dispatches or posts itself.
    """

    def __init__(
        self,
        *,
        delegate: Any,
        configured: ConfiguredExecutionSet,
        record: Q7AControlRecord,
        candidate_commit: str,
        candidate_tree: str,
        metadata_path: Path,
        proposal_marker_path: Path,
        controlled_proposal: Callable[
            [InstrumentRuntime, datetime, datetime], StrategyProposal
        ],
    ) -> None:
        self.delegate = delegate
        self.configured = configured
        self.record = verify_control_record(
            record.raw,
            candidate_commit=candidate_commit,
            candidate_tree=candidate_tree,
            configured=configured,
            metadata_path=metadata_path,
        )
        self.controlled_proposal = controlled_proposal
        if not isinstance(proposal_marker_path, Path):
            _fail("PROPOSAL_MARKER_PATH_INVALID")
        self.proposal_marker_path = proposal_marker_path
        self._proposal_marker_raw: bytes | None = None
        self._issued_proposal_sha256: str | None = None
        self._proposal_sha256: str | None = None
        self._issued_proposal: StrategyProposal | None = None
        self._target_evaluation_used = False

    @property
    def proposal_sha256(self) -> str | None:
        return self._proposal_sha256

    def refresh_market_status(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.delegate.refresh_market_status(runtime, now)

    def refresh_risk(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.delegate.refresh_risk(runtime, now)

    def reconcile_portfolio(self, runtime: InstrumentRuntime, now: datetime) -> Any:
        return self.delegate.reconcile_portfolio(runtime, now)

    def evaluate_closed_candle(
        self, runtime: InstrumentRuntime, candle_time: datetime, now: datetime
    ) -> Any:
        if runtime.config.instrument_id == self.record.fields["target_instrument_id"]:
            if self._target_evaluation_used:
                _fail("SECOND_LIFECYCLE_FORBIDDEN")
            proposal = self.controlled_proposal(runtime, candle_time, now)
            if type(proposal) is not StrategyProposal:
                _fail("CONTROLLED_PROPOSAL_INVALID")
            proposal = _seal_proposal(proposal)
            if (
                type(now) is not datetime
                or now.tzinfo is None
                or now.utcoffset() is None
            ):
                _fail("CONTROL_TIME_INVALID")
            if now.astimezone(timezone.utc) < datetime.fromisoformat(
                self.record.fields["created_at"].replace("Z", "+00:00")
            ):
                _fail("CONTROL_TIME_INVALID")
            proposal_sha256 = _sha256(_canonical(proposal.to_dict()))
            marker = _canonical(
                {
                    "domain": "CL8_Q7A_PROPOSAL_MARKER_V1",
                    "version": 1,
                    "control_record_sha256": self.record.record_sha256,
                    "proposal_sha256": proposal_sha256,
                    "target_runtime_key_sha256": self.record.fields[
                        "target_runtime_key_sha256"
                    ],
                    "issued_at": now.astimezone(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%S.%fZ"
                    ),
                }
            )
            _write_once(self.proposal_marker_path, marker, "PROPOSAL_ALREADY_ISSUED")
            self._proposal_marker_raw = marker
            self._issued_proposal_sha256 = proposal_sha256
            self._issued_proposal = proposal
            self._target_evaluation_used = True
        else:
            proposal = self.delegate.evaluate_closed_candle(runtime, candle_time, now)
        return proposal

    def coordination_request(
        self,
        runtime: InstrumentRuntime,
        proposal: StrategyProposal,
        candle_time: datetime,
        now: datetime,
    ) -> GuiCoordinationRequest:
        fields = self.record.fields
        if (
            runtime.config.instrument_id != fields["target_instrument_id"]
            or _sha256(runtime.runtime_key.encode("utf-8"))
            != fields["target_runtime_key_sha256"]
        ):
            _fail("NON_TARGET_COORDINATION_FORBIDDEN")
        if self._proposal_sha256 is not None:
            _fail("SECOND_LIFECYCLE_FORBIDDEN")
        if (
            proposal is not self._issued_proposal
            or not self._target_evaluation_used
            or type(proposal) is not StrategyProposal
            or proposal.runtime_key != runtime.runtime_key
            or proposal.instrument_id != runtime.config.instrument_id
            or proposal.strategy_profile_hash != runtime.config.strategy_config_hash
            or type(proposal.primary_target_lots) is not int
            or proposal.primary_target_lots != 1
            or proposal.execution_authorized is not False
        ):
            _fail("CONTROLLED_PROPOSAL_INVALID")
        try:
            marker = self.proposal_marker_path.read_bytes()
        except OSError:
            _fail("PROPOSAL_MARKER_INVALID")
        if self._proposal_marker_raw is None or marker != self._proposal_marker_raw:
            _fail("PROPOSAL_MARKER_INVALID")
        if self._issued_proposal_sha256 != _sha256(_canonical(proposal.to_dict())):
            _fail("PROPOSAL_MARKER_INVALID")
        request = self.delegate.coordination_request(
            runtime, proposal, candle_time, now
        )
        if self._issued_proposal_sha256 != _sha256(_canonical(proposal.to_dict())):
            _fail("PROPOSAL_MARKER_INVALID")
        if (
            type(request) is not GuiCoordinationRequest
            or request.proposal is not proposal
            or request.profile.instrument_id != runtime.config.instrument_id
            or request.profile.to_runtime_config(runtime.config.account_id).to_dict()
            != runtime.config.to_dict()
            or type(request.lot_size) is not int
            or request.lot_size != fields["target_lot_size"]
            or type(request.portfolio_risk_candidate_quote)
            is not PortfolioRiskCandidateQuote
            or not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
            or not isinstance(request.evaluated_at, datetime)
            or request.evaluated_at.tzinfo is None
            or request.evaluated_at.utcoffset() is None
        ):
            _fail("COORDINATION_REQUEST_INVALID")
        decision_age = (
            now.astimezone(timezone.utc) - request.evaluated_at.astimezone(timezone.utc)
        ).total_seconds()
        if decision_age < -5 or decision_age > 5:
            _fail("COORDINATION_REQUEST_STALE")
        quote_age = (
            now.astimezone(timezone.utc)
            - request.portfolio_risk_candidate_quote.price_at.astimezone(timezone.utc)
        ).total_seconds()
        if quote_age < -5 or quote_age > 300:
            _fail("QUOTE_NOT_FRESH")
        self._proposal_sha256 = self._issued_proposal_sha256
        return request


def build_synthetic_lineage_evidence(
    *,
    record: Q7AControlRecord,
    hooks: Q7AControlledHooks,
    proposal: StrategyProposal,
    queued: CentralOrderIntent,
    terminal: CentralOrderIntent,
    provider_order: Mapping[str, object],
    operation_id: str,
    observation: Any,
    transaction: Any,
    opening_ledger_revision: int,
    opening_ledger_head_sha256: str,
    ledger_snapshot: Any,
    reconciliation: Any,
    portfolio: Any,
    risk_execution_ids: tuple[str, ...],
    authority: Any,
    locked_proof_sha256: str,
    identity_key: bytes,
    identity_key_id: str,
) -> bytes:
    """Bind the one offline fake fill to accepted owner read-backs.

    This is evidence only: it cannot create an order, ledger posting, Risk
    effect, Portfolio state or CL7 transition.  Raw private IDs stay out of
    the returned canonical bytes.
    """

    if (
        type(record) is not Q7AControlRecord
        or hooks.record.record_sha256 != record.record_sha256
        or type(proposal) is not StrategyProposal
        or hooks.proposal_sha256 != _sha256(_canonical(proposal.to_dict()))
        or type(queued) is not CentralOrderIntent
        or type(terminal) is not CentralOrderIntent
        or queued.status != "QUEUED"
        or terminal.status != "RECONCILED"
        or queued.intent_id != terminal.intent_id
        or queued.candidate != terminal.candidate
        or queued.authorization != terminal.authorization
        or queued.candidate.instrument_id != record.fields["target_instrument_id"]
        or queued.candidate.runtime_key != proposal.runtime_key
        or queued.candidate.strategy_profile_hash != proposal.strategy_profile_hash
        or queued.candidate.target_lots != proposal.primary_target_lots
        or queued.authorization.risk_order_allowed is not True
        or queued.authorization.portfolio_risk is None
        or queued.authorization.portfolio_risk.finalized is not True
        or provider_order.get("orderRequestId") != queued.intent_id
        or provider_order.get("orderId") != terminal.broker_order_id
        or provider_order.get("executionReportStatus") != "EXECUTION_REPORT_STATUS_FILL"
        or provider_order.get("lotsExecuted") != "1"
        or operation_id != f"synthetic-operation-for-{queued.intent_id}"
        or terminal.executed_lots != 1
        or terminal.outcome != "FILLED"
        or terminal.risk_execution_status != "RECORDED"
        or terminal.risk_execution_id != queued.intent_id
        or risk_execution_ids != (queued.intent_id,)
        or type(identity_key) is not bytes
        or type(identity_key_id) is not str
        or len(identity_key) < 32
    ):
        _fail("LINEAGE_MISMATCH")
    ledger_head = LedgerHead.from_canonical_bytes(
        ledger_snapshot.ledger_head_json_ascii
    )
    source = observation.source
    expected_source_scope = hmac.new(
        identity_key,
        _canonical(
            {
                "account_scope_sha256": record.fields["account_scope_sha256"],
                "component": "PAYMENT",
                "domain": "v3.10-cl3-source-scope",
                "identity_key_id": identity_key_id,
                "operation_id": operation_id,
                "operation_state": "OPERATION_STATE_EXECUTED",
                "provider": "TBANK",
                "version": 1,
            }
        ),
        hashlib.sha256,
    ).hexdigest()
    positions = tuple(
        position
        for position in portfolio.positions
        if position.instrument_id == queued.candidate.instrument_id
    )
    if (
        source.account_scope_sha256 != record.fields["account_scope_sha256"]
        or source.source_kind != "TBANK_OPERATION"
        or source.source_scope_sha256 != expected_source_scope
        or transaction.source != source
        or transaction.classification is not LedgerClassification.TRADE_SETTLEMENT
        or ledger_snapshot.ledger_revision != opening_ledger_revision + 1
        or ledger_head.sha256 != ledger_snapshot.ledger_head_sha256
        or ledger_head.previous_head_sha256 != opening_ledger_head_sha256
        or ledger_head.transition_kind != "TRANSACTION"
        or ledger_head.transition_sha256 != transaction.sha256
        or reconciliation.status.value != "MATCHED"
        or reconciliation.delta_minor_units != 0
        or reconciliation.proof.account_scope_sha256
        != record.fields["account_scope_sha256"]
        or reconciliation.proof.identity_key_id != identity_key_id
        or reconciliation.projection.opening_record_sha256
        != authority.opening_record_sha256
        or reconciliation.projection.ledger_head_sha256
        != ledger_snapshot.ledger_head_sha256
        or reconciliation.projection.ledger_revision != ledger_snapshot.ledger_revision
        or portfolio.account.account_id != queued.candidate.account_id
        or len(positions) != 1
        or positions[0].actual_lots != 1
        or positions[0].target is None
        or positions[0].target.target_lots != 1
        or positions[0].target.strategy_id != queued.candidate.strategy_id
        or positions[0].ownership is None
        or positions[0].ownership.config_hash != queued.candidate.strategy_profile_hash
        or positions[0].reconciliation.status.value != "MATCHED"
        or terminal.reconciled_portfolio_revision != portfolio.revision
        or authority.account_scope_sha256 != record.fields["account_scope_sha256"]
        or authority.identity_key_id != identity_key_id
        or authority.post_attempt_count != 1
        or authority.pending_dispatch_proof_sha256 is not None
        or authority.state.value != "EXACT_CASH_DISARMED"
        or terminal.cl7_locked_dispatch_proof_sha256 != locked_proof_sha256
    ):
        _fail("LINEAGE_MISMATCH")
    return _canonical(
        {
            "domain": "CL8_Q7A_SYNTHETIC_LINEAGE_V1",
            "version": 1,
            "control_record_sha256": record.record_sha256,
            "proposal_sha256": hooks.proposal_sha256,
            "account_scope_sha256": record.fields["account_scope_sha256"],
            "intent_id_sha256": _sha256(queued.intent_id.encode("utf-8")),
            "provider_order_id_sha256": _sha256(
                terminal.broker_order_id.encode("utf-8")
            ),
            "operation_source_scope_sha256": source.source_scope_sha256,
            "observation_sha256": observation.sha256,
            "transaction_sha256": transaction.sha256,
            "risk_decision_id_sha256": _sha256(
                queued.authorization.risk_decision_id.encode("utf-8")
            ),
            "portfolio_risk_decision_sha256": queued.authorization.portfolio_risk.decision_id,
            "portfolio_revision": portfolio.revision,
            "ledger_revision": ledger_snapshot.ledger_revision,
            "ledger_head_sha256": ledger_snapshot.ledger_head_sha256,
            "reconciliation_sha256": reconciliation.sha256,
            "locked_dispatch_proof_sha256": locked_proof_sha256,
            "authority_record_revision": authority.record_revision,
            "authority_record_sha256": authority.sha256,
            "post_attempt_count": authority.post_attempt_count,
            "terminal_status": terminal.status,
        }
    )


class Q7AFakeSandboxTransport:
    """Deterministic no-network provider double for the CL7 adapter tests."""

    SCENARIOS = frozenset(
        {"filled", "rejected", "timeout", "ambiguous_success", "partial", "unfilled"}
    )

    def __init__(
        self,
        *,
        scenario: str,
        account_id: str,
        target_instrument_id: str,
        before_post: Callable[[], None] | None = None,
    ) -> None:
        if scenario not in self.SCENARIOS:
            _fail("FAKE_SCENARIO_INVALID")
        if (
            type(account_id) is not str
            or not account_id
            or type(target_instrument_id) is not str
        ):
            _fail("FAKE_SCOPE_INVALID")
        self.scenario = scenario
        self.account_id = account_id
        self.target_instrument_id = target_instrument_id
        self.post_calls = 0
        self.lookup_calls = 0
        self.request_id: str | None = None
        self.order_id = "synthetic-broker-order"
        self.attempt_state: str | None = None
        self.before_post = before_post

    def get_trading_status(self, instrument_id: str) -> dict[str, object]:
        if instrument_id != self.target_instrument_id:
            _fail("FAKE_TARGET_MISMATCH")
        return {
            "tradingStatus": "SECURITY_TRADING_STATUS_NORMAL_TRADING",
            "apiTradeAvailableFlag": True,
            "limitOrderAvailableFlag": True,
            "bestpriceOrderAvailableFlag": True,
        }

    def get_operations_by_cursor_once(self, *_args: object, **_kwargs: object) -> Any:
        _fail("FAKE_OPERATION_SYNC_FIXTURE_REQUIRED")

    def get_portfolio(self, *_args: object, **_kwargs: object) -> Any:
        _fail("FAKE_PORTFOLIO_FIXTURE_REQUIRED")

    def get_withdraw_limits(self, *_args: object, **_kwargs: object) -> Any:
        _fail("FAKE_WITHDRAW_LIMITS_FIXTURE_REQUIRED")

    def post_order(self, *_args: object, **_kwargs: object) -> Any:
        _fail("LEGACY_POST_FORBIDDEN")

    def post_order_once(
        self,
        account_id: str,
        instrument_id: str,
        lots: int,
        direction: str,
        *,
        order_id: str,
        order_type: str,
        time_in_force: str,
    ) -> dict[str, object]:
        if self.post_calls:
            _fail("SECOND_POST_FORBIDDEN")
        if (
            account_id != self.account_id
            or instrument_id != self.target_instrument_id
            or type(lots) is not int
            or lots != 1
            or direction != "BUY"
            or type(order_id) is not str
            or not order_id
            or not order_type
            or not time_in_force
        ):
            _fail("FAKE_POST_SCOPE_INVALID")
        self.post_calls = 1
        self.request_id = order_id
        if self.before_post is not None:
            self.before_post()
        if self.scenario == "rejected":
            raise TBankAPIError(
                "synthetic rejection",
                status_code=400,
                transient=False,
                service="SandboxService",
                method="PostSandboxOrder",
                direct_response=True,
                redirect_followed=False,
            )
        if self.scenario in {"timeout", "ambiguous_success"}:
            raise TBankAPIError(
                "synthetic uncertain outcome",
                transient=True,
                service="SandboxService",
                method="PostSandboxOrder",
                direct_response=False,
            )
        report_status = {
            "filled": "EXECUTION_REPORT_STATUS_FILL",
            "partial": "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
            "unfilled": "EXECUTION_REPORT_STATUS_NEW",
        }[self.scenario]
        return {
            "orderId": self.order_id,
            "orderRequestId": order_id,
            "executionReportStatus": report_status,
            "lotsExecuted": "1" if self.scenario == "filled" else "0",
        }

    def get_order_state(
        self, account_id: str, order_id: str, *, by_request_id: bool = True
    ) -> dict[str, object]:
        if (
            account_id != self.account_id
            or order_id != self.request_id
            or not by_request_id
        ):
            _fail("FAKE_LOOKUP_SCOPE_INVALID")
        self.lookup_calls += 1
        return {
            "orderId": self.order_id,
            "orderRequestId": order_id,
            "executionReportStatus": (
                "EXECUTION_REPORT_STATUS_FILL"
                if self.scenario in {"filled", "ambiguous_success"}
                else "EXECUTION_REPORT_STATUS_NEW"
            ),
            "lotsExecuted": "1"
            if self.scenario in {"filled", "ambiguous_success"}
            else "0",
        }


__all__ = (
    "CONTRACT_COMMIT",
    "CONTRACT_TREE",
    "CONTROL_DOMAIN",
    "CONTROL_MODE",
    "EXPERIMENT_ID",
    "Q7AControlRecord",
    "Q7AControlledHooks",
    "Q7AFakeSandboxTransport",
    "Q7ASyntheticError",
    "build_control_record",
    "build_synthetic_lineage_evidence",
    "verify_control_record",
    "write_control_record_once",
)
