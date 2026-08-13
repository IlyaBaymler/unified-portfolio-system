# Instrument selection — эволюция после v3.8

Дата: 2026-08-13.

## Три уровня

### 1. ConfiguredExecutionSet — v3.8

Ручной allowlist конкретных Sandbox contours. Система исполняет только заранее
разрешённый operator configuration set. Текущий `InstrumentRuntime` является
внутренним transitional ExecutionSlot этого уровня.

### 2. ConfiguredCandidateSet + StrategyRuntime — v4.0

Portfolio Supervisor получает формализованные `StrategyProposal` по заранее
определённому набору кандидатов и детерминированно формирует TargetPortfolio и
RebalancePlan.

### 3. InstrumentUniverse / Universe Manager — v5.x

Система формирует point-in-time набор допустимых кандидатов по данным рынка и
политикам:

```text
liquidity
spread
history completeness
listing status
asset class
venue health
allowlist / denylist
portfolio policy
```

Universe Manager выдаёт candidate set. Решение о target принимает Supervisor,
ограничение выполняет Portfolio Risk, исполнение — Execution Engine.

## Почему Universe не нужен в v3.8

Для проверки multi-position execution достаточно заранее заданных
SBER/LKOH/YDEX. Market-wide scanning одновременно с очередью, reservation и
account-wide reconciliation смешал бы research selection с safety-critical
execution.

## Почему текущий InstrumentRuntime переходный

Один instrument в будущем может иметь несколько `StrategyRuntime` с разными
timeframe и версиями. Долгосрочная identity строится вокруг:

```text
instrument_id + strategy_id + config_hash + timeframe
```

Instrument-level grouping остаётся проекцией. Текущий v3.8 persisted runtime
сохраняется как проверенный ExecutionSlot, но не объявляется окончательным
domain owner или публичным контрактом v4+.

## Зрелый selection pipeline

```text
InstrumentCatalog
-> Universe Manager
-> Eligible Candidate Set
-> StrategyRuntime evaluation
-> StrategyProposal
-> Portfolio Supervisor
-> Portfolio Risk
-> TargetPortfolio
-> RebalancePlan
-> Execution
```

Ни Catalog, ни Universe, ни StrategyRuntime не имеют прямого маршрута к broker
POST.
