# MOEX Research Robot v3.7.0 Stable Candidate

Исследовательский менеджер инвестиционного портфеля для **T-Invest Sandbox**.
Версия `0.3.7` фиксирует принятую `v3.7-beta1` как кандидата на стабильное
одноинструментное ядро без изменения торговой архитектуры.

```text
GUI:                         v3.7.0
Python package:              0.3.7
Release channel:             stable
Qualification status:        candidate
PortfolioState:              schema 2, canonical-only
Execution:                   T-Invest Sandbox only
Real account execution:      disabled
Multi-asset execution:       disabled
Automatic position adoption: disabled
```

## Что зафиксировано

- Portfolio warnings вычисляются заново для текущего broker snapshot;
- SecretProvider observability остаётся metadata-only;
- recovered transient outage даёт infrastructure WARN, а unresolved failure —
  FAIL;
- compatibility shadow `OK/DEGRADED/DISABLED` не подменяет canonical readiness;
- Risk, robot и Portfolio Manager используют единый sibling `runtime`;
- schema 2, canonical-only reads, single writer, preflight revision lease,
  post-fill reconciliation и idempotent Risk accounting не изменены.

## Acceptance provenance

`build_manifest.json` разделяет два набора evidence:

- `sandbox_acceptance` — унаследованный historical v3.6.0 core dataset:
  30 исполнений;
- `beta1_sandbox_acceptance` — принятый v3.7-beta1 dataset:
  16 ч 09 мин, 6 BUY→HOLD→SELL и 12/12 orders.

Stable candidate не считается принятым автоматически. Финальный 24–48-часовой
burn-in и explicit user acceptance остаются отдельными gate Issue #34.

## Быстрый запуск Windows

1. Распакуйте ZIP в новую папку.
2. Не переносите `.env`, runtime JSON или SQLite из непроверенного пакета.
3. Выполните:

```bat
install_and_verify_v3_7_0.bat
run_gui.bat
```

Перед Sandbox Execution проверьте account ID, fresh `MATCHED`, отсутствие
pending/uncertain order и Risk `PASS`. Реальный счёт не разрешён.

## Документация

- `V3_7_0_STABLE_ARCHITECTURE_RU.md` — архитектурный freeze;
- `V3_7_0_STABLE_TEST_PLAN_RU.md` — qualification matrix;
- `V3_7_0_STABLE_RECOVERY_RUNBOOK_RU.md` — recovery и rollback;
- `UPDATE_TO_V3_7_0_STABLE.md` — clean install/upgrade;
- `RELEASE_MANIFEST_V3_7_0_STABLE.txt` — состав и статус gate.
