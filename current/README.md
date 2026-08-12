# MOEX Research Robot v3.7-beta1

Исследовательский менеджер инвестиционного портфеля для **T-Invest Sandbox**.
Версия `0.3.7b1` основана на принятом `v3.7-alpha3` и стабилизирует
наблюдаемость canonical PortfolioState schema 2 без расширения торговых
возможностей.

```text
GUI:                         v3.7-beta1
Python package:              0.3.7b1
PortfolioState:              schema 2, canonical-only
Execution:                   T-Invest Sandbox only
Real account execution:      disabled
Multi-asset execution:       disabled
Automatic position adoption: disabled
```

## Что изменено в beta1

- предупреждения портфеля вычисляются заново для каждого broker snapshot и не
  переносятся из предыдущего состояния;
- проверка SecretProvider возвращает только безопасный статус
  `credential_present`, `credential_absent`, `provider_unavailable` или
  `.env_fallback`, никогда не раскрывая токен;
- отчёт Risk Burn-in различает восстановленный transient API outage,
  неразрешённый transient outage и non-transient failure;
- compatibility shadow имеет единый статус `OK`, `DEGRADED` или `DISABLED` и
  отображается в GUI, readiness и support bundle, не подменяя canonical
  readiness;
- portable GUI, Risk Engine, robot state и canonical PortfolioState используют
  единый mutable sibling `runtime`-каталог, включая запуск после restart.

## Статус приёмки

Финальный Windows/Sandbox acceptance завершён 2026-08-12 — PASS.
Функциональный smoke 2026-08-11 подтвердил:

- установка, standalone-запуск и restart из `run_gui.bat`;
- один полный BUY→HOLD→SELL, 2/2 заявок исполнены;
- 2/2 post-fill canonical reconciliation и 2/2 Risk accounting;
- 0 duplicate submit, Risk runtime, API и canonical transaction errors;
- финальный state `READY/FRESH/MATCHED`, `blocking=false`, shadow `OK`,
  warnings `0`.

Расширенный gate 2026-08-12 также пройден:

- 16 ч 09 мин непрерывного burn-in, 6 полных BUY→HOLD→SELL;
- 12/12 заявок submitted/accepted/filled, reconciled и учтены Risk;
- intentional disconnect восстановлен до `READY/FRESH/MATCHED` без повторного
  broker POST;
- restart с открытой позицией сохранил actual/target `1/1` без duplicate submit;
- `OPEN → MARKET_IDLE → OPEN` — PASS;
- Risk Burn-in report и support bundle просмотрены;
- 0 duplicate submit, fill без canonical reconciliation, execution без Risk
  accounting и unresolved pending/uncertain execution.

Стандартный Risk profile восстановлен: `max_position_lots=1`,
`max_orders_per_day=4`. Следующий этап — отдельная квалификация `v3.7.0 Stable`.

## Быстрый запуск Windows

1. Распакуйте исходный ZIP в новую папку.
2. Не переносите `.env`, runtime JSON и базу событий из непроверенного пакета.
3. Выполните:

```bat
install_and_verify_v3_7_beta1.bat
run_gui.bat
```

Перед включением Sandbox Execution проверьте account ID, свежесть broker
snapshot, `MATCHED`, отсутствие pending/uncertain order и Risk gate `PASS`.
Beta1 не включает выполнение на реальном счёте.

Mutable-файлы сохраняются рядом с `app`, в каталоге `runtime`. Не копируйте их
в `app` и не запускайте разные beta1-сборки с одним общим runtime одновременно.

## Документация

- `V3_7_BETA1_ARCHITECTURE_RU.md` — границы и потоки данных;
- `V3_7_BETA1_TEST_PLAN_RU.md` — обязательные проверки;
- `V3_7_BETA1_RECOVERY_RUNBOOK_RU.md` — восстановление и rollback;
- `UPDATE_TO_V3_7_BETA1.md` — безопасное обновление с принятого alpha3;
- `RELEASE_MANIFEST_V3_7_BETA1.txt` — состав и выпускные ограничения.

Стабильные инварианты alpha3 сохранены: canonical-only preflight, single writer,
revision/checksum lease, запрет автоматического cutover и запрет повторного POST
неопределённой заявки.
