# MOEX Research Robot v3.7-alpha3 — Canonical State Cutover

Принятая alpha-версия перед переходом к `v3.7-beta1`.

## Версия

- Display: `v3.7-alpha3`
- Python package: `0.3.7a3`
- PortfolioState schema: `2`
- Portfolio mode: `canonical-only`
- Sandbox only: `true`
- Real account execution: `false`
- Multi-instrument execution: `false`

## Acceptance

Подтверждены migration tests и runtime burn-in:

- ~15 ч 19 мин Sandbox-сессий;
- 10 исполнений;
- 4 полных Strategy BUY→SELL;
- 0 duplicate submit;
- 0 fill без canonical reconciliation;
- 0 execution без Risk accounting;
- 68/68 canonical transactions committed;
- revision 0→19 без rollback;
- disconnect/restart/circuit breaker/MARKET_IDLE — PASS.

Подробный acceptance record: `docs/releases/V3_7_ALPHA3_ACCEPTANCE_RU.md`.

## Исходный код

Каноническая alpha-ветка: `v3-7-alpha3`.

GitHub автоматически предоставляет ZIP исходного кода этой ветки через стандартную функцию **Code → Download ZIP**.

## Проверенные локальные release-артефакты

```text
2a643a9a48e53db1731fd91d6487c4040b774d947c92d63943665d655e429455  moex_trading_robot_research_v3_7_alpha3.zip
21086f9868fcbae4738671c6778e6212e3f89d80ce6a42431079ed1b8c402045  moex_trading_robot_v3_7_alpha3_hotfix.zip
```

Следующий этап: `v3.7-beta1` — stabilization and observability cleanup.
