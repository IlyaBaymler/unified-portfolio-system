# Repository layout

Дата обновления: 2026-08-11.

## Назначение

GitHub хранит проверяемую историю исходного кода, тестов, документации, Issues и release metadata. Рабочий runtime и секреты хранятся отдельно.

## Текущая верхнеуровневая структура

```text
.
├── docs/                       # архитектура, планы, acceptance и процесс
├── releases/                   # release records, manifests, SHA-256
├── README.md
├── ROADMAP.md
├── SECURITY.md
└── .gitignore
```

После синхронизации локального Codex version branch также должна содержать фактические исходники, тесты и build scripts соответствующей версии. Их путь не дублируется в этом документе жёстко: каноническая структура определяется самим version branch и release manifest.

## Ветки

Актуальная схема:

- `main` — принятая проектная/документационная база и release metadata;
- `v3-7-alpha3` — frozen accepted alpha baseline;
- `v3-7-beta1` — активная ветка beta1;
- `release-*` / исторические version branches — архивные точки прошлых версий;
- `develop` — историческая ветка, не обязательная в текущем локальном Codex workflow.

Новая реализация анализируется только после push в version branch. Локальная копия Codex сама по себе не является проверяемым источником для acceptance.

## Что должно попадать в version branch

- исходный код;
- unit/integration tests;
- build/install scripts;
- version/build manifest;
- changelog;
- architecture/test/recovery docs;
- безопасные test summaries.

## Файлы, запрещённые к публикации

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
reports/ с несаницированными данными
virtual environments / caches / local build output
```

Release artifacts должны проходить release hygiene и secret scan до публикации.
