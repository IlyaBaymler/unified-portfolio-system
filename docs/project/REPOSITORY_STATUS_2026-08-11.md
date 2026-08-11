# Repository Status — 2026-08-11

## Принятая база

- Stable baseline: `v3.6.0`.
- Accepted development baseline: `v3.7-alpha3`.
- Current development target: `v3.7-beta1`.

## GitHub Issues

- #17 — зонтичная задача Portfolio Manager; оставить открытой до `v3.7.0 Stable`.
- #29 — alpha2 canonical preflight; закрыта как completed.
- #30 — alpha2 checklist; закрыта как completed.
- #31 — beta1 stabilization and observability cleanup; оставить open.
- #32 — beta1 implementation checklist and acceptance matrix; оставить open.

## Ветки

Рекомендуемое состояние:

```text
main                 — стабильная документация проекта
v3-7-alpha3          — принятая alpha-база, freeze
v3-7-beta1           — активная beta-разработка
release-v3.6.0       — историческая stable release branch
```

Beta-ветку создавать от принятой alpha3, а не от старого `main`, если source alpha3 ещё не влит в main.

## Обязательная repository hygiene

Не публиковать:

```text
.env
*token*
risk_state.json
robot_state.json
portfolio_state.json
portfolio_legacy_shadow.json
canonical_migration_report.json
trading_events.db*
*.log
backups/
support/
runtime/
```

Публиковать:

```text
.env.example
README.md
ROADMAP.md
versioned changelog
architecture/test/recovery docs
release manifest
SHA-256
source ZIP или GitHub Release artifact
```

## Следующая проверка репозитория

Перед публикацией beta1:

- актуальный README;
- актуальный ROADMAP;
- Issue #31/#32 отражают фактический scope;
- alpha3 acceptance record присутствует;
- beta1 не содержит старых alpha/RC документов в корне сборки;
- `.gitignore` покрывает schema 2 runtime и migration artifacts;
- full release hygiene и secret scan PASS.
