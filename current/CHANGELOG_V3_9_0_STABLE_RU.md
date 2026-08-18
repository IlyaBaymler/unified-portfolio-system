# Changelog v3.9.0 Stable Candidate

Версия пакета: `0.3.9`  
Дата подготовки M6: 2026-08-15

## Основные изменения после v3.7.0

- multi-instrument StrategyRuntime и configured candidate set;
- Central queue, cash reservations и единый Sandbox ExecutionAdapter;
- account-wide Portfolio Risk current/projected metrics и deterministic caps;
- shadow observation с переходом к explicit ENFORCED admission;
- pre-dispatch proof revalidation и отдельная reauthorization;
- operator global/instrument kill switches с exact confirmation;
- external position/cash recovery без автоматического adoption/resync;
- checksummed Risk persistence schema 4, backup/restore и sanitized support;
- read-only M5.3 persistence/standalone qualification tooling.

## Не изменено

- только T-Invest Sandbox;
- real-account execution отсутствует;
- short и silent external-position adoption запрещены;
- ambiguous provider response не разрешает duplicate submit;
- Stable publication требует отдельного M6 acceptance.
