# Roadmap

Дата обновления: 2026-08-11.

## v3.6.0 — Stable Sandbox Core — завершено

- Strategy Engine: SMA, Donchian, Ensemble, PRIMARY/SHADOW;
- Risk Engine и Risk Dashboard;
- Startup Recovery Coordinator;
- атомарный runtime и SQLite EventJournal;
- backup/verify/restore и support bundle;
- Windows Credential Manager;
- MARKET_IDLE и transient API resilience;
- Windows standalone без установленного Python;
- длительный Sandbox acceptance без duplicate submit.

## v3.7-alpha1.x — Read-only Portfolio Manager — завершено

- каноническая broker-agnostic модель портфеля;
- GUI виртуального портфеля;
- ownership, origin, target и reconciliation;
- safe external-close acknowledgement;
- Risk Profile GUI editor;
- version/timestamp/collision-safe exports;
- diagnostic fill feedback;
- transport recovery для IncompleteRead.

## v3.7-alpha2 — Canonical Preflight — завершено

- единый `PortfolioPreflightContext` для Risk и Execution;
- snapshot revision/checksum lease;
- revision recheck перед broker POST;
- mandatory post-fill canonical reconciliation;
- canonical/legacy dual-read transition;
- race, restart, disconnect и external-activity acceptance.

## v3.7-alpha3 — Canonical State Cutover — принято

- `PortfolioState` schema 2;
- canonical-only read path;
- `PortfolioTransactionCoordinator` — единственный writer;
- explicit schema 1 → 2 migration;
- write-only compatibility shadow;
- recovery через broker + EventJournal + canonical state;
- migration tests — PASS;
- 15+ часов burn-in, 10 исполнений, 4 Strategy BUY→SELL;
- 0 duplicate submit, 0 missing reconciliation/accounting;
- revision monotonicity и disconnect/restart/MARKET_IDLE — PASS.

## v3.7-beta1 — активный этап

Цель: стабилизация принятой canonical-only архитектуры без новых торговых функций.

Обязательный scope:

- warnings пересчитываются из текущего snapshot;
- `READY + MATCHED + blocking=false` не содержит stale blocking warnings;
- bootstrap различает Credential Manager, `.env` fallback, absent и unavailable;
- recovered transient outages отражаются как infrastructure WARN/PASS;
- compatibility shadow имеет независимый статус `OK/DEGRADED/DISABLED`;
- schema 2, single-writer, preflight и post-fill protocol не меняются;
- полный alpha3 regression, standalone и 12–24 h Sandbox burn-in.

Следующий результат: `v3.7-beta1 / 0.3.7b1`.

## v3.7.0 — Portfolio Manager Stable

- accepted canonical-only Portfolio Manager для одного инструмента;
- финальная release hygiene;
- clean install, upgrade и rollback;
- длительный beta/stable burn-in;
- единый источник состояния для GUI, Risk, Execution и Recovery.

## v3.8.0 — Multi-Instrument Sandbox

- до трёх инструментов;
- InstrumentRuntime;
- Global Scheduler;
- Central Order Manager;
- cash reservation;
- последовательная очередь заявок;
- account-wide reconciliation.

## v3.9.0 — Portfolio Risk Engine

- суммарная экспозиция;
- концентрация инструмента, стратегии и класса активов;
- portfolio-wide turnover/loss/drawdown;
- общий денежный резерв;
- global и instrument-level kill switch.

## v3.10.0 — Cash-flow Manager

- пополнения, выводы, дивиденды, купоны, комиссии и налоги;
- CashLedger;
- отделение доходности от внешних потоков;
- бюджет ребалансировки.

## v4.0.0 — Unified Portfolio System Sandbox

- целевые веса;
- Capital Allocation;
- Rebalance Plan;
- несколько инструментов и стратегий;
- performance attribution;
- единый audit trail.

## v4.1.0 — Ограниченный реальный контур

Только после отдельного решения и полного Sandbox gate:

1. Read-only real portfolio.
2. Confirmation Mode.
3. Limited Autonomous с allowlist и минимальными лимитами.
4. Расширение автономности после отдельного acceptance.

## Не делать до соответствующего этапа

- не разрешать real execution в v3.7;
- не добавлять multi-instrument до v3.8;
- не менять PortfolioState schema в beta1;
- не совмещать observability fixes с новыми стратегиями;
- не удалять recovery/audit данные ради упрощения;
- не считать Sandbox acceptance доказательством прибыльности.
