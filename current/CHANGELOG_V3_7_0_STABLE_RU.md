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

## Функциональный freeze

Торговая логика относительно принятой beta1 не изменена. Сохраняются:

- T-Invest Sandbox only;
- PortfolioState schema 2 и canonical-only reads;
- single-writer coordinator и mandatory post-fill reconciliation;
- Risk defaults: 1 lot, максимум 4 исполнения в день;
- real-account, multi-instrument и automatic adoption disabled.
