# V3.10 Issue #72 — GUI/runtime prerequisite rescope: bounded contract freeze

Статус:

`ISSUE #72 RESCOPE CONTRACT CANDIDATE / IMPLEMENTATION BLOCKED / Q0 BLOCKED`

Родительская программа: v3.10 stable-line → CL8 Stable qualification and release.

Этот документ устраняет четыре material findings финального review Issue #72:

```text
#72-R1-01 account-level configured-set Start/Stop отсутствует
#72-R1-02 GUI read model не показывает полный canonical runtime context
#72-R1-03 основной GUI execution path остаётся legacy single-bot path
#72-R1-04 immutable mechanical/behavioral Q0 evidence отсутствует
```

Contract не является implementation, runtime cutover, provider-access, Sandbox experiment,
Stable acceptance или publication authority.

---

## 1. Exact predecessor и lineage

Issue #72 rescope начинается только от последнего интегрированного CL7 stable-line head:

```text
repository =
baimleriv/unified-portfolio-system

program branch =
program/v3-10-v4-stable-line

exact predecessor commit =
ef2eba758bbffb587dbe237a96372b2273fdee03

exact predecessor tree =
f55788ff362a24d368b0d01dc0d68e85d8183199

contract branch =
agent/v3-10-issue72-gui-runtime-rescope

HEAD at branch creation =
ef2eba758bbffb587dbe237a96372b2273fdee03

merge-base =
ef2eba758bbffb587dbe237a96372b2273fdee03

ahead = 0
behind = 0
worktree = clean
changed paths = 0
```

`main`, PR #177 head `a11cfc1f90055ef29d86606fe8377b4ddc2c10f0`,
historical GUI branches and other parallel lines are not ancestry or integration authority
for Issue #72.

The accepted CL8 contract and accepted qualification-infrastructure result may be used as
external governance evidence. They do not become the base of this branch and do not count
as accepted Issue #72 implementation.

---

## 2. Contract-freeze allowlist

До independent review и explicit acceptance exact contract commit/tree разрешён ровно один
changed path:

```text
docs/project/V3_10_ISSUE72_GUI_RUNTIME_RESCOPE_CONTRACT_RU.md
```

Любой другой added, modified, deleted, renamed или untracked repository path:

```text
SCOPE_VIOLATION -> RESCOPE
```

До contract acceptance запрещены изменения GUI, runtime, tests, fixtures, tools, workflows,
release metadata, documentation вне этого файла и любых runtime/provider states.

---

## 3. Frozen future implementation allowlist

Только после:

1. independent/adversarial contract review exact predecessor → contract head;
2. closure всех material contract findings;
3. explicit acceptance exact contract commit/tree;

разрешается создать отдельную implementation branch непосредственно от exact accepted
contract head.

Implementation delta ограничен ровно 14 paths:

```text
current/desktop_gui.py
current/trading_robot/dashboard_view.py
current/trading_robot/global_scheduler.py
current/trading_robot/instrument_runtime.py
current/trading_robot/gui_runtime_controller.py

current/tools/v3_10_issue72_q0_evidence.py

current/tests/test_v3_10_issue72_gui_runtime.py
current/tests/fixtures/v3_10_issue72_gui_runtime_vectors.json

docs/project/V3_10_ISSUE72_GUI_RUNTIME_REVIEW_RU.md
docs/project/V3_10_ISSUE72_RISK_POLICY_GUI_ADR_RU.md
docs/plans/V3_10_ISSUE72_MULTI_INSTRUMENT_SANDBOX_RUNBOOK_RU.md
docs/plans/V3_10_ISSUE72_SANDBOX_ACCOUNT_CLEANUP_RUNBOOK_RU.md

current/README.md
ROADMAP.md
```

Normative allowlist identity is exact set membership plus exact total count `14`. Any path
outside the listed set is a scope expansion and requires a new explicit `RESCOPE`.
Ordinal descriptions of source, test or documentation groups are non-normative and forbidden.

The following accepted owners are immutable in this milestone:

```text
current/trading_robot/bot.py
current/trading_robot/central_order_coordinator.py
current/trading_robot/central_order_manager.py
current/trading_robot/sandbox_execution_adapter.py
current/trading_robot/runtime_cash_authority.py
current/trading_robot/risk.py
current/trading_robot/risk_runtime.py
current/trading_robot/portfolio.py
current/trading_robot/portfolio_repository.py
current/trading_robot/cash_ledger_domain.py
current/trading_robot/cash_ledger_persistence.py
current/trading_robot/cash_availability.py
```

Issue #72 must consume these owners through their accepted public interfaces. It may not
repair or redefine them implicitly.

---

## 4. Authority boundary

Issue #72 receives bounded authority to implement and qualify a GUI-facing orchestration
and read-model layer for the existing configured multi-instrument Sandbox runtime.

It does not receive authority for:

```text
real-account execution
provider access during implementation/review
provider POST during implementation/review
Sandbox burn-in
Sandbox account deletion
automatic account cleanup
CL7 runtime cutover or arm/disarm
CashLedger/CashAvailability ownership
Central reservation ownership
Portfolio actual/target ownership
RiskPolicy/RiskState ownership
automatic adoption of external positions
release cut
Stable acceptance
tag/GitHub Release/publication
PR #177 Ready or merge
```

Green tests, a committed implementation, CI, a mergeable PR, or Issue #72 acceptance grant
none of these later authorities.

---

## 5. Frozen owner map

Canonical owners remain:

| State or action | Exact owner |
|---|---|
| Configured runtime membership | `MultiInstrumentProfileStore` |
| Per-instrument runtime lifecycle | `InstrumentRuntimeStore` / `InstrumentRuntime` |
| Account-level temporal scheduling | `GlobalScheduler` |
| Strategy proposal | accepted strategy adapter/hooks |
| Actual/target/reconciliation | canonical `PortfolioState` |
| Queue, intent and reservation | `CentralOrderManager` |
| Proposal admission | `CentralOrderCoordinator` |
| Risk authorization/state | accepted Risk services |
| Cash authority/cutover proof | CL7 `RuntimeCashAuthority` |
| Provider mutation | `SandboxExecutionAdapter` only |
| GUI orchestration | new `GuiRuntimeController` |
| GUI rendering | `desktop_gui.py` + `dashboard_view.py` |
| Q0 evidence validation | `v3_10_issue72_q0_evidence.py` |

`GuiRuntimeController` is not a new persisted economic state owner. It coordinates accepted
services, returns immutable result objects and owns only process-local GUI session control.

`dashboard_view.py` performs deterministic joins and formatting. It does not calculate
targets, reservations, reconciliation, Risk decisions, cash availability or execution
authorization.

Tkinter widgets do not read or write authoritative JSON files directly.

---

## 6. Required GUI economic call graph

The only accepted main GUI `SANDBOX_EXECUTION` call graph is:

```text
ConfiguredExecutionSet
        ↓
GuiRuntimeController
        ↓
GlobalScheduler
        ↓
accepted strategy proposal hook
        ↓
CentralOrderCoordinator
        ↓
CentralOrderManager
        ↓
SandboxExecutionAdapter
        ↓
CL7 locked proof / RuntimeCashAuthority
        ↓
Sandbox POST
```

For the main account-level workflow:

```text
desktop_gui.py MUST NOT instantiate SandboxTradingBot
desktop_gui.py MUST NOT import provider POST functions
GuiRuntimeController MUST NOT instantiate SandboxTradingBot
GuiRuntimeController MUST NOT call post_order/post_order_once
GlobalScheduler MUST remain execution-agnostic
provider mutation MUST be reachable only through SandboxExecutionAdapter
```

The historical `SandboxTradingBot` may remain for predecessor recovery compatibility outside
the main account-level workflow. Issue #72 does not delete or rewrite it.

No UI control may create a second proposal, Central intent, reservation or provider call for
the same canonical scheduler decision.

All provider-mutating diagnostic controls inherited by `desktop_gui.py` are removed from the
active GUI or hard-disabled with no command callback. The forbidden transitive sinks are:

```text
SandboxOrderDiagnostics.execute
SandboxOrderDiagnostics.close_unattributed_position
TBankSandboxClient.post_order
TBankSandboxClient.post_order_once
```

Read-only diagnostic snapshots and recovery-only surfaces may remain when they cannot submit,
close or cancel an order. Q0 must inspect Tk command bindings and the committed Python call
graph from every active GUI callback to these sinks. Absence of the literal text
`post_order` in `desktop_gui.py` is insufficient. The immutable
`current/trading_robot/diagnostics.py` remains historical code and receives no new authority.

---

## 7. ConfiguredExecutionSet identity

For this milestone, the complete configured execution set is the ordered tuple returned by
`MultiInstrumentProfileStore.load_mode("SANDBOX_EXECUTION")`.

Canonical ordering:

```text
(account_id, instrument_id, candle_interval, strategy_id, runtime_config_hash)
```

Every configured profile must bind exactly one persisted `InstrumentRuntime`. The full set
must:

- contain between one and three instruments under the accepted v3.8 bound;
- use one exact account scope;
- contain no duplicate instrument or execution-scope identity;
- match profile/runtime configuration hashes;
- contain no orphan runtime for the same mode;
- bind the exact runtime revision read during prevalidation.

A missing, extra, corrupt, cross-account or identity-mismatched member blocks the entire
account-level start.

---

## 8. Account-level Start

The GUI exposes one `Start Sandbox` action for the complete configured set.

Before any runtime becomes newly `ACTIVE`, `GuiRuntimeController.start_configured_set`
must deterministically validate all members:

```text
configured set exists and is complete
same account scope
profile/runtime identity exact
PortfolioState readable and canonical
all configured positions have usable actual/target/reconciliation state
Central state readable
no incompatible Central blocker
no unresolved account-scope mismatch
Risk policy/state readable and ready
non-null same-account PortfolioRiskRuntime bound to coordinator and adapter
no kill-switch/resync blocker
CL7 authority record readable and account scope exact
CL7 authority state = EXACT_CASH_ARMED
CL7 pending_dispatch_proof_sha256 = null
recovery-first obligations resolved
all runtime revisions unchanged
```

Prevalidation is side-effect free.

The compatibility matrix for the main account-level economic
`SANDBOX_EXECUTION` Start is closed:

| CL7 state | Start result |
|---|---|
| `EXACT_CASH_ARMED` with exact account scope and no pending proof | MAY CONTINUE |
| `LEGACY_ACTIVE` | BLOCK: `CL7_EXACT_AUTHORITY_REQUIRED` |
| `CUTOVER_PREPARED` | BLOCK: `CL7_CUTOVER_INCOMPLETE` |
| `CUTOVER_CONFIRMED` | BLOCK: `CL7_CUTOVER_INCOMPLETE` |
| `EXACT_CASH_DISARMED` | BLOCK: `CL7_EXACT_AUTHORITY_DISARMED` |
| `EXACT_CASH_DISPATCH_PENDING` | BLOCK: `CL7_RECOVERY_REQUIRED` |

Issue #72 cannot prepare, confirm, activate, arm, disarm, cancel or roll back CL7 authority.
`EXACT_CASH_ARMED` must already have been established under a separate accepted CL7 gate.

The GUI controller must construct both `CentralOrderCoordinator` and
`SandboxExecutionAdapter` with the same non-null accepted `PortfolioRiskRuntime` instance.
Its account ID must equal the configured set, Central manager, Portfolio repository and
adapter policy account scope. Missing runtime, stale/mismatched identity or unavailable
authoritative quote blocks before proposal admission; dispatch-time mismatch blocks before
the attempt marker and provider call. Optional predecessor constructor parameters do not
make Portfolio Risk optional for this GUI workflow.

The controller then requests one bounded group transition from
`GlobalScheduler.start_configured_set(...)`.

Required all-or-nothing result:

```text
all requested runtimes become ACTIVE
or
zero newly ACTIVE runtimes
```

Partial start is forbidden in v3.10. If runtime 3 of 3 fails prevalidation, runtimes 1 and 2
remain byte-for-value in their pre-call state.

Repeated Start for the exact already-active set is idempotent and creates no duplicate
proposal or provider call. Start for a different set while one set is active fails closed.

---

## 9. Atomic group transition and runtime-store CAS

`GlobalScheduler.start_configured_set` and `stop_configured_set` may extend the existing
execution-agnostic scheduler only. The implementation allowlist opens
`current/trading_robot/instrument_runtime.py` solely for one same-lock CAS boundary.

The public store API is frozen conceptually as:

```python
InstrumentRuntimeStore.compare_and_swap_all(
    *,
    expected: Sequence[InstrumentRuntime],
    successor: Sequence[InstrumentRuntime],
    expected_account_id: str,
) -> tuple[InstrumentRuntime, ...]
```

Equivalent naming is allowed only if the semantics and dedicated tests remain exact.

Inside one `InterProcessFileLock` critical section the store must:

1. load and checksum-verify the current complete registry without releasing or reacquiring
   the lock;
2. normalize `expected`, serialize the complete expected/current registry documents with
   the accepted canonical encoder and require byte-for-value equality; account ID, every
   runtime field, ordered runtime keys, revisions, statuses and configuration hashes are
   therefore inside the comparison;
3. return `GROUP_CAS_MISMATCH` without writing when any value differs;
4. validate the complete `successor` collection and its account scope;
5. perform one accepted atomic full-document replacement;
6. load and checksum-verify the committed registry while the same lock is still held;
7. compare it byte-semantically with the normalized successor set;
8. return the exact committed read-back.

The implementation may factor private unlocked load/write helpers inside
`InstrumentRuntimeStore`; public `save()` compatibility remains intact. Nested acquisition
of the same file lock is forbidden.

The scheduler algorithm is frozen:

1. retain the exact in-memory before-set;
2. prevalidate all members and derive the complete immutable successor set;
3. call `compare_and_swap_all` exactly once with the complete before/successor sets;
4. replace in-memory state only with the exact committed read-back;
5. publish lifecycle events only after successful exact read-back.

A concurrent writer between controller prevalidation and store entry is detected by the
inside-lock expected-set comparison and cannot be overwritten. CAS mismatch yields zero
writes and zero in-memory transition.

A store/write failure reports `GROUP_COMMIT_FAILED`; the scheduler retains its before-set
and performs a fresh read before any retry. It does not guess whether persistence committed.

Any non-exact committed read-back returns `GROUP_POSTCONDITION_FAILED`, blocks scheduling,
and requires recovery. It may not apply a compensating economic action.

No economic lock is held while waiting for GUI confirmation.

---

## 10. Account-level Stop

The GUI exposes one `Stop Sandbox` action for the whole active configured set.

Stop:

- validates the exact active set and account scope;
- transitions the complete set to `STOPPED` through one group operation;
- stops new strategy evaluation and proposal creation;
- does not delete Central intents, reservations, pending/uncertain states or CL7 custody;
- does not cancel provider orders automatically;
- does not rewrite Portfolio, Risk or Cash state;
- is idempotent for an exact already-stopped set.

If unresolved Central/provider state exists, Stop still prevents new work and reports that
recovery remains required. It never reports economic closure merely because scheduler
runtimes are stopped.

---

## 11. Restart, disconnect and market-state semantics

On restart, the controller restores the complete configured set from accepted stores and
generates a new session ID. It does not infer active work from GUI memory.

Recovery ordering:

```text
restore identities
→ inspect Central pending/in-flight/submitted/uncertain
→ inspect CL7 pending dispatch marker/proof
→ reconcile accepted owners
→ unblock scheduler evaluation only when recovery obligations are resolved
```

Pending or uncertain state forces recovery-first and blocks a new proposal for the affected
account scope.

A disconnect:

- does not create a replacement scheduler;
- does not duplicate proposal/intent/provider calls;
- preserves the configured set and exact custody;
- produces bounded non-modal status updates;
- applies accepted retry/backoff only in the service layer.

`OPEN → MARKET_IDLE → OPEN` is account-wide for one scheduler session. During
`MARKET_IDLE`, strategy proposal generation is paused for the complete set; recovery and
bounded status refresh remain allowed. Returning to `OPEN` resumes from persisted
watermarks without replaying a completed candle.

---

## 12. Dashboard read model

Each configured instrument row must expose immutable canonical fields:

```text
account_scope_sha256
instrument_id
ticker
candle_interval
strategy_id
runtime_key
runtime_config_hash
runtime_revision
runtime_status

actual_lots
target_lots
portfolio_revision
reconciliation_status
ownership_status

central_revision
queued_reserved_cash
central_blocking_status
pending_status
in_flight_status
submitted_status
uncertain_status

risk_policy_hash
risk_state_revision
risk_readiness
portfolio_risk_status
kill_switch_status
resync_status

cl7_authority_revision
cl7_authority_mode
cash_actionability_status

source_status
detail
```

Values are copied from accepted owner snapshots. Missing or mismatched owner evidence yields
an explicit `UNKNOWN`, `MISMATCH` or `BLOCKED` status; it never becomes zero, empty
success or READY by default.

`actual_lots` and `target_lots` remain distinct. Two or more simultaneous non-zero
canonical positions must render without collapsing rows or assuming `position_count <= 1`.

Central `QUEUED`, `IN_FLIGHT`, `SUBMITTED` and `UNCERTAIN` are distinct visible
states. A count alone is insufficient.

The dashboard may format accepted `Money` values for display but may not recompute
reservation, cash availability or Risk arithmetic.

---

## 13. GUI reliability rules

Mandatory behavior:

```text
one controller instance per GUI process
one active account-level scheduler session
duplicate refresh paths = 0
provider POST paths in widgets/controller = 0
widget-owned economic calculations = 0
popup storms under repeated transient failures = 0
raw Account ID/token in shareable output = 0
```

Long operations run outside the Tk event thread. Updates are marshalled back through one UI
queue. Repeated equivalent transient failures update one bounded status surface rather than
opening repeated modal dialogs.

The active GUI must not contain hard-coded stale milestone labels
`v3.6/v3.7/v3.8/v3.9`. Product version displayed from canonical package/release metadata is
allowed when it truthfully reflects the installed build. Pre-release UI sections should use
version-neutral functional names.

---

## 14. Risk Policy GUI ADR

The required ADR must select exactly:

```text
OPTION A =
READ_ONLY_GUI_PLUS_EXISTING_ACCEPTED_OPERATOR_TOOLS
```

For v3.10 the GUI may display current Risk policy/state identity, readiness, limits,
kill-switch and resync status through accepted read models.

It must not:

- edit authoritative RiskPolicy;
- write Risk JSON;
- apply policy changes;
- introduce draft/apply/rollback mutation logic;
- become a second Risk owner.

Option B, typed GUI policy editing, is deferred to a separate future milestone.
Option C, direct/live widget mutation, is rejected.

The ADR records this decision and maps each displayed Risk field to its accepted owner.

---

## 15. Sandbox-account disposition and private observation gate

Issue #72 implementation receives no provider-account deletion authority.

Before final Issue #72 acceptance, exactly one terminal disposition must be bound:

```text
REDUNDANT_ACCOUNT_RETAINED_WITH_REASON
or
REDUNDANT_ACCOUNT_CLEANUP_COMPLETED
```

Any fresh private Sandbox account-list observation requires a separately announced
`Preparation Stage` and the user's literal command `START EXPERIMENT`. The implementation,
tests, review and contract acceptance authorize no provider read.

Observation-only disposition uses:

```text
experiment_id =
ISSUE72-SANDBOX-ACCOUNT-DISPOSITION-V1
```

Cleanup uses a different experiment:

```text
experiment_id =
ISSUE72-SANDBOX-ACCOUNT-CLEANUP-V1
```

Cleanup additionally requires a masked/hash preview, one exact operator confirmation for the
specific redundant scope, one supported provider action and fresh post-action read-back.
Authorization for the observation-only experiment never authorizes cleanup.

### 15.1. SanitizedSandboxAccountListV1

The canonical sanitized account-list preimage has exact keys:

```text
version
experiment_id
observed_at
fresh_until
account_count
accounts
provider_response_sha256
raw_identifiers_absent
```

Required constants and constraints:

```text
version = 1
raw_identifiers_absent = true
fresh_until - observed_at <= 300 seconds
provider_response_sha256 = identity calculated outside shareable evidence handling
```

`accounts` is ordered by `account_scope_sha256`. Each entry has exact keys:

```text
account_scope_sha256
status
is_active
is_redundant_candidate
```

No raw Account ID, token, account name or provider payload is permitted.

### 15.2. SandboxAccountDispositionEvidenceV1

The disposition record has exact common keys:

```text
version
disposition
experiment_id
preparation_record_sha256
start_experiment_record_sha256
observed_at
fresh_until
account_list_evidence_sha256
active_account_scope_sha256
redundant_account_scope_sha256
runtime_reference_scan_sha256
configuration_reference_scan_sha256
backup_reference_scan_sha256
acceptance_reference_scan_sha256
open_positions_status
open_orders_status
pending_uncertain_status
raw_identifiers_absent
variant
record_sha256
```

Required invariants:

```text
version = 1
active_account_scope_sha256 != redundant_account_scope_sha256
raw_identifiers_absent = true
all SHA fields = lowercase 64-hex
observed_at/fresh_until = exact values from SanitizedSandboxAccountListV1
record_sha256 = SHA-256 of canonical record with record_sha256 omitted
```

For `REDUNDANT_ACCOUNT_RETAINED_WITH_REASON`, `variant` has exact keys:

```text
reason_code
reason_text_sha256
review_record_sha256
provider_mutation_performed
```

and `provider_mutation_performed = false`. All four reference scans must prove the
redundant scope is unreferenced. The reason code comes from the closed set:

```text
PROVIDER_CLEANUP_UNAVAILABLE
CLEANUP_DEFERRED_BY_OPERATOR
ACCOUNT_RETAINED_FOR_AUDIT
```

For `REDUNDANT_ACCOUNT_CLEANUP_COMPLETED`, `variant` has exact keys:

```text
masked_preview_sha256
operator_confirmation_sha256
provider_action_receipt_sha256
post_action_observed_at
post_account_list_evidence_sha256
active_account_unchanged
redundant_account_absent
review_record_sha256
```

Both booleans must be `true`. The post-action list must independently satisfy
`SanitizedSandboxAccountListV1` and bind the same active scope.

No cleanup occurs during GUI startup, bootstrap, implementation tests, review or Q0 evidence
generation. The Q0 generator consumes and verifies a separately produced disposition record;
it performs no provider call itself.

Raw Account ID is forbidden in Git, logs, reports, screenshots and support bundles. A
synthetic fixture, free-form statement or hash of an untyped text claim cannot establish
either terminal disposition.

---

## 16. Mechanical and behavioral acceptance oracle

The final Issue #72 review evaluates this closed set:

```text
I72-01 exact accepted rescope contract commit/tree
I72-02 implementation delta == exact 14-path allowlist
I72-03 GUI Start operates on complete ConfiguredExecutionSet
I72-04 one failing runtime => zero partial account-level start
I72-05 GUI Stop operates on whole configured set
I72-06 3 synthetic configured instruments visible simultaneously
I72-07 dashboard binds actual + target lots
I72-08 dashboard binds Central reservations and blocking state
I72-09 QUEUED / IN_FLIGHT / SUBMITTED / UNCERTAIN visible
I72-10 reconciliation state visible per instrument
I72-11 Portfolio Risk / Risk halt-resync status visible
I72-12 two simultaneous non-zero canonical positions render correctly
I72-13 GUI economic path does not instantiate SandboxTradingBot
I72-14 GUI/controller has zero direct or transitive reachability to forbidden provider-mutation sinks
I72-15 Strategy proposal reaches CentralOrderCoordinator
I72-16 provider mutation reachable only through SandboxExecutionAdapter
I72-17 main economic Start requires pre-existing EXACT_CASH_ARMED and CL7 dispatch-proof path
I72-18 Central/account/missing-or-mismatched PortfolioRisk blocker prevents proposal and dispatch
I72-19 restart restores configured runtime set without duplicate proposal
I72-20 restart with pending/uncertain performs recovery-first
I72-21 disconnect does not duplicate proposal/intent/provider call
I72-22 OPEN → MARKET_IDLE → OPEN preserves configured set
I72-23 transient refresh failure produces no popup storm
I72-24 account-scope mismatch fails closed
I72-25 GUI has no authoritative RiskPolicy mutation
I72-26 Risk Policy ADR == READ_ONLY_EXISTING_OPERATOR_TOOLS
I72-27 sandbox-account disposition is one closed accepted enum
I72-28 cleanup runbook contains masked preview + confirmation + read-back
I72-29 no raw Account ID/token in GUI/export/Q0 evidence
I72-30 no stale hard-coded v3.6/v3.7/v3.8/v3.9 active GUI labels
I72-31 full predecessor regression has no new semantic failure
I72-32 Q0 evidence binds verified case records/payloads from committed checks, not prefilled PASS
```

Every case is mandatory. There are no wildcards, optional cases, score thresholds or
reviewer-added substitutions. One FAIL or unbound case blocks acceptance.

Offline implementation review uses fakes and synthetic owner snapshots. It performs zero
provider calls and zero provider mutations. Real Sandbox burn-in remains a separate CL8 gate.

---

## 17. Q0 evidence generator trust model

`current/tools/v3_10_issue72_q0_evidence.py` accepts trusted expected values as explicit
caller inputs. It does not learn expected commit/tree/hash/status values from the candidate
report it validates.

It must:

- resolve implementation commit and tree from Git;
- prove accepted contract ancestry;
- compute exact implementation changed paths;
- execute every repository/behavioral producer against the exact committed tree;
- consume external evidence only for the separately gated account disposition;
- verify required documents by bytes and SHA-256;
- verify source-call-graph invariants;
- bind the exact regression result;
- recompute every case-record, payload, manifest and summary hash;
- reject unknown/missing/duplicate fields and case IDs;
- reject self-declared PASS without the exact underlying record and payload;
- never access provider credentials or accounts.

The generator has two modes:

```text
generate = execute committed repository checks and emit case records
verify   = verify exact records/manifest and independently rerun all non-external producers
```

For `EXTERNAL_ACCOUNT_DISPOSITION`, verify mode requires the trusted expected disposition
record SHA-256 as an external argument. It never learns that expected value from the
candidate report.

---

## 18. Canonical evidence encoding

All repository evidence uses canonical JSON bytes:

```text
encoding = UTF-8
BOM = forbidden
object keys = lexicographically sorted
array order = schema-defined
separators = "," and ":"
insignificant whitespace = none
NaN/Infinity = forbidden
terminal newline = none
hash = lowercase SHA-256 hex of exact canonical bytes
```

Evidence values are limited to strings, booleans, integers, arrays and objects. Floating
point values are forbidden.

Each acceptance case result has exact keys:

```json
{
  "case_id": "I72-01",
  "evidence_sha256": "64-lowercase-hex",
  "reason": "bounded machine-readable reason",
  "status": "PASS"
}
```

Allowed status is `PASS` or `FAIL`. Results are sorted by numeric case ID before summary
hashing. Duplicate or unknown IDs fail closed.

`acceptance_case_summary_sha256` is SHA-256 of the canonical ordered array containing all
32 case results.

### 18.1. Q0CaseEvidenceV1

Every `evidence_sha256` references one full canonical record with exact keys:

```text
version
case_id
candidate_commit
candidate_tree
accepted_contract_commit
accepted_contract_tree
producer_kind
producer_id
producer_command_sha256
input_identities
started_at
completed_at
exit_code
result_payload
result_payload_sha256
status
```

Required constants and constraints:

```text
version = 1
case_id = matching I72-xx
candidate commit/tree = exact reviewed implementation
accepted contract commit/tree = trusted external expected values
status = PASS or FAIL
result_payload_sha256 = SHA-256 of canonical result_payload
evidence_sha256 = SHA-256 of the complete Q0CaseEvidenceV1 record
```

Allowed `producer_kind` values are closed:

```text
GIT_CUSTODY
COMMITTED_AST_REACHABILITY
PYTEST_NODE
DOCUMENT_SCHEMA
FULL_REGRESSION
EXTERNAL_ACCOUNT_DISPOSITION
```

`producer_id` is an exact pytest node ID or a frozen tool check name; wildcards and
free-form labels are forbidden. `input_identities` is an ordered array of exact
`{"name": string, "sha256": lowercase-64-hex}` objects.

For repository and behavioral cases, the generator itself executes the producer from the
exact committed tree and constructs `result_payload` from observed counters, identities and
outputs. `result_payload` has exact keys:

```text
assertions
artifact_identities
counters
observations
```

`assertions` is an ordered array of exact
`{"actual": value, "expected": value, "name": string, "passed": boolean}` objects.
`artifact_identities` is an ordered array of exact
`{"name": string, "sha256": lowercase-64-hex}` objects. `counters` and
`observations` use the closed per-case keysets declared by the tool's immutable
`I72-01..32` producer table; unknown or missing keys fail. Behavioral producer schemas must
include the applicable call counters for proposal, Central intent, Risk admission, Risk
dispatch validation, adapter dispatch, provider mutation, active runtimes and popup events.

A fixture-provided `status`, a payload equal to `"PASS"`, a manually supplied success
boolean, or a record whose producer did not execute against the bound commit/tree is invalid.

For `EXTERNAL_ACCOUNT_DISPOSITION`, `result_payload` contains the complete verified
`SandboxAccountDispositionEvidenceV1`; its record hash must equal the trusted external
argument.

### 18.2. Q0CaseEvidenceManifestV1

The manifest has exact keys:

```text
version
candidate_commit
candidate_tree
accepted_contract_commit
accepted_contract_tree
records
manifest_sha256
```

`records` contains exactly 32 entries sorted by case ID. Each entry has exact keys:

```text
case_id
evidence_sha256
producer_kind
producer_id
result_payload_sha256
```

`manifest_sha256` is SHA-256 of the canonical manifest with that field omitted. The
verifier receives the manifest and all 32 complete records, recomputes every hash, checks
candidate/contract bindings and reruns every producer except the externally gated account
observation/action.

`acceptance_case_results[].evidence_sha256` must equal the corresponding manifest and
record identity. Missing record bytes, inaccessible payload, substitution, cross-candidate
reuse or producer mismatch fails closed.

---

## 19. Q0 candidate schema

The generator emits one object with exact keys:

```text
version
issue
implementation_commit
implementation_tree
accepted_contract_commit
accepted_contract_tree
rescope_contract_sha256
gui_audit_sha256
risk_policy_adr_sha256
multi_instrument_runbook_sha256
account_cleanup_runbook_sha256
acceptance_case_results
acceptance_case_summary_sha256
case_evidence_manifest_sha256
account_disposition
full_regression_result
provider_calls_performed
provider_mutations_performed
generated_at
q0_candidate_sha256
```

Required constants:

```text
version = 1
issue = 72
provider_calls_performed = false
provider_mutations_performed = false
```

`generated_at` is an RFC3339 UTC value supplied by a controlled clock.

`q0_candidate_sha256` is computed from the canonical object with the
`q0_candidate_sha256` field omitted. The field is then inserted without changing any
other value.

`case_evidence_manifest_sha256` binds the exact verified
`Q0CaseEvidenceManifestV1`.

`account_disposition` contains exact keys `disposition` and
`disposition_evidence_sha256`. The hash must identify a verified
`SandboxAccountDispositionEvidenceV1` supplied under the external trust rule. It contains
no raw account identifier.

---

## 20. Source and call-graph checks

The acceptance tool must inspect the exact committed implementation tree, not mutable
working-tree files.

Minimum static invariants:

```text
desktop_gui.py:
  SandboxTradingBot constructor in main account-level path = 0
  post_order/post_order_once calls = 0
  active command reachability to SandboxOrderDiagnostics.execute = 0
  active command reachability to SandboxOrderDiagnostics.close_unattributed_position = 0
  authoritative JSON direct writes from widgets = 0

gui_runtime_controller.py:
  SandboxTradingBot imports/constructors = 0
  post_order/post_order_once calls = 0
  direct provider client construction = 0
  CentralOrderCoordinator binding = exactly 1 accepted boundary
  SandboxExecutionAdapter binding = exactly 1 accepted boundary
  PortfolioRiskRuntime binding = non-null and same exact instance in both boundaries
  accepted CL7 Start state = EXACT_CASH_ARMED only

global_scheduler.py:
  provider imports/calls = 0
  Risk/Cash calculation ownership = 0
  group persistence calls per transition = exactly 1 CAS call

instrument_runtime.py:
  expected-set comparison occurs inside the same lock as write
  CAS mismatch writes = 0
  exact successor read-back occurs before lock release
```

The tool parses the committed Python AST, resolves Tk `command=` bindings and follows
bounded calls through methods in the 14-path implementation set plus the named immutable
diagnostic/provider sinks. A disabled button with a reachable mutation callback still fails.
Removing only the literal text `post_order` does not pass.

Static checks supplement behavioral tests. The dedicated test must exercise injected fakes
and count proposal, Central intent, Risk admission/dispatch validation, adapter dispatch and
provider-boundary calls. Missing or mismatched `PortfolioRiskRuntime` must produce zero
proposal admission and zero dispatch.

---

## 21. Regression identity

The Issue #72 full predecessor regression runs against the exact implementation head.

The only dispositioned non-semantic custody failures before CL8 adoption are:

```text
tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_28_three_path_delta_and_immutable_predecessor_files
tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_17_exact_three_path_delta
tests/test_v3_10_runtime_cash_cutover_recovery.py::test_exact_implementation_allowlist
tests/test_v3_10_cash_ledger_opening_reconciliation.py::test_v310_cl4_20_three_path_delta_and_predecessor_custody
tests/test_v3_10_cash_availability.py::test_exact_successor_custody_and_three_path_delta
tests/test_v3_10_reporting_risk_cash_context.py::test_v310_cl6_01_exact_contract_lineage_and_three_path_delta
```

Acceptance rule:

```text
actual failures == exact six-node set above
or
a listed custody node passes because its historical allowlist context is absent,
provided no new failure appears and the review records the exact reduction

new semantic failure = 0
unexpected failure = 0
missing test collection = 0
```

A disappearing known failure never compensates for a new failure.

---

## 22. Required implementation documents

`V3_10_ISSUE72_GUI_RUNTIME_REVIEW_RU.md` must map every identified GUI/runtime item to:

```text
KEEP / REFACTOR / REMOVE / DEFER
accepted data owner
mutation boundary
implementation path
test/evidence ID
```

It must cover single-instrument assumptions, stale labels, direct runtime-file access,
provider clients, refresh paths, modal errors, computed widget state and actions that may
change execution/Risk state.

The multi-instrument runbook defines offline preparation and the separately gated live
Sandbox sequence. It must not contain credentials or claim that implementation tests are a
live run.

The account-cleanup runbook freezes both terminal dispositions and the exact experiment
gate without performing the experiment.

`current/README.md` and `ROADMAP.md` may describe the accepted final behavior and
remaining provider/experiment/release gates only after implementation exists.

---

## 23. Dedicated test requirements

The one dedicated test file and fixture must cover:

- exact branch/contract/14-path custody;
- group start success for three instruments;
- each prevalidation failure with zero partial start;
- concurrent-writer CAS mismatch with zero lost update and zero partial start;
- group stop and unresolved custody preservation;
- exact committed read-back and non-exact read-back failure;
- two non-zero canonical positions;
- complete dashboard owner bindings and missing-owner fail-closed cases;
- Central lifecycle states;
- non-null same-instance PortfolioRisk binding and missing/mismatched failure;
- every CL7 state in the closed Start compatibility matrix;
- restart, pending/uncertain recovery-first and disconnect;
- account-wide `OPEN → MARKET_IDLE → OPEN`;
- duplicate proposal/intent/provider counters;
- popup coalescing;
- cross-account rejection;
- read-only Risk ADR;
- account-disposition validation;
- privacy/redaction;
- committed AST/Tk callback reachability checks for transitive diagnostic sinks;
- Q0 case-record, payload, manifest and cross-candidate substitution tests;
- full exact oracle `I72-01..32`.

Fixtures provide inputs and negative vectors. They may not contain a prefilled object whose
booleans alone substitute for executing the relevant behavior.

---

## 24. Review and acceptance sequence

Contract sequence:

```text
one contract-only commit
→ exact custody check
→ independent/adversarial exact-range review
→ bounded correction only if separately authorized
→ finding-scoped closure review
→ explicit contract acceptance commit/tree
```

Implementation sequence:

```text
isolated implementation branch from exact accepted contract head
→ HEAD = merge-base / 0 ahead / 0 behind / clean
→ implementation only in 14 paths
→ local deterministic tests and full regression
→ one implementation commit or coherent bounded series
→ independent/adversarial exact-range review
→ bounded correction only if separately authorized
→ closure review
→ external review evidence record
→ explicit Issue #72 acceptance
```

Explicit Issue #72 acceptance must bind:

```text
implementation commit
implementation tree
Q0 candidate SHA-256
independent review verdict = PASS
independent review evidence SHA-256
material findings = 0
terminal Sandbox-account disposition
```

If any material finding survives, disposition is
`REQUEST_CHANGES / RESCOPE / ABORT / DEFER`; acceptance is forbidden.

---

## 25. Stable-line and CL8 adoption order

After explicit Issue #72 acceptance, integration is a separate decision.

Required order:

```text
stable-line ef2eba758...
→ accepted Issue #72 contract
→ accepted Issue #72 implementation
→ non-force stable-line integration
→ post-integration exact read-back

new CL8 qualification-adoption branch
→ created from new exact stable-line head
→ mechanically reapply accepted a11cfc1f... qualification delta
→ exact custody and regression review
→ Q0 binds accepted Issue #72 evidence
→ only then reconsider PR #177 replacement/closure/Ready strategy
```

The current PR #177 remains:

```text
DRAFT
NOT READY
NOT MERGE AUTHORIZED
evidence/reference only until accepted Issue #72 disposition
```

No rebase, merge, retarget, close/reopen or body update of PR #177 is authorized by this
contract.

---

## 26. Contract correction custody

The independent contract review of exact candidate
`51e9f98864b11dbe4e99142c268c421fd5069c1a` fixed the closed finding set:

```text
I72-C-R1-01 GROUP_RUNTIME_CAS_BOUNDARY_MISSING
I72-C-R1-02 TRANSITIVE_GUI_PROVIDER_BYPASS_NOT_CLOSED
I72-C-R1-03 MAIN_GUI_CL7_AUTHORITY_STATE_MATRIX_UNFROZEN
I72-C-R1-04 PORTFOLIO_RISK_BINDING_REMAINS_OPTIONAL
I72-C-R1-05 SANDBOX_ACCOUNT_DISPOSITION_EVIDENCE_UNBOUND
I72-C-R1-06 PER_CASE_Q0_EVIDENCE_SUBSTITUTION_GAP
I72-C-R1-07 IMPLEMENTATION_ALLOWLIST_CARDINALITY_CONTRADICTION
```

One bounded contract-only correction batch is permitted with:

```text
correction parent =
51e9f98864b11dbe4e99142c268c421fd5069c1a

allowed changed path =
docs/project/V3_10_ISSUE72_GUI_RUNTIME_RESCOPE_CONTRACT_RU.md

all other paths =
IMMUTABLE
```

This batch also corrects the non-material CashLedger filename inventory. It grants no
implementation/provider/experiment/GitHub authority.

After the correction commit, contract correction budget is exhausted. Review is limited to
finding-scoped closure `I72-C-R1-01..07`. A surviving material finding produces
`RESCOPE / ABORT / DEFER`; it does not open a recursive correction loop.

---

## 27. Frozen disposition

Until all later gates are separately completed:

```text
ISSUE #72 FINAL REVIEW = REQUEST_CHANGES
ISSUE #72 RESCOPE = AUTHORIZED
ISSUE #72 CONTRACT = CANDIDATE / NOT ACCEPTED
ISSUE #72 IMPLEMENTATION = BLOCKED
Q0 = BLOCKED
PR #177 = DRAFT / NOT READY
CL8 RELEASE CUT = BLOCKED
PROVIDER ACCESS = NOT AUTHORIZED
SANDBOX ACCOUNT CLEANUP = NOT AUTHORIZED
SANDBOX BURN-IN = NOT AUTHORIZED
REAL-ACCOUNT EXECUTION = FORBIDDEN
STABLE ACCEPTANCE = NOT GRANTED
TAG / GITHUB RELEASE = NOT AUTHORIZED
```

Any later authority must name the exact accepted commit/tree and the exact bounded action.