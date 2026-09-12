# Архитектурный freeze v3.10.0 Stable Release Candidate

## Владельцы состояния

- `PortfolioRepository` владеет canonical portfolio state;
- `CentralOrderManager` единолично владеет queue, reservations и dispatch lease;
- CL2 `CashLedgerStore` владеет append-only ledger persistence и OperationInbox;
- CL7 `RuntimeCashAuthorityStore` владеет durable cash-authority state machine;
- Portfolio Risk владеет Risk policy/state и использует CL6 cash context;
- `SandboxExecutionAdapter` остаётся единственной provider mutation boundary.

CL3 provider observation не является economic posting authority. CL4 opening и
reconciliation, CL5 CashAvailability и CL6 reporting context являются immutable
proof layers и не создают нового cash owner.

## Exact dispatch

Account-level configured set проходит одну Central-owned lifecycle. Перед POST
обязательны согласованные Portfolio/Central/Risk revisions, CL4 `MATCHED`, CL5
availability, CL6 Risk projection, CL7 state/attempt marker и final freshness
gate. Любой stale, mixed, missing, ambiguous или wrong-scope input даёт zero POST.

Durable attempt marker записывается до физического POST. После crash или ambiguous
outcome выполняются lookup/recovery/reconciliation; automatic resubmit запрещён.

## GUI/runtime boundary

GUI отображает manifest-derived version и read models. Он не читает и не изменяет
authoritative JSON/SQLite напрямую, не создаёт второй scheduler/provider owner и
не обходит Central/Risk/CL7 authorization. Start/Stop относится ко всему
configured instrument set одного account scope.

## Не входит

- real-account execution;
- automatic account cleanup или position/cash adoption;
- automatic Stable acceptance/publication;
- provider access без отдельного experiment gate;
- изменение CL1–CL7 semantics этим release cut.
