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

- `454 passed`;
- Risk Lab `8/8 PASS`;
- release hygiene, compileall и deterministic ZIP audit — PASS;
- PyInstaller portable tree и standalone layout — PASS.

Установка и GUI-launch portable-пакета подтверждены пользователем на Windows.
В ходе Sandbox restart обнаружен и исправлен split-runtime: рабочий цикл читал
Risk/robot/portfolio state из `app`, тогда как GUI сохранял их в `runtime`.
Для приёмки требуется повторить Sandbox Risk restart на обновлённом архиве.

## Исходный архив

```text
d5ca027168e14370d512071547abf54f80ad5e91353358803e90ad001de308fb  moex_trading_robot_research_v3_7_beta1.zip
```
