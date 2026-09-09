# v3.10 Cash Ledger -> v4 Portfolio Manager: stable-line program contract

Status: `M0 CONTRACT CORRECTION CANDIDATE / CL0-R1-01..05`

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

- The accepted v3.9 ownership model remains authoritative until an explicitly accepted CL7 cutover changes it.
- `PortfolioRepository` remains the canonical actual position/cash/ownership snapshot owner before that cutover.
- `CentralOrderManager` remains the single owner of queue, reservations and intents before that cutover. Neither CashLedger, CashAvailability, Portfolio Risk, reporting nor GUI may become reservation owner implicitly.
- `ExecutionAdapter` remains the single provider-transport boundary before any separately accepted successor contract changes that boundary.
- Broker current cash has exactly one explicit authoritative owner at every runtime stage. CashLedger evidence is not automatically broker truth and cannot acquire current-cash ownership through observation, reconciliation, reporting or migration capability alone.
- Any execution authorization that consumes cash/Risk state must be bound to one explicit proof set and revalidated immediately before provider POST/economic mutation; stale, mixed, missing or drifted proof fails closed to zero mutation.
- Restart/recovery MUST NOT automatically resubmit, replay an economic mutation, silently repair authoritative state or convert an ambiguous provider outcome into success.
- Canonical exact-money state MUST be provider-compatible and preserve the complete T-Bank `MoneyValue` value without loss of `nano`: canonical scale 9 integer minor units or an exactly equivalent `(units, nano)` representation. Currency, precision/scale and money schema/version are part of canonical semantic identity. Binary floating point, Decimal formatting inference and silent rounding are forbidden in authoritative identities.
- Unknown schema/version/currency/codec/custody fails closed.
- Read-only/reporting milestones MUST NOT perform provider POST, broker dispatch, economic mutation, migration/adoption, or hidden state repair.
- Provider/private-account access, authenticated observation and experiments require their own explicit preparation and experiment gate.
- Real-account execution is not authorized by this program contract.
- v3.9 release artefacts, tests and behaviour remain the regression oracle throughout v3.10 qualification.

## 4. Scope discipline

Every implementation milestone MUST declare before implementation:

1. exact accepted predecessor commit/tree and the exact branch base;
2. exact file/path allowlist;
3. semantic scope and explicit non-goals;
4. authority gained by successful completion;
5. authority explicitly NOT gained;
6. acceptance tests and adversarial vectors;
7. rollback/recovery expectation where state is touched;
8. concurrency/revision/custody assumptions wherever more than one state owner, process or external observation can participate;
9. privacy/redaction boundary wherever provider/private data can be observed.

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

### Integration-readiness review

Integration-readiness is NOT another general implementation review and MUST NOT reopen an accepted implementation correction cycle. It is restricted to publication/integration facts:

- exact accepted head/tree and predecessor/base/merge-base identity;
- changed-file allowlist and accepted artifact/source identity;
- exact-head CI identity/result;
- dependency, import/call-graph or integration drift introduced by publication/baseline movement;
- preservation of the accepted authority boundary.

If integration-readiness discovers a new material code/design defect, semantic mismatch or safety defect, the milestone disposition is `RESCOPE`, `ABORT` or `DEFER`; no second implementation correction batch is authorized.

## 6. Evidence, custody and CI policy

- Candidate identity is bound to exact commit and tree.
- Acceptance authority is carried by committed contract/evidence plus an accepted exact commit/tree identity. Exact-SHA CI and external observations may support that authority but do not replace it.
- PR and Issue bodies are mutable status mirrors, not canonical acceptance sources. Self-referential body hashes, recursive acceptance-successor chains and paired Issue/umbrella read-back protocols are not mandatory acceptance mechanisms for this stable line.
- Local deterministic tests/static checks precede remote CI.
- Remote CI is evidence, not a debugging loop.
- Prefer one exact-head CI run per publication candidate; failed CI is diagnosed locally before a new candidate is published.
- Do not rerun unchanged failing CI merely to obtain green status.
- Green CI alone never grants milestone acceptance, merge, runtime authority, experiment authority or release authority.

## 7. Standard gate sequence and stable-line topology

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
-> bounded `INTEGRATION-READINESS REVIEW`
-> `READY`
-> separate explicit `MERGE DECISION`
-> `POST-MERGE VERIFICATION`
-> `MILESTONE CLOSE`.

No arrow is automatic. Merge, destructive actions, experiments, provider/private-input use and release remain separate authority boundaries.

Stable-line development is strictly successor-linear:

`v3.9.0`
-> accepted/integrated `CL0`
-> accepted/integrated `CL1`
-> accepted/integrated `CL2`
-> `CL3`
-> `CL4`
-> `CL5`
-> `CL6`
-> `CL7`
-> `CL8 / v3.10 Stable`
-> `PM0`
-> `PM1`
-> `PM2`
-> `PM3`
-> `PM4`
-> `PM5`
-> `PM6 / v4 Stable`.

After each milestone closes, `program/v3-10-v4-stable-line` advances only to that exact accepted integrated head. A successor branch MUST be created from that exact accepted predecessor head/tree. It MUST NOT be created from the historical program root, an earlier milestone, contemporary `main` or an unaccepted candidate.

## 8. v3.10 Cash Ledger milestones

### CL0 - Program and architecture freeze

Scope: documentation/audit only. Freeze ownership, data model boundaries, milestone map and acceptance rules. No runtime/code changes.

### CL1 - Provider-exact Money and balanced CashLedger core

Pure domain only: provider-compatible exact T-Bank `MoneyValue` representation preserving `units+nano` without rounding; canonical currency + scale/precision + schema/version identity; immutable transactions/postings; per-currency balancing; versioned classifications; source/economic idempotency identities; deterministic serialization/hashes; and strict original -> reversal -> correction lineage semantics. No filesystem, broker, GUI, runtime activation or persistence.

### CL2 - Append-only persistence and OperationInbox

Versioned local persistence, WAL/integrity rules, append-only transaction custody, separate revision/CAS semantics, sanitized immutable operation observations, deterministic export/backup/isolated restore, crash/fault boundaries and no-clobber output custody. No broker transport or active runtime ownership.

### CL3 - Broker read adapters and deterministic classification

Read-only provider boundary, exact codecs, operation classification and source identities, bounded pagination/retry/completeness telemetry, absolute timeout/deadline semantics and privacy-safe error/evidence paths. No provider POST and no runtime cash authority.

### CL4 - Opening proof and shadow reconciliation

Create-once opening provenance, explicit opening mode, one-response/proof binding where applicable, deterministic reconciliation against broker current cash, discrepancy taxonomy, locked pre-migration semantic revalidation and fail-closed adoption candidate. Shadow only; no automatic adoption.

### CL5 - CashAvailability and reservation projection

Define one immutable, read-only CashAvailabilitySnapshot from a consistent proof boundary. At minimum it binds canonical broker-cash revision/checksum/as-of, `CentralOrderManager` reservation revision/projection hash, CashLedger revision/watermark, reconciliation identity/status, broker available/blocked amounts, local reservations, pending inflows/outflows, unclassified delta and a deterministic snapshot checksum.

CL5 MUST prove the semantics and overlap of broker available/blocked cash with local reservations before deriving free/investable cash. No universal subtraction formula is presumed. Unknown overlap, stale/mixed revisions, account/currency mismatch or unresolved reconciliation fails closed to an explicit non-actionable/manual-review disposition. Pending inflow MUST NOT finance a BUY before accepted final settlement. The projection remains non-owning and read-only.

Any later economic-mutation path that consumes CashAvailability MUST bind the exact snapshot proof and revalidate the complete relevant cash/reservation/Risk proof immediately before mutation; any drift yields zero mutation.

### CL6 - Reporting and Portfolio Risk cash context

Cash-flow-adjusted reporting/performance and separately gated read-only Risk cash context. External flows are not strategy P&L; trade principal is not an external contribution. Reporting failure does not itself authorize execution. No hidden authority transfer to Risk and no dispatch.

### CL7 - Runtime integration, restart/recovery and operator surface

Controlled runtime ownership transition from the explicitly named v3.9 owners, restart/crash recovery, rollback, GUI/operator observability, kill switches, isolated controlled-clock qualification and state-machine acceptance. The cutover contract MUST enumerate pre-cutover and post-cutover owners and prove that no interval has zero or multiple current-cash/reservation/execution owners. Restart performs no automatic resubmit or silent adoption. Authenticated/private-input qualification remains behind explicit Preparation Stage and exact experiment authorization.

### CL8 - v3.10 Stable qualification and release

Full v3.9 regression oracle, v3.10 acceptance matrix, corruption/restart/recovery matrix, clean install/upgrade/rollback, standalone/package verification, deterministic artefacts, privacy/support-bundle scan, controlled-clock qualification, multi-instrument Sandbox burn-in where explicitly authorized, independent release review and explicit stable acceptance. Qualification must prove zero duplicate submit/cash effect/reservation, zero stale-proof dispatch, and zero fill lacking canonical reconciliation and Risk accounting.

v3.10 is not considered stable before CL8 closes.

## 9. v4 Portfolio Manager milestones

v4 starts only from the exact accepted v3.10 Stable commit/tag. It MUST NOT start from current `main` or directly from v3.9 once v3.10 exists.

### PM0 - Portfolio Manager contract freeze

Freeze ownership boundaries, portfolio state model, policy/target model, cash/risk dependencies and non-goals.

### PM1 - Canonical read-only portfolio snapshot

Exact position/cash/valuation snapshot with explicit provenance, revision/checksum binding and freshness. No trading decisions or dispatch.

### PM2 - Portfolio policy and constraints

Pure policy model: target allocations, limits, cash floor, concentration/turnover constraints and explicit infeasibility reporting.

### PM3 - Deterministic portfolio planning

Pure explainable rebalance/planning engine producing proposed actions and reasons. No broker calls.

### PM4 - Cash- and Risk-aware planning

Consume accepted v3.10 CashAvailability and Risk context through read-only contracts. Planner remains non-authoritative for broker execution and cannot reuse stale planning evidence as execution authorization.

### PM5 - Sandbox/operator integration

GUI/operator review, proposal lifecycle, deterministic replay and sandbox-only integration. Any execution handoff is a separate explicit contract and must preserve existing execution ownership and fresh pre-POST authorization semantics.

### PM6 - v4 Stable qualification and release

Regression, restart/recovery, deterministic replay, standalone/package qualification, independent review and explicit stable acceptance.

Direct real-account autonomous portfolio execution is OUT OF SCOPE for v4 unless separately proposed and accepted after PM6.

## 10. M0 exact allowlist and exit criteria

M0 allowlist:

- `docs/plans/V3_10_V4_STABLE_LINE_PROGRAM_RU.md` only.

M0 MUST NOT modify application code, tests, CI, release files, runtime state, existing v3.9 files or historical v3.10 records.

The only authorized CL0 correction batch is the single batch closing the fixed independent-review set `CL0-R1-01..05`. Its closure review is restricted to those five IDs. No additional CL0 correction batch is authorized.

M0 exit requires:

1. exact branch ancestry proven from `v3.9.0 / 412e126...`;
2. independent/adversarial review completed with fixed set `CL0-R1-01..05`;
3. the one authorized correction batch remains inside the M0 allowlist;
4. finding-scoped closure review resolves all five findings;
5. if any of `CL0-R1-01..05` remains material, disposition is `RESCOPE` rather than another correction round;
6. explicit M0 acceptance;
7. CL0 integration into `program/v3-10-v4-stable-line` as the exact accepted successor to v3.9;
8. only then may CL1 receive its own contract/branch from the exact accepted CL0 head.
