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
- accepted alpha3 rollback artifact: SHA-256/manifest/safety, `443 passed` и
  release hygiene — PASS.

Установка, standalone-запуск и restart из `run_gui.bat` подтверждены
пользователем на Windows. После split-runtime fix выполнен полный Sandbox
BUY→HOLD→SELL: 2/2 заявок submitted/accepted/filled, 2/2 canonical
reconciliation и Risk accounting, 0 duplicate submit и runtime/API/canonical
transaction errors. Финальный state — `READY/FRESH/MATCHED`, `blocking=false`,
shadow `OK`, warnings `0`.

Финальный beta1 acceptance — PASS 2026-08-12. Выполнены 16 ч 09 мин burn-in,
6 полных BUY→HOLD→SELL и 12/12 broker orders с canonical reconciliation и Risk
accounting. Intentional disconnect, restart с открытой позицией и
`OPEN → MARKET_IDLE → OPEN` прошли без duplicate POST; Risk Burn-in report и
support bundle просмотрены. Стандартный `max_orders_per_day=4` восстановлен.

## Исходный архив

```text
7cd4fc8335e22440289c2d2c810abf44aa5178bec4562c78d94fbfcae4c3b929  moex_trading_robot_research_v3_7_beta1.zip
```
