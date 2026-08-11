# v3.7-beta1 — Functional Acceptance Record

Дата фиксации: 2026-08-11.  
Версия: `v3.7-beta1 / 0.3.7b1`.  
Основание: повторная чистая реализация от принятого `v3.7-alpha3`.

## Решение

Функциональный Windows/Sandbox acceptance пройден. Обнаруженный при первом
рестарте split-runtime defect устранён: portable GUI, Risk Engine, robot state
и canonical PortfolioState используют единый sibling `runtime`-каталог.

Это решение не является финальным release acceptance: перед публикацией
остаётся расширенный 12–24-часовой burn-in и recovery matrix.

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

## Оставшийся release gate

- 12–24 h Sandbox burn-in;
- 2–4 Strategy BUY→SELL суммарно;
- intentional disconnect и recovery до fresh `MATCHED`;
- restart с открытой позицией;
- `OPEN → MARKET_IDLE → OPEN`;
- review Risk Burn-in report и support bundle;
- подтверждение нулевых duplicate submit, missing reconciliation и missing Risk
  accounting на всей длительной сессии.
