# Candle interval / timeframe — план эволюции

Дата: 2026-08-12.

## Основной принцип

`candle_interval` не является свойством позиции и не должен оставаться глобальным параметром всего робота.

Временные понятия разделяются:

```text
candle_interval
    = размер свечи, используемой конкретным StrategyRuntime

decision cadence
    = когда StrategyRuntime проверяет появление новой закрытой свечи

scheduler cadence
    = как часто Global Scheduler обслуживает runtime

risk refresh cadence
    = как часто обновляется/проверяется Risk-контекст

reconciliation cadence
    = как часто canonical PortfolioState сверяется с брокером
```

Эти параметры могут иметь разные значения и не должны автоматически изменяться вместе.

## v3.7 — текущая база

Одноинструментное ядро использует один выбранный `candle_interval` в конфигурации Strategy/Bot runtime. Timeframe участвует в идентификации strategy ownership и восстановлении состояния.

В `v3.7.0 Stable` temporal model не расширяется: задача версии — заморозить и квалифицировать существующее ядро.

## v3.8 — per-instrument fixed candle interval

Issue #39.

Каждый `InstrumentRuntime` получает собственный фиксированный `candle_interval`.

Пример:

```text
SBER → 1h
LKOH → 30m
YDEX → 15m
```

Требования:

- Global Scheduler не предполагает один общий timeframe;
- `last_processed_candle` хранится отдельно для каждого runtime;
- config identity/hash включает timeframe;
- restart/recovery восстанавливает исходный timeframe;
- MARKET_IDLE, Risk и reconciliation продолжают работать независимо от появления новой свечи;
- смена timeframe не выполняется молча внутри активного lifecycle;
- dynamic Supervisor-selected timeframe не входит в v3.8.

## v4.x — per-strategy timeframe

Issue #40.

Timeframe переносится на уровень `StrategyRuntime`, чтобы один инструмент мог обслуживаться несколькими стратегиями на разных масштабах:

```text
SBER
├── SMA / 1h
├── Donchian / 30m
└── MeanReversion / 5m
```

Следствие: вопрос «какой candle interval принадлежит позиции SBER» перестаёт быть корректным. Позиция является результатом Target Portfolio / strategy contributions, а timeframe относится к конкретному strategy proposal/profile.

Требования:

- identity: `instrument + strategy + config_hash + timeframe`;
- независимый temporal state каждого StrategyRuntime;
- StrategyIntent/StrategyProposal содержит timeframe и decision horizon;
- performance attribution различает одинаковые стратегии на разных timeframe;
- canonical ownership/recovery не должны зависеть от одного единственного candle interval для всей позиции.

## v5.x — multi-timeframe strategy modules

Issue #41.

Одна стратегия может использовать несколько временных масштабов одновременно:

```text
Trend context → 1h
Setup         → 15m
Entry context → 5m
```

Timeframe-комбинация является частью versioned StrategyProfile. Сначала используются детерминированные multi-timeframe modules, затем rule-based selection, и только после появления достаточных данных допускается статистическое/ML-assisted scoring.

Portfolio Supervisor/Strategy Selector должен выбирать проверенный profile/module, а не произвольно менять длительность свечи в работающей стратегии.

## Dynamic timeframe / Supervisor

Адаптивный выбор временного горизонта допускается только как верхнеуровневый выбор между заранее определёнными и проверенными strategy profiles.

Допустимо:

```text
Regime = TREND
→ выбрать SMA_1H profile

Regime = RANGE
→ увеличить вес MeanReversion_15M profile
```

Нежелательно:

```text
volatility changed
→ незаметно переписать running strategy 60m → 5m
```

Такое изменение меняет статистику стратегии, turnover, signal frequency, holding time, ATR/stop scale и должно рассматриваться как новая versioned configuration.

## Safety boundary

Ни `candle_interval`, ни Strategy Selector, ни AI/ML слой не могут обходить:

```text
Portfolio Policy
→ Portfolio Risk
→ canonical preflight
→ Execution safety
→ post-fill reconciliation
```

Supervisor/ML формирует предложение или выбирает StrategyProfile; broker POST остаётся только в Execution Engine после обязательных safety gates.

## Acceptance strategy

Постоянный core regression сохраняется для duplicate submit, reconciliation, Risk accounting, restart/recovery и corrupt/external-state scenarios.

Новые temporal tests добавляются по мере расширения:

- v3.8: несколько instruments с разными fixed timeframe;
- v4.x: несколько StrategyRuntime одного instrument с разными timeframe;
- v5.x: synchronization нескольких temporal inputs и deterministic profile selection;
- adaptive/ML: сначала SHADOW/Sandbox, затем отдельный acceptance.
