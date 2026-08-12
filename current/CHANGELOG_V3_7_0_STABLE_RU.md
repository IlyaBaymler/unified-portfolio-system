# Changelog v3.7.0 Stable

Версия пакета: `0.3.7`
База: принятая `v3.7-beta1`
Дата сборки: `2026-08-12`

## Release qualification

- Версия и release channel переведены в `v3.7.0 / stable`.
- Historical v3.6 sandbox dataset и beta1 acceptance evidence получили явный
  provenance в `build_manifest.json`.
- Добавлены stable verifier, source/standalone packaging и release documents.
- Добавлены tests, запрещающие смешивать 30 historical executions с beta1
  dataset 6 cycles / 12 orders.

## Security hardening qualification

- Support bundle автоматически определяет канонический Account ID из
  `portfolio_state.json`, даже если `--account-id` не передан.
- Известные чувствительные значения удаляются не только из отдельных полей,
  но и из составных строк, включая `transaction_id` событий журнала.
- Добавлен regression-тест для embedded Account ID; реальный восстановленный
  runtime прошёл независимый exact-value scan: Account ID `0`, token `0`,
  forbidden members `0`, checksum mismatches `0`.

## Функциональный freeze

Торговая логика относительно принятой beta1 не изменена. Сохраняются:

- T-Invest Sandbox only;
- PortfolioState schema 2 и canonical-only reads;
- single-writer coordinator и mandatory post-fill reconciliation;
- Risk defaults: 1 lot, максимум 4 исполнения в день;
- real-account, multi-instrument и automatic adoption disabled.
