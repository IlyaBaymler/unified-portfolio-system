# Roadmap

## v3.6.0 — Stable Sandbox Core — завершено

- Strategy Engine: SMA, Donchian, Ensemble, PRIMARY/SHADOW;
- Risk Engine: лимиты позиции, оборота, убытка и числа заявок;
- Startup Recovery Coordinator и идемпотентный lifecycle;
- атомарный runtime и SQLite EventJournal;
- backup/verify/restore и support bundle;
- Windows Credential Manager;
- виртуальный портфель, ownership и reconciliation;
- Production Readiness Gate;
- MARKET_IDLE;
- transient API resilience и circuit breaker;
- две длительные acceptance-сессии, 30 исполнений, 0 duplicate submit.

## v3.7.0 — Portfolio Manager

- каноническая модель `Portfolio`, `Account`, `CashBalance`, `Position`, `PendingOrder`;
- PortfolioRepository, PortfolioReconciler и PortfolioManager;
- единый источник состояния для GUI, Risk и Execution;
- фактические и целевые лоты;
- ownership и unattributed positions;
- P&L и экспорт JSON/CSV;
- synthetic multi-position tests.

## v3.8.0 — Multi-Instrument Sandbox

- до трёх инструментов на одном Sandbox-счёте;
- отдельный InstrumentRuntime;
- global scheduler;
- централизованный Order Manager;
- cash reservation и защита от oversubscription;
- последовательная очередь заявок;
- account-wide reconciliation.

## v3.9.0 — Portfolio Risk Engine

- суммарная экспозиция;
- концентрация инструмента, стратегии и класса активов;
- portfolio-wide turnover/loss/drawdown;
- общий денежный резерв;
- лимиты активных позиций и pending orders;
- global и instrument-level kill switch.

## v3.10.0 — Cash-flow Manager

- пополнения и выводы;
- дивиденды, купоны, комиссии и налоги;
- CashLedger;
- отделение инвестиционной доходности от внешних денежных потоков;
- бюджет ребалансировки и резервирование средств.

## v4.0.0 — Unified Portfolio System Sandbox

- целевые веса портфеля;
- Capital Allocation;
- Rebalance Plan;
- несколько инструментов и стратегий;
- performance attribution;
- единый audit trail;
- 7–14 дней multi-asset Sandbox acceptance.

## v4.1.0 — Ограниченный реальный контур

Этап начинается только после отдельного решения и полного Sandbox gate.

1. Read-only real portfolio.
2. Confirmation Mode для каждой заявки.
3. Limited Autonomous: allowlist, один инструмент, минимальные лимиты.
4. Расширение автономности только после отдельного acceptance.

Sandbox и live используют разные токены, runtime, RiskState, profiles, logs и backup.

## Параллельные направления

- Strategy Research Platform: walk-forward, costs/slippage, champion–challenger;
- Windows standalone и CI;
- release hygiene, secret scan и deterministic packaging;
- bonds и Cash-flow extensions после стабилизации Portfolio Manager.

## Не делать до соответствующего этапа

- не разрешать реальный execution в v3.6/v3.7;
- не запускать multi-asset до Portfolio Manager;
- не отправлять заявки параллельно до cash reservation;
- не рассчитывать портфель внутри GUI;
- не объединять Sandbox и live runtime;
- не считать Sandbox acceptance доказательством прибыльности.
