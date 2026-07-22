# Architecture

## Целевая схема

```text
Unified Portfolio System
├── Portfolio Manager
├── Strategy Engine
├── Risk Engine
├── Cash-flow Manager
├── Execution Engine
├── Broker Adapter
├── Monitoring & Audit
└── Autonomous Service
```

## Основной принцип

Стратегия не владеет брокерской позицией напрямую. Она формирует намерение (`StrategyIntent`), а Portfolio Manager агрегирует намерения, Risk Engine ограничивает риск, Cash-flow Manager проверяет доступный капитал, после чего Execution Engine формирует и сопровождает заявку.

## Поток решения

```text
Market Data
→ Strategy Engine
→ StrategyIntent
→ Portfolio Manager
→ Risk Engine
→ Cash-flow Manager
→ Execution Engine
→ Broker
→ Portfolio Reconciliation
```

## Источник истины

Portfolio Manager должен хранить:

- состояние счёта;
- позиции и денежные остатки;
- целевые веса и количества;
- владельцев решений;
- pending orders;
- статус reconciliation;
- состояние риска и cash-flow.

## Разделение ответственности

- Strategy Engine: только сигналы и намерения.
- Portfolio Manager: итоговая целевая структура.
- Risk Engine: ограничения и запреты.
- Cash-flow Manager: резерв, выплаты и реинвестирование.
- Execution Engine: идемпотентные заявки и восстановление.
- GUI: просмотр состояния и управление, но не вычислительное ядро.
