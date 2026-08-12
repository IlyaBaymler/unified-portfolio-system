# Следующий шаг

Дата обновления: 2026-08-12.

`v3.7-beta1 / 0.3.7b1` принята, PR #35 reviewed и merged в `main`.
Issues #31/#32/#33 завершены. Активный этап — Issue #34 / `v3.7.0 Stable`.

## Что уже подготовлено локально

В отдельной ветке `v3-7-0-stable` от merge commit `4e4a12b` подготовлен
Stable candidate без изменения торговой архитектуры:

- версия `v3.7.0 / 0.3.7`, channel `stable`, status `candidate`;
- историческое v3.6 evidence отделено от принятого beta1 acceptance;
- full regression `456 passed`;
- recovery/migration/backup subset `62 passed`;
- Risk Lab `8/8 PASS`;
- standalone build/layout PASS;
- accepted beta1 rollback artifact `454 passed`;
- clean deterministic source ZIP и release hygiene PASS.

PR #36 reviewed и merged в `main` commit
`d696f74b428929c8355e84009ca0cb442646ed37`. Tag `v3.7.0` и GitHub Release не
создавались.

Clean install/upgrade, standalone launch без установленного Python и
backup/verify/restore подтверждены. При review реального support bundle найден
release-blocker: Account ID сохранялся внутри составного `transaction_id`.
Небезопасный локальный bundle удалён. Через PR #38 добавлены auto-discovery
канонического Account ID, embedded-value redaction и regression-тест. Новый bundle прошёл
независимый scan: Account ID `0`, token `0`, forbidden members `0`, checksum
mismatches `0`.

## Следующий контрольный набор

1. Проверить rollback на принятую beta1 в тестовой копии.
2. Провести финальный Sandbox burn-in 24–48 часов.
3. Просмотреть итоговый evidence и явно принять либо отклонить Stable.

После acceptance можно отдельно обновить Issue #34, создать tag `v3.7.0` и
опубликовать GitHub Release.

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

Подробный протокол: `docs/releases/V3_7_0_STABLE_QUALIFICATION_RU.md`.
