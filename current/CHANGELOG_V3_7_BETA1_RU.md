# Changelog v3.7-beta1

Версия пакета: `0.3.7b1`  
База: принятый релиз `v3.7-alpha3`  
Дата сборки: `2026-08-11`

## Исправлено

- Устранено накопление устаревших portfolio warnings между refresh-циклами.
- SecretProvider bootstrap теперь отличает отсутствие credential от
  недоступности provider и отдельно сообщает `.env_fallback`.
- В Risk Burn-in восстановленный transient API outage больше не считается
  runtime failure; неразрешённый и non-transient сбой остаются FAIL.
- Compatibility shadow нормализован до `OK`, `DEGRADED`, `DISABLED` и добавлен
  в безопасную observability-поверхность.
- Portable GUI теперь передаёт Risk Engine, robot state и canonical portfolio
  единый mutable `runtime`-каталог; устранён split-runtime после рестарта.

## Не изменено

- Выполнение разрешено только в T-Invest Sandbox.
- Canonical PortfolioState schema 2 остаётся единственным источником решения.
- Risk defaults: 1 lot и не более 4 заявок в день.
- Multi-asset execution, real account execution и automatic adoption отключены.
