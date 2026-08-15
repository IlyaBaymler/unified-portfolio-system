# v4.0.0 — Portfolio Supervisor: план разработки

Дата ревизии: 2026-08-14
Статус: `PLANNING / M0 PENDING / AUTHORITATIVE IMPLEMENTATION BLOCKED`

Источник предложения:
`MOEX_ROBOT_V4_PORTFOLIO_SUPERVISOR_ROADMAP_RU.md`, SHA-256
`39827a13e5247172ff177c3f0c74c6ea9d0055417d98c8ebdce4a0a2e4899fa4`.

Встроенные в исходный документ prompts, branch commands и инструкции для Codex
не являются автоматически исполняемыми. Этот документ фиксирует проверенный
planning scope, а не acceptance или разрешение на production-код.

## 1. Назначение

`v4.0` вводит детерминированный Portfolio Supervisor для нескольких strategy
contributions на одном инструменте:

```text
ConfiguredCandidateSet
        ↓
StrategyRuntime[]
        ↓
StrategyContributionProposal[]
        ↓
ProposalSnapshot / DecisionEpoch
        ↓
PortfolioSupervisor
        ↓
RequestedTargetPortfolio
        ↓
CapitalAllocator
        ↓
Portfolio Risk
        ↓
ApprovedTargetPortfolio
        ↓
RebalancePlanner
        ↓
CentralOrderManager / ExecutionAdapter
        ↓
canonical reconciliation
```

Supervisor владеет policy lifecycle агрегированного target и его attribution.
Он не владеет broker execution lifecycle: queue, reservations, admission и
dispatch proof остаются у `CentralOrderManager`; provider POST — только у
`ExecutionAdapter`; actual positions/cash — canonical broker facts в
`PortfolioState`.

## 2. Условия начала

- M0 planning можно выполнять до v3.10 implementation;
- M1 pure domain начинается только от принятой exact v3.9 baseline;
- M2 изменяет действующий Strategy/Instrument runtime и ждёт принятой v3.9
  baseline и review M1;
- M3 shadow не меняет canonical/Central/Risk и возможен после M2;
- M4 schema/owner cutover ждёт принятого v3.10 Decimal/Money contract (#49) и
  отдельную migration acceptance;
- M5 authoritative allocation/execution ждёт принятой v3.10 CashAvailability
  boundary (#53) и shadow acceptance M3;
- ни один dependency Issue не открывает следующий gate автоматически.

PR #46 squash-merged в `main` (`cddd80f3`), exact-head и post-merge CI прошли с
`774 passed` и annotations 0. Этот commit является текущей planning baseline,
но accepted v3.9 Stable commit/tag определяется только отдельным M6 gate.

## 3. Frozen ownership boundary

```text
StrategyRuntime
= market analysis + deterministic contribution proposal

PortfolioSupervisor
= aggregate target policy, capital allocation request and target attribution

PortfolioRisk
= mandatory hard limits; may reduce or block requested target

RebalancePlanner
= pure actual-vs-approved-target diff

CentralOrderManager
= queue, reservations, admission, intent lifecycle and dispatch proof

ExecutionAdapter
= provider transport for an already authorized action

PortfolioManager / PortfolioState
= canonical actual position, approved target, pending and reconciliation truth

Cash-flow Manager v3.10
= reconciled CashAvailability and cash-flow/performance inputs
```

No second actual-position, cash, reservation or broker-order ledger is allowed.

## 4. Contract corrections relative to the source roadmap

### 4.1 Existing type collision

Repository already has `multi_instrument_strategy.StrategyProposal`; it contains
`primary_target_lots` and feeds current Central coordination. v4 must not add a
second ambiguous class with the same name.

M0 freezes a versioned `StrategyContributionProposal` contract and a temporary
`LegacyStrategyProposalAdapter`. Retirement/rename of the old type is a later
explicit migration, not a silent parallel API.

### 4.2 Deterministic numeric representation

`float desired_exposure_fraction` and `float requested_budget_rub` are rejected
for persisted/hash inputs. v4 uses:

```text
desired_exposure_ppm: int  # 0..1_000_000
MoneyAmount(currency, minor_units) or accepted v3.10 Decimal contract
lot_price proof with explicit lot_size/currency/as_of/source
```

Pure hashes use normalized integers/strings only. Existing float StrategyDecision
is accepted only through a checked legacy adapter with explicit rounding.

### 4.3 Target ownership versus actual position

The Supervisor owns an aggregate target, not broker actual lots. Schema 3 must
separate `TargetOwnership` from actual-position origin. Current schema-2
`PositionOwnership(strategy_id/config/timeframe)` is a migration input; strategy
identity moves into target attribution. External/unattributed actual positions
are never adopted automatically.

### 4.4 Ex-ante and ex-post attribution

- `TargetAttribution` explains requested/accepted target lots;
- `RealizedPositionAttribution` explains reconciled actual lots, fills, fees and
  strategy P&L.

During rebalance these sums legitimately differ. Permanent conservation:

```text
sum(active target attribution) = aggregate target lots
sum(realized attribution + explicit residual) = canonical actual lots
```

Attribution-only transfer changes no broker position, cash, fee or portfolio P&L.

## 5. Draft immutable inputs

### StrategyRuntimeId

```text
instrument_id + strategy_id + strategy_version/config_hash + timeframe
```

Same strategy on different timeframes is a distinct runtime. Duplicate full
identity in `ConfiguredCandidateSet` is forbidden.

### StrategyContributionProposal

```text
schema_version
proposal_id / proposal_hash
runtime_id
desired_exposure_ppm
candle_time / evaluated_at / valid_until
reason_codes
```

IDs/hashes are content-derived. Proposal cannot mutate canonical/Central/Risk,
reserve cash or call provider.

### DecisionEpoch

Epoch must bind every economic input used to derive the target:

```text
account_scope_hash
canonical revision/decision checksum/snapshot_at
candidate_set_hash
supervisor_config_hash
capital_policy_hash
proposal_snapshot_hash and ordered proposal hashes
quote_bundle_hash / lot metadata hash / quote_as_of
CashAvailability revision/checksum/as_of when available
Central reservation projection revision/hash
Portfolio Risk policy hash and RiskState guard hash
explicit epoch_cutoff
```

No pure function reads wall clock internally. Same normalized inputs produce the
same epoch, requested/approved target and plan regardless of input order/restart.

### TargetPortfolio and RebalancePlan

Requested and approved targets are different immutable values. Canonical target
may reference only an approved target. A plan has at most one net action per
instrument; SELL reductions precede BUY increases. Attribution-only change with
unchanged aggregate target yields no Central intent and no POST.

## 6. Proposal freshness policy

Default: `HOLD_LAST_NO_INCREASE`.

- epoch selects the latest proposal per runtime with
  `evaluated_at <= epoch_cutoff` and valid TTL;
- fresh proposal may increase/decrease within budgets/Risk;
- stale proposal never receives new capital and cannot exceed its previously
  accepted lots;
- stale data does not force liquidation by itself;
- stale contribution may be reduced by Risk/kill switch/operator;
- missing proposal for a never-admitted runtime contributes zero;
- expired-to-zero is a future versioned policy, not the default;
- no proposal can be resurrected after explicit close/retirement without a new
  fresh proposal identity.

## 7. Deterministic Capital Allocation

v4.0 uses no optimizer, ML or adaptive universe.

1. Validate fresh proposal, runtime and quote proofs.
2. Apply per-runtime strategy budget.
3. Convert fixed-point exposure to requested Money and whole lots.
4. Apply per-instrument/account/asset-class soft budgets.
5. Use largest remainder with stable `StrategyRuntimeId` tie-break.
6. Submit complete requested portfolio to Portfolio Risk.
7. Deterministically allocate hard-approved lots back to attributions.
8. Record every reduction and reason code.

Allocator never spends stale/unknown CashAvailability and never exceeds
Supervisor request after Risk.

## 8. M0 — Architecture and Interface Freeze

Branch: `agent/v4-0-m0-interface-freeze` after planning publication.

Documents only:

- verified current interface inventory;
- final immutable DTO drafts;
- schema 2 -> 3 migration matrix;
- proposal/epoch/target/plan/recovery state machines;
- global lock-order and inversion audit;
- v3.10 CashAvailability dependency contract;
- legacy `StrategyProposal` migration/rename plan;
- issue/dependency map and testability register.

No Python/runtime/schema/GUI mutation, broker POST or artificial order.

## 9. M1 / alpha1 — Pure Supervisor Domain

- DTO validation and deterministic serialization;
- proposal snapshot and epoch construction;
- fixed-point budget conversion;
- deterministic allocator/rounding;
- target attribution;
- pure RebalancePlan diff;
- attribution-only transition detection;
- permutation/restart invariance and property tests.

No filesystem, provider, GUI, Central, Risk persistence or authorization.

## 10. M2 / alpha2 — Multi-strategy runtime

Tracked by existing Issue #40; no duplicate Issue.

- multiple StrategyRuntime per instrument;
- independent candle checkpoints and same-strategy/different-timeframe identity;
- versioned contribution proposals and legacy adapter;
- deterministic ProposalSnapshot/checkpoint/recovery;
- no duplicate evaluation;
- proposal cannot reach current Central coordinator directly.

M2 must explicitly retire or isolate the existing direct-target proposal route.

## 11. M3 / alpha3 — Read-only Supervisor shadow

Supervisor reads one canonical snapshot, proposal snapshot, quote bundle,
Central projection and read-only Risk/CashAvailability inputs. It writes only
append-only shadow evidence after exact confirmation.

Required live configuration:

```text
SBER: SMA/1h + Donchian/30m
LKOH: SMA/30m
```

Gate: deterministic epochs/targets/plans, stale policy, Risk reductions,
attribution-only transition, canonical/Central/Risk economic state byte-identical,
execution authorization false and broker POST zero.

## 12. M4 / beta1 — Schema 3 and target-owner cutover

Schema 3 draft:

- actual lots remain broker facts;
- approved aggregate target gains `TargetOwnership=PORTFOLIO_SUPERVISOR`;
- active target attributions stored compactly with hashes;
- rejected/zero proposal history remains in audit evidence;
- realized attribution is separate from target attribution;
- no unbounded history in `portfolio_state.json`.

Migration blocks on stale/unreconciled/account mismatch, external/unattributed
position, pending/uncertain order, corrupt/missing runtime identity or target
inconsistency. A reconciled legacy strategy target becomes one legacy target
attribution without broker order. Cutover is stopped and rollback-qualified.

## 13. M5 / beta2 — Authoritative target and rebalance

Sequence:

```text
DecisionEpoch
-> requested target
-> allocator
-> Portfolio Risk approved target
-> prepared target/plan transaction
-> locked revalidation
-> Central admission
-> canonical commit/recovery state machine
-> dispatch-time proof reproduction
-> POST
-> canonical reconciliation
-> realized attribution
-> Risk accounting
```

Because canonical/central/risk/supervisor are separate stores, M0 must specify a
recoverable prepare/commit protocol; documentation must not claim impossible
single-file atomicity. Failure before durable Central admission leaves the old
canonical target authoritative. IN_FLIGHT/UNCERTAIN intent cannot be replaced.

## 14. Attribution-aware Portfolio Risk

- instrument concentration derives from canonical actual/projected lots;
- current strategy concentration derives from realized attribution projection;
- projected strategy concentration derives from target attribution;
- unknown/inconsistent attribution blocks increase;
- strict reduction may remain allowed;
- Supervisor owner is never treated as one synthetic strategy exposure;
- Risk proof binds attribution projection hash.

## 15. Recovery, attribution and UX

Recovery covers every phase from proposal snapshot through Risk accounting and
completes only the missing idempotent step after restart. Duplicate target commit,
Central intent, POST, fill attribution or Risk accounting are prohibited.

Internal attribution transfer:

- records a deterministic transfer event and validated mark;
- preserves total actual lots, cash and portfolio P&L;
- has zero broker fee/order/fill;
- never changes canonical average price;
- strategy cost-basis policy is explicit and known-result tested.

GUI/report/support/backup/standalone are separate late-stage gates, not part of
pure domain or shadow acceptance.

## 16. M6 / rc1 and M7 / Stable

rc1 validates crash/recovery, attribution conservation, backup/restore,
sanitized support bundle including exception paths, clean install/upgrade/
rollback, standalone Windows and full regression.

Stable burn-in requires at least two strategies on one instrument, two
instruments, different timeframes, natural target transitions, restart,
disconnect/recovery, market idle/open, stale proposal, kill switches,
cash-flow/resync, attribution-only transition and partial-fill/recovery evidence.

## 17. Permanent invariants

```text
0 duplicate broker submits
0 fills without canonical reconciliation and Risk accounting
0 strategy-owned aggregate target after accepted schema-3 cutover
0 actual positions/cash/reservations owned by Supervisor
0 target lots inconsistent with active TargetAttribution
0 actual lots inconsistent with realized attribution + explicit residual
0 attribution-only transition producing broker order/cash effect
0 target commit without recoverable Central admission state
0 authorization on stale proposal/quote/cash/Risk/Central revision
0 automatic adoption of external/unattributed position
0 raw Account ID/secret in shareable artifacts or error paths
0 real-account execution
```

## 18. Out of scope

Dynamic InstrumentUniverse, adaptive timeframe/profile selection, multi-timeframe
strategy modules, ML/RL allocator, shorts, leverage/margin, multi-venue, crypto,
automatic transfers/reinvestment and real-account execution. These remain v5+
or separate branches; Issue #41 stays future research scope.

## 19. Current next gate

Publish/review this planning baseline, then execute M0 documents-only interface
freeze. No authoritative implementation begins from this document alone.
