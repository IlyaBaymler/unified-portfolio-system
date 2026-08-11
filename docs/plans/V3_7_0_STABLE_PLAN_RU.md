# v3.7.0 Stable — план квалификации релиза

Версия: `v3.7.0` / `0.3.7`.
База: принятая `v3.7-beta1`.

## Цель

Зафиксировать canonical-only Portfolio Manager как стабильное одноинструментное Sandbox-ядро без расширения торговой функциональности.

## Архитектурный freeze

В Stable не добавляются:

- новый `PortfolioState` schema;
- multi-instrument execution;
- cash reservation;
- portfolio allocation/rebalancing;
- новые стратегии;
- short positions;
- real-account execution.

Сохраняются:

- `PortfolioState` schema 2;
- canonical-only reads;
- `PortfolioTransactionCoordinator` как single writer;
- immutable snapshot/revision preflight;
- revision recheck непосредственно перед broker POST;
- mandatory post-fill canonical reconciliation;
- EventJournal и идемпотентный Risk accounting;
- startup/crash recovery;
- write-only compatibility shadow как неавторитетный compatibility layer.

## Обязательный gate

1. `v3.7-beta1` полностью принята по Issues #31/#32.
2. Полный regression suite PASS.
3. Crash/recovery matrix PASS.
4. Migration schema 1 -> 2 regression PASS.
5. 0 duplicate submit.
6. 0 fill без canonical reconciliation.
7. 0 execution без Risk accounting.
8. 0 unresolved pending/uncertain execution.
9. 0 stale blocking warnings в `READY/MATCHED`.
10. Credential Manager/SecretProvider observability корректна и не раскрывает секреты.
11. Recovered transient outages не классифицируются как application failure.
12. Backup/verify/restore PASS.
13. Support bundle/reports проходят secret scan.
14. Clean install и upgrade с принятой alpha3/beta1 PASS.
15. Standalone без установленного Python PASS.
16. Rollback на принятую beta1 проверен на тестовой копии.
17. Рекомендуемый финальный Sandbox burn-in: 24–48 часов.

## Release artifacts

- source ZIP;
- standalone package, если применяется;
- SHA-256;
- release manifest;
- acceptance record;
- changelog;
- test summary;
- sanitized burn-in report;
- recovery/upgrade/rollback инструкции.

## Acceptance decision

`v3.7.0 Stable` допускается к публикации только после отдельного пользовательского подтверждения результатов beta/stable acceptance. Реальное исполнение остаётся отключённым.

## После Stable

Следующий функциональный этап: `v3.8.0 Multi-Instrument Sandbox`.
