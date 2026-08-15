# Architecture

Дата обновления: 2026-08-13.

## Текущая архитектура v3.7

`v3.7` фиксирует одноинструментное Sandbox-ядро с canonical-only Portfolio Manager.

```text
Market Data
→ Strategy Engine
→ proposed target
→ Canonical PortfolioState / Portfolio Snapshot Lease
→ Portfolio Preflight
→ Risk Engine
→ ExecutionIntent
→ revision recheck immediately before broker POST
→ Execution Engine
→ Broker Adapter
→ broker position reconciliation
→ PortfolioTransactionCoordinator
→ canonical post-fill reconciliation
→ EventJournal
→ Risk accounting
```

## Источник истины

`portfolio_state.json` schema 2 является единственным авторитетным источником портфельного состояния для GUI, Portfolio Preflight, Risk/Execution admission и Recovery.

В PortfolioState входят:

- account/instrument scope;
- broker actual lots;
- committed strategy target;
- position ownership и origin;
- pending/uncertain order state;
- freshness и reconciliation;
- revision/checksum;
- manual-review и migration metadata.

Не входят в PortfolioState как его ответственность:

- Risk counters/limits — они принадлежат `risk_state.json` / Risk Engine;
- session/strategy runtime и last processed candle — принадлежат runtime/strategy state;
- неизменяемая история lifecycle — принадлежит EventJournal;
- Cash-flow Manager — ещё не реализован в v3.7.

## Single writer

Любое изменение canonical PortfolioState проходит через `PortfolioTransactionCoordinator`.

GUI, Strategy Engine, Risk Engine и Execution Engine не должны напрямую редактировать `portfolio_state.json`.

## Compatibility shadow

Legacy portfolio shadow после schema-2 cutover является только write-only compatibility projection.

Он:

- не разрешает заявку;
- не исправляет canonical state;
- не участвует в RiskDecision;
- не является recovery source of truth.

Ошибка shadow отражается отдельно как observability status и не подменяет canonical readiness.

## Recovery

Startup/crash recovery использует:

```text
Broker state
+ EventJournal
+ canonical PortfolioState
+ RiskState
```

Неизвестный submit не повторяется без broker lookup. После подтверждённого fill lifecycle считается завершённым только после canonical reconciliation и идемпотентного Risk accounting.

## Temporal architecture

### Основной принцип

`candle_interval` относится к configured execution slot в v3.8 и к
`StrategyRuntime` начиная с v4.0. Он не является глобальным свойством всего
робота или неизменным свойством позиции.

Разделяются пять временных понятий:

```text
candle_interval
    размер свечи, используемой Strategy Engine

decision cadence
    когда execution slot/StrategyRuntime проверяет новую закрытую свечу

scheduler cadence
    как часто Global Scheduler обслуживает runtime

risk refresh cadence
    частота обновления/контроля Risk-контекста

reconciliation cadence
    частота сверки canonical PortfolioState с брокером
```

Они независимы. Например, стратегия может использовать свечи `1h`, Global Scheduler работать каждую минуту, Portfolio Risk обновляться чаще стратегии, а broker reconciliation выполняться по отдельному interval/event trigger.

### v3.7

Сохраняется один configured timeframe для одноинструментного Strategy/Bot runtime. `v3.7.0 Stable` не расширяет temporal model.

### v3.8 — configured ExecutionSlot

Issue #39.

Каждый заранее настроенный contour получает собственный фиксированный
`candle_interval` и независимый `last_processed_candle`:

```text
ExecutionSlot[SBER] → 1h
ExecutionSlot[LKOH] → 30m
ExecutionSlot[YDEX] → 15m
```

В реализованной ветке эта переходная внутренняя роль называется и сохраняется
как checksummed `InstrumentRuntime`. Он содержит temporal/lifecycle projection,
но не является владельцем canonical position/target: источником истины остаётся
`PortfolioState`. Имя/schema `InstrumentRuntime` не объявляется обязательным
публичным доменным контрактом v4+.

Global Scheduler не должен предполагать общий timeframe и не должен связывать появление новой свечи с Risk/reconciliation lifecycle.

Timeframe должен входить в versioned config identity/hash. Его изменение не выполняется молча в середине активного strategy/execution lifecycle.

### v4.x — StrategyRuntime

Issue #40.

После появления нескольких стратегий на один instrument timeframe переносится на уровень `StrategyRuntime`:

```text
SBER
├── SMA / 1h
├── Donchian / 30m
└── MeanReversion / 5m
```

В этой архитектуре позиция SBER не имеет единственного «правильного» candle interval. Позиция является canonical фактом/target портфеля, а timeframe принадлежит конкретному StrategyProfile/StrategyProposal.

Предпочтительная identity runtime:

```text
instrument_id
+ strategy_id
+ strategy_version/config_hash
+ timeframe
```

Каждый runtime имеет собственный temporal state и performance attribution.

### v5.x — multi-timeframe / Universe / adaptive selection

Issue #41.

Strategy Module может использовать несколько temporal inputs одновременно:

```text
Trend context → 1h
Setup         → 15m
Entry context → 5m
```

Portfolio Supervisor/Strategy Selector выбирает versioned StrategyProfile/module, прошедший validation для конкретного набора timeframe. Supervisor не должен произвольно переписывать `60m → 5m` внутри уже работающей стратегии без явной versioned transition.

Rule-based selection вводится раньше ML-assisted selection. Любой адаптивный слой сначала работает в SHADOW/Sandbox.

## Instrument selection и runtime domains

```text
InstrumentCatalog
    справочник известных инструментов, без выбора target

ConfiguredExecutionSet
    operator allowlist v3.8

ExecutionSlot
    переходный temporal/lifecycle contour v3.8

StrategyRuntime
    формальная вычислительная identity v4.0

InstrumentUniverse
    point-in-time candidate set v5.x
```

Текущие `MultiInstrumentProfile` реализуют ConfiguredExecutionSet, а текущий
persisted `InstrumentRuntime` — ExecutionSlot. Ни одна из этих моделей не
заменяет canonical `PositionState`. `InstrumentUniverse` не имеет прямого
маршрута к broker POST.

Подробно:
`docs/project/ARCHITECTURE_REVIEW_2026-08-13_RU.md` и
`docs/plans/INSTRUMENT_SELECTION_EVOLUTION_RU.md`.

## Timeframe и ownership

В v3.7 `PositionOwnership` содержит `candle_interval` как часть точной идентификации действующей конфигурации. Это допустимо для одноинструментного/одностратегийного lifecycle.

При переходе к нескольким strategy contributions один `candle_interval` не должен описывать всю позицию. Canonical PortfolioState хранит состояние/target/ownership, а подробный temporal context относится к versioned StrategyRuntime/StrategyProposal и performance attribution.

Изменение timeframe должно менять config identity/hash, чтобы restart/recovery не мог ошибочно считать `SMA/1h` и `SMA/15m` одной и той же стратегией.

### v4.0 — aggregate target ownership

Portfolio Supervisor владеет policy lifecycle агрегированного target и
attribution, но не actual broker position и не execution lifecycle:

```text
StrategyContributionProposal[]
-> Portfolio Supervisor requested target
-> Portfolio Risk approved target
-> RebalancePlanner net action
-> Central queue/reservation/admission
-> ExecutionAdapter POST
-> PortfolioManager reconciliation
```

Существующий `multi_instrument_strategy.StrategyProposal` уже содержит direct
`primary_target_lots` и связан с Central coordinator. v4 не создаёт второй
одноимённый тип: M0 определяет versioned `StrategyContributionProposal` и
контролируемый legacy adapter/retirement path.

Broker actual lots остаются canonical facts. Schema-3 draft вводит ownership
aggregate target, а strategy identities переносит в `TargetAttribution`.
Ex-post realized attribution хранится отдельно и обязана сходиться с canonical
actual lots через явный residual. Supervisor не создаёт второй actual-position,
cash, reservation или broker-order ledger.

Persisted/hashable contribution и budget contracts не используют binary float:
применяется fixed-point exposure и согласованный Money/minor-units contract.
DecisionEpoch связывает proposal, quote, canonical, cash, Risk и Central
revisions/checksums; attribution-only изменение не создаёт broker action.

## Safety boundary для adaptive timeframe

Даже если в будущем timeframe/profile выбирается Supervisor, Regime Model или ML-модуль, путь остаётся:

```text
Strategy/ML proposal
→ Portfolio Supervisor
→ Policy Guard
→ Portfolio Risk
→ canonical preflight
→ Execution Engine
→ Broker
→ post-fill reconciliation
```

Никакой temporal/AI слой не получает прямой broker POST и не может отключить Risk/Policy gates.

Подробный план: `docs/plans/CANDLE_INTERVAL_EVOLUTION_RU.md`.

## Текущая граница v3.7

```text
T-Invest Sandbox only
one executable instrument
long-only
PortfolioState schema 2
canonical-only reads
single writer
real account execution disabled
multi-instrument execution disabled
single configured strategy timeframe
```

## Целевая архитектура проекта

После стабилизации v3.7 система постепенно расширяется:

```text
Unified Portfolio System
├── Portfolio Manager
├── Instrument Catalog / Universe Manager (v5.x)
├── Strategy Engine(s) / StrategyRuntime(s)
├── Global Scheduler
├── Portfolio Supervisor
├── Portfolio Risk Engine
├── Cash-flow Manager
├── Capital Allocation / Rebalance Plan
├── Central Order Manager / Execution Engine
├── Execution Venue Adapter(s)
├── Monitoring & Audit
└── Autonomous Service
```

На текущем этапе `TInvestAdapter/Broker Adapter` остаётся единственной реально используемой execution boundary. Обобщение до `ExecutionVenueAdapter` является будущей архитектурной точкой расширения и не требует refactor стабильного v3.7 core заранее.

## Боковая multi-venue ветка: Crypto / Digital Assets

Crypto integration рассматривается как необязательная боковая ветка после появления устойчивого multi-instrument Portfolio Manager, Portfolio Risk, Cash-flow и Target Portfolio/Rebalance layers.

Главный принцип — общий экономический портфель и отдельные execution domains:

```text
Portfolio Supervisor
        ↓
Target Portfolio
        ↓
Portfolio Risk / Policy
        ↓
Rebalance Planner
        ↓
Execution Plan
   ┌────┴─────────────┐
   ▼                  ▼
Securities venue   Crypto venue
   ↓                  ↓
TInvestAdapter     CryptoVenueAdapter
```

Portfolio layer должен постепенно становиться asset-agnostic. Он не должен предполагать, что каждый актив:

- торгуется целыми биржевыми лотами;
- имеет MOEX-like торговую сессию;
- принадлежит broker account одного типа;
- имеет одинаковые precision/minimum-order правила.

В общем domain contract предпочтительно поддерживать `asset_class`, `venue`, `quantity`, `quantity_step`, optional `lot_size`, `min_quantity`, `min_notional`, price/quantity precision и valuation currency.

Для crypto-domain отдельно учитываются:

- fractional quantity;
- рынок 24/7;
- venue-specific health/maintenance/rate limits;
- stablecoin как отдельный asset class subtype, а не fiat cash;
- venue/custody concentration;
- отдельные ограничения на trading/deposit/withdrawal availability.

Первый scope боковой ветки — Spot only и read-only/paper-first. Leverage, margin, perpetuals, options, staking, lending, DeFi, bridges и autonomous withdrawals не входят в первоначальную интеграцию.

Safety boundary остаётся общей:

```text
Strategy / Supervisor proposal
→ Portfolio Policy
→ Portfolio Risk
→ Rebalance / Execution Plan
→ venue-specific preflight
→ ExecutionVenueAdapter
→ reconciliation
→ EventJournal / accounting
```

Выбор конкретного crypto venue и переход к real execution требуют отдельной актуальной проверки законодательства, юрисдикции, KYC/AML, доступности API и security-модели непосредственно перед реализацией.

Подробный план: `docs/plans/CRYPTO_INTEGRATION_BRANCH_RU.md`.

Эта целевая схема не означает, что Supervisor, Cash-flow, multi-instrument, multi-timeframe, multi-venue, crypto execution или portfolio allocation уже присутствуют в v3.7. Их добавление относится к следующим версиям и отдельным веткам roadmap.
