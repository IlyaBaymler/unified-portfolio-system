# План квалификации v3.7.0 Stable

## Automated gate

Запустить `install_and_verify_v3_7_0.bat` или
`VERIFY_V3_7_0_STABLE.bat`.

Обязательные результаты:

- version/manifest `0.3.7 / stable` — PASS;
- acceptance provenance: historical 30 executions и beta1 6 cycles / 12 orders
  не смешаны;
- full regression — PASS;
- migration schema 1 → 2 и crash/recovery matrix — PASS;
- backup/create/verify/preview/restore — PASS;
- release hygiene, secret scan, compileall и deterministic ZIP — PASS;
- standalone layout — PASS;
- Risk Lab — `8/8 PASS`;
- schema 2, canonical-only, real/multi-instrument disabled — PASS.

## Safety regression

- duplicate submit = 0;
- fill без canonical reconciliation = 0;
- execution без Risk accounting = 0;
- unresolved pending/uncertain execution = 0;
- stale blocking warnings в READY/MATCHED = 0;
- recovered transient не классифицируется как application FAIL;
- support bundle и source ZIP не содержат secrets/runtime.

## Локальный результат 2026-08-12

- stable release contract — `9 passed`;
- full regression — `456 passed`;
- recovery/migration/backup/readiness — `62 passed`;
- Risk Lab — `8/8 PASS`;
- release hygiene, deterministic audit, compileall — PASS;
- PyInstaller build и standalone layout — PASS;
- официальный beta1 rollback artifact — SHA/manifest/safety/hygiene PASS,
  `454 passed`.
- clean install/upgrade, standalone launch без Python и backup/restore — PASS;
- sanitized support bundle — Account ID `0`, token `0`, forbidden members `0`,
  checksum mismatches `0` по независимому scan.

## Windows qualification

1. Clean install в новую папку.
2. Upgrade с принятой beta1 после verified backup.
3. Standalone launch без системного Python.
4. Restart с открытой позицией без повторного POST.
5. Intentional disconnect/recovery до fresh `MATCHED`.
6. `OPEN → MARKET_IDLE → OPEN`.
7. Backup/verify/restore и support bundle review.
8. Rollback на принятую beta1 в тестовой копии.
9. Финальный Sandbox burn-in 24–48 часов.

## Release decision

До выполнения Windows qualification и explicit user acceptance manifest обязан
сохранять `stable_qualification.user_acceptance=false`. Git tag/Release и
закрытие Issue #34 выполняются только после отдельного подтверждения.
