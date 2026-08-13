# v3.8.0 — Static Configured Multi-Position Sandbox

Дата ревизии: 2026-08-13.

Статус: `AUTOMATED MULTI-LOT QUALIFICATION PASS / REAL SANDBOX ACCEPTANCE PENDING`.

Архитектурное решение:
`docs/project/ARCHITECTURE_REVIEW_2026-08-13_RU.md`.

## Цель

Расширить canonical-only одноинструментное ядро до 2–3 заранее настроенных
Sandbox contours без Portfolio Supervisor, автоматического выбора инструментов
или real-account execution.

## ConfiguredExecutionSet

Оператор явно задаёт:

```text
instrument + strategy profile + fixed timeframe + risk mode
```

Набор versioned, checksummed, проходит startup validation и не может быть
молча перезаписан first-time bootstrap.

Текущая реализация использует `MultiInstrumentProfileStore`. Persisted
`InstrumentRuntime` рассматривается как внутренний transitional ExecutionSlot,
а не как долгосрочный canonical aggregate.

## Входит в v3.8

- 2–3 configured instruments;
- несколько canonical positions в одном `PortfolioState`;
- последовательная account-wide очередь intent;
- cash reservation для конкурирующих BUY-intents;
- account-wide pending/uncertain gate и reconciliation;
- отдельный fixed `candle_interval` и `last_processed_candle` каждого slot;
- независимые decision/scheduler/Risk/reconciliation cadences;
- restart без blind replay;
- operator-only Sandbox execution route;
- read-only GUI projection и support/backup integrity.

## Multi-lot extension

Revised product scope включает сценарии:

```text
0 -> 3
3 -> 5
5 -> 2
2 -> 0
```

Частичное уменьшение реализовано как обычный Strategy/Risk target transition,
а не external mismatch. Автоматическая end-to-end qualification проходит всю
цепочку с четырьмя уникальными intent и ровно четырьмя provider POST fake
transport: каждый шаг восстанавливается из persisted state, инспектируется и
завершается canonical/Risk reconciliation без duplicate submit.

Отдельно пройдены provider partial fill с безопасным продолжением оставшегося
target и конкурирующие multi-lot BUY reservations. Реальная Sandbox acceptance
ветки пока проводилась только с `max_order_lots=1`, поэтому release gate нельзя
считать окончательно принятым по автоматическим результатам.

## Не входит

```text
InstrumentUniverse / market-wide scanning
automatic instrument discovery
Portfolio Supervisor / Strategy Selector
Capital Allocation / optimizer
dynamic timeframe switching
parallel broker POST
real account execution
```

Формальный `StrategyRuntime` с несколькими strategy/timeframe на instrument
относится к v4.0. `InstrumentUniverse` и adaptive selection относятся к v5.x.

## Переходная внутренняя модель

Допустимый v3.8 ExecutionSlot хранит только:

```text
configuration identity
temporal lifecycle
last processed candle
status
current-lots projection
pending intent references
```

Canonical position/target/pending truth остаётся в `PortfolioState` и Central
Order state. Расхождение блокирует dispatch, а не исправляется автоматически.

## Фактически пройдено

- checksummed SBER/LKOH и SBER/LKOH/YDEX configuration;
- отдельные 1h/30m/15m timeframe;
- deterministic queue и cash reservation;
- SBER SELL и LKOH BUY: ровно два Sandbox POST, terminal fill, canonical/Risk
  reconciliation;
- restart/inspection без повторного submit;
- three-instrument configure/prepare/restart с состоянием SBER 0, LKOH 1,
  YDEX 0 и без execution signal;
- explicit first-time bootstrap `max_order_lots=5` с checksum/identity
  validation;
- повторный isolated acceptance из canonical revision 40: SBER/LKOH/YDEX с
  `max_order_lots=5`, три market-driven `NO_POSITION_CHANGE` (`0→0`),
  preflight/Risk `PASS`, Central revision 0, нулевые Risk execution counters и
  0 provider POST; token-free backup и EventJournal integrity — PASS;
- automated multi-lot `0->3->5->2->0`, partial fill continuation, restart без
  resubmit и multi-instrument cash contention;
- dispatch-time Risk policy/state guard: kill switch, Risk resync, изменение
  counters/policy и legacy authorization без guard proof блокируют POST;
- full regression 576 и targeted v3.8 matrix 116;
- backup/support secret scan.

## Остаётся до revised release decision

1. Выполнить реальную operator-only Sandbox acceptance
   `0->3->5->2->0`, включая restart/inspection между dispatch и reconcile.
   Проверка revision-40 runtime с лимитом 5 без Strategy signal подтверждает
   readiness, но не заменяет этот execution gate.
2. Подтвердить реальные cash/Risk значения, canonical lots и четыре уникальных
   execution ID; duplicate POST должен оставаться равен нулю.
3. Не активировать автоматический GUI/bot/scheduler execution без отдельного
   архитектурного решения.
4. Отдельно определить version/build manifest; текущие файлы остаются 0.3.7.
