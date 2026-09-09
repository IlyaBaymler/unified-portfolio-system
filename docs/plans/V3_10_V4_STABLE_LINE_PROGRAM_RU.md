# v3.10 Cash Ledger -> v4 Portfolio Manager: stable-line program contract

Status: `M0 CONTRACT FREEZE CANDIDATE`

## 1. Program identity

Repository: `baimleriv/unified-portfolio-system`.

Immutable accepted oracle/baseline:

- tag: `v3.9.0`;
- commit: `412e126166831a8bac0435d61932ab328fc61f12`;
- tree: `18c2822d2aa7f174a99d7d7dc5f5a5851dc59eba`.

Stable-line root branch: `program/v3-10-v4-stable-line`.

M0 branch: `agent/v3-10-clean-m0-contract-freeze`.

The stable line MUST NOT be rebased onto or merged with contemporary `main` merely to obtain later work. Existing v3.10/MoneyV2 and later branches are evidence/reference sources only. Code may be re-derived or selectively reimplemented after review; their ancestry is not inherited automatically.

## 2. Mission

1. Deliver a stable v3.10 Cash Ledger / Cash-flow subsystem from the accepted v3.9 baseline.
2. Only after v3.10 stable acceptance, build v4 Portfolio Manager as its separately gated successor.
3. Preserve v3.9 behaviour as the regression oracle unless a milestone explicitly accepts a bounded semantic change.
4. Avoid recursive review/fix governance and correction treadmill behaviour.

## 3. Permanent safety and ownership invariants

- Broker current cash has exactly one explicit authoritative owner at any runtime stage.
- `CentralOrderState` remains the owner of internal order reservations unless a future separately accepted contract explicitly changes that ownership.
- CashLedger evidence is not automatically broker truth.
- Exact-money canonical state MUST NOT use binary floating point or silent rounding.
- Unknown schema/version/currency/codec/custody fails closed.
- Read-only/reporting milestones MUST NOT perform provider POST, broker dispatch, economic mutation, migration/adoption, or hidden state repair.
- Provider/private-account access, authenticated observation and experiments require their own explicit preparation and experiment gate.
- Real-account execution is not authorized by this program contract.
- v3.9 release artefacts, tests and behaviour remain the regression oracle throughout v3.10 qualification.

## 4. Scope discipline

Every implementation milestone MUST declare before implementation:

1. exact base commit/tree;
2. exact file/path allowlist;
3. semantic scope and explicit non-goals;
4. authority gained by successful completion;
5. authority explicitly NOT gained;
6. acceptance tests and adversarial vectors;
7. rollback/recovery expectation where state is touched.

Any change outside the accepted allowlist is a hard scope failure. Scope expansion requires a new milestone or explicit re-scope decision; it is not folded into a correction batch.

## 5. Anti-correction-treadmill review policy

Each milestone has a bounded review chain.

### Contract review

- one independent/adversarial review of the frozen contract before implementation;
- at most one bounded correction batch for material findings;
- one closure review restricted to those accepted findings.

### Implementation review

- one independent/adversarial review of the exact implementation candidate;
- at most one bounded correction batch for material findings;
- one closure review restricted to the accepted findings.

If a closure review still finds a material blocker, the milestone does NOT enter another REVIEW -> FIX loop. The disposition is one of:

- `ABORT`;
- `RESCOPE` as a new milestone/contract;
- `DEFER` with the blocker recorded.

New unrelated findings discovered during a closure review are recorded for a successor milestone unless they invalidate safety or custody of the current milestone; in that case the current milestone stops and is re-scoped. No recursive correction chain is permitted.

## 6. Evidence and CI policy

- Candidate identity is bound to exact commit and tree.
- Local deterministic tests/static checks precede remote CI.
- Remote CI is evidence, not a debugging loop.
- Prefer one exact-head CI run per publication candidate; failed CI is diagnosed locally before a new candidate is published.
- Do not rerun unchanged failing CI merely to obtain green status.
- Green CI alone never grants milestone acceptance, merge, runtime authority, experiment authority or release authority.

## 7. Standard gate sequence

For every code-bearing milestone:

`CONTRACT FREEZE`
-> `CONTRACT INDEPENDENT REVIEW`
-> optional single `CONTRACT CORRECTION BATCH`
-> `CONTRACT ACCEPTANCE`
-> `IMPLEMENTATION`
-> `LOCAL VERIFICATION`
-> `IMPLEMENTATION INDEPENDENT REVIEW`
-> optional single `IMPLEMENTATION CORRECTION BATCH`
-> `IMPLEMENTATION ACCEPTANCE`
-> `PUBLICATION / EXACT-HEAD CI`
-> `INTEGRATION-READINESS REVIEW`
-> `READY`
-> separate explicit `MERGE DECISION`
-> `POST-MERGE VERIFICATION`
-> `MILESTONE CLOSE`.

No arrow is automatic. Merge, destructive actions, experiments, provider/private-input use and release remain separate authority boundaries.

## 8. v3.10 Cash Ledger milestones

### CL0 - Program and architecture freeze

Scope: documentation/audit only. Freeze ownership, data model boundaries, milestone map and acceptance rules. No runtime/code changes.

### CL1 - Exact Money and balanced CashLedger core

Pure domain only: exact money representation, immutable transactions/postings, balancing, classifications, idempotency identities, reversal/correction semantics. No filesystem, broker, GUI, runtime activation or persistence.

### CL2 - Append-only persistence and OperationInbox

Versioned local persistence, WAL/integrity rules, append-only transaction custody, sanitized immutable operation observations, deterministic export/backup/isolated restore. No broker transport or active runtime ownership.

### CL3 - Broker read adapters and deterministic classification

Read-only provider boundary, exact codecs, operation classification and source identities. No provider POST and no runtime cash authority.

### CL4 - Opening proof and shadow reconciliation

Create-once opening provenance, explicit opening mode, deterministic reconciliation against broker current cash, discrepancy taxonomy and fail-closed adoption candidate. Shadow only; no automatic adoption.

### CL5 - CashAvailability and reservation projection

Define `available cash = authoritative current cash - accepted internal reservations +/- explicitly accepted adjustments` without changing reservation ownership. Read-only projection first; no broker action.

### CL6 - Reporting and Portfolio Risk cash context

Cash-flow-adjusted reporting/performance and separately gated read-only Risk cash context. No hidden authority transfer to Risk and no dispatch.

### CL7 - Runtime integration, restart/recovery and operator surface

Controlled runtime ownership transition, restart/crash recovery, rollback, GUI/operator observability, kill switches, isolated controlled-clock qualification and state-machine acceptance. Authenticated/private-input qualification remains behind explicit Preparation Stage and exact experiment authorization.

### CL8 - v3.10 Stable qualification and release

Full v3.9 regression oracle, v3.10 acceptance matrix, recovery matrix, standalone/package verification, deterministic artefacts, controlled-clock qualification, burn-in where explicitly authorized, independent release review and explicit stable acceptance.

v3.10 is not considered stable before CL8 closes.

## 9. v4 Portfolio Manager milestones

v4 starts only from the exact accepted v3.10 Stable commit/tag. It MUST NOT start from current `main` or directly from v3.9 once v3.10 exists.

### PM0 - Portfolio Manager contract freeze

Freeze ownership boundaries, portfolio state model, policy/target model, cash/risk dependencies and non-goals.

### PM1 - Canonical read-only portfolio snapshot

Exact position/cash/valuation snapshot with explicit provenance and freshness. No trading decisions or dispatch.

### PM2 - Portfolio policy and constraints

Pure policy model: target allocations, limits, cash floor, concentration/turnover constraints and explicit infeasibility reporting.

### PM3 - Deterministic portfolio planning

Pure explainable rebalance/planning engine producing proposed actions and reasons. No broker calls.

### PM4 - Cash- and Risk-aware planning

Consume accepted v3.10 CashAvailability and Risk context through read-only contracts. Planner remains non-authoritative for broker execution.

### PM5 - Sandbox/operator integration

GUI/operator review, proposal lifecycle, deterministic replay and sandbox-only integration. Any execution handoff is a separate explicit contract and must preserve existing execution ownership.

### PM6 - v4 Stable qualification and release

Regression, restart/recovery, deterministic replay, standalone/package qualification, independent review and explicit stable acceptance.

Direct real-account autonomous portfolio execution is OUT OF SCOPE for v4 unless separately proposed and accepted after PM6.

## 10. M0 exact allowlist and exit criteria

M0 allowlist:

- `docs/plans/V3_10_V4_STABLE_LINE_PROGRAM_RU.md` only.

M0 MUST NOT modify application code, tests, CI, release files, runtime state, existing v3.9 files or historical v3.10 records.

M0 exit requires:

1. exact branch ancestry proven from `v3.9.0 / 412e126...`;
2. this contract reviewed independently/adversarially;
3. no unresolved material contract finding after the bounded review policy;
4. explicit M0 acceptance;
5. only then may CL1 receive its own separate contract/branch.
