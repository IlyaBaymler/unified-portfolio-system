# Архитектурный freeze v3.9.0 Stable Candidate

## Владельцы состояния

- `PortfolioRepository` — canonical actual position/cash/ownership snapshot;
- `CentralOrderManager` — единственный владелец queue, reservations и intents;
- `PortfolioRiskStateRepository` — schema 4 Risk policy/state/proof recovery;
- `EventJournal` — append-only lifecycle и Risk accounting evidence;
- `ExecutionAdapter` — единственная граница provider transport.

GUI, StrategyRuntime и reporting не получают права напрямую изменять эти
домены. Strategy proposal не является execution authorization.

## Admission и dispatch

Risk evaluator строит deterministic current/projected metrics. Увеличение риска
допускается только по свежему canonical/queue/policy/state proof. Перед provider
POST proof переоценивается; stale/mixed/missing input даёт `0 POST`. Safe
reduction остаётся subject to ownership, reconciliation и kill-switch policy.

## Recovery

Restart не выполняет automatic resubmit. Ambiguous provider response остаётся
`UNCERTAIN`. External position/cash change блокирует risk increase до явного
ack/resync. Backup/restore охватывает checksummed canonical/Central/Risk stores,
EventJournal и last-good companions.

## Не входит

- real-account execution;
- short positions;
- silent position adoption или cash resync;
- automatic kill-switch reset;
- Portfolio Supervisor v4 ownership/schema changes.
