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

### Последняя стабильная версия

**MOEX Research Robot v3.6.0 Stable** — принятое одноинструментное ядро для T-Invest Sandbox.

### Текущая принятая версия разработки

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

### Активный этап

**v3.7-beta1 — canonical Portfolio Manager stabilization and observability cleanup.**

Beta1 не меняет торговую архитектуру. В scope входят:

- пересчёт Portfolio warnings из текущего snapshot без stale carry-over;
- metadata-only проверка Windows Credential Manager в bootstrap report;
- классификация восстановленных transient outages как infrastructure WARN/PASS;
- отдельная observability write-only compatibility shadow;
- полный regression alpha3, standalone и 12–24-часовой Sandbox burn-in.

GitHub-задачи этапа:

- Issue #31 — общий scope beta1;
- Issue #32 — implementation checklist и acceptance matrix;
- Issue #33 — обязательный handoff локальной реализации Codex и test evidence в `v3-7-beta1`.

### Следующий этап

После принятия beta1 начинается **v3.7.0 Stable release qualification** — Issue #34.

Это не функциональное расширение: schema 2, canonical-only reads, single-writer, Risk/Execution protocol и broker lifecycle замораживаются; выполняются full regression, clean install/upgrade, backup/restore, standalone, rollback, release hygiene и финальный Sandbox burn-in.

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
- `v3-7-beta1` — активная beta implementation branch.

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
