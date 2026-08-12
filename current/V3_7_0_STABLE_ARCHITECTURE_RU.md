# Архитектура v3.7.0 Stable

## Авторитетный контур

```text
broker snapshot
→ PortfolioReconciler
→ canonical PortfolioState schema 2
→ immutable revision/checksum preflight
→ broker POST
→ mandatory post-fill canonical reconciliation
→ idempotent Risk accounting
```

`PortfolioTransactionCoordinator` остаётся единственным writer. Legacy shadow
является только write-only observability projection и не авторизует execution.

## Observability freeze

- warnings принадлежат текущему snapshot и не накапливаются;
- SecretProvider публикует только safe metadata;
- recovered transient требует последующего fresh `MATCHED` evidence;
- shadow `OK/DEGRADED/DISABLED` не изменяет canonical readiness.

## Portable layout

```text
MOEX_Research_Robot_v3_7_0/
├── app/       executable and immutable resources
├── runtime/   risk, robot, portfolio and EventJournal
├── backups/
├── reports/
├── logs/
└── support/
```

Все mutable adapters используют один sibling `runtime`. Совместное
использование runtime разными установками запрещено.

## Safety boundary

Sandbox only, one executable instrument, long-only. Real-account execution,
multi-instrument, short positions, allocation/rebalancing и automatic adoption
не входят в v3.7.0.
