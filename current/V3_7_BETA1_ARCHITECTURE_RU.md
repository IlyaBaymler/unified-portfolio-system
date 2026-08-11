# Архитектура v3.7-beta1

## Авторитетное состояние

`broker snapshot → reconciler → canonical PortfolioState schema 2 → preflight`

Warnings принадлежат одному наблюдению: adapter создаёт новый набор, reconciler
добавляет только актуальные причины. Старые предупреждения не сохраняются как
часть следующего решения.

## Credential observability

Bootstrap обращается к `SecretProvider` и публикует только метаданные:
`credential_present`, `credential_absent`, `provider_unavailable` или
`.env_fallback`. Значение credential не попадает в отчёт, лог или support bundle.

## API failure classification

`API_REQUEST_FAILED` считается recovered transient только если после него в той
же последовательности наблюдений опубликован свежий безопасный canonical
snapshot. Без такого доказательства transient остаётся unresolved и даёт FAIL;
non-transient всегда даёт FAIL.

## Compatibility shadow

Shadow — write-only совместимость, не источник авторизации. Его status:

- `OK` — запись доступна;
- `DEGRADED` — запись нарушена;
- `DISABLED` — shadow отключён или не настроен.

Статус виден в GUI, readiness report и support manifest. `DEGRADED` не меняет
canonical readiness, но остаётся заметным оператору.

## Portable runtime layout

Standalone-пакет разделён на неизменяемый код и mutable state:

```text
MOEX_Research_Robot_v3_7_beta1/
├── app/       executable, libraries, bundled resources
└── runtime/   risk, robot, portfolio, EventJournal, logs and reports
```

`desktop_gui.py` передаёт один `runtime`-каталог Risk Engine, robot state,
Portfolio Manager и recovery-компонентам. Каталог `app` не является источником
mutable Risk/robot/portfolio state. Этот инвариант сохраняется после restart и
исключает расхождение между профилем, сохранённым GUI, и execution runtime.

## Неизменяемые границы

Canonical-only read, single-writer transaction, revision/checksum lease,
fail-closed preflight и запрет unsafe POST replay сохранены из alpha3.
