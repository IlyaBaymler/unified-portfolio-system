# Unified Portfolio System

Приватный исследовательский проект единой системы управления инвестиционным портфелем.

Целевая архитектура объединяет:

- торговый робот и исполнение заявок;
- Portfolio Manager как единый источник состояния счёта;
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
- migration tests schema 1 → schema 2 — PASS;
- около 15 ч 19 мин Sandbox burn-in;
- 10 исполнений, включая 4 полных Strategy BUY→SELL;
- 0 duplicate submit;
- 0 fill без canonical reconciliation;
- 0 execution без Risk accounting;
- revision 0→19 без rollback;
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
- Issue #32 — implementation checklist и acceptance matrix.

## Архитектурные границы

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

- `releases/` — архивы опубликованных версий и сопровождающие заметки;
- `docs/releases/` — release/acceptance records;
- `docs/plans/` — планы следующих этапов;
- `docs/project/` — сводный статус проекта;
- `ROADMAP.md` — последовательность версий;
- `SECURITY.md` — правила работы с секретами и реальным счётом;
- `develop` — общая ветка разработки;
- `v3-7-alpha3` — принятая alpha-ветка;
- `v3-7-beta1` — ветка стабилизации beta1.

## Безопасность репозитория

Репозиторий не должен содержать:

```text
.env
API-токены
Account ID
runtime JSON
SQLite DB/WAL/SHM
логи
backup
support bundle
локальные lock-файлы
```

Использовать только `.env.example`, release manifest, SHA-256 и очищенные диагностические материалы.
