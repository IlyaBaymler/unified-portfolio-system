# Changelog v3.7-alpha3

Версия пакета: `0.3.7a3`  
Дата: 2026-08-10

## Добавлено

- `PortfolioState` schema 2 с migration metadata;
- `PortfolioTransactionCoordinator` — единственный writer canonical state;
- compare-and-swap по revision и защита от rollback;
- идемпотентные canonical transactions;
- `PortfolioCutoverManager` и CLI preview/cutover;
- обязательное точное подтверждение `CUTOVER PORTFOLIO STATE 2`;
- pre-cutover schema 1 backup и SHA-256;
- `canonical_migration_report.json`;
- `LegacyPortfolioShadowWriter` в режиме write-only;
- события migration/transaction/shadow;
- canonical ownership recovery и external-close acknowledgement;
- bootstrap-статус `MIGRATION_REQUIRED`.

## Изменено

- Risk, Execution, Recovery, GUI и Readiness читают портфель только через canonical repository/snapshot lease;
- legacy dual-read отключён после cutover;
- canonical manager больше не выполняет silent migration;
- operator ownership/acknowledgement больше не используют LegacyPortfolioManager;
- post-fill reconciliation остаётся обязательной перед завершением lifecycle.

## Сохранено

- Strategy Engine и RiskPolicy;
- deterministic `orderRequestId`;
- MARKET_IDLE;
- IncompleteRead retry/circuit breaker;
- diagnostic fill feedback;
- Risk Profile GUI;
- default exports в текущий `reports`;
- backup/restore, Credential Manager и standalone.

## Ограничения

- Sandbox only;
- один исполняемый инструмент;
- long-only;
- compatibility shadow не является источником торговли;
- rollback требует pre-cutover backup.
