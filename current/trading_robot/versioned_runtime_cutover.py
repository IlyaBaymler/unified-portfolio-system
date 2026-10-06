"""Explicit copy-to-new-target v4 selection, parked without trading authority.

Preparation does not write existing owners. Confirmation re-reads economics,
locks the actual owners, persists a plan, quarantines the old source, then
selects the exact new source. Recovery accepts only the planned authority
prefix and re-reads evidence unless selection was already durable. It never
arms, posts, runs Risk admission, writes cash or repairs corrupt authority.
The selected source is available only through locked_selected_source; default
v1 composition cannot arm this new state. Full v4 dispatch/recovery is separate.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cash_ledger_persistence as cl2
from . import cash_observation_versions as versions
from . import versioned_fee_corrections as journal
from . import versioned_operational_store as v4
from .exact_cash_settlement import _pairs, _safe_path
from .exact_late_fee import _owner_locks
from .exact_own_funds import MAX_AGE_NS, timestamp_ns
from .exact_settlement_closure import _config, _owners
from .journal import JournalEvent
from .runtime_cash_authority import (
    RuntimeCashAuthorityRecord, RuntimeCashAuthorityState, _transition_pair,
)
from .state_persistence import atomic_write_json
from .versioned_fee_evidence import _canonical, _parse, _sealed, _sha
from .versioned_financial_capture import _capture
from .versioned_financial_readers import VersionedReadPins
from .versioned_source_binding import (
    BindingCheckpoint, DurableSourceBinding, _source_identity,
)

DOMAIN = "V4_EXPLICIT_SOURCE_SELECTION_V1"
HELD = "VERSIONED_SOURCE_CUTOVER_HELD"
SELECTED = "VERSIONED_SOURCE_SELECTED_DISARMED"
PHRASE = "SELECT VERSIONED CASH DISARMED "
MAX_PLAN_BYTES = 4 * 1024 * 1024
_PREP_FIELDS = {"domain", "version", "kind", "target_root", "runtime_identity", "owners_before",
    "config_sha256", "source_export_sha256", "journal_identity", "journal_pins", "candidate_pins",
    "target_identity", "registry_checkpoint", "runtime_scope_sha256", "owner_binding_sha256",
    "prepared_at", "initial_context_sha256", "cash_nano", "covered_until"}
_COMMIT_FIELDS = {"domain", "version", "kind", "prepared_sha256", "before", "held", "after"}


class VersionedCutoverError(RuntimeError):
    """Finite error; a durable cutover HOLD is never implicitly cleared."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise VersionedCutoverError("VERSIONED_CUTOVER_" + code)


def _point(fault: Callable[[str], None] | None, point: str) -> None:
    if fault is not None:
        fault(point)


def _physical(root: Path, connection: Any, custody: cl2._DatabaseCustody) -> dict[str, str]:
    cl2._validate_open_root(root, connection, custody)
    identity = custody.identity
    return {"root": str(root.resolve(strict=True)), "device": str(identity.device),
            "inode": str(identity.inode)}


def _identity(a: Any, r: Any) -> dict[str, Any]:
    # _config checks the actual typed graph, account, policies and ACTIVE set.
    _config(a, r)
    return {"account_scope_sha256": a.cash_authority_manager.store._load_unlocked(
                allow_missing_legacy=False).account_scope_sha256,
        "key_id": a.cl7_identity_key_id,
        "source": _physical(
            a.cl7_ledger_store.root,
            a.cl7_ledger_store._connection,
            a.cl7_ledger_store._custody,
        ),
        "authority_path": str(a.cash_authority_manager.store.path.resolve(strict=True)),
        "portfolio_lock": str(r.manager.repository.lock_path.resolve()),
        "risk_lock": str(r.risk.state_store.lock_path.resolve()),
        "central_lock": str(a.manager.store.lock_path.resolve()),
        "profiles_lock": str(r.profiles.lock_path.resolve()),
        "runtimes_lock": str(r.runtimes.lock_path.resolve())}


def _common(a: Any) -> dict[str, Any]:
    return dict(codec_registry=tuple(a.cl7_ledger_store._registry.values()),
        identity_key=a.cl7_identity_key, identity_key_id=a.cl7_identity_key_id,
        account_scope_sha256=a.cash_authority_manager.store._load_unlocked(
            allow_missing_legacy=False).account_scope_sha256)


def _path(root: object) -> Path:
    p = cl2._path_from(root)
    cl2._validate_existing_components(p)
    return p


def _load(path: Path, key: bytes, fields: set[str], *, missing: bool = False) -> tuple[dict, str] | None:
    _safe_path(path)
    if missing and not path.exists():
        return None
    _require(path.is_file() and 0 < path.stat().st_size <= MAX_PLAN_BYTES, "PLAN_MISSING_OR_INVALID")
    raw = path.read_bytes()
    _require(len(raw) <= MAX_PLAN_BYTES, "PLAN_TOO_LARGE")
    doc = _parse(_canonical(json.loads(raw, object_pairs_hook=_pairs)))
    _require(type(doc) is dict and set(doc) == {"payload", "hmac_sha256"}, "PLAN_ENVELOPE_INVALID")
    body = doc["payload"]
    _require(type(body) is dict and set(body) == fields and body["domain"] == DOMAIN
             and type(body["version"]) is int and body["version"] == 1, "PLAN_SCHEMA_INVALID")
    signature = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
    _require(type(doc["hmac_sha256"]) is str and hmac.compare_digest(signature, doc["hmac_sha256"]), "PLAN_AUTHENTICATION_FAILED")
    return body, _sha(_canonical(doc))


def _write(path: Path, body: dict, key: bytes, fields: set[str]) -> str:
    _safe_path(path)
    _require(not path.exists(), "PLAN_EXISTS")
    raw = _sealed(body, key)
    _require(len(raw) <= MAX_PLAN_BYTES, "PLAN_TOO_LARGE")
    atomic_write_json(path, _parse(raw), retry_delays=(), jitter_fraction=0,
                      write_checksum=False, keep_last_good=False)
    actual, digest = _load(path, key, fields)
    _require(actual == body and digest == _sha(raw), "PLAN_READBACK_INVALID")
    return digest


@dataclass(frozen=True, slots=True)
class PreparedVersionedCutover:
    target_root: Path
    plan_sha256: str
    cash_nano: int  # Private attribute; not part of the public summary.

    @property
    def confirmation(self) -> str:
        return PHRASE + self.plan_sha256

    def public_summary(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_PREPARED", "plan_sha256": self.plan_sha256,
                "status": "PREPARED_NOT_SELECTED", "runtime_authority_granted": False}


@dataclass(frozen=True, slots=True)
class SelectedVersionedSource:
    plan_sha256: str
    authority_sha256: str
    pins: v4.OperationalPins
    checkpoint: BindingCheckpoint
    replay: bool

    def public_summary(self) -> dict[str, Any]:
        return {"domain": DOMAIN + "_RESULT", "plan_sha256": self.plan_sha256,
            "authority_sha256": self.authority_sha256, "source_export_sha256": self.pins.export_sha256,
            "status": "VERSIONED_SOURCE_SELECTED_DISARMED", "replay": self.replay,
            "source_selection_performed": True, "trading_cutover_complete": False,
            "runtime_authority_granted": False, "automatic_rearm_allowed": False}


def _fresh(a: Any, r: Any, store: Any, pins: VersionedReadPins, *, origin=None, held=None):
    result = _capture(a, r, store, pins, _cutover_origin=origin, _cutover_held=held, _authority_lock_held=True)
    body = _parse(result.context.payload_bytes)
    _require(body["status"] == "CONSISTENT_REVIEW_ONLY", "FRESH_CONTEXT_BLOCKED")
    return result, body


def prepare_versioned_cutover(a: Any, *, recovery: Any, candidate: v4.VersionedOperationalStore,
        candidate_pins: v4.OperationalPins, journal_store: journal.VersionedFeeCorrectionStore,
        journal_pins: VersionedReadPins, target_root: object,
        fault_injector: Callable[[str], None] | None = None) -> PreparedVersionedCutover:
    """Copy to a NEW target and bind actual unchanged owners; never arm/select.

    Only a zero-batch v4 seed of the first validated same-ID correction is
    supported. A failed preparation may leave an unused target if its signed
    prepared plan was already written. No existing target is overwritten.
    """
    r, root = recovery, _path(target_root)
    _require(type(candidate) is v4.VersionedOperationalStore
             and type(candidate_pins) is v4.OperationalPins
             and type(journal_store) is journal.VersionedFeeCorrectionStore
             and type(journal_pins) is VersionedReadPins, "INPUT_TYPE_INVALID")
    with a.cash_authority_manager.store.locked():
        for old in (a.cl7_ledger_store.root, candidate.root, journal_store.root):
            cl2._reject_overlapping_roots(old, root)
        cl2._validate_target_parent(root)
        _require(not root.exists(), "TARGET_EXISTS")
        owners, config, identity = _owners(a, r), _config(a, r), _identity(a, r)
        before = RuntimeCashAuthorityRecord.from_canonical_dict(owners["authority"])
        _require(before.state is RuntimeCashAuthorityState.EXACT_CASH_DISARMED, "SOURCE_NOT_DISARMED")
        common = _common(a)
        raw = candidate.export_bytes()
        checked = v4._validate(raw, candidate._registry, candidate._key, candidate._key_id,
                               candidate._account, candidate_pins)
        _require(checked.snapshot().batch_count == 0, "NONEMPTY_CANDIDATE_UNSUPPORTED")
        _require(candidate._key == a.cl7_identity_key and candidate._key_id == a.cl7_identity_key_id
                 and candidate._account == before.account_scope_sha256
                 and _parse(raw)["frozen_correction_export_ascii"].encode("ascii") == journal_store.export_bytes(),
                 "CANDIDATE_SEED_INVALID")
        fresh, body = _fresh(a, r, journal_store, journal_pins)
        _require(int(body["expected_cash_nano"]) == checked.snapshot().cash_nano
                 and candidate_pins.ledger_revision == before.ledger_revision + 1,
                 "CANDIDATE_CASH_INVALID")
        source_sha = _sha(a.cl7_ledger_store.export_bytes())
        runtime_hash = _sha(_canonical(identity))
        owner_hash = _sha(_canonical({"owners": owners, "config": config}))
        target, durable, owned = None, False, False
        try:
            root.mkdir(mode=0o700)
            owned = True
            v4.restore_operational_export(raw, root / "ledger", pins=candidate_pins, **common)
            target = v4.VersionedOperationalStore.open(root / "ledger", **common)
            _point(fault_injector, "prepare.after_copy")
            binding = DurableSourceBinding.create(root / "pins", source=target,
                expected_pins=candidate_pins, runtime_scope_sha256=runtime_hash,
                owner_binding_sha256=owner_hash, created_at=body["evaluated_at"])
            state = binding.snapshot()
            _point(fault_injector, "prepare.after_registry")
            with _owner_locks(a, r, ledger=True), binding.locked_binding() as (_, view):
                _require(_owners(a, r) == owners and _config(a, r) == config
                         and _identity(a, r) == identity and _sha(a.cl7_ledger_store.export_bytes()) == source_sha
                         and candidate.export_bytes() == raw and view.export_bytes() == raw,
                         "SOURCE_CHANGED_DURING_PREPARE")
                plan = {"domain": DOMAIN, "version": 1, "kind": "PREPARED",
                    "target_root": str(root.resolve()), "runtime_identity": identity,
                    "owners_before": owners, "config_sha256": config,
                    "source_export_sha256": source_sha, "journal_identity": _physical(
                        journal_store.root, journal_store._connection, journal_store._custody
                    ),
                    "journal_pins": {name: getattr(journal_pins, name) for name in journal_pins.__dataclass_fields__},
                    "candidate_pins": candidate_pins.to_dict(), "target_identity": _source_identity(target),
                    "registry_checkpoint": {"root_sha256": state.checkpoint.root_sha256,
                        "sequence": state.checkpoint.sequence, "event_head_sha256": state.checkpoint.event_head_sha256},
                    "runtime_scope_sha256": runtime_hash, "owner_binding_sha256": owner_hash,
                    "prepared_at": body["evaluated_at"], "initial_context_sha256": _sha(fresh.context.payload_bytes),
                    "cash_nano": str(checked.snapshot().cash_nano), "covered_until": checked.snapshot().covered_until}
                view.assert_active()
                digest = _write(root / "prepared.json", plan, a.cl7_identity_key, _PREP_FIELDS)
                durable = True
                _point(fault_injector, "prepare.after_plan")
            return PreparedVersionedCutover(root, digest, checked.snapshot().cash_nano)
        finally:
            if target is not None:
                target.close()
            if owned and not durable and root.exists():
                # Only this call's newly created directory; never a user target.
                shutil.rmtree(root)


def _load_prepared(a: Any, r: Any, root: Path, digest: str, *, _check_initial_owners: bool = True) -> dict:
    versions._hash(digest)
    p, actual = _load(root / "prepared.json", a.cl7_identity_key, _PREP_FIELDS)
    _require(actual == digest and p["kind"] == "PREPARED" and p["target_root"] == str(root.resolve()),
             "PLAN_IDENTITY_INVALID")
    _require(p["runtime_identity"] == _identity(a, r)
             and p["runtime_scope_sha256"] == _sha(_canonical(p["runtime_identity"]))
             and p["owner_binding_sha256"] == _sha(_canonical({"owners": p["owners_before"],
                                                                  "config": p["config_sha256"]})),
             "RUNTIME_IDENTITY_INVALID")
    _require(_config(a, r) == p["config_sha256"]
             and _sha(a.cl7_ledger_store.export_bytes()) == p["source_export_sha256"], "OLD_SOURCE_CHANGED")
    current = _owners(a, r)
    if _check_initial_owners:
        _require(all(current[k] == p["owners_before"][k] for k in ("portfolio", "risk", "central")),
                 "OWNERS_CHANGED")
    versions._time(p["prepared_at"])
    return p


@contextmanager
def _target(a: Any, p: dict) -> Iterator[tuple[Any, Any]]:
    root = Path(p["target_root"])
    source = v4.VersionedOperationalStore.open(root / "ledger", **_common(a))
    try:
        _require(_source_identity(source) == p["target_identity"], "TARGET_IDENTITY_CHANGED")
        cp = BindingCheckpoint(**p["registry_checkpoint"])
        binding = DurableSourceBinding.open(root / "pins", source=source, checkpoint=cp,
            runtime_scope_sha256=p["runtime_scope_sha256"], owner_binding_sha256=p["owner_binding_sha256"])
        state = binding.snapshot()
        _require(state.checkpoint == cp and state.pins == v4.OperationalPins(**p["candidate_pins"])
                 and state.pending_plan_sha256 is None, "TARGET_REGISTRY_CHANGED")
        yield source, binding
    finally:
        source.close()


def _transitions(a: Any, p: dict, digest: str, at: str) -> dict:
    before = RuntimeCashAuthorityRecord.from_canonical_dict(p["owners_before"]["authority"])
    held = a.cash_authority_manager._change(before, at=at, kind=HELD,
        state=RuntimeCashAuthorityState.EXACT_CASH_SOURCE_CUTOVER_PENDING,
        pending_dispatch_proof_sha256=digest)
    pins = v4.OperationalPins(**p["candidate_pins"])
    after = a.cash_authority_manager._change(held, at=at, kind=SELECTED,
        state=RuntimeCashAuthorityState.EXACT_CASH_VERSIONED_DISARMED,
        pending_dispatch_proof_sha256=None, activation_context_sha256=digest,
        ledger_head_sha256=pins.ledger_head_sha256, ledger_revision=pins.ledger_revision,
        operations_complete_through=p["covered_until"])
    _transition_pair(before, held); _transition_pair(held, after)
    return {"domain": DOMAIN, "version": 1, "kind": "COMMIT_PLAN", "prepared_sha256": digest,
            "before": before.to_canonical_dict(), "held": held.to_canonical_dict(), "after": after.to_canonical_dict()}


def _commit_plan(a: Any, p: dict, digest: str) -> dict | None:
    loaded = _load(Path(p["target_root"]) / "commit.json", a.cl7_identity_key, _COMMIT_FIELDS, missing=True)
    if loaded is None:
        return None
    body, _ = loaded
    _require(body == _transitions(a, p, digest, body["held"]["transition_at"]), "COMMIT_PLAN_INVALID")
    return body


def confirm_versioned_cutover(a: Any, *, recovery: Any, target_root: object,
        expected_plan_sha256: str, confirmation: str, journal_store: journal.VersionedFeeCorrectionStore,
        fault_injector: Callable[[str], None] | None = None) -> SelectedVersionedSource:
    """Select only the parked v4 source; resume exact before/HOLD/selected prefixes.

    Fresh economics is required on every unfinished retry. A fully completed
    replay is local identity verification only, NOT a fresh cash attestation.
    No implicit abort, fallback or partial authority-file repair is performed.
    """
    _require(type(confirmation) is str and confirmation == PHRASE + expected_plan_sha256,
             "EXPLICIT_CONFIRMATION_REQUIRED")
    _require(type(journal_store) is journal.VersionedFeeCorrectionStore, "JOURNAL_TYPE_INVALID")
    r, root = recovery, _path(target_root)
    with a.cash_authority_manager.store.locked():
        p = _load_prepared(a, r, root, expected_plan_sha256)
        _require(
            _physical(journal_store.root, journal_store._connection, journal_store._custody)
            == p["journal_identity"],
            "JOURNAL_CHANGED",
        )
        commit = _commit_plan(a, p, expected_plan_sha256)
        current = a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False)
        before = RuntimeCashAuthorityRecord.from_canonical_dict(p["owners_before"]["authority"])
        finished = commit is not None and current.to_canonical_dict() == commit["after"]
        allowed = [p["owners_before"]["authority"]] if commit is None else [commit[k] for k in ("before", "held", "after")]
        _require(current.to_canonical_dict() in allowed, "AUTHORITY_PREFIX_INVALID")
        if not finished:
            # ONE budget spans external capture and the final local commit.
            # Do not restart monotonic age when the capture function returns.
            wall_start, tick_start = timestamp_ns(a.cl7_clock()), a.cl7_monotonic_ns()
            _require(type(tick_start) is int and tick_start >= 0, "CLOCK_INVALID")
            origin = before if current.state is RuntimeCashAuthorityState.EXACT_CASH_SOURCE_CUTOVER_PENDING else None
            _, body = _fresh(a, r, journal_store, VersionedReadPins(**p["journal_pins"]),
                             origin=origin, held=current if origin else None)
            _require(body["expected_cash_nano"] == p["cash_nano"], "TARGET_ECONOMICS_CHANGED")
            evaluated_ns = timestamp_ns(body["evaluated_at"])
            _require(0 <= evaluated_ns-wall_start <= MAX_AGE_NS, "CLOCK_INVALID")
        else:
            wall_start = tick_start = evaluated_ns = 0

        def guard() -> None:
            _require(_load_prepared(a, r, root, expected_plan_sha256) == p, "PREPARED_CHANGED")
            if not finished:
                wall, tick = timestamp_ns(a.cl7_clock()), a.cl7_monotonic_ns()
                _require(type(tick) is int and 0 <= tick-tick_start <= MAX_AGE_NS
                         and wall_start <= wall <= wall_start+MAX_AGE_NS, "SELECTION_STALE")

        with _owner_locks(a, r, ledger=True), _target(a, p) as (_, binding), binding.locked_binding() as (_, view):
            guard(); view.assert_active()
            _require(view.pins == v4.OperationalPins(**p["candidate_pins"]), "TARGET_PINS_CHANGED")
            if finished:
                return SelectedVersionedSource(expected_plan_sha256, current.sha256, view.pins,
                                               BindingCheckpoint(**p["registry_checkpoint"]), True)
            _require(a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False) == current,
                     "AUTHORITY_CHANGED")
            if commit is None:
                commit = _transitions(a, p, expected_plan_sha256, a.cl7_clock())
                _write(root / "commit.json", commit, a.cl7_identity_key, _COMMIT_FIELDS)
            _point(fault_injector, "confirm.after_plan")
            held = RuntimeCashAuthorityRecord.from_canonical_dict(commit["held"])
            after = RuntimeCashAuthorityRecord.from_canonical_dict(commit["after"])
            guard(); view.assert_active()
            if current == before:
                current = a.cash_authority_manager.store._commit_unlocked(held,
                    expected_revision=current.record_revision, expected_sha256=current.sha256)
            _point(fault_injector, "confirm.after_hold")
            guard(); view.assert_active()
            r.manager.journal.record(JournalEvent(category="versioned_cutover",
                event_type="VERSIONED_SOURCE_SELECTION_VERIFIED", mode="SANDBOX_EXECUTION",
                status="DISARMED_ONLY", timestamp_utc=a.cl7_clock(), payload={
                    "prepared_sha256": expected_plan_sha256, "target_export_sha256": view.pins.export_sha256,
                    "before_authority_sha256": before.sha256, "after_authority_sha256": after.sha256,
                    "trading_authority_granted": False}))
            _point(fault_injector, "confirm.after_audit")
            guard(); view.assert_active()
            a.cash_authority_manager.store._commit_unlocked(after,
                expected_revision=current.record_revision, expected_sha256=current.sha256)
            _point(fault_injector, "confirm.after_selection")
            return SelectedVersionedSource(expected_plan_sha256, after.sha256, view.pins,
                                           BindingCheckpoint(**p["registry_checkpoint"]), False)


@contextmanager
def locked_selected_source(a: Any, *, recovery: Any, target_root: object,
        expected_plan_sha256: str) -> Iterator[tuple[Any, Any]]:
    """Resolve persisted source selection under actual owner+registry+DB locks.

    Only the initial selected pins are supported. Future v4 sync must couple
    registry advancement to selected authority in a separate protocol. This
    lease is not permission for PostOrder; the STEP27 binding stays review-only.
    """
    root, r = _path(target_root), recovery
    with a.cash_authority_manager.store.locked(), _owner_locks(a, r, ledger=True):
        p = _load_prepared(a, r, root, expected_plan_sha256)
        commit = _commit_plan(a, p, expected_plan_sha256)
        _require(commit is not None and a.cash_authority_manager.store._load_unlocked(
            allow_missing_legacy=False).to_canonical_dict() == commit["after"], "NOT_SELECTED")
        with _target(a, p) as (_, binding), binding.locked_binding() as (adapter, view):
            yield adapter, view
            view.assert_active()
            _require(_load_prepared(a, r, root, expected_plan_sha256) == p
                     and a.cash_authority_manager.store._load_unlocked(allow_missing_legacy=False).to_canonical_dict()
                     == commit["after"], "SOURCE_CHANGED")
