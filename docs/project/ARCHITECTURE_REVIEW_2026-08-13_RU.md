# Architecture Review — границы runtime и instrument selection после v3.8

Дата: 2026-08-13.

Источник ревизии: `portfolio_architecture_revision_after_v3_8.zip`, SHA-256
`2EE2E54F6F849CB6E3BD31AF6F5E95B145B5E4189D82A479A98E34094D27ADC6`.

Статус: `ADOPTED WITH IMPLEMENTATION-COMPATIBLE CLARIFICATION`.

## Решение

Функциональная цель v3.8 остаётся узкой: несколько заранее настроенных
инструментов/позиций в T-Invest Sandbox, последовательная очередь, минимальный
cash reservation и account-wide reconciliation.

`InstrumentUniverse` не входит в v3.8. Формальный долгоживущий
`StrategyRuntime` относится к Portfolio Supervisor foundation в v4.0.

В текущей ветке v3.8 уже реализован и принят checksummed `InstrumentRuntime`.
Поэтому требование пакета «не иметь persisted InstrumentRuntime» задним числом
не применяется буквально. Вместо удаления проверенного механизма фиксируется
его ограниченный смысл:

- это внутренняя переходная реализация `ExecutionSlot`;
- она хранит temporal/lifecycle projection и связь с Central Order state;
- она не владеет canonical position, target или broker truth;
- `PortfolioState` остаётся единственным canonical источником позиции;
- имя и schema `InstrumentRuntime` не считаются обязательным публичным
  контрактом следующих версий;
- переход к нескольким стратегиям/timeframe на instrument выполняется через
  новый versioned `StrategyRuntime`, а не расширением одного instrument owner.

## Разделение понятий

### InstrumentCatalog

Справочник метаданных известных инструментов: UID, ticker, class code, asset
type, lot size, currency и trading status. Catalog не выбирает target и не
отправляет заявки.

### ConfiguredExecutionSet

Явный operator allowlist для v3.8:

```text
instrument + strategy profile + fixed timeframe + risk mode
```

В текущей реализации это checksummed набор `MultiInstrumentProfile`, а не
динамическая вселенная инструментов.

### ExecutionSlot

Переходный внутренний lifecycle одного configured contour. Текущий persisted
`InstrumentRuntime` реализует именно эту роль: identity/config hash,
`last_processed_candle`, status, current-lots projection и pending intent IDs.

ExecutionSlot не заменяет `PortfolioState` и не создаёт второго владельца
позиции.

### StrategyRuntime

Будущая формальная вычислительная единица v4.0:

```text
account_id
+ instrument_id
+ strategy_id
+ strategy_version/config_hash
+ timeframe
```

Она нужна, когда один instrument обслуживается несколькими стратегиями или
timeframe и требуется performance attribution.

### InstrumentUniverse

Point-in-time набор кандидатов, прошедших eligibility-фильтры: liquidity,
spread, completeness, listing, asset class, venue health и policy. Universe
формирует candidate set, но не получает маршрут к broker POST.

## Соответствие текущей реализации

```text
MultiInstrumentProfileStore  -> ConfiguredExecutionSet
InstrumentRuntime            -> transitional persisted ExecutionSlot
GlobalScheduler              -> temporal service, no broker POST
PortfolioState               -> canonical position/target/pending truth
CentralOrderManager          -> sequential account-wide intent coordination
SandboxExecutionAdapter      -> operator-only Sandbox POST boundary
```

## Что меняется в roadmap

```text
v3.8  static configured multi-position foundation;
      текущий InstrumentRuntime остаётся внутренним transitional mechanism
v3.9  aggregate Portfolio Risk
v3.10 Cash-flow accounting
v4.0  StrategyRuntime + Supervisor + deterministic TargetPortfolio/RebalancePlan
v5.x  InstrumentCatalog/Universe Manager + adaptive selection, SHADOW first
```

## Revised multi-lot scope

Приложенный пакет добавляет multi-lot переходы `0->3->5->2->0`.
Автоматическая qualification этого flow пройдена через действующие
Strategy/Risk/preflight/Central Order/reconciliation contracts, включая partial
reduction, provider partial fill, restart без replay и cash contention.

Исполненная реальная v3.8 acceptance пока ограничена 1 lot. Дополнительный
isolated runtime из canonical revision 40 успешно настроен на
`max_order_lots=5`: SBER/LKOH/YDEX прошли market-driven Strategy/Risk prepare,
но все три результата были `NO_POSITION_CHANGE`, без intent и provider POST.
Это подтверждает readiness границ и persistence, но окончательное принятие
revised release gate всё ещё требует отдельного operator-only Sandbox прогона
`0→3→5→2→0`.

## Неизменные инварианты

```text
0 duplicate submit
0 fill without canonical reconciliation
0 execution without Risk accounting
unknown or ambiguous state -> fail closed
pending or uncertain -> no blind replay
real account execution disabled
```
