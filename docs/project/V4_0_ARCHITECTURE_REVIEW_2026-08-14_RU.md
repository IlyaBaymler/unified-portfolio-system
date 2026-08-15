# Review архитектуры v4.0 Portfolio Supervisor

Дата: 2026-08-14

Дополнение M0: 2026-08-15 — третий post-fix final review завершён с PASS,
documents-only interface freeze явно принят пользователем; публикация
отслеживается PR #70, runtime authority не предоставлена.

Источник: `MOEX_ROBOT_V4_PORTFOLIO_SUPERVISOR_ROADMAP_RU.md`
SHA-256: `39827a13e5247172ff177c3f0c74c6ea9d0055417d98c8ebdce4a0a2e4899fa4`.

## Итог

Основная архитектура принята: несколько StrategyRuntime формируют contributions,
Supervisor строит один aggregate target, Portfolio Risk остаётся обязательным
hard gate, RebalancePlanner создаёт одну net action на инструмент, а Central и
Execution задают единственный authoritative v4 path к provider POST.

До M0 исправлены неоднозначности, которые могли создать второй execution owner,
недетерминированные hashes или параллельные модели.

## Established facts текущего repository

- `PortfolioState` schema 2 — canonical actual/target/pending/reconciliation;
- `PositionOwnership` сейчас содержит strategy/config/timeframe;
- `PortfolioTarget` также содержит strategy/config/candle context;
- `InstrumentRuntime` объединяет один instrument и одну strategy/timeframe;
- `StrategyDecision` уже имеет float `target_weight` и final `target_lots`;
- существующий `multi_instrument_strategy.StrategyProposal` содержит
  `primary_target_lots` и подаётся в `CentralOrderCoordinator`;
- `CentralOrderManager` владеет queue/reservations/admission;
- current `SandboxTradingBot` и `SandboxOrderDiagnostics` содержат отдельные
  direct provider POST routes; они не входят в authoritative v4 path и требуют
  явной isolation boundary;
- frozen lock order v3.9:
  `canonical -> Risk profile -> Risk state -> Central`;
- Portfolio Risk сейчас использует canonical owner `strategy_id` для strategy
  context и должен перейти на attribution-aware projection;
- Issue #40 уже покрывает per-strategy timeframe/multi-strategy scheduling;
- Issue #41 относится к v5 multi-timeframe/adaptive selection и не входит в v4.

## Исправленные архитектурные конфликты

### 1. Execution ownership

Исходная формулировка передавала Supervisor «execution lifecycle». Исправлено:
Supervisor владеет aggregate target policy; Central владеет intent lifecycle;
ExecutionAdapter — transport; PortfolioManager — canonical actual/reconciliation.

### 2. Float в hashable contracts

Предложенные `desired_exposure_fraction: float` и RUB budget float заменены
fixed-point exposure и Money/minor-units contract. Legacy floats проходят только
через проверяемый adapter с явным rounding.

### 3. Два StrategyProposal

Новый public contract назван `StrategyContributionProposal`. M0 обязан описать
migration старого direct-target `StrategyProposal`; параллельное использование
двух одноимённых типов запрещено.

### 4. Actual owner versus target owner

Broker actual position не принадлежит Supervisor. Schema 3 вводит
`TargetOwnership`; legacy `PositionOwnership` используется только для migration
в target attribution. External position не adopted автоматически.

### 5. Attribution conservation

Ex-ante target attribution и ex-post realized attribution разделены. Их суммы
сравниваются соответственно с aggregate target и canonical actual lots; во время
rebalance они не обязаны совпадать друг с другом.

### 6. DecisionEpoch completeness

Epoch дополнен quote/lot metadata, CashAvailability, Central projection,
RiskState guard, explicit cutoff, freshness references и phase-specific stale-cap
source identity. Иначе одинаковые proposal hashes могли давать разные budget/lot
decisions. До schema 3 source — только exact-confirmed shadow checkpoint; после
cutover — только committed canonical attribution без shadow fallback.

### 7. Atomicity claim

Несколько JSON/SQLite stores нельзя объявить атомарными без протокола. На дату
этого review M0 должен был выбрать recoverable prepare/commit state machine,
idempotency keys и rollback semantics, не оставляющие ложный canonical target.

### 8. Parallel-start claim

На 2026-08-14 допустим только planning M0. M1 ждёт принятую v3.9 baseline; M2 меняет
runtime и также ждёт baseline; M4/M5 дополнительно зависят от принятого v3.10
Decimal/Money contract (#49). #53 покрывает read-only shadow CashAvailability;
authoritative M5 ждёт принятый Portfolio Risk cash context #55 и отдельную
activation acceptance. Core M3 может использовать explicit sentinel до #53, но
cash subgate и final closure #60 остаются заблокированы до принятого #53.

## Решения, которые на 2026-08-14 оставались draft до M0

- exact schema-3 representation and migration rollback;
- Supervisor persistence topology;
- final lock order with Supervisor/CashAvailability stores;
- transaction coordinator protocol across canonical/Central/Risk/Supervisor;
- accepted fixed-point/Money type after v3.10 interface freeze;
- proposal TTL per runtime and epoch scheduling cadence;
- internal attribution transfer cost-basis policy;
- residual attribution handling after partial fill/external activity;
- whether schema 3 is one migration or staged additive schemas.

## Safety decision

План принят как planning baseline; Issues #57-#68 созданы, а существующий #40
переиспользован для alpha2. Production code, schema migration, Central mutation,
broker POST и live/Sandbox orders не разрешены.

## Дополнение по результатам M0 source audit — 2026-08-15

Пункты выше сохранены как исходный planning review. В принятом M0 contract:

- проверен current interface/persistence inventory на baseline `65fe0fb`;
- выбран bounded `supervisor_state.json`, а `instrument_runtimes.json` эволюционирует
  in place; параллельные actual/order/cash ledgers запрещены;
- legacy direct-target `StrategyProposal -> CentralOrderCandidate` route подлежит
  retirement/isolation до M2 acceptance;
- выбран `HOLD_LAST_NO_INCREASE` и complete `DecisionEpoch` contract;
- schema 2 -> 3 выполняется одним stopped/rollback-qualified cutover без broker
  action;
- target transaction связывает canonical-before и exact prepared canonical-after;
  crash после Central admission до canonical CAS остаётся non-dispatchable;
- non-terminal Supervisor record сохраняет полный normalized canonical-after
  payload, а `SupervisorTargetTransactionCoordinator` является единственным
  schema-3 target-subtree writer;
- Risk-authorized/Central-not-admitted также является отдельным durable,
  non-dispatchable recovery window;
- до schema-3 stale cap использует shadow-only `ShadowAcceptedTargetCheckpoint`,
  а после cutover — последний committed `risk_approved_lots` из canonical-before
  без fallback; source identity входит в epoch hash, а checkpoint sequence
  начинается с `1`, строго contiguous per account и блокирует conflicting
  same-sequence hash;
- target/transaction/canonical hashes образуют acyclic graph;
- целевой partial lock order определён; shadow snapshot boundary ждёт #53, а
  authoritative Portfolio Risk cash-context nesting и activation — #55;
- Money/CashAvailability, cost-basis/residual policy и их implementation evidence
  остаются explicit blockers соответствующих поздних gates.

Нормативный contract находится в `V4_0_INTERFACE_FREEZE_RU.md`, verified
facts — в `V4_0_CURRENT_INTERFACE_INVENTORY_RU.md`, проверки — в
`../plans/V4_0_M0_TESTABILITY_REGISTER_RU.md`. Explicit acceptance относится
только к interface contract: implementation evidence остаётся pending. Его
documents-only публикация отслеживается PR #70 и не даёт runtime authority.
