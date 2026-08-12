# v3.7-beta1 — Acceptance Record

Дата функционального smoke: 2026-08-11.
Дата финального acceptance: 2026-08-12.
Версия: `v3.7-beta1 / 0.3.7b1`.
Основание: повторная чистая реализация от принятого `v3.7-alpha3`.

## Решение

Финальный Windows/Sandbox acceptance пройден. Обнаруженный при первом
рестарте split-runtime defect устранён: portable GUI, Risk Engine, robot state
и canonical PortfolioState используют единый sibling `runtime`-каталог.

Расширенный burn-in, intentional disconnect, restart с открытой позицией,
`OPEN → MARKET_IDLE → OPEN` и review Risk Burn-in report/support bundle
завершены без блокирующих дефектов. `v3.7-beta1 / 0.3.7b1` принята как база для
отдельной квалификации `v3.7.0 Stable`.

## Automated gate

| Проверка | Результат |
|---|---:|
| Full pytest regression | 454 passed |
| Risk Lab | 8/8 PASS |
| Release hygiene | PASS |
| Compileall | PASS |
| Deterministic ZIP audit | PASS |
| Standalone layout verifier | PASS |

## Windows/Sandbox functional smoke

Источник evidence: `trading_events_v3_7_beta1_2026-08-11_171624.csv`.

| Показатель | Результат |
|---|---:|
| Установка и standalone launch | PASS |
| Restart из `run_gui.bat` | PASS |
| Полный BUY→HOLD→SELL | 1 |
| ORDER_SUBMITTED / ACCEPTED / FILLED | 2 / 2 / 2 |
| Post-fill canonical reconciliation | 2/2 |
| Risk accounting | 2/2 |
| Duplicate submit | 0 |
| Risk runtime errors | 0 |
| API request failures | 0 |
| Canonical transaction failures | 0 |
| Preflight blocks | 0 |
| ERROR/CRITICAL events | 0 |

BUY завершён с expected/actual position `1/1`; SELL — `0/0`. После SELL робот
продолжил безопасные HOLD-циклы и завершён оператором.

Финальный опубликованный PortfolioState:

```text
state_status: READY
snapshot_freshness: FRESH
reconciliation: MATCHED
blocking: false
revision: 5
compatibility_shadow: OK
warnings: 0
```

## Расширенный burn-in

Sanitized source summary:
`trading_events_v3_7_beta1_2026-08-12_101955.csv`.

```text
SHA-256: cfd28823626385fe1f75bb5959818c6b67cd49f1905ef077b81b29b988d5e03c
```

| Показатель | Результат |
|---|---:|
| Продолжительность | 16 ч 09 мин |
| Последовательные events | 1260, без gaps/duplicates |
| Полные BUY→HOLD→SELL | 6 |
| ORDER_SUBMITTED / ACCEPTED / FILLED | 12 / 12 / 12 |
| Canonical reconciliation | 12/12 |
| Risk accounting | 12/12 |
| Risk decisions | 55/55 PASS |
| Portfolio preflight | 55/55 PASS |
| MARKET_IDLE enter / exit / heartbeat | 2 / 2 / 13 |
| Orders во время MARKET_IDLE | 0 |
| Duplicate submit | 0 |
| Runtime/API/canonical failures | 0 |

Финальный state: actual/target `0/0`, revision `25`,
`READY/FRESH/MATCHED`, `blocking=false`, shadow `OK`, warnings `0`.
Стандартный Risk report: `14/14 PASS`.

## Restart и disconnect recovery

Sanitized source summary:
`trading_events_v3_7_beta1_2026-08-12_132057_02.csv`.

```text
SHA-256: c64d40c8d6ee841d836851cfafb7b4bf4a6d5cae9310675ea1c25d4508e35a29
```

- 366 последовательных events, без gaps/duplicates;
- первая сессия завершилась с открытой позицией actual/target `1/1`;
- после restart робот восстановил HOLD `1/1` без нового broker POST;
- intentional DNS disconnect создал три retry и один transient API failure;
- после восстановления опубликован fresh canonical state
  `READY/FRESH/MATCHED`, revision `7`, `blocking=false`, shadow `OK`, warnings `0`;
- Risk Burn-in report: 11 PASS, 2 ожидаемых infrastructure WARN, 0 FAIL;
- recovered transient: 1, unresolved: 0, non-transient: 0;
- duplicate submit и unresolved execution: 0;
- обе сессии завершены штатно в `STOPPED`.

## Финальный release gate

```text
duplicate submit: 0
fill without canonical reconciliation: 0
execution without Risk accounting: 0
unresolved pending/uncertain execution: 0
stale blocking warnings in READY/MATCHED: 0
false missing-token warnings with protected credential: 0
recovered transient outages classified as application failure: 0
real account execution: disabled
multi-instrument execution: absent
```

Risk Burn-in report и support bundle просмотрены пользователем. Временный
burn-in лимит `max_orders_per_day=32` возвращён к стандартному значению `4`.
Issues #31/#32 могут быть закрыты как completed; Issue #34 разблокирована.
