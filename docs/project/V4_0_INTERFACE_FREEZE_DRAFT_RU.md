# v4.0 Portfolio Supervisor — draft interface freeze

Дата: 2026-08-14
Статус: `DRAFT / M0 REVIEW REQUIRED / NO RUNTIME AUTHORITY`

## 1. Interface matrix

| Contract | Current v3.9 source | v4 draft | M0 decision |
|---|---|---|---|
| actual position/cash | `PortfolioState` schema 2 | unchanged authority | verify migration boundary |
| current strategy target | `PortfolioTarget` | aggregate approved target | define schema 3 |
| strategy owner | `PositionOwnership` | legacy migration input | replace with target ownership/attribution |
| runtime identity | one `InstrumentRuntime` | many `StrategyRuntimeId` per instrument | align with Issue #40 |
| direct proposal | existing `StrategyProposal.primary_target_lots` | `StrategyContributionProposal` | define adapter/retirement |
| target calculation | current primary strategy | Supervisor + allocator | pure until M3 shadow |
| hard portfolio gate | Portfolio Risk v3.9 | unchanged authority, attribution-aware | define projection hash |
| queue/reservation | `CentralOrderManager` | unchanged authority | no Supervisor reservation store |
| provider transport | `SandboxExecutionAdapter` | unchanged authority | no direct Supervisor POST |
| cash budget | canonical cash + v3.9 gate | v3.10 CashAvailability (#53) | authoritative only after #53 acceptance |
| lifecycle audit | EventJournal | references + Supervisor evidence | no second actual ledger |

## 2. Draft state machines

### Proposal

```text
OBSERVED -> VALIDATED -> ELIGIBLE -> SNAPSHOTTED
    |           |            |
    +-> INVALID +-> STALE    +-> SUPERSEDED
```

Proposal persistence never creates target, reservation, intent or POST.

### Decision epoch

```text
INPUTS_CAPTURED
-> REQUESTED_TARGET_COMPUTED
-> RISK_EVALUATED
-> APPROVED_TARGET_COMPUTED
-> REBALANCE_PLAN_COMPUTED
-> SHADOW_RECORDED          # M3
```

Before M5 every state has `execution_authorized=false`.

### Authoritative target transaction candidate

```text
PREPARED
-> REVALIDATED
-> CENTRAL_ADMITTED
-> CANONICAL_TARGET_COMMITTED
-> READY_FOR_DISPATCH
-> DISPATCHED
-> RECONCILED
-> ATTRIBUTED
-> RISK_ACCOUNTED
```

M0 must define durable recovery for failure between stores. `IN_FLIGHT` and
`UNCERTAIN` block replacement/resubmit. A target transaction ID and every
downstream idempotency key derive from epoch/approved-target/plan identity.

## 3. Draft immutable contracts

### StrategyRuntimeId

```text
instrument_id
strategy_id
strategy_version
config_hash
timeframe
runtime_hash
```

### StrategyContributionProposal

```text
schema_version
proposal_id / proposal_hash
runtime_id
desired_exposure_ppm
candle_time / evaluated_at / valid_until
reason_codes
```

### DecisionEpoch

```text
epoch_id
account_scope_hash
epoch_cutoff
canonical revision/checksum/as_of
candidate_set_hash
proposal_snapshot_hash
quote_bundle_hash/as_of
supervisor/capital policy hashes
CashAvailability revision/checksum/as_of
Central reservation revision/hash
Portfolio Risk policy hash / RiskState guard hash
```

### TargetAttribution

```text
runtime_id / proposal_id
requested_lots / allocated_lots / risk_approved_lots
requested_budget / accepted_budget
allocation rank/remainder
reason_codes
attribution_hash
```

`accepted_lots` is not overloaded across allocator and Risk stages.

### RequestedTargetPortfolio / ApprovedTargetPortfolio

Both bind epoch, canonical-before revision and ordered position targets. Only
approved target can become canonical. A rejected requested target remains audit
evidence and never appears as current target.

### RebalancePlan

```text
plan_id / plan_hash
epoch_id / approved_target_hash
canonical revision
one ordered net action per instrument
attribution-only transitions
```

## 4. Schema 2 -> 3 draft migration matrix

| Source state | Proposed action | Broker action |
|---|---|---|
| reconciled open, valid legacy owner/target/runtime | create one legacy target attribution, Supervisor target owner | none |
| reconciled flat without pending/target | keep no active attribution; no synthetic position required | none |
| target mismatch or stale canonical | block/manual review | none |
| external/unattributed actual position | preserve origin, block adoption/increase | none |
| pending or uncertain order | block until reconciliation | none |
| missing/corrupt config or timeframe | block/manual review | none |
| account/currency mismatch | block | none |

Migration requires pre-backup, exact confirmation, execution stop, post-migration
integrity/reconciliation and isolated rollback proof. Schema-2 original remains
recoverable; unknown schema is never downgraded automatically.

## 5. Draft lock order

Current frozen order:

```text
canonical -> Risk profile -> Risk state -> Central
```

Candidate v4 order:

```text
canonical
-> Supervisor config/state
-> CashAvailability read proof (or its owner lock, to be verified)
-> Risk profile
-> Risk state
-> Central
```

This is not frozen yet. M0 must inspect all nested callbacks, reconciliation,
backup and recovery paths for inversion. Pure epoch/target/plan calculation runs
before locks; after acquisition every revision/hash is reproduced.

## 6. Freshness and asynchronous timeframe

- epoch has explicit cutoff;
- one latest eligible proposal per runtime;
- no proposal newer than cutoff;
- TTL is versioned per runtime/profile;
- missing new candle is not equivalent to exit;
- `HOLD_LAST_NO_INCREASE` caps stale contribution at prior accepted lots;
- quote, canonical, cash, Risk and Central proofs have independent freshness;
- any required stale/unknown proof blocks increase.

## 7. Attribution invariants

```text
sum target attributions = aggregate target
sum realized attributions + residual = canonical actual
internal transfer changes neither canonical actual nor portfolio cash/P&L
external/unexplained actual cannot receive automatic strategy attribution
partial fills move only reconciled quantity into realized attribution
```

## 8. Open M0 blockers

1. Final name/migration of existing `StrategyProposal`.
2. Schema-3 additive versus single cutover strategy.
3. Multi-store transaction/recovery protocol.
4. Lock order including Supervisor and v3.10 stores.
5. Money/fixed-point type shared with accepted v3.10 contract #49.
6. Attribution transfer cost-basis and residual policy.
7. Partial-fill attribution ordering relative to canonical/Risk accounting.
8. Backup/restore atomic boundary for new stores.

Until these are reviewed, interface status remains `DRAFT`.
