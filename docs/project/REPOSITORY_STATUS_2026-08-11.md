# Repository Status — 2026-08-11

## Принятая база

- Stable baseline: `v3.6.0`.
- Accepted development baseline: `v3.7-alpha3`.
- Current development target: `v3.7-beta1`.
- Следующий release-qualification этап после принятия beta1: `v3.7.0 Stable`.

## Фактическое состояние GitHub

На момент проверки `main` и `v3-7-beta1` содержали одну и ту же pre-beta baseline и не имели отдельного implementation diff beta1. Локальная реализация Codex должна быть отправлена в `v3-7-beta1` до анализа и acceptance.

GitHub используется как auditable boundary между локальной имплементацией и ChatGPT-review.

## GitHub Issues

- #17 — зонтичная задача Portfolio Manager; оставить открытой до `v3.7.0 Stable`.
- #29 — alpha2 canonical preflight; closed/completed.
- #30 — alpha2 checklist; closed/completed.
- #31 — beta1 stabilization and observability cleanup; open.
- #32 — beta1 implementation checklist and acceptance matrix; open.
- sync/evidence issue beta1 — контролирует публикацию локальной реализации Codex и test evidence;
- `v3.7.0 Stable` issue — release qualification после принятия beta1.

## Ветки

```text
main                 — принятая проектная/документационная база и release metadata
v3-7-alpha3          — frozen accepted alpha baseline
v3-7-beta1           — активная beta implementation branch
release-v3.6.0       — историческая stable release branch
```

`develop` является исторической интеграционной веткой и не обязателен в текущем local-Codex workflow.

## Codex -> GitHub -> ChatGPT

```text
Codex local implementation/tests/build
→ push version branch
→ GitHub commit/diff/evidence
→ ChatGPT analysis/acceptance
→ Issues/docs/roadmap/release decision
→ next Codex task
```

## Обязательная repository hygiene

Не публиковать:

```text
.env
*token*
Account ID
risk_state.json
robot_state.json
portfolio_state.json
portfolio_legacy_shadow.json
sandbox_diagnostic_state.json
canonical_migration_report*.json
runtime_bootstrap_report.json
trading_events.db*
*.log
backups/
support/
runtime/
несаницированные reports/
```

Публиковать:

```text
.env.example
source + tests
README.md
ROADMAP.md
versioned changelog
architecture/test/recovery docs
release manifest
sanitized test summary
SHA-256
source ZIP или GitHub Release artifact
```

## Следующий контрольный пункт

Перед beta1 acceptance:

- implementation diff присутствует в `v3-7-beta1`;
- Issue #31/#32 отражают фактический scope;
- full pytest и targeted beta tests PASS;
- Risk Lab 8/8 PASS;
- migration/crash/recovery regression PASS;
- Windows/Sandbox acceptance выполнен;
- standalone PASS;
- `.gitignore` покрывает schema 2 runtime/migration artifacts;
- full release hygiene и secret scan PASS.

После принятия beta1 начинается только release qualification `v3.7.0 Stable`; новые торговые функции до Stable не добавляются.
