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
- explicit schema 1 -> 2 migration;
- write-only compatibility shadow;
- recovery через broker + EventJournal + canonical state;
- migration tests — PASS;
- 15+ часов burn-in, 10 исполнений, 4 Strategy BUY->SELL;
- 0 duplicate submit, 0 missing reconciliation/accounting;
- revision monotonicity и disconnect/restart/MARKET_IDLE — PASS.

## v3.7-beta1 — функциональный acceptance пройден

Цель: стабилизация принятой canonical-only архитектуры без новых торговых функций.

Реализованный scope:

- warnings пересчитываются из текущего snapshot;
- `READY + MATCHED + blocking=false` не содержит stale blocking warnings;
- bootstrap различает Credential Manager, `.env` fallback, absent и unavailable;
- recovered transient outages отражаются как infrastructure WARN/PASS;
- compatibility shadow имеет независимый статус `OK/DEGRADED/DISABLED`;
- schema 2, single-writer, preflight и post-fill protocol не меняются;
- единый sibling `runtime` для Risk, robot и portfolio state в portable-сборке;
- полный alpha3 regression и standalone functional smoke.

Проверено 2026-08-11:

- `454 passed`, Risk Lab `8/8 PASS`;
- установка, standalone-запуск и restart из `run_gui.bat` — PASS;
- один полный BUY→HOLD→SELL с 2/2 исполнениями;
- 0 duplicate submit, missing reconciliation, missing Risk accounting и
  runtime/API/canonical transaction errors;
- финальный canonical state `READY/FRESH/MATCHED`, `blocking=false`, shadow
  `OK`, warnings `0`.

Оставшийся release gate: 12–24 h Sandbox burn-in, intentional disconnect,
restart с открытой позицией, `OPEN → MARKET_IDLE → OPEN` и review
reports/support bundle.

### Beta1 handoff gate

Source, tests, build manifest и sanitized evidence подготовлены в локальном
Codex и публикуются в `v3-7-beta1` для GitHub/ChatGPT review. Issue #33
отслеживает этот handoff отдельно от длительного burn-in.

Текущие Issues:

- #31 — beta1 scope;
- #32 — implementation/acceptance checklist;
- #33 — Codex -> GitHub implementation/evidence handoff.

Следующий результат: принятая `v3.7-beta1 / 0.3.7b1`.

## v3.7.0 — Portfolio Manager Stable — следующий этап

Issue #34 — release qualification and final acceptance.

Цель: зафиксировать canonical-only Portfolio Manager как стабильное одноинструментное Sandbox-ядро без изменения архитектуры.

Gate:

- beta1 acceptance complete;
- full regression и crash/recovery PASS;
- migration schema1->schema2 regression PASS;
- clean install/upgrade PASS;
- backup/verify/restore и support bundle PASS;
- standalone without Python PASS;
- rollback to accepted beta1 PASS;
- release hygiene/secret scan PASS;
- 0 duplicate submit;
- 0 fill without canonical reconciliation;
- 0 execution without Risk accounting;
- финальный Sandbox burn-in, рекомендуется 24–48 h.

## v3.8.0 — Multi-Instrument Sandbox

Первый функциональный этап после `v3.7.0 Stable`:

- до трёх инструментов;
- `InstrumentRuntime`;
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

- не разрешать real execution в v3.7/v3.8;
- не добавлять multi-instrument до v3.8;
- не менять PortfolioState schema в beta1/Stable без отдельной архитектурной причины;
- не совмещать observability/release fixes с новыми стратегиями;
- не удалять recovery/audit данные ради упрощения;
- не считать Sandbox acceptance доказательством прибыльности.
