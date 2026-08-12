# Unified Portfolio System

Приватный исследовательский проект единой системы управления инвестиционным портфелем.

Целевая архитектура объединяет:

- торговый робот и исполнение заявок;
- Portfolio Manager как единый источник портфельного состояния;
- Risk Engine;
- Cash-flow Manager;
- автоматическое реинвестирование;
- мониторинг, журналирование и постепенную автономизацию.

## Текущий статус

### Последняя опубликованная стабильная версия

**MOEX Research Robot v3.6.0 Stable** — принятое одноинструментное ядро для T-Invest Sandbox.

### Принятый alpha baseline

**v3.7-alpha3 Canonical State Cutover** — пользовательский acceptance пройден.

Подтверждено:

- `PortfolioState` schema 2;
- canonical-only read path;
- `PortfolioTransactionCoordinator` как единственный writer;
- write-only compatibility shadow;
- обязательный canonical preflight и post-fill reconciliation;
- migration tests schema 1 -> schema 2 — PASS;
- около 15 ч 19 мин Sandbox burn-in;
- 10 исполнений, включая 4 полных Strategy BUY->SELL;
- 0 duplicate submit;
- 0 fill без canonical reconciliation;
- 0 execution без Risk accounting;
- revision 0->19 без rollback;
- disconnect, circuit breaker persistence, restart и MARKET_IDLE — PASS.

### Текущая принятая версия разработки

**v3.7-beta1 / `0.3.7b1` — финальный acceptance пройден 2026-08-12.**

Beta1 не меняет торговую архитектуру. Реализованы:

- пересчёт Portfolio warnings из текущего snapshot без stale carry-over;
- metadata-only проверка Windows Credential Manager в bootstrap report;
- классификация восстановленных transient outages как infrastructure WARN/PASS;
- отдельная observability write-only compatibility shadow;
- split-runtime fix: Risk, robot и portfolio state используют единый sibling
  `runtime`-каталог portable-сборки.

Подтверждено:

- полный regression — `454 passed`, Risk Lab — `8/8 PASS`;
- установка, standalone-запуск и restart из `run_gui.bat` — PASS;
- один полный Sandbox BUY→HOLD→SELL, 2/2 заявок исполнены и учтены Risk;
- 0 duplicate submit, runtime/API/canonical transaction errors;
- финальный canonical state: `READY`, `FRESH`, `MATCHED`, `blocking=false`,
  compatibility shadow `OK`, warnings `0`.

Дополнительно подтверждены 16 ч 09 мин burn-in, 6 полных BUY→HOLD→SELL,
12/12 заявок с reconciliation/Risk accounting, intentional disconnect,
restart с открытой позицией и `OPEN → MARKET_IDLE → OPEN`. На всей принятой
сессии: 0 duplicate submit, missing reconciliation, missing Risk accounting и
unresolved execution. Итоговый Risk report и support bundle просмотрены.
Финальный результат зафиксирован в
`docs/releases/V3_7_BETA1_FUNCTIONAL_ACCEPTANCE_RU.md`.

Проверяемый GitHub handoff для Issues #31–#33 находится в
`docs/releases/V3_7_BETA1_GITHUB_HANDOFF_RU.md`.

GitHub-задачи этапа:

- Issue #31 — beta1 scope, completed;
- Issue #32 — implementation/acceptance matrix, completed;
- Issue #33 — Codex → GitHub handoff, completed;
- PR #35 — reviewed и merged в `main`;
- Issue #34 — активная `v3.7.0 Stable` qualification.

### Stable-кандидат

**v3.7.0 / `0.3.7` подготовлен локально как release candidate.** Это не
функциональное расширение: schema 2, canonical-only reads, single-writer,
Risk/Execution protocol и broker lifecycle остаются замороженными.

Локально подтверждено:

- полный regression — `455 passed`;
- crash/recovery, migration и backup/restore subset — `61 passed`;
- Risk Lab — `8/8 PASS`;
- standalone build/layout, release hygiene и clean source ZIP — PASS;
- accepted beta1 rollback artifact — `454 passed`;
- deterministic source ZIP — два совпадающих SHA-256.

Остаются ручные Windows/Sandbox gates: clean install/upgrade, фактический
standalone-запуск без Python, rollback exercise, финальный 24–48-часовой
burn-in, support bundle review и отдельное пользовательское acceptance.
До этого кандидат не является опубликованным Stable.

Подробности: `docs/releases/V3_7_0_STABLE_QUALIFICATION_RU.md`.

### Следующий функциональный этап

После `v3.7.0 Stable` следующий функциональный этап — `v3.8.0 Multi-Instrument Sandbox`.

## Рабочая связка ChatGPT + Codex

```text
Codex local
  -> implementation / tests / build
  -> push version branch
GitHub
  -> auditable code diff / Issues / evidence
ChatGPT
  -> анализ результатов / архитектурный контроль / acceptance
  -> новые Issues / docs / roadmap / release decision
  -> следующий task для Codex
```

Код новой версии должен быть отправлен в соответствующую version branch до ChatGPT-review. Локальные неподтверждённые результаты сами по себе не считаются acceptance evidence.

## Архитектурные границы v3.7

```text
T-Invest Sandbox only
one executable instrument
long-only
PortfolioState schema 2
canonical-only reads
single writer
real account execution disabled
multi-instrument execution disabled
```

Реальный торговый счёт не разрешён. Переход к multi-instrument execution относится к v3.8 и возможен только после принятия v3.7.0 Stable.

## Структура репозитория

- `releases/` — release records, manifests и SHA-256;
- `docs/releases/` — release/acceptance records;
- `docs/plans/` — планы следующих этапов;
- `docs/project/` — сводный статус, Codex sync gate и review notes;
- `docs/DEVELOPMENT_PROCESS.md` — процесс ChatGPT/Codex/GitHub;
- `docs/ARCHITECTURE.md` — текущая v3.7 и целевая архитектура;
- `ROADMAP.md` — последовательность версий;
- `SECURITY.md` — правила работы с секретами и execution boundary;
- `v3-7-alpha3` — frozen accepted alpha baseline;
- `v3-7-beta1` — принятая beta implementation/evidence branch;
- `v3-7-0-stable` — ветка квалификации Stable candidate, PR #36.

`develop` сохраняется как историческая ветка и не является обязательной частью текущего local-Codex workflow.

## Безопасность репозитория

Репозиторий не должен содержать:

```text
.env
API tokens / credentials
Account ID
risk_state.json
robot_state.json
portfolio_state.json
portfolio_legacy_shadow.json
sandbox_diagnostic_state.json
canonical_migration_report*.json
runtime_bootstrap_report.json
trading_events.db*
working logs
runtime/
backups/
support/
unsanitized reports/
```

Использовать только `.env.example`, исходники/тесты, release manifests, SHA-256 и очищенные диагностические материалы.
