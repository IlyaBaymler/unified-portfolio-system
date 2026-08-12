# Master Update 2026-08-12 — v3.7.0 Stable Candidate

## База

PR #35 с принятой `v3.7-beta1 / 0.3.7b1` merged в `main` commit
`4e4a12bda87bc82cd9d195e5960001646c7de6d3`. Stable qualification выполняется
по Issue #34 без расширения scope.

## Изменения Stable

1. Версия выпуска — `v3.7.0 / 0.3.7`, channel `stable`.
2. Historical v3.6 и beta1 acceptance datasets разделены в manifest.
3. Подготовлены stable verifier, docs, source ZIP и standalone contract.
4. Архитектурный и safety freeze закреплены тестами.

## Статус

Локальный automated qualification подтверждён:

- stable release contract — `9 passed`;
- full regression — `455 passed`;
- recovery/migration/backup targeted — `61 passed`;
- Risk Lab — `8/8 PASS`;
- release hygiene, compileall и deterministic ZIP audit — PASS;
- PyInstaller build и standalone layout — PASS;
- rollback artifact принятой beta1 — SHA/manifest/safety PASS, `454 passed`.

Windows clean install/upgrade, фактический запуск standalone без Python и
финальный Sandbox burn-in подтверждаются отдельно. Rollback package локально
проверен в изолированной копии; user-host rollback exercise остаётся gate #34.

До explicit user acceptance `stable_qualification.user_acceptance=false` и
релиз не публикуется как принятый.
