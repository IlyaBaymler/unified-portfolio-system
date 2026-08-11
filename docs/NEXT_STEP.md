# Next step

Дата обновления: 2026-08-11.

## Текущий статус

- Stable baseline: `v3.6.0`.
- Accepted development baseline: `v3.7-alpha3`.
- Active target: `v3.7-beta1 / 0.3.7b1`.
- Локальная имплементация выполняется в Codex; ChatGPT используется для анализа результатов и acceptance.

## Немедленный следующий шаг

Синхронизировать локальную реализацию Codex с удалённой веткой `v3-7-beta1` и выполнить evidence-based beta review.

Минимальный handoff:

1. implementation commit SHA;
2. diff `v3-7-alpha3 -> v3-7-beta1`;
3. full pytest summary;
4. targeted beta1 observability tests;
5. Risk Lab 8/8;
6. migration/crash/recovery regression;
7. build manifest `0.3.7b1`;
8. release hygiene/secret scan;
9. Windows/Sandbox acceptance и burn-in результаты.

## После принятия beta1

Следующий этап разработки — `v3.7.0 Stable`.

Stable является release-qualification этапом, а не функциональной разработкой. В нём не меняются `PortfolioState` schema 2, canonical-only архитектура, broker lifecycle и Risk protocol. Основные задачи: полный regression, clean upgrade/install, standalone, backup/restore, support bundle, rollback, release hygiene и финальный Sandbox burn-in.

Если beta acceptance выявит блокирующий дефект, допускается `v3.7-beta1.x`; иначе переход выполняется напрямую к `v3.7.0 Stable`.

После `v3.7.0 Stable` следующий функциональный этап — `v3.8.0 Multi-Instrument Sandbox`.
