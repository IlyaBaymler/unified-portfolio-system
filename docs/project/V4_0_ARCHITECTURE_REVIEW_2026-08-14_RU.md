# Review архитектуры v4.0 Portfolio Supervisor

Дата: 2026-08-14

Источник: `MOEX_ROBOT_V4_PORTFOLIO_SUPERVISOR_ROADMAP_RU.md`
SHA-256: `39827a13e5247172ff177c3f0c74c6ea9d0055417d98c8ebdce4a0a2e4899fa4`.

## Итог

Основная архитектура принята: несколько StrategyRuntime формируют contributions,
Supervisor строит один aggregate target, Portfolio Risk остаётся обязательным
hard gate, RebalancePlanner создаёт одну net action на инструмент, а Central и
Execution сохраняют единственный путь к provider POST.

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
RiskState guard, explicit cutoff и freshness references. Иначе одинаковые
proposal hashes могли давать разные budget/lot decisions.

### 7. Atomicity claim

Несколько JSON/SQLite stores нельзя объявить атомарными без протокола. M0 должен
выбрать recoverable prepare/commit state machine, idempotency keys и rollback
semantics, не оставляющие ложный canonical target.

### 8. Parallel-start claim

Сейчас допустим только planning M0. M1 ждёт принятую v3.9 baseline; M2 меняет
runtime и также ждёт baseline; M4/M5 дополнительно зависят от принятого v3.10
Decimal/Money contract (#49) и CashAvailability acceptance (#53).

## Решения, которые остаются draft до M0

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
