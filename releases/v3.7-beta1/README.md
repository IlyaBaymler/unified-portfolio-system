# MOEX Research Robot v3.7-beta1 — stabilization candidate

Чистая beta1-разработка начата от принятого архива `v3.7-alpha3`.

## Версия и границы

- Display: `v3.7-beta1`
- Python package: `0.3.7b1`
- PortfolioState: schema 2, canonical-only
- T-Invest Sandbox only
- Real-account execution: disabled
- Multi-asset execution: disabled

## Реализовано

- observation-scoped portfolio warnings;
- metadata-only SecretProvider probe;
- recovered/unresolved transient API classification;
- compatibility shadow statuses `OK`, `DEGRADED`, `DISABLED`.
- единый sibling `runtime` для Risk, robot и portfolio state portable-сборки.

## Проверено локально

- `454 passed`;
- Risk Lab `8/8 PASS`;
- release hygiene, compileall и deterministic ZIP audit — PASS;
- PyInstaller portable tree и standalone layout — PASS.

Установка, standalone-запуск и restart из `run_gui.bat` подтверждены
пользователем на Windows. После split-runtime fix выполнен полный Sandbox
BUY→HOLD→SELL: 2/2 заявок submitted/accepted/filled, 2/2 canonical
reconciliation и Risk accounting, 0 duplicate submit и runtime/API/canonical
transaction errors. Финальный state — `READY/FRESH/MATCHED`, `blocking=false`,
shadow `OK`, warnings `0`.

Функциональный acceptance — PASS. До финальной приёмки beta1 остаётся
расширенный 12–24-часовой burn-in с disconnect, restart с открытой позицией,
MARKET_IDLE recovery и review диагностических артефактов.

## Исходный архив

```text
730a1d1dbef8bafd87bede739dd6d33574896ee6f0a466c4a861592771cd4ee2  moex_trading_robot_research_v3_7_beta1.zip
```
