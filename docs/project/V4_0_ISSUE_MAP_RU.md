# v4.0 Portfolio Supervisor — карта GitHub Issues

Дата: 2026-08-14
Post-review correction: 2026-08-15
Статус: `ISSUES #57-#68 CREATED / IMPLEMENTATION NOT STARTED`

## Dependency graph

```text
v4 umbrella
└── M0 interface freeze
    └── alpha1 pure domain
        └── alpha2 runtime/scheduling (#40, existing)
            └── alpha3 shadow
                └── beta1 schema/owner cutover
                    └── attribution-aware Risk
                        └── beta2 authoritative target/Central
                            ├── recovery/idempotency ──┐
                            └── fill/P&L attribution ──┴──> GUI/evidence
                                                              └── rc1 qualification
                                                                  └── v4.0.0 Stable acceptance
```

## Planned issue set

| ID | Scope | Key dependency |
|---|---|---|
| [#57](https://github.com/baimleriv/unified-portfolio-system/issues/57) | v4.0 umbrella | accepted v3.9 and staged v3.10 dependencies |
| [#58](https://github.com/baimleriv/unified-portfolio-system/issues/58) | M0 interface freeze | planning baseline |
| [#59](https://github.com/baimleriv/unified-portfolio-system/issues/59) | alpha1 pure Supervisor domain | accepted M0 and v3.9 baseline |
| [#40](https://github.com/baimleriv/unified-portfolio-system/issues/40) | alpha2 per-strategy runtime/scheduling | #59 + accepted v3.9 baseline; reuse synchronized existing Issue |
| [#60](https://github.com/baimleriv/unified-portfolio-system/issues/60) | alpha3 read-only shadow | #40 |
| [#61](https://github.com/baimleriv/unified-portfolio-system/issues/61) | beta1 schema 3 / target-owner migration | #60 + accepted v3.10 Decimal/Money #49 |
| [#62](https://github.com/baimleriv/unified-portfolio-system/issues/62) | Portfolio Risk attribution-aware concentration | #61 models |
| [#63](https://github.com/baimleriv/unified-portfolio-system/issues/63) | beta2 TargetPortfolio/Rebalance/Central | #61/#62 + accepted v3.10 CashAvailability #53 |
| [#64](https://github.com/baimleriv/unified-portfolio-system/issues/64) | recovery/idempotent target transactions | #63 state machine |
| [#65](https://github.com/baimleriv/unified-portfolio-system/issues/65) | fill/P&L/internal transfer attribution | #61/#63 + v3.10 cash-flow interfaces |
| [#66](https://github.com/baimleriv/unified-portfolio-system/issues/66) | GUI/reports/backup/support bundle | #61-#65 |
| [#67](https://github.com/baimleriv/unified-portfolio-system/issues/67) | rc1 Windows/Sandbox qualification | #40 and #61-#66 |
| [#68](https://github.com/baimleriv/unified-portfolio-system/issues/68) | v4.0.0 Stable acceptance | #67 + explicit user acceptance |

Related but out of scope: [#41](https://github.com/baimleriv/unified-portfolio-system/issues/41)
for v5 multi-timeframe/adaptive selection. Existing Issue #40 is explicitly
linked to parent #57 and dependency #59; its older #39 relation is retained as
predecessor context, not as the active v4 alpha2 admission gate.

## Closure policy

- M0 closes only after interface review; no code acceptance is inferred;
- alpha1/alpha2/alpha3 use separate branches and PRs;
- schema migration and authoritative activation require separate exact
  confirmations and rollback gates;
- beta2 cannot close from synthetic tests alone;
- Stable requires final 24–48 h Sandbox burn-in and explicit user acceptance;
- no Issue authorizes real-account execution.
