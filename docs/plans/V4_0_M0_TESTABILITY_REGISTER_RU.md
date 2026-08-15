# v4.0 M0 — testability and traceability register

Дата: 2026-08-15

Статус: `M0 INTERFACE ACCEPTED 2026-08-15 / IMPLEMENTATION EVIDENCE PENDING`

M0 не реализует тесты. Реестр задаёт observable acceptance criteria, чтобы
последующие milestones не могли закрываться только утверждением в документации.
`PENDING` означает будущий implementation/evidence gate, а не дефект M0.

## 1. Contract and determinism

| ID | Frozen requirement | Required evidence | Gate | Status |
|---|---|---|---|---|
| V4-ID-01 | runtime ID учитывает instrument/strategy/version/config/timeframe | unit tests: any field change changes ID; identical content survives restart | M1 | PENDING |
| V4-ID-02 | candidate set rejects duplicate full runtime identity | negative validation and permutation tests | M1 | PENDING |
| V4-PROP-01 | proposal has no target/execution authority and no float | schema/property tests; import/dependency boundary test | M1 | PENDING |
| V4-PROP-02 | legacy float conversion uses explicit half-even ppm rounding | boundary vectors, NaN/Infinity/range rejection | M2 | PENDING |
| V4-PROP-03 | legacy direct-target route cannot reach Central | integration test fails closed; zero intent/POST | M2 | PENDING |
| V4-EPOCH-01 | every economic input reference is mandatory | omission/mixed revision/account/currency/cutoff negatives | M1/M3 | PENDING |
| V4-EPOCH-02 | same normalized inputs yield same hashes across order/restart | permutation, serialization and restart golden tests | M1/M3 | PENDING |
| V4-NUM-01 | no binary float in persisted/hashable v4 economic contracts | recursive schema/property scan | M1+ | PENDING |
| V4-NUM-02 | MinorMoney binds currency/scale/minor_units and never compares mixed scale implicitly | validation, exact-conversion and serialization vectors | M1 | PENDING |
| V4-ID-03 | epoch -> target -> plan -> transaction -> ownership -> canonical-after identity graph is acyclic | hash-material golden fixtures and dependency-order tests | M1/M5 | PENDING |
| V4-LIFE-01 | proposal/epoch/target/plan envelopes allow only frozen transitions and never mutate DTO hash | transition matrix, unknown-state and restart tests | M1/M3 | PENDING |

## 2. Target, Risk and plan

| ID | Frozen requirement | Required evidence | Gate | Status |
|---|---|---|---|---|
| V4-TARGET-01 | target attribution sums exactly to aggregate target | property tests over zero, ties and remainder allocation | M1 | PENDING |
| V4-TARGET-02 | Risk-approved target remains between current and requested | property/integration tests including strict reduction | M1/#62 | PENDING |
| V4-TARGET-03 | requested/allocated/Risk-approved lots and Money remain separate attributions | stage conservation and reduction known-result tests | M1/#62 | PENDING |
| V4-PLAN-01 | at most one ordered net action per instrument | permutation and multi-strategy netting tests | M1 | PENDING |
| V4-PLAN-02 | SELL actions precede BUY actions | stable ordering golden tests | M1 | PENDING |
| V4-PLAN-03 | attribution-only change produces no intent/order/cash effect | pure + Central integration; provider spy count zero | M1/M5 | PENDING |
| V4-ATTR-01 | realized attributions + explicit residual equal canonical actual | fill/partial-fill/restart property tests | #65 | PENDING |
| V4-ATTR-02 | internal transfer preserves actual/cash/P&L/average price | known-result accounting vectors; zero fee/order/fill | #65 | PENDING |

## 3. Freshness and dependencies

| ID | Frozen requirement | Required evidence | Gate | Status |
|---|---|---|---|---|
| V4-FRESH-01 | latest proposal is selected by explicit cutoff/TTL | async timeframe and out-of-order proposal tests | M1/M3 | PENDING |
| V4-FRESH-02A | pre-schema3 stale cap uses latest valid shadow-only accepted checkpoint and binds its exact identity | first-sequence=1 golden vector; per-account contiguous sequence/restart/hash tests; identical retry idempotency; conflicting duplicate, gap/reorder, missing-zero, corrupt/mixed and no-authority negatives | M1/M3 | PENDING |
| V4-FRESH-02B | schema-3+ stale cap uses committed canonical-before `risk_approved_lots` only, never shadow fallback | allocated-10/Risk-approved-4 regression; missing/corrupt canonical attribution and shadow-fallback negatives | M4/M5 | PENDING |
| V4-FRESH-03 | stale alone does not liquidate; never-admitted missing is zero | state transition tests | M1/M3 | PENDING |
| V4-CASH-00 | accepted #53 CashAvailability is consumed read-only in shadow and never authorizes execution | pre-#53 sentinel cannot pass/close the cash subgate; accepted snapshot identity/freshness tests; canonical/Central/Risk byte identity; POST zero | M3/#60 CLOSE | BLOCKED BY #53 |
| V4-CASH-01 | unavailable/stale/mismatched authoritative cash context blocks increase | integration tests with every bound revision/hash/as_of and inactive-#55 negatives | M5 | BLOCKED BY #55 + ACTIVATION |
| V4-CASH-02 | reservation/ledger/canonical cash is not double-subtracted | fixed Money known-result tests | M5 | BLOCKED BY #49/#55 + ACTIVATION |
| V4-CASH-03 | any pre-POST drift invalidates composite proof | mutation-at-each-boundary integration matrix | M5 | BLOCKED BY #55 + ACTIVATION |

## 4. Migration, persistence and recovery

| ID | Frozen requirement | Required evidence | Gate | Status |
|---|---|---|---|---|
| V4-MIG-01 | eligible schema-2 state maps to exact schema-3 attribution with zero broker action | golden migration/round-trip/restart test; POST count zero | M4 | PENDING |
| V4-MIG-02 | stale, mismatch, external, pending/uncertain, corrupt identity block | one negative fixture per matrix row | M4 | PENDING |
| V4-MIG-03 | unknown schema never auto-downgrades; backup rollback exact | corrupt/unknown fixtures and byte/checksum rollback proof | M4 | PENDING |
| V4-TX-01 | prepare binds exact before and after canonical identities | serialization/CAS tests | M4/M5 | PENDING |
| V4-TX-01A | Risk-authorized/Central-not-admitted restart is non-dispatchable and idempotent | injected exception after Risk save and before Central commit | M5/#64 | PENDING |
| V4-TX-01B | non-terminal recovery record durably contains exact normalized canonical-after payload | crash/reload/checksum/byte-equivalence and premature-compaction negatives | M5/#64 | PENDING |
| V4-TX-02 | crash after Central/before canonical is non-dispatchable and recoverable | injected crash/restart; exact commit or safe cancel | M5/#64 | PENDING |
| V4-TX-02A | safe post-admission cancellation reaches durable `ABORTED_AFTER_CENTRAL_CANCELLED` and releases reservation once | injected canonical mismatch, restart and duplicate-cancel tests | M5/#64 | PENDING |
| V4-TX-03 | crash after canonical/before close completes only missing step | injected crash/restart; no duplicate commit/intent | M5/#64 | PENDING |
| V4-TX-04 | IN_FLIGHT/UNCERTAIN never replacement/resubmit | restart/disconnect recovery with provider spy | M5/#64 | PENDING |
| V4-PERSIST-01 | Supervisor state is bounded, checksummed and idempotent | corruption/last-good/revision/restart tests | M4/M5 | PENDING |
| V4-PERSIST-01A | payload compaction occurs only after verified transaction terminal identities/evidence | terminal/non-terminal compaction and restore tests | M5/#64 | PENDING |
| V4-PERSIST-02 | backup/restore/readiness/support/standalone include all evolved stores | archive manifest/hash/restore/clean install evidence | #66/#67 | PENDING |

## 5. Locks, safety and observability

| ID | Frozen requirement | Required evidence | Gate | Status |
|---|---|---|---|---|
| V4-LOCK-01 | all economic paths follow one partial order | lock instrumentation over admission/reconcile/recovery | M5/#64 | PENDING |
| V4-LOCK-01A | recovery discovery releases standalone Supervisor lock before canonical -> Supervisor mutation order | instrumented restart/concurrency inversion test | M5/#64 | PENDING |
| V4-LOCK-02 | Central/Risk/Cash never callback into earlier owner | architectural test/static dependency assertion | M2/M5 | PENDING |
| V4-LOCK-03 | timeout/inversion fails closed without mutation | concurrent timeout tests; state checksums unchanged | M5/#64 | PENDING |
| V4-OWN-01 | only target transaction coordinator mutates schema-3 target subtree; reconciler preserves it | competing-writer/CAS/reconciliation byte-equivalence tests | M4/M5 | PENDING |
| V4-SAFE-01 | authoritative v4 modules reach provider POST only through ExecutionAdapter | dependency boundary/provider spy plus forbidden-import tests for legacy bot/diagnostics | every gate | PENDING |
| V4-SAFE-02 | no artificial/real-account order in qualification | configuration guards and evidence counters | M3-M7 | PENDING |
| V4-REDACT-01 | raw Account ID/secret absent from normal/error artifacts | support bundle, exception, log, journal and path scans | #66/#67 | PENDING |
| V4-AUDIT-01 | transaction/epoch/target/plan evidence is append-only and cross-referenced | journal schema/golden/restart tests | M3-M5 | PENDING |

## 6. M0 documents-only review checklist

| ID | Review fact | Evidence in this branch | Status |
|---|---|---|---|
| V4-M0-INV | current v3.9 inventory tied to source symbols/stores | `V4_0_CURRENT_INTERFACE_INVENTORY_RU.md` | ACCEPTED 2026-08-15 |
| V4-M0-DTO | unique names, fields, hashes and numeric rules | `V4_0_INTERFACE_FREEZE_RU.md` sections 3-5 | ACCEPTED 2026-08-15 |
| V4-M0-OWN | one mutation owner per economic domain | freeze section 2 | ACCEPTED 2026-08-15 |
| V4-M0-MIG | explicit schema 2 -> 3 matrix and zero-action rule | freeze section 9 | ACCEPTED 2026-08-15 |
| V4-M0-TX | crash windows and recoverable prepare/commit protocol | freeze section 7 | ACCEPTED 2026-08-15 |
| V4-M0-LOCK | current and target lock order plus external dependency | freeze section 10 | ACCEPTED 2026-08-15 |
| V4-M0-CASH | exact accepted-v3.10 dependency/fail-closed boundary | freeze section 11 | ACCEPTED 2026-08-15 |
| V4-M0-LIFE | proposal/epoch/target/plan and recovery transitions are explicit | freeze sections 6.1 and 7 | ACCEPTED 2026-08-15 |
| V4-M0-SCOPE | no runtime/schema/GUI/economic mutation; GitHub issue-body synchronization is metadata-only and grants no authority | branch diff, freeze section 1 and `V4_0_ISSUE_MAP_RU.md` | ACCEPTED 2026-08-15 |

Все M0 review rows явно приняты пользовательским gate 2026-08-15 после третьего
post-fix final review с результатом PASS. Это не переводит implementation rows из
`PENDING`: M1/M2 остаются заблокированы до принятой exact v3.9 baseline/M6, #60
не закрывается до #53 и `V4-CASH-00`, а M4/M5 сохраняют свои внешние dependencies
и отдельные confirmations.
