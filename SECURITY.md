# Security policy

Дата обновления: 2026-08-11.

## Запрещённые данные в репозитории

Нельзя коммитить:

- `.env`, API-токены и credential values;
- идентификаторы реальных и Sandbox-счетов;
- `robot_state.json`, `risk_state.json`, `portfolio_state.json`, `portfolio_legacy_shadow.json`;
- `sandbox_diagnostic_state.json`, `canonical_migration_report*.json`, `runtime_bootstrap_report.json`;
- `trading_events.db`, WAL/SHM и несаницированные экспорты событий;
- рабочие журналы, backup/support bundles и runtime-каталоги;
- банковские реквизиты и персональные данные.

## Принципы безопасной разработки

- T-Invest Sandbox используется как единственный execution-контур v3.7.
- Real-account execution в v3.7 отключён независимо от номера сборки и не разрешается переходом в Stable.
- Любая неопределённость canonical state, pending/uncertain execution или reconciliation приводит к fail-closed блокировке новых заявок.
- `PortfolioState` schema 2 является canonical source of truth; write-only compatibility shadow не может разрешать торговое действие.
- Изменение риск-лимитов и операторское разрешение ownership/target mismatch требуют явного подтверждения и audit event.
- Неизвестный broker POST не повторяется без lookup/recovery по исходному request ID.
- После fill lifecycle завершается только после canonical reconciliation и Risk accounting.
- State-файлы сохраняются атомарно и резервируются отдельно от исходного кода.
- Перед release выполняются release hygiene, secret scan и проверка содержимого архивов/support bundle.
- SecretProvider/Windows Credential Manager проверяется metadata-only; значение секрета не логируется и не включается в reports.

## GitHub и локальный Codex

Локальный Codex может работать с runtime для отладки, но в GitHub отправляются только исходники, тесты, документация и санитизированные test summaries. Перед push необходимо проверять staged diff на state-файлы, Account ID и секреты.

## Реальный контур

Переход к реальному счёту относится к отдельному будущему gate после стабильного Sandbox-контура и не входит в `v3.7.0 Stable` или `v3.8 Multi-Instrument Sandbox`. Требуются отдельная архитектура, ограничения, confirmation mode и новый acceptance.
