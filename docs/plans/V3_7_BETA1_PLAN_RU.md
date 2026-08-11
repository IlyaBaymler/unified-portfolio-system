# v3.7-beta1 — План стабилизации

Версия: `0.3.7b1`.  
База: принятая `v3.7-alpha3`.

## Цель

Стабилизировать canonical-only Portfolio Manager перед `v3.7.0 Stable`, не расширяя торговую функциональность и не меняя проверенный broker order lifecycle.

## Обязательные исправления

### 1. Детерминированный набор Portfolio warnings

Warnings вычисляются заново для каждого текущего snapshot.

Инвариант:

```text
state_status = READY
reconciliation = MATCHED
blocking = false
```

несовместим со stale warnings:

```text
UNATTRIBUTED_OPEN_POSITION
TARGET_MISMATCH
snapshot has not been collected
BROKER_SNAPSHOT_STALE
MANUAL_REVIEW_REQUIRED
```

История предыдущих refresh не должна влиять на новый warning set.

### 2. SecretProvider observability в bootstrap

Bootstrap использует metadata-only probe и не читает значение секрета.

Статусы:

```text
credential_present
credential_absent
provider_unavailable
not_checked
.env_fallback
```

При credential в Windows Credential Manager не должно быть ложного warning `Sandbox API-токен пока не задан`.

### 3. Transient outage в burn-in report

Восстановленные:

```text
DNS failure
IncompleteRead
connection reset
timeout
HTTP 5xx/429
```

классифицируются как infrastructure WARN/PASS при выполнении условий:

- retries/circuit breaker отработали;
- duplicate submit = 0;
- uncertain execution не потерян;
- после recovery canonical state снова `MATCHED`.

FAIL остаётся для:

- runtime/application exception;
- duplicate submit;
- missing canonical reconciliation;
- missing Risk accounting;
- unresolved pending/uncertain execution;
- secret leak;
- unsafe recovery.

### 4. Compatibility shadow observability

Отдельный статус:

```text
OK
DEGRADED
DISABLED
```

Shadow failure не меняет canonical PortfolioState и не подменяет canonical readiness.

## Архитектурный freeze

Не изменять:

```text
PortfolioState schema 2
canonical-only reads
single-writer coordinator
preflight revision check
post-fill canonical reconciliation
explicit migration path
Sandbox only / one instrument / long-only
```

Не добавлять:

```text
multi-instrument execution
cash reservation
portfolio allocation/rebalancing
new strategies
short positions
real-account execution
```

## Automated gate

- полный alpha3 regression;
- targeted warning recomputation tests;
- SecretProvider metadata-only tests;
- transient outage report classification tests;
- compatibility shadow degraded tests;
- migration schema 1 → 2 regression;
- crash/recovery matrix;
- Risk Lab 8/8;
- standalone, release hygiene и secret scan;
- 0 duplicate submit;
- 0 fill без canonical reconciliation;
- 0 execution без Risk accounting.

## Windows/Sandbox acceptance

Минимум:

1. Upgrade accepted alpha3 → beta1.
2. Schema 2 и canonical-only сохраняются.
3. `READY/MATCHED` не содержит stale warnings.
4. Credential Manager отражается корректно.
5. Intentional disconnect создаёт infrastructure WARN, не ложный FAIL.
6. 2–4 Strategy BUY→SELL.
7. Restart с открытой позицией.
8. `OPEN → MARKET_IDLE → OPEN`.
9. Standalone без Python.
10. Reports/support bundle reviewed.
11. 12–24 h burn-in.

## Release gate

```text
0 duplicate submit
0 fill without canonical reconciliation
0 execution without Risk accounting
0 stale blocking warnings in READY/MATCHED
0 false missing-token warnings for protected credential
0 recovered transient outages classified as application failure
real account disabled
multi-instrument execution absent
```
