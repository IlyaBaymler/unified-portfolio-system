# MOEX Research Robot v3.7-alpha3

Исследовательский торговый робот для **T-Invest Sandbox**. Версия `v3.7-alpha3` выполняет **Canonical State Cutover**: `PortfolioState` schema 2 становится единственным авторитетным источником фактической позиции, Strategy target, ownership, pending/uncertain orders и reconciliation.

```text
GUI:                         v3.7-alpha3
Python package:              0.3.7a3
PortfolioState schema:       2
Portfolio source:            CANONICAL
Portfolio Manager mode:      canonical-only
Legacy portfolio reads:      disabled
Compatibility shadow:        write-only
Execution:                   Sandbox only, long-only, 1 instrument
Real account:                disabled
Multi-asset execution:       disabled
```

## Архитектура

```text
Broker snapshot
→ canonical PortfolioState schema 2
→ PortfolioTransactionCoordinator
→ immutable PortfolioSnapshotLease
→ canonical-only preflight
→ Risk Engine
→ revision recheck before POST
→ Execution Engine
→ broker fill
→ canonical post-fill reconciliation
→ Risk accounting
```

`robot_state.json` сохраняет непортфельный runtime: последнюю обработанную свечу, состояние стратегии, сессию и служебные маркеры. Портфельные actual/target/ownership/pending поля больше не читаются как источник решения.

## Обновление с v3.7-alpha2

1. Остановить Dry-run и Sandbox Execution.
2. Убедиться, что нет pending/uncertain order, незавершённого fill и Risk accounting.
3. Создать проверенный runtime backup.
4. Распаковать hotfix поверх каталога alpha2.
5. Запустить verifier:

```bat
install_and_verify_v3_7_alpha3.bat
```

6. Выполнить preview миграции:

```bat
run_portfolio_cutover.bat preview --account-id <SANDBOX_ACCOUNT_ID>
```

7. Если `allowed=true`, выполнить явный cutover:

```bat
run_portfolio_cutover.bat cutover ^
  --account-id <SANDBOX_ACCOUNT_ID> ^
  --confirmation "CUTOVER PORTFOLIO STATE 2"
```

8. Проверить `canonical_migration_report.json`, затем запустить GUI.

Alpha3 **не выполняет cutover автоматически**. Небезопасное состояние приводит к `CANONICAL_MIGRATION_BLOCKED` без перезаписи schema 1.

## Основные файлы runtime

- `portfolio_state.json` — canonical schema 2;
- `portfolio_legacy_shadow.json` — необязательная write-only совместимость для rollback-аудита;
- `canonical_migration_report.json` — результат preview/cutover;
- `robot_state.json` — непортфельный Strategy/session runtime;
- `risk_state.json` — Risk counters, limits, kill switch;
- `trading_events.db` — append-only аудит.

## Минимальный тест

```text
verifier
→ cutover preview
→ schema 1 → 2 cutover
→ Production Readiness PASS
→ Strategy BUY
→ HOLD
→ Strategy SELL
→ restart с открытой позицией
→ disconnect
→ MARKET_IDLE
```

Критерии:

```text
legacy portfolio reads = 0
direct PortfolioState writes outside coordinator = 0
duplicate submit = 0
fill without canonical reconciliation = 0
execution without Risk accounting = 0
revision rollback = 0
```

## Ограничения

- только T-Invest Sandbox;
- один исполняемый инструмент;
- только long-only;
- multi-asset scheduler и cash reservation отсутствуют;
- real account execution отсутствует;
- версия проверяет надёжность архитектуры, а не прибыльность стратегии.

Подробно: `V3_7_ALPHA3_ARCHITECTURE_RU.md`, `V3_7_ALPHA3_TEST_PLAN_RU.md`, `V3_7_ALPHA3_RECOVERY_RUNBOOK_RU.md`.
