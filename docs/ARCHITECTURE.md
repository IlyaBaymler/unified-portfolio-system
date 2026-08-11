# Architecture

Дата обновления: 2026-08-11.

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
- session/strategy runtime и last processed candle — принадлежат `robot_state.json`;
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
```

## Целевая архитектура проекта

После стабилизации v3.7 система постепенно расширяется:

```text
Unified Portfolio System
├── Portfolio Manager
├── Strategy Engine(s)
├── Portfolio Risk Engine
├── Cash-flow Manager
├── Capital Allocation / Rebalance Plan
├── Central Order Manager / Execution Engine
├── Broker Adapter(s)
├── Monitoring & Audit
└── Autonomous Service
```

Эта целевая схема не означает, что Cash-flow, multi-instrument или portfolio allocation уже присутствуют в v3.7. Их добавление относится к следующим версиям roadmap.
