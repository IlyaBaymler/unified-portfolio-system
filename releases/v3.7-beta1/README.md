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

## Проверено локально

- `453 passed`;
- Risk Lab `8/8 PASS`;
- release hygiene, compileall и deterministic ZIP audit — PASS;
- PyInstaller portable tree и standalone layout — PASS.

Перед публичным выпуском остаётся GUI-launch smoke portable-пакета на обычной
Windows Python/Tcl/Tk среде. Bundled Codex runtime собрал executable, но его
собственная Tcl script library непригодна для функционального GUI smoke.

## Исходный архив

```text
2187928f100e6ce1385cb1dc77870dfc7a11bfe594a3210cc496b824ff082dfd  moex_trading_robot_research_v3_7_beta1.zip
```
