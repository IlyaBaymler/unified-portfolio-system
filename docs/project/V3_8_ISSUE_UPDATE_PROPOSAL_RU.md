# v3.8+ GitHub Issues — предложение к обновлению

Дата: 2026-08-13.

Статус: `DOCUMENTED ONLY / GITHUB NOT UPDATED`.

## Issue #39

Сохранить разные fixed timeframe и decoupling candle/decision/scheduler/Risk/
reconciliation cadence. Уточнить, что текущий `InstrumentRuntime` — внутренний
transitional ExecutionSlot v3.8, а формальный `StrategyRuntime` относится к
v4.0.

## Отдельная задача v3.8 multi-position / multi-lot

Acceptance proposal:

- 2–3 explicit instruments;
- multi-lot transitions `0->3->5->2->0`;
- sequential queue и cash reservation;
- account-wide reconciliation;
- no InstrumentUniverse;
- permanent v3.7 regression.

Автоматическая qualification задачи пройдена: `0->3->5->2->0`, partial fill,
restart/inspection, Risk/canonical accounting и cash contention. GitHub Issue
не обновлён, а реальная Sandbox acceptance остаётся pending: текущие реальные
заявки были single-lot. Дополнительная readiness-проверка выполнена в новом
изолированном runtime из canonical revision 40: SBER/LKOH/YDEX настроены с
`max_order_lots=5`, три market-driven prepare дали `NO_POSITION_CHANGE`,
preflight/Risk `PASS`, intent и provider POST отсутствуют. Это подтверждает
готовность контура, но не закрывает реальный multi-lot execution gate.

Draft PR #42 review дополнительно закрыл dispatch-time Risk freshness: intent
сохраняет guard hash значимых полей RiskState, а operator adapter под lock
сверяет актуальные policy/state перед POST. Kill switch, resync, изменение
counters/policy и legacy authorization без proof дают zero-POST. Актуальные
automated gates: targeted 116, full regression 576.

## v4.0 StrategyRuntime / Supervisor foundation

- identity: instrument + strategy + config + timeframe;
- StrategyProposal;
- deterministic ConfiguredCandidateSet;
- TargetPortfolio/RebalancePlan;
- no dynamic Universe.

## v5.x InstrumentUniverse

- point-in-time eligibility;
- liquidity/spread/history/listing/venue filters;
- survivorship-bias-safe membership;
- SHADOW/Sandbox first;
- no direct execution path.

Issue #40 сохраняет per-strategy timeframe в v4.0. Issue #41 сохраняет
multi-timeframe/Supervisor-selected profile в v5.x с dependency на
StrategyRuntime и Supervisor/Universe foundations.
