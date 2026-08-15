# v4.0 M0 — verified inventory текущих интерфейсов

Дата: 2026-08-15

Baseline: `main` commit `65fe0fb34ce8f59d60fac834237eab3b676a1b2f`

Статус: `VERIFIED SOURCE INVENTORY / READ-ONLY / NO ACCEPTANCE CLAIM`

Документ отделяет существующие факты v3.9 от проектируемых v4 contracts. Он
составлен по source и persisted boundaries, а не только по roadmap. Целевые
решения M0 находятся в `V4_0_INTERFACE_FREEZE_RU.md`.

## 1. Canonical portfolio

Источник: `current/trading_robot/portfolio_model.py`.

- `PORTFOLIO_STATE_SCHEMA_VERSION = 2`; loader принимает schema 1/2 и
  нормализует в schema 2.
- `CashBalance.available` и `blocked` — `float`; это legacy boundary, не
  допустимый образец для hashable v4 Money.
- `PortfolioTarget` содержит instrument target lots и optional
  strategy/config/candle provenance.
- `PositionOwnership` содержит strategy/config/timeframe provenance.
- `PositionState` совместно содержит actual broker facts, target, ownership,
  pending orders, reconciliation и origin.
- `PortfolioState` содержит account, freshness/source, cash, positions, warnings,
  blocking state, revision и last transaction state.
- `decision_material()` включает cash, actual/target lots, ownership, pending и
  reconciliation; `decision_sha256` строится по canonical JSON.

Authority fact: actual positions/cash и canonical reconciliation уже имеют одного
владельца. v4 не должен создавать второй actual/cash store.

Источник: `current/trading_robot/portfolio_repository.py`.

- state защищён checksum и `.lastgood` recovery;
- `save(..., expected_revision=...)` предоставляет optimistic revision check;
- `locked_snapshot()` держит canonical lock, но явно запрещает `save()` под этим
  lock, потому что save повторно захватывает его;
- документированный current lock order:
  `canonical -> Risk profile -> Risk state -> Central`.

Implementation gap: безопасный v4 target commit требует отдельный reviewed
compare-and-swap/save-while-locked primitive. Текущий unlock-then-save оставляет
race window и не удовлетворяет M0 transaction contract.

## 2. Strategy and instrument runtime

Источник: `current/trading_robot/instrument_runtime.py`.

- `INSTRUMENT_RUNTIME_SCHEMA_VERSION = 1`;
- `InstrumentRuntimeConfig` связывает account, instrument, ticker/class,
  timeframe, strategy ID/config hash и независимые cadences;
- `runtime_config_hash` связывает config/instrument/timeframe/strategy;
- `runtime_key` дополнительно включает account and hash prefix;
- persisted `InstrumentRuntime` содержит lifecycle, time checkpoints, current
  lots, pending IDs и revision.

M0 collision: текущая запись является instrument runtime с одной strategy, а v4
нужны несколько stable `StrategyRuntimeId` на instrument. Эволюция существующего
`instrument_runtimes.json` предпочтительнее параллельного store.

Источник: `current/trading_robot/strategy_runtime.py`.

- `StrategyDecision.target_weight` — `float`;
- final `target_lots` — integer;
- решение содержит signal, strategy/config provenance, reason and indicators.

Это допустимый legacy input только для checked adapter; persisted/hashable v4
proposal не может копировать float.

## 3. Existing direct-target proposal route

Источник: `current/trading_robot/multi_instrument_strategy.py`.

`StrategyProposal` уже существует и содержит:

- runtime/instrument/ticker/timeframe/candle identity;
- strategy profile hash and primary strategy;
- `primary_target_lots`;
- decisions/comparison/generated time;
- enforced `execution_authorized=false`.

Источник: `current/trading_robot/central_order_manager.py`.

`CentralOrderCandidate.from_strategy_proposal()` напрямую преобразует этот
proposal в candidate и использует `primary_target_lots`.

Collision fact: добавление второго v4 class `StrategyProposal` создало бы два
неоднозначных authoritative routes. M0 поэтому вводит только
`StrategyContributionProposal` и требует retirement/isolation direct route до M2
acceptance.

## 4. CentralOrderManager and execution boundary

Источник: `current/trading_robot/central_order_manager.py`.

- `CENTRAL_ORDER_SCHEMA_VERSION = 1`;
- `CentralOrderCandidate` связывает account, instrument, runtime, timeframe,
  strategy, current/target lots, price in kopecks, lot size, order/TIF;
- current/target lots long-only and non-negative;
- reservation exact in integer kopecks; state содержит revision, sequence,
  intents, blocker и `reserved_cash_kopecks`;
- `central_reservation_projection_hash()` связывает account, revision,
  exclusions and reservations;
- store mutation владеет Central lock, увеличивает revision и атомарно сохраняет
  JSON/checksum;
- `PortfolioRiskAuthorizationProof` связывает canonical revision/checksum,
  Central revision/projection, Risk policy/state hashes, quote provenance,
  approved target и finalized admission revision/hash;
- `admit_portfolio()` выполняет builder под Central lock и фиксирует reservation;
- `prepare_next()` умеет принимать already locked portfolio snapshot;
- restart переводит `IN_FLIGHT` в `UNCERTAIN`; resubmit запрещён;
- `mark_reconciled()` следует current order: canonical, Risk accounting, Central.

Источники:

- `current/trading_robot/sandbox_execution_adapter.py`;
- `current/trading_robot/bot.py`;
- `current/trading_robot/diagnostics.py`;
- `current/trading_robot/tbank_sandbox.py`.

Current fact требует точного scope:

- Central-admitted v3.9 route вызывает provider `post_order` через
  `SandboxExecutionAdapter`;
- `CentralOrderManager` и `CentralOrderCoordinator` сами provider POST не делают;
- repository всё ещё содержит прямые legacy POST routes:
  `SandboxTradingBot` (`bot.py`) и explicitly confirmed
  `SandboxOrderDiagnostics` (`diagnostics.py`);
- `TBankSandboxClient.post_market_order()` является backward-compatible wrapper
  над transport `post_order`, а не отдельным admission owner.

v4 authoritative modules не имеют права импортировать, вызывать или использовать
legacy bot/diagnostic routes как обход Central/ExecutionAdapter. Их наличие
фиксируется как migration gap, а не скрывается утверждением о единственном
repository-wide caller.

## 5. Portfolio Risk

Источники:

- `current/trading_robot/portfolio_risk_model.py`;
- `current/trading_robot/portfolio_risk_runtime.py`;
- `current/trading_robot/portfolio_risk_recovery.py`.

Facts:

- `PortfolioRiskInput` связывает canonical revision/checksum/as_of, Central
  revision/projection, RiskState guard, positions, reservations and cash;
- current Risk positions, prices, cash, NAV, amounts and concentration values
  содержат floats;
- current `PortfolioRiskPolicy` и `RiskState` не имеют revision fields;
  authoritative identity представлена policy hash и RiskState guard hash;
- `PortfolioRiskDecision` ограничивает approved target интервалом current/requested
  и остаётся pure с `execution_authorized=false`;
- authoritative admission lock order:
  canonical snapshot -> Risk profile -> Risk state -> Central admission;
- RiskState last-decision fields persist inside the Central admission builder,
  before Central validation/mutation and before the final result is returned;
- external-cash recovery follows the same canonical/profile/state order.

M0 consequence: Portfolio Risk сохраняет hard-gate authority, но его current
float models — transitional adapter boundary. Они не определяют v4 Money/hash
contract и должны быть согласованы с accepted v3.10 #49 и authoritative #55.
#53 определяет только read-only shadow CashAvailability interface. Current
cross-file operation нельзя объявлять atomic: v4 transaction recovery обязан
распознавать Risk-authorized/не Central-admitted crash window.

## 6. Event journal

Источник: `current/trading_robot/journal.py`.

- EventJournal schema 2;
- append-only `record()` и специализированные risk/shadow events уже являются
  общей evidence boundary;
- journal не является canonical/Central/Risk authority.

M0 consequence: Supervisor добавляет versioned event types/references в
существующий journal; новый actual/order/cash SQLite ledger запрещён.

## 7. Current persisted topology

| File/store | Current schema | Current owner/purpose | v4 M0 disposition |
|---|---:|---|---|
| `strategy_profiles.json` | 1 | strategy config | reuse/evolve only by named gate |
| `multi_instrument_profiles.json` | 1 | multi-instrument config | no parallel authority |
| `instrument_runtimes.json` | 1 | runtime checkpoints | evolve in place to schema 2 at M2 |
| `portfolio_state.json` | 2 | canonical actual/target/reconciliation | schema 3 only at M4 cutover |
| `central_order_state.json` | 1 | intents/reservations | reuse unchanged owner |
| `risk_profiles.json` | 2 | Risk policy | reuse unchanged owner |
| `risk_state.json` | 4 | Risk accounting/state | reuse unchanged owner |
| `trading_events.db` | 2 | append-only evidence | reuse, add event types later |
| `supervisor_state.json` | absent | — | bounded new cross-store recovery store later |

Current backup/support/readiness flows know the existing stores. Every new or
evolved store needs explicit bootstrap, checksum, backup/restore, support bundle,
standalone and failure-path redaction work in its implementation milestone.

## 8. Current v3.10 dependency state

GitHub #49, #53 and #55 are open. Planning contracts exist, but no accepted
runtime authority is assumed by M0. #53 имеет shadow-only scope; authoritative
Portfolio Risk cash context и composite authorization относятся к #55 и требуют
отдельной activation acceptance.

Required accepted boundary before authoritative v4:

- deterministic Decimal/Money with currency, scale and canonical serialization;
- accepted #53 immutable read-only CashAvailability snapshot для M3 shadow,
  связывающий canonical cash, Central reservations, cash ledger, reconciliation,
  broker snapshot, pending flows, unclassified delta, free investable cash и
  freshness без execution authority;
- accepted and separately activated #55 authoritative Portfolio Risk cash context
  и composite authorization proof, detecting any revision/checksum/as_of drift
  before provider boundary.

Until acceptance, M1 may test pure normalized DTOs only after its separate
accepted v3.9 baseline gate. M3 core may use an explicit pre-v3.10 sentinel, but
#60 cash acceptance remains open until accepted #53 and `V4-CASH-00`; M4/M5 keep
their separate blockers.

## 9. Verified implementation gaps

| ID | Gap | Gate that must close it |
|---|---|---|
| GAP-01 | no StrategyContributionProposal DTO | M1/M2 |
| GAP-02 | legacy direct-target route reaches Central | M2 |
| GAP-02A | legacy bot and diagnostic modules contain direct provider POST routes | M2/M5 boundary isolation; #66 diagnostics review |
| GAP-03 | one strategy per current instrument runtime record | M2 |
| GAP-04 | no immutable complete DecisionEpoch | M1/M3 |
| GAP-05 | no Supervisor target/attribution model | M1/M4 |
| GAP-06 | no canonical CAS while lock is held | M4/M5 |
| GAP-07 | no durable cross-store target transaction record | M5/#64 |
| GAP-07A | RiskState decision can persist before Central mutation completes | M5/#64 |
| GAP-08 | no accepted v3.10 Money, shadow CashAvailability and authoritative Portfolio Risk cash context | #49 before M4; #53 before M3 cash shadow; #55 + activation before M5 |
| GAP-09 | exact realized cost-basis/residual transfer policy not frozen | #65 |
| GAP-10 | backup/support/standalone do not include Supervisor store | #66 |

Ни один gap не исправляется в M0 documents-only branch.

## 10. Source audit boundary

Проверены canonical models/repository, strategy/runtime models, legacy proposal,
Central admission/state/recovery, execution adapter, Portfolio Risk models/runtime/
recovery, EventJournal and current persistence topology. M0 не утверждает, что
future v3.10 source уже существует или принят. Все statements о v4 после разделов
с current facts являются design decisions, а не текущим runtime behavior.
