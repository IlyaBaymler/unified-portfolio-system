# v4.0 Portfolio Supervisor — принятый interface freeze

Дата ревизии: 2026-08-15
Статус: `M0 INTERFACE ACCEPTED 2026-08-15 / PUBLICATION TRACKED BY PR #70 / NO RUNTIME AUTHORITY`

Этот документ является результатом documents-only аудита Issue #58 и фиксирует
принятые интерфейсы до начала M1. Explicit user acceptance записан 2026-08-15
после третьего post-fix final review с результатом PASS. Acceptance не означает
изменение runtime, schema cutover, implementation evidence или разрешение на
исполнение. Публикация отслеживается отдельным PR #70 и не расширяет acceptance.

## 1. Граница M0

M0 фиксирует:

- единственные публичные v4 DTO и их hash/serialization rules;
- владельца aggregate target и границу с actual broker facts;
- retirement старого direct-target proposal route;
- schema 2 -> 3 migration contract без broker action;
- prepare/commit/recovery протокол между canonical, Supervisor и Central;
- глобальный lock order и запрет обратных callbacks;
- fail-closed зависимость от принятых v3.10 Money/CashAvailability contracts;
- проверяемость каждого решения будущими тестами и evidence.

M0 не меняет Python, persisted schemas, GUI, canonical state, Risk/Central state,
не создаёт искусственную заявку и не вызывает provider POST.
Синхронизация GitHub issue bodies относится только к planning metadata: она не
изменяет repository/runtime artifacts, не закрывает Issues и не даёт acceptance
или execution authority.

Verified current facts вынесены отдельно в
`docs/project/V4_0_CURRENT_INTERFACE_INVENTORY_RU.md`; будущие проверки — в
`docs/plans/V4_0_M0_TESTABILITY_REGISTER_RU.md`.

## 2. Frozen ownership boundary

| Domain | Единственный mutation owner | v4 boundary |
|---|---|---|
| broker actual positions/cash, pending and reconciliation fields | canonical reconciler через `PortfolioRepository` | target subtree сохраняется byte-equivalent при actual reconciliation |
| Supervisor policy/config and uncommitted decision records | `PortfolioSupervisor` / bounded Supervisor store | не меняет canonical actual или execution state |
| committed aggregate target/target-attribution fields в schema 3 | `SupervisorTargetTransactionCoordinator` через canonical CAS | единственный writer target subtree; записывает только exact prepared approved result |
| hard limits and risk accounting | Portfolio Risk | может уменьшить/заблокировать request; не увеличивает его |
| queue, reservations, admission, intent and dispatch proof | `CentralOrderManager` | Supervisor не создаёт второй order/reservation ledger |
| authoritative v4 provider transport | `ExecutionAdapter` | v4 modules не могут использовать legacy bot/diagnostic POST routes |
| reconciled cash availability | Cash-flow Manager v3.10 | #53 only shadow; authoritative v4 ждёт #49 and accepted/activated #55 |
| lifecycle/audit evidence | existing `EventJournal` | append-only evidence, не authority для economic state |

`TargetOwnership(kind=PORTFOLIO_SUPERVISOR)` описывает владельца aggregate
target. `PositionState.origin` остаётся фактом происхождения actual position.
Schema-2 `PositionOwnership(strategy_id/config_hash/timeframe)` используется
только как migration provenance и не переносится как владелец aggregate target.
`PortfolioRepository` является storage/concurrency boundary, а не вторым policy
owner. Canonical reconciler не может вычислять или изменять target; при записи
actual facts он сохраняет target subtree и использует expected revision. Только
`SupervisorTargetTransactionCoordinator` может заменить этот subtree через
reviewed save-while-locked CAS.

## 3. Canonical serialization and numeric rules

Все persisted или hashable v4 contracts обязаны удовлетворять правилам:

1. Canonical JSON — UTF-8, keys sorted lexicographically, без insignificant
   whitespace; собственное поле hash исключается из hash material. DTO не может
   включать hash контейнера, который включает этот DTO.
2. Hash — lowercase SHA-256 canonical JSON bytes.
3. Binary `float`, NaN и Infinity запрещены во всех proposal, epoch, budget,
   target, attribution, plan и transaction documents.
4. Exposure — integer parts-per-million: `0..1_000_000`.
5. Lots, lot size, revisions, sequence and minor monetary units — integers.
6. Money использует принятый v3.10 Decimal/Money contract; до его acceptance
   временный M1 `MinorMoney` содержит ровно
   `(currency: uppercase ISO string, scale: non-negative int, minor_units: int)`.
   Разные scale не сравниваются без explicit exact conversion; неявная float
   conversion запрещена.
7. Timestamps — UTC RFC 3339 с `Z`, нормализованной precision и явным `as_of`;
   wall clock никогда не читается внутри pure calculation.
8. Sets serialизуются как arrays в заранее определённом stable order. Duplicate
   identities запрещены, а не молча дедуплицируются.
9. `MISSING_PRE_V3_10`/`NOT_APPLICABLE` — versioned explicit sentinel value;
   обязательное поле нельзя просто опустить.

## 4. Frozen immutable contracts

Имена ниже являются единственными публичными именами v4. Добавлять параллельный
`StrategyProposal` или альтернативный target DTO запрещено.

### 4.1 StrategyRuntimeId

```text
schema_version
instrument_id
strategy_id
strategy_version
strategy_config_hash
timeframe
runtime_id                 # content hash of the fields above
```

`StrategyRuntimeId` не содержит raw Account ID. Одна и та же strategy/config на
разных timeframes — разные runtimes. Account scope задаётся отдельно через
`account_scope_hash`; persisted runtime instance key равен
`hash(account_scope_hash, runtime_id)`. Полный duplicate в configured set —
validation error.

### 4.2 StrategyContributionProposal

```text
schema_version
proposal_id / proposal_hash
account_scope_hash
runtime_id
instrument_id
desired_exposure_ppm
candle_time
evaluated_at
valid_until
source_decision_hash
reason_codes[]             # stable sorted enum values
```

Proposal — immutable intent contribution. Он не содержит `target_lots`, не
авторизует execution, не изменяет canonical/Risk/Central, не резервирует cash и
не вызывает provider. `proposal_id` content-derived и idempotent.

### 4.3 ConfiguredCandidateSet and ProposalSnapshot

```text
ConfiguredCandidateSet:
  schema_version
  account_scope_hash
  config_revision / config_hash
  ordered_runtime_ids[]
  candidate_set_hash

ProposalSnapshot:
  schema_version
  account_scope_hash
  epoch_cutoff
  candidate_set_hash
  ordered(runtime_id, proposal_id, proposal_hash, eligibility_state)[]
  proposal_snapshot_hash
```

Order — ascending `runtime_id`, then `proposal_id`. Snapshot содержит ровно один
latest eligible proposal на runtime либо explicit missing/stale state. Proposal
newer than cutoff не может попасть в epoch.

### 4.4 ShadowAcceptedTargetCheckpoint

До schema-3 canonical не содержит committed `TargetAttribution`, поэтому M3
фиксирует отдельный immutable checkpoint только как append-only shadow evidence:

```text
ShadowAcceptedTargetCheckpoint:
  schema_version
  account_scope_hash
  checkpoint_sequence  # per account_scope_hash; first = 1; next = previous + 1
  source_epoch_hash
  source_approved_target_hash
  source_proposal_snapshot_hash
  ordered(runtime_id, risk_approved_lots, attribution_hash)[]
  recorded_at
  checkpoint_hash
  acceptance_scope = SHADOW_ONLY
  execution_authorized = false
```

Checkpoint создаётся только после exact confirmation перехода
`SHADOW_RECORDED`. `checkpoint_hash` считается по canonical serialization всех
полей контракта, кроме самого `checkpoint_hash`. Запись не меняет canonical,
Central, Risk или cash, не резервирует средства и не даёт dispatch/POST
permission. Existing `EventJournal` event с полным normalized checkpoint payload
является source of record; `supervisor_state.json` может кэшировать только latest
checkpoint reference. Первый sequence каждого `account_scope_hash` равен `1`;
каждый следующий строго равен `previous + 1`. Retry уже записанного sequence
является idempotent только при byte-equivalent canonical payload и identical
`checkpoint_hash`; тот же sequence с другим hash, gap или reorder означает
equivocation/corruption и fail-closed блокирует всю checkpoint chain.

### 4.5 DecisionEpoch

```text
schema_version
epoch_id / epoch_hash
account_scope_hash
epoch_cutoff
canonical_before_revision/checksum/as_of
candidate_set_hash
proposal_snapshot_hash + ordered proposal hashes
supervisor_policy_revision/hash
capital_policy_revision/hash
quote_bundle_hash/as_of + ordered lot metadata hashes
cash_availability_revision/checksum/as_of/status
central_revision/reservation_projection_hash
risk_policy_hash
risk_state_guard_hash
stale_cap_source_kind  # NONE | SHADOW_CHECKPOINT | CANONICAL_COMMITTED
stale_cap_source_revision/checksum/as_of
```

Каждая ссылка обязательна. До принятой v3.10 boundary cash fields содержат
explicit sentinel и не дают authoritative permission. Mixed account scope,
revision, currency или cutoff делает весь epoch invalid. Same normalized inputs
обязаны давать одинаковые epoch/target/plan hashes независимо от input order и
restart. Stale-cap source kind и exact identity являются hash material epoch.

### 4.6 RequestedTargetPortfolio and ApprovedTargetPortfolio

Оба значения содержат schema, account scope, epoch reference, canonical-before
reference, ordered per-instrument targets, `TargetAttribution[]`, reason codes и
content hash.

```text
sum(requested TargetAttribution lots) = requested aggregate target lots
sum(approved TargetAttribution lots)  = approved aggregate target lots
```

Portfolio Risk получает полный requested portfolio. Для long-only v4 по каждому
instrument `approved` находится на замкнутом отрезке между current actual и
requested target; Risk может оставить, приблизить к current или заблокировать,
но не увеличить отклонение. Только approved target может войти в canonical.
Rejected requested target остаётся audit evidence.

### 4.7 TargetOwnership and attribution

```text
TargetOwnership:
  kind = PORTFOLIO_SUPERVISOR
  supervisor_policy_hash
  approved_target_hash
  decision_epoch_hash
  committed_transaction_id

TargetAttribution:
  runtime_id / proposal_id
  requested_lots
  allocated_lots
  risk_approved_lots
  requested_money / allocated_money / risk_approved_money  # MinorMoney or accepted v3.10 Money
  allocation_rank / remainder_minor_units
  reason_codes[]
  attribution_hash

RealizedPositionAttribution:
  runtime_id or EXPLICIT_RESIDUAL
  reconciled_lots
  fill/fee/cost-basis references
  realized_attribution_hash
```

Allocator and Risk fields are not overloaded into one `accepted_lots`. Target
and realized attribution are distinct. Permanent conservation:

```text
sum(active target attribution) = aggregate approved target
sum(realized attribution + explicit residual) = canonical actual lots
```

External/unexplained actual cannot receive automatic strategy attribution.
Attribution-only transfer changes no canonical actual, cash, average broker
price, fee or portfolio P&L and produces no Central intent.

Hash/ID dependency graph является ациклическим и фиксирован в таком порядке:

1. Pre-schema3 `ShadowAcceptedTargetCheckpoint` может ссылаться только на уже
   завершённый earlier source epoch/approved target; contiguous per-account
   sequence запрещает ссылку на текущий или будущий epoch и conflicting replay.
2. `DecisionEpoch` -> `epoch_hash`; optional stale-cap checkpoint является его
   immutable predecessor.
3. Requested and Approved target hashes считаются только по соответствующим
   immutable portfolio DTO; `TargetOwnership` и transaction metadata в их hash
   material не входят.
4. `RebalancePlan` -> `plan_hash` из epoch/approved-target/canonical-before.
5. `transaction_id` равен SHA-256 canonical JSON envelope с domain
   `V4_TARGET_TRANSACTION_V1`, account scope, epoch hash, approved-target hash,
   plan hash и canonical-before revision/checksum.
6. `TargetOwnership` связывает уже вычисленные epoch/approved-target/transaction
   identities и включается только в prepared canonical-after.
7. Canonical-after checksum считается последним. Он не участвует в derivation
   transaction ID.

Central action idempotency key выводится из versioned domain,
`transaction_id`, `plan_hash`, instrument ID и action hash. Никакой descendant
hash не включается обратно в hash его ancestor.

### 4.8 RebalancePlan

```text
schema_version
plan_id / plan_hash
epoch_id / approved_target_hash
canonical_before_revision/checksum
ordered net actions[]       # at most one per instrument
ordered attribution_only_transitions[]
```

Plan is pure `actual vs approved target`. SELL reductions are ordered before BUY
increases; within a side the stable order is instrument ID. Zero net difference
or attribution-only transition never reaches Central.

## 5. Legacy StrategyProposal retirement

В текущем v3.9 `multi_instrument_strategy.StrategyProposal` содержит
`primary_target_lots`, а `CentralOrderCandidate.from_strategy_proposal()`
принимает его напрямую. Freeze decision:

1. Единственный новый public type — `StrategyContributionProposal`.
2. `LegacyStrategyProposalAdapter` — временная входная граница M2, не второй
   authoritative proposal route.
3. Adapter требует explicit profile/config/account scope, проверяет
   `execution_authorized=false`, primary-decision consistency, finite range и
   provenance; float `target_weight` округляется в ppm по документированному
   half-even rule.
4. Adapter output содержит только normalized integer/string/hash fields и не
   переносит `primary_target_lots` как authoritative target.
5. Direct construction Central candidate из legacy proposal должно быть
   отключено или изолировано до M2 acceptance. После cutover Central принимает
   только admitted `RebalancePlan` action с complete proof.
6. Одновременное существование двух authoritative proposal routes запрещено.

## 6. Proposal freshness — HOLD_LAST_NO_INCREASE

- epoch выбирает latest eligible proposal с `evaluated_at <= cutoff` и valid TTL;
- fresh proposal может увеличить/уменьшить contribution в рамках budgets/Risk;
- stale runtime не получает новый capital. Exact source выбирается по phase:

| Phase | `stale_cap_source_kind` | Exact source and rule |
|---|---|---|
| M1 pure | `NONE` or `SHADOW_CHECKPOINT` | explicit normalized fixture; `NONE` даёт cap zero |
| M3 before schema 3 | `SHADOW_CHECKPOINT` | unique head of a validated contiguous per-account chain с `recorded_at <= cutoff` и bound source epoch cutoff `<= cutoff`; identical retry is one logical record, conflicting same-sequence hash/gap/reorder invalidates the chain |
| M4+ after cutover | `CANONICAL_COMMITTED` | last committed `risk_approved_lots` из target subtree `canonical_before` |

- для shadow source `revision/checksum/as_of` означают соответственно
  `checkpoint_sequence/checkpoint_hash/recorded_at`; для canonical source —
  `canonical_before_revision/checksum/as_of`;
- missing source/attribution означает cap zero; corrupt, mixed-account,
  non-contiguous/non-monotonic, conflicting same-sequence hash или
  checksum-invalid source блокирует epoch/increase;
- после schema-3 cutover canonical target attribution является единственным
  допустимым source: fallback к shadow checkpoint запрещён;
- stale alone не принуждает liquidation;
- Risk, kill switch или operator может уменьшить stale contribution;
- never-admitted missing runtime contributes zero;
- explicit retirement closes contribution; возобновление требует нового fresh
  proposal identity;
- stale/unknown quote, canonical, CashAvailability, Risk или Central proof
  блокирует любое increase;
- policy `EXPIRE_TO_ZERO` не является v4.0 default и требует новой версии.

## 6.1 Contract lifecycle state machines

Lifecycle state хранится в versioned envelope/EventJournal record, который
ссылается на immutable DTO hash. Изменение state никогда не меняет DTO content
или его hash. Unknown state/transition fails closed.

### Proposal lifecycle

```text
OBSERVED -> VALIDATED -> ACTIVE
    |                         |-- snapshot(FRESH_ELIGIBLE) --> ACTIVE
    +-> INVALID              +-> EXPIRED_STALE
                              |       |-- snapshot(STALE_HELD) --> EXPIRED_STALE
                              +-> SUPERSEDED
                              +-> RETIRED
```

Proposal может входить в несколько epochs, поэтому snapshot не переводит и не
изменяет DTO. `FRESH_ELIGIBLE` вычисляется для explicit epoch cutoff;
`EXPIRED_STALE`, `SUPERSEDED` и `RETIRED` не возвращаются в fresh eligibility,
продолжение требует нового proposal ID. Snapshot использует states
`FRESH_ELIGIBLE`, `STALE_HELD`, `MISSING_ZERO`, `INVALID`, `SUPERSEDED` или
`RETIRED` и содержит ровно один state на configured runtime.

### DecisionEpoch lifecycle

```text
INPUTS_CAPTURED
-> REQUESTED_TARGET_COMPUTED
-> RISK_PREVIEWED
-> APPROVED_TARGET_COMPUTED
-> PLAN_COMPUTED
   |-> SHADOW_RECORDED         # M3; exact confirmation may append checkpoint
   +-> TRANSACTION_PREPARED    # M5

any pre-terminal state -> INVALIDATED
complete older epoch   -> SUPERSEDED
```

`RISK_PREVIEWED` не является durable authorization. Drift любого bound input до
следующей стадии создаёт `INVALIDATED`; epoch не пересобирается под тем же ID.
До M5 все lifecycle records имеют `execution_authorized=false`.
`ShadowAcceptedTargetCheckpoint` записывается только после exact-confirmed
`SHADOW_RECORDED`; он сохраняет `acceptance_scope=SHADOW_ONLY` и не меняет это
ограничение.

### Target lifecycle

```text
REQUESTED -> RISK_APPROVED -> PREPARED -> COMMITTED
     |             |
     +-> RISK_REJECTED
                   +-> INVALIDATED
```

Requested and approved targets — разные immutable DTO/hashes. `COMMITTED`
достигается только после transaction-linked Risk authorization, Central
admission и canonical CAS. `RISK_REJECTED`/`INVALIDATED` target не может быть
переиспользован как approved target.

### RebalancePlan lifecycle

```text
COMPUTED -> NO_BROKER_ACTION                    # zero/attribution-only
COMPUTED -> PREPARED -> CENTRAL_ADMITTED
                      -> CANONICAL_COMMITTED -> DISPATCH_ELIGIBLE
any pre-dispatch state -> INVALIDATED or RECOVERY_REQUIRED
```

Plan envelope следует target transaction identity; он не владеет Central state.
`DISPATCH_ELIGIBLE` означает только возможность пройти существующие отдельные
arming/revalidation gates, а не permission или POST. `NO_BROKER_ACTION` terminal
и никогда не создаёт Central intent.

## 7. Recoverable target transaction

Canonical target commit изменяет canonical revision/checksum, поэтому proof,
связанный только с pre-commit snapshot, стал бы stale до dispatch. M0 фиксирует
dual identity: proof связывает `canonical_before` и заранее сериализованный exact
`canonical_after`.

```text
PREPARED
-> REVALIDATED
-> RISK_AUTHORIZED
-> CENTRAL_ADMITTED
-> CANONICAL_COMMITTED
-> CLOSED

terminal/non-happy states:
ABORTED_BEFORE_ADMISSION
ABORTED_AFTER_CENTRAL_CANCELLED
RECOVERY_REQUIRED
MANUAL_REVIEW
```

Durable prepare record в `supervisor_state.json` содержит transaction ID,
epoch/approved-target/plan hashes, exact canonical-before revision/checksum,
полный normalized `prepared_canonical_after` payload и его checksum, Risk
guard/expected decision identity и ожидаемый Central admission identity. Ссылка
или checksum без recoverable payload недостаточны. После locked revalidation Risk
authorization связывается с transaction и обеими canonical identities; Central
admission дополнительно связывается с тем же Risk decision. Canonical затем делает
CAS и записывает ровно сохранённый after payload. Dispatch воспроизводит after
revision/hash.

Locked Risk authorization обязан подтвердить exact prepared approved-target hash.
Если hard gate возвращает иной target, исходный transaction переходит в
`ABORTED_BEFORE_ADMISSION`; для нового approved target создаются новые target,
plan и transaction identities до какого-либо Central admission.

`RISK_AUTHORIZED` означает durable admission decision, но не fill/Risk accounting.
Если RiskState last-decision записан, а Central mutation не завершена, transaction
остаётся non-dispatchable. Recovery распознаёт тот же transaction-linked decision,
затем либо безопасно повторяет Central admission под полным lock/revalidation,
либо aborts и требует fresh epoch; он не считает target committed.

Current `PortfolioRepository.locked_snapshot()` запрещает save при удерживаемом
canonical lock. Поэтому M4/M5 implementation обязан добавить один reviewed
CAS/save-while-locked primitive; обход через unlock/reload запрещён.

### Crash matrix

| Crash point | Authoritative state | Recovery action | Dispatch |
|---|---|---|---|
| before Central admission | old canonical | idempotently abort prepared transaction | forbidden |
| after Risk authorization, before Central mutation | old canonical + transaction-linked Risk decision | reuse only after complete locked revalidation or abort for fresh epoch; no Risk accounting is inferred | forbidden |
| after Central, before canonical CAS | old canonical + non-dispatchable queued intent | commit stored exact prepared after if before still matches; otherwise durably cancel queued intent and enter `ABORTED_AFTER_CENTRAL_CANCELLED`, or manual review | forbidden |
| after canonical CAS, before transaction close | new canonical | verify transaction ID/after hash/Central intent and close missing step | only after full proof reproduction |
| after close/before provider | new canonical + admitted Central intent | normal Central prepare/arming/revalidation | explicit existing gates |
| `IN_FLIGHT`/`UNCERTAIN` | broker outcome unknown | reconcile only; never replace/resubmit | forbidden until resolved |

While a transaction is non-terminal, Supervisor store durably owns its recovery
record including exact prepared canonical-after payload; it does not own Central
intent or actual-position state. After `CLOSED` or a verified abort, the bounded
store may compact the payload only after canonical/Central terminal identities
and EventJournal evidence are durable, retaining hashes/status/idempotency refs.
Central owns intent lifecycle; `SupervisorTargetTransactionCoordinator` is the
sole writer of committed canonical target fields; canonical reconciler owns
actual/reconciliation fields; EventJournal owns append-only evidence.
Documentation does not claim impossible multi-file atomicity.

## 8. Persisted file topology

M0 freezes a minimal topology, not schema mutation:

- evolve `instrument_runtimes.json` in place from schema 1 to schema 2 for
  per-strategy runtime entries; do not add parallel `strategy_runtimes.json`;
- add bounded `supervisor_state.json` in implementation gates for policy, latest
  proposal/epoch references and non-terminal transaction recovery records with
  exact prepared canonical-after payload; terminal payload compaction follows the
  verified rule in section 7;
- evolve `portfolio_state.json` from schema 2 to schema 3 only at M4 cutover;
- reuse `central_order_state.json`, `risk_profiles.json`, `risk_state.json` and
  `trading_events.db`; do not create another cash/order/execution ledger;
- proposal/history detail lives in bounded checkpoints plus EventJournal evidence,
  never as unbounded history inside canonical state;
- pre-schema3 `ShadowAcceptedTargetCheckpoint` хранится только как existing
  EventJournal append-only evidence; Supervisor state кэширует только latest
  reference, не новый economic ledger;
- bootstrap, readiness, backup/restore, support bundle and standalone inclusion of
  new/evolved files is required in their implementation gates.

## 9. Schema 2 -> 3 migration contract

Preconditions: exact backup, execution stopped, fresh/reconciled canonical,
matching account/currency, no pending/uncertain intent, valid runtime/config
identity and isolated rollback proof.

| Schema-2 source | Schema-3 result | Broker action |
|---|---|---|
| reconciled open with valid legacy owner/target/runtime | one legacy target attribution + Supervisor target owner; actual origin preserved | none |
| reconciled flat with no pending/target | no active attribution; no synthetic position | none |
| target mismatch or stale canonical | block/manual review | none |
| external/unattributed actual | preserve actual origin and explicit residual; block adoption/increase | none |
| pending or uncertain order | block until reconciliation | none |
| missing/corrupt config, hash or timeframe | block/manual review | none |
| account/currency mismatch | block | none |

Migration is one stopped, confirmed cutover; schema-2 backup remains recoverable.
Unknown schema is never automatically downgraded. Post-migration integrity,
checksum, reconciliation and restart/rollback evidence are mandatory. Migration
must produce zero Central intent, provider POST, fill, fee and cash effect.
Successful cutover commits the initial canonical target attribution. Every later
epoch uses `CANONICAL_COMMITTED`; pre-schema3 shadow checkpoints are ignored and
cannot serve as fallback.

## 10. Frozen lock partial order

Current verified v3.9 order:

```text
canonical -> Risk profile -> Risk state -> Central
```

v4 partial order candidate frozen for implementation:

```text
canonical
-> Supervisor transaction store
-> accepted/activated #55 cash-context owner locks
-> Risk profile
-> Risk state
-> Central
```

#53 фиксирует только read-only shadow snapshot interface. Exact internal
CashAvailability/ledger lock suborder, authoritative Portfolio Risk cash-context
lease и его activation остаются external blocker до принятия #55; accepted #55
order must fit in the single slot above or M0 requires a versioned amendment.
EventJournal, support and backup locks are not nested inside economic locks. Pure
calculation runs before locks; after acquiring locks all revision/hash/freshness
inputs are reproduced.

Startup/recovery may briefly read Supervisor state under its lock only to discover
a transaction ID, but обязано release этот lock до canonical acquisition. Любая
mutation/recovery затем reacquires locks только в frozen order
`canonical -> Supervisor -> ...`; Supervisor lock никогда не удерживается при
попытке впервые захватить canonical. PREPARED persistence and canonical CAS use
the same ordered critical section and expected canonical revision/checksum.

Central, Risk and Cash code must never callback into an earlier owner. v4
authoritative flow always passes an already locked canonical snapshot to Central;
paths that let Central reload canonical are prohibited. Timeouts fail closed and
emit sanitized evidence.

## 11. v3.10 compatibility boundary

M1 pure DTOs may be implemented against normalized integer Money only after the
separate accepted v3.9 baseline gate. M3 core may run with an explicit
`MISSING_PRE_V3_10` sentinel, but #60 cash acceptance, M4 and M5 remain blocked by
their following dependencies:

- #49: deterministic Money/Decimal currency, scale and serialization;
- #53: immutable read-only `CashAvailability` for M3 shadow only, binding canonical
  cash, Central reservation projection, cash ledger, reconciliation and broker
  snapshot without execution authority;
- #55: authoritative Portfolio Risk cash context and composite pre-POST lease;
  M5 additionally requires a separate activation acceptance.

Authoritative `DecisionEpoch` and dispatch proof after accepted/activated #55 must
bind account scope,
currency, canonical revision/checksum, Central revision/projection, ledger
revision/checksum, reconciliation checksum/status, broker snapshot time,
freshness, local reservations, pending flows, unclassified delta and free
investable cash. Any unavailable/stale/mismatched component, negative invariant
or possible double subtraction fails closed. v4 does not reinterpret v3.9 float
Risk values as accepted Money and does not replace CashAvailability authority.

## 12. Permanent invariants

```text
0 duplicate broker submits
0 fills without canonical reconciliation and Risk accounting
0 aggregate target owned by strategy after schema-3 cutover
0 actual position/cash/reservation owned by Supervisor
0 target lots inconsistent with active target attribution
0 actual lots inconsistent with realized attribution + explicit residual
0 attribution-only transition producing Central intent/order/cash effect
0 target commit without recoverable Central admission state
0 dispatch on stale or mixed revision/hash/freshness proof
0 accepted checkpoint chain with a conflicting per-account sequence/hash
0 automatic adoption of external/unattributed actual
0 persisted/hashable v4 economic float
0 raw Account ID/secret in shareable artifact or exception path
0 real-account execution
```

## 13. Deferred implementation blockers

M0 contract accepted as interface foundation, but M0 acceptance alone does not
authorize starting M1. Named gates retain explicit blockers:

1. Accepted exact v3.9 baseline/M6 before M1 or M2 starts.
2. Accepted v3.10 Money/Decimal (#49) before M4; shadow CashAvailability #53 before
   M3 cash acceptance and final closure #60; accepted Portfolio Risk cash context
   #55 plus separate activation acceptance before authoritative M5.
3. Concrete CAS/save-while-locked API and transaction record implementation.
4. Exact v3.10 internal lock suborder and complete inversion tests.
5. Cost-basis/residual/internal-transfer policy and partial-fill attribution
   ordering for #65.
6. Backup/restore/support/standalone implementation for new/evolved stores.
These are blockers of named implementation gates, not permission to widen M0.

## 14. Acceptance record

Пользовательский gate `ACCEPT V4 M0 INTERFACE FREEZE` выполнен 2026-08-15 после
третьего post-fix final review с результатом PASS. Принят только documents-only
interface contract. Все runtime/schema/GUI/economic mutations, implementation
evidence и execution authority остаются за отдельными milestones и gates.

Публикация contract отслеживается PR #70 (`Closes #58`). Его merge публикует
только документацию и не открывает implementation gate. M1/M2 по-прежнему
заблокированы до принятой exact v3.9 baseline/M6.
