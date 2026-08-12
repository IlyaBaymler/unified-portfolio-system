# Следующий шаг

Дата обновления: 2026-08-12.

`v3.7-beta1 / 0.3.7b1` принята после automated gate, standalone smoke,
16-часового burn-in и recovery acceptance. Issues #31/#32/#33 закрываются как
completed. PR #35 готовится к review; merge в `main` остаётся отдельным
решением.

## Текущий этап: Issue #34 / v3.7.0 Stable

Release qualification выполняется без новых торговых функций и без изменения
PortfolioState schema 2, canonical-only reads, single-writer coordinator,
Risk/Execution protocol или broker lifecycle.

Первый контрольный набор:

1. Зафиксировать принятую beta1 как qualification baseline.
2. Повторить full regression, migration и crash/recovery matrix.
3. Проверить clean install и upgrade с принятой beta1.
4. Проверить backup/verify/restore и sanitized support bundle.
5. Проверить standalone without Python и rollback на принятую beta1 в тестовой
   копии.
6. Повторить release hygiene/secret scan.
7. Провести финальный 24–48-часовой Sandbox burn-in.

## Неизменяемый safety gate

```text
0 duplicate submit
0 fill without canonical reconciliation
0 execution without Risk accounting
0 unresolved pending/uncertain execution
0 stale blocking warnings in READY/MATCHED
0 secret/runtime files in release archive
real account disabled
multi-instrument execution absent
```

Подробный план: `docs/plans/V3_7_0_STABLE_PLAN_RU.md`.
