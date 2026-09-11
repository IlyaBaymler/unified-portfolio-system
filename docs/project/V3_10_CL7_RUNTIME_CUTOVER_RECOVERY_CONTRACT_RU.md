# V3.10 CL7 — Runtime cash-authority cutover, locked revalidation and recovery: bounded contract freeze

Статус:

`CL7 CONTRACT FREEZE CANDIDATE / IMPLEMENTATION BLOCKED / CUTOVER NOT AUTHORIZED`

Parent program: stable-line v3.10 → v4.

Historical evidence: stable-line program contract, accepted CL1–CL6, historical Issues #55–#56, accepted v3.9 Portfolio/Central/Risk/Sandbox execution and recovery boundaries.

Accepted predecessor:

`CL6 = ACCEPTED / INTEGRATED / COMPLETED`.

---

## 1. Exact predecessor и lineage

CL7 contract work начинается только от exact accepted/integrated CL6 stable-line head:

```text
repository =
baimleriv/unified-portfolio-system

program branch =
program/v3-10-v4-stable-line

exact predecessor commit =
2667dbea770a1a5df25bbba67f0e7e8b5f27dc63

exact predecessor tree =
6fd9b24bb1c4a05d82c3e5f96e043bb5011550b2

contract branch =
agent/v3-10-clean-cl7-contract-freeze

HEAD at branch creation =
2667dbea770a1a5df25bbba67f0e7e8b5f27dc63

merge-base =
2667dbea770a1a5df25bbba67f0e7e8b5f27dc63

ahead = 0
behind = 0
worktree = clean
changed paths = 0
```

Допустимая ancestry:

```text
v3.9 oracle
-> accepted CL0
-> accepted CL1
-> accepted CL2
-> accepted CL3
-> accepted CL4
-> accepted CL5
-> accepted CL6
-> CL7 contract candidate
```

`main`, historical v3.10/MoneyV2 branches и historical implementations являются только evidence/reference и не являются ancestry или integration authority.

---

## 2. Contract-freeze allowlist

До отдельного exact-head CL7 contract acceptance разрешено менять ровно один repository path:

```text
docs/project/V3_10_CL7_RUNTIME_CUTOVER_RECOVERY_CONTRACT_RU.md
```

Любой другой changed, added, deleted, renamed или untracked repository path:

```text
SCOPE_VIOLATION -> RESCOPE
```

На contract-freeze этапе запрещены изменения application code, tests, fixtures, workflows, release files и runtime state.

---

## 3. Frozen future implementation allowlist

Только после:

1. independent/adversarial CL7 contract review;
2. closure всех material contract findings;
3. explicit acceptance exact contract commit/tree;

может быть создана отдельная implementation branch непосредственно от accepted CL7 contract head.

Future implementation delta ограничен ровно следующими repository paths:

```text
current/trading_robot/runtime_cash_authority.py

current/trading_robot/tbank_sandbox.py

current/trading_robot/central_order_manager.py

current/trading_robot/sandbox_execution_adapter.py

current/trading_robot/bot.py

current/trading_robot/diagnostics.py

current/trading_robot/runtime_bootstrap.py

current/run_bot.py

current/tools/v3_10_runtime_cash_cutover.py

current/tests/test_v3_10_runtime_cash_cutover_recovery.py

current/tests/fixtures/v3_10_runtime_cash_cutover_vectors.json
```

Accepted CL7 contract immutable во время implementation.

Все иные predecessor paths immutable.

Максимальный cumulative CL7 surface относительно exact CL6 predecessor:

```text
1 contract path
+
11 implementation paths
=
12 paths
```

Любой двенадцатый implementation path либо изменение predecessor path вне списка:

```text
SCOPE_EXPANSION_REQUIRED -> RESCOPE
```

Implementation allowlist не разрешает GUI, release packaging, CI/workflow, support-bundle, standalone или historical test rewrites.

---

## 4. Why CL7 implementation surface is wider than CL1–CL6

CL1–CL6 были domain/persistence/read-only milestones.

На exact CL6 predecessor:

- CL2 CashLedger persistence существует, но не является active runtime pipeline;
- CL3 broker operation adapter существует, но не подключён к main execution loop;
- CL4/CL5/CL6 строят exact proofs, но не владеют execution decision boundary;
- legacy `SandboxTradingBot` имеет direct Sandbox order path;
- controlled `SandboxExecutionAdapter` является отдельной Central-based order path;
- diagnostics имеет отдельный explicitly confirmed Sandbox order path.

Поэтому CL7 обязан не создать четвёртый путь, а выполнить bounded convergence:

```text
CL1–CL6 exact evidence
        ↓
one RuntimeCashAuthority decision boundary
        ↓
one exact Central/Sandbox dispatch handoff
```

В exact mode direct legacy/diagnostic POST paths должны fail closed.

---

## 5. Bounded mission

CL7 реализует только:

1. persistent cash-authority cutover state;
2. exact owner transition from legacy cash authorization to CL1–CL6 exact evidence;
3. runtime CL3 → CL2 operation synchronization;
4. create/reuse CL4 FROM_NOW opening;
5. fresh CL4 reconciliation;
6. fresh CL5 CashAvailability;
7. fresh CL6 PortfolioRiskCashContext;
8. final local locked revalidation;
9. one persistent pre-POST attempt marker;
10. one Sandbox submission handoff through `SandboxExecutionAdapter`;
11. restart/recovery semantics with no automatic resubmit;
12. operator CLI for status/cutover/arm/disarm/rollback/recovery/one dispatch;
13. synthetic/controlled-clock state-machine qualification.

---

## 6. Explicit non-goals

CL7 does NOT include:

- v3.10 Stable release/tag;
- 24–48 h Sandbox burn-in;
- release artifact/package qualification;
- standalone-without-system-Python qualification;
- clean-install/upgrade/rollback release matrix;
- support-bundle release review;
- GUI implementation;
- automatic GlobalScheduler → broker dispatch;
- autonomous multi-instrument execution;
- production/real-account execution;
- automatic migration of historical MoneyV1/MoneyV2 state;
- automatic repair of corrupt authoritative state;
- automatic provider resubmit;
- hidden fallback from exact authority to legacy authority;
- a second CashLedger;
- a second Central reservation owner;
- a second provider mutation owner in exact mode.

Those Stable qualification/release activities remain CL8 unless separately re-scoped.

---

## 7. Authority gained only after separate runtime activation

CL7 code acceptance and merge do not activate exact cash authority.

Repository integration grants only implementation availability.

Actual runtime activation requires the CL7 operator state machine.

Actual authenticated Sandbox access additionally requires:

```text
Preparation Stage
+
explicit user experiment authorization
```

No contract, implementation, merge or CI result is itself provider/private-input authority.

Real-account execution remains forbidden.

---

# PART A — OWNERSHIP AND STATE MACHINE

## 8. Pre-cutover ownership

Before exact cutover:

```text
actual positions / ownership =
PortfolioRepository

spendable cash for legacy Risk/BUY =
legacy Portfolio / predecessor runtime cash path

CashLedger =
non-owning evidence

CashAvailability =
non-owning proof

PortfolioRiskCashContext =
non-owning proof

reservations / queue / intents =
CentralOrderManager

Risk state / policy =
existing Risk stores/runtime

provider order mutation =
accepted predecessor Sandbox execution paths
```

---

## 9. Post-cutover ownership

After exact cutover:

```text
actual positions / ownership =
PortfolioRepository

spendable cash for any new exact-mode execution decision =
RuntimeCashAuthority
using fresh CL1–CL6 proof set

CashLedger =
non-owning authoritative evidence input

CashAvailability =
non-owning derived proof

PortfolioRiskCashContext =
mandatory pre-POST proof, still not owner itself

reservations / queue / intents =
CentralOrderManager

Risk state / policy =
existing Risk stores/runtime

provider order mutation =
SandboxExecutionAdapter only
```

`RuntimeCashAuthority` owns the decision boundary, not a second mutable cash balance.

---

## 10. Permanent single-owner invariant

For every valid durable CL7 state exactly one cash-for-execution authority is defined.

Mapping:

```text
LEGACY_ACTIVE
CUTOVER_PREPARED
CUTOVER_CONFIRMED
    -> LEGACY_CASH_AUTHORITY

EXACT_CASH_DISARMED
EXACT_CASH_ARMED
EXACT_CASH_DISPATCH_PENDING
    -> CL7_EXACT_CASH_AUTHORITY
```

`CUTOVER_PREPARED` and `CUTOVER_CONFIRMED` keep legacy ownership but freeze new provider mutation.

`EXACT_CASH_DISARMED` owns exact cash authority while provider execution is disabled.

There is no state in which both legacy cash and exact cash may authorize one new order.

---

## 11. Effective recovery-blocked state

Malformed/missing-required/corrupt CL7 authority custody yields an effective non-serialized state:

```text
RECOVERY_BLOCKED
```

Its semantics:

```text
cash-for-execution decision owner =
CL7 recovery guard

actionable cash =
NONE

provider mutation =
FORBIDDEN
```

This state exists only to deny economic mutation until custody is resolved.

It MUST NOT silently become `LEGACY_ACTIVE`.

---

## 12. Persistent state enum

Version 1 exact closed set:

```text
LEGACY_ACTIVE

CUTOVER_PREPARED

CUTOVER_CONFIRMED

EXACT_CASH_DISARMED

EXACT_CASH_ARMED

EXACT_CASH_DISPATCH_PENDING
```

Adding a durable state requires version transition or CL7 rescope.

---

## 13. Exact state transitions

Allowed transitions:

```text
LEGACY_ACTIVE
-> CUTOVER_PREPARED

CUTOVER_PREPARED
-> CUTOVER_CONFIRMED

CUTOVER_PREPARED
-> LEGACY_ACTIVE
   [cancel preparation]

CUTOVER_CONFIRMED
-> EXACT_CASH_DISARMED

CUTOVER_CONFIRMED
-> LEGACY_ACTIVE
   [cancel before activation]

EXACT_CASH_DISARMED
-> EXACT_CASH_ARMED

EXACT_CASH_ARMED
-> EXACT_CASH_DISARMED

EXACT_CASH_ARMED
-> EXACT_CASH_DISPATCH_PENDING

EXACT_CASH_DISPATCH_PENDING
-> EXACT_CASH_ARMED
   [only after safe resolution / no account blocker]

EXACT_CASH_DISPATCH_PENDING
-> EXACT_CASH_DISARMED
   [operator recovery closure]

EXACT_CASH_DISARMED
-> LEGACY_ACTIVE
   [bounded rollback only when post_attempt_count == 0]
```

Every other transition:

```text
STATE_TRANSITION_INVALID
```

### 13.1 Exact `transition_kind` and record-mutation table

Version 1 `transition_kind` is the following closed set:

```text
BOOTSTRAP_LEGACY
PREPARE_CUTOVER
PREPARATION_EVIDENCE_BOUND
CONFIRM_CUTOVER
CANCEL_CUTOVER
ACTIVATE_EXACT
ARM_EXACT
DISARM_EXACT
SYNC_ADVANCED
DISPATCH_ATTEMPT_RECORDED
DISPATCH_REJECTED_REARMED
DISPATCH_ACCOUNTED_REARMED
RECOVERY_CLOSED_DISARMED
ROLLBACK_TO_LEGACY
```

Exact mapping:

```text
no record -> LEGACY_ACTIVE
    BOOTSTRAP_LEGACY

LEGACY_ACTIVE -> CUTOVER_PREPARED
    PREPARE_CUTOVER

CUTOVER_PREPARED -> CUTOVER_PREPARED
    PREPARATION_EVIDENCE_BOUND

CUTOVER_PREPARED -> CUTOVER_CONFIRMED
    CONFIRM_CUTOVER

CUTOVER_PREPARED | CUTOVER_CONFIRMED -> LEGACY_ACTIVE
    CANCEL_CUTOVER

CUTOVER_CONFIRMED -> EXACT_CASH_DISARMED
    ACTIVATE_EXACT

EXACT_CASH_DISARMED -> EXACT_CASH_ARMED
    ARM_EXACT

EXACT_CASH_ARMED -> EXACT_CASH_DISARMED
    DISARM_EXACT

CUTOVER_PREPARED | EXACT_CASH_DISARMED | EXACT_CASH_ARMED
-> same state
    SYNC_ADVANCED

EXACT_CASH_ARMED -> EXACT_CASH_DISPATCH_PENDING
    DISPATCH_ATTEMPT_RECORDED

EXACT_CASH_DISPATCH_PENDING -> EXACT_CASH_ARMED
    DISPATCH_REJECTED_REARMED | DISPATCH_ACCOUNTED_REARMED
    [same uninterrupted dispatch process only]

EXACT_CASH_DISPATCH_PENDING -> EXACT_CASH_DISARMED
    RECOVERY_CLOSED_DISARMED
    [explicit recover command after a process/restart recovery boundary]

EXACT_CASH_DISARMED -> LEGACY_ACTIVE
    ROLLBACK_TO_LEGACY
```

Every row except bootstrap is one CAS update and MUST:

```text
record_revision = previous.record_revision + 1
previous_record_sha256 = previous.sha256
transition_at = injected operation time
```

All fields not explicitly changed by the selected row are preserved byte-for-value.

`cutover_generation`:

```text
bootstrap = 0
PREPARE_CUTOVER = previous.cutover_generation + 1
every other row = preserve
```

Generation overflow fails before write with `STATE_TRANSITION_INVALID`.

`PREPARE_CUTOVER` validates/sets account scope, environment and identity-key ID,
clears the new-generation preparation evidence fields and sets:

```text
activation_context_sha256 = null
ledger_head_sha256 = null
ledger_revision = null
opening_cutoff = null
opening_record_sha256 = null
operations_complete_through = null
pending_dispatch_proof_sha256 = null
```

`PREPARATION_EVIDENCE_BOUND` atomically binds the complete opening,
ledger-head/revision and contiguous watermark set. Partial binding is forbidden.

`ACTIVATE_EXACT` sets:

```text
activation_context_sha256 = exact accepted CL6 context_sha256
ever_exact_activated = true
```

and refreshes the bound ledger/head/watermark evidence from the activation run.
`activation_context_sha256` is then preserved for that generation, including
disarm and rollback; a later `PREPARE_CUTOVER` starts a new generation and clears it.

`SYNC_ADVANCED` changes only the exact ledger revision/head and contiguous
operations watermark selected by that completed sync. It is not written when
the complete sync produces no custody change.

`DISPATCH_ATTEMPT_RECORDED` is the only row that increments
`post_attempt_count` and sets `pending_dispatch_proof_sha256`.

The two uninterrupted-process rearm rows and `RECOVERY_CLOSED_DISARMED` clear
`pending_dispatch_proof_sha256`; they never decrement `post_attempt_count`.
Their exact eligibility is defined in sections 80 and 84.

`CANCEL_CUTOVER` and `ROLLBACK_TO_LEGACY` never reset revisions,
`cutover_generation`, `ever_exact_activated` or `post_attempt_count` and never
delete CL2/CL4 evidence. No other same-state record rewrite is valid.

---

## 14. Transient non-persistent stages

The following are algorithm stages, not durable authority states:

```text
PROVIDER_READ_SYNC

LOCAL_SNAPSHOT_LOCKING

LOCKED_REVALIDATION

CENTRAL_PREPARE

ATTEMPT_RECORD_WRITE

PROVIDER_POST

POST_OUTCOME_CLASSIFICATION

RECONCILIATION

RISK_ACCOUNTING
```

A crash in one of these stages is resolved exclusively from durable CL7/Central/legacy runtime state.

No transient stage itself grants authority after restart.

---

# PART B — RUNTIME ARTIFACTS AND CUSTODY

## 15. Runtime artifact names

CL7 version 1 reserves:

```text
runtime_cash_authority.json
runtime_cash_authority.json.sha256
runtime_cash_authority.json.lastgood
runtime_cash_authority.json.lock

cash_ledger_v3_10.sqlite3
```

These are runtime-private artifacts and MUST NOT be committed.

The CL2 database continues to use accepted CL2 schema/contracts; CL7 does not define a second ledger schema.

---

## 16. Bootstrap behavior

`runtime_bootstrap.py` may create an initial authority record only when no authority custody exists.

Initial record:

```text
state = LEGACY_ACTIVE
record_revision = 0
cutover_generation = 0
ever_exact_activated = false
post_attempt_count = 0
```

Bootstrap MUST NOT:

- create an opening;
- query provider operations;
- activate exact authority;
- arm execution;
- infer Account ID;
- write identity key;
- overwrite existing authority custody;
- repair corrupt authority custody automatically.

Existing authority file is validate-only.

Corrupt existing authority custody is an error.

---

## 17. Missing-record compatibility rule

To preserve pre-CL7 local/unit compatibility, an absent CL7 record may be interpreted as synthetic `LEGACY_ACTIVE` only when ALL are absent:

```text
runtime_cash_authority.json
runtime_cash_authority.json.sha256
runtime_cash_authority.json.lastgood
cash_ledger_v3_10.sqlite3
```

The lock file is coordination infrastructure, not authority custody. Its mere
presence does not defeat this compatibility rule; an actually held/unavailable
lock still blocks access normally.

If any CL7/CL2 custody artifact exists but the active authority record is missing/invalid:

```text
RECOVERY_BLOCKED
```

This prevents ordinary single-file loss after exact activation from silently re-enabling legacy execution.

Coordinated deletion of all runtime custody artifacts is outside automatic recovery and requires operator restoration/review.

---

## 18. RuntimeCashAuthorityRecord exact fields

Canonical version-1 fields:

```text
account_scope_sha256
activation_context_sha256
cutover_generation
domain
environment
ever_exact_activated
identity_key_id
ledger_head_sha256
ledger_revision
opening_cutoff
opening_record_sha256
operations_complete_through
pending_dispatch_proof_sha256
post_attempt_count
previous_record_sha256
record_revision
state
transition_at
transition_kind
version
```

Canonical domain:

```text
v3.10-cl7-runtime-cash-authority
```

---

## 19. Record scalar encoding

In-memory:

```text
record_revision: int
cutover_generation: int
ledger_revision: int | None
post_attempt_count: int
version: int
```

`bool` is forbidden as integer.

Range:

```text
0 <= value <= 9_223_372_036_854_775_807
```

Canonical JSON:

```text
record_revision
cutover_generation
ledger_revision
post_attempt_count
```

are decimal strings without leading zero.

`version` is JSON integer.

---

## 20. Record optionality

`LEGACY_ACTIVE` initial bootstrap has:

```text
account_scope_sha256 = null
activation_context_sha256 = null
identity_key_id = null
ledger_head_sha256 = null
ledger_revision = null
opening_cutoff = null
opening_record_sha256 = null
operations_complete_through = null
pending_dispatch_proof_sha256 = null
```

An exact `CUTOVER_PREPARED / PREPARE_CUTOVER` freeze record has non-null:

```text
account_scope_sha256
identity_key_id
```

and null:

```text
activation_context_sha256
ledger_head_sha256
ledger_revision
opening_cutoff
opening_record_sha256
operations_complete_through
pending_dispatch_proof_sha256
```

An exact `CUTOVER_PREPARED` record whose `transition_kind` is
`PREPARATION_EVIDENCE_BOUND` or `SYNC_ADVANCED`, and every `CUTOVER_CONFIRMED`
record, require non-null account scope, key ID,
ledger head/revision, opening cutoff/record and contiguous operations watermark;
`activation_context_sha256` remains null.

Every `EXACT_CASH_*` record requires all those preparation fields and:

```text
activation_context_sha256
```

to be non-null.

For every state `pending_dispatch_proof_sha256` MUST be non-null only in
`EXACT_CASH_DISPATCH_PENDING`.

A non-initial `LEGACY_ACTIVE` record reached through cancel/rollback may retain
validated account/evidence/activation fields from the prior generation exactly
as required by section 13.1. Its pending proof is always null. The tuple
`ever_exact_activated`, `post_attempt_count`, `cutover_generation` is never reset.

---

## 21. State-derived invariants

```text
ever_exact_activated == true
```

is mandatory in all `EXACT_CASH_*` states and remains true after any rollback.

```text
post_attempt_count > 0
```

permanently forbids automatic/bounded rollback to legacy for the current runtime lineage.

`pending_dispatch_proof_sha256 != null` iff:

```text
state == EXACT_CASH_DISPATCH_PENDING
```

---

## 22. Canonical JSON

CL7 canonical JSON:

```python
json.dumps(
    value,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("ascii")
```

Forbidden:

- BOM;
- duplicate keys;
- external whitespace;
- NaN/Infinity;
- float in authoritative CL7 DTO;
- surrogate code point;
- non-canonical integer string;
- non-canonical reserialization.

---

## 23. Record SHA and checksum custody

`RuntimeCashAuthorityRecord.sha256`:

```text
SHA256(record.canonical_bytes)
```

The `.sha256` companion contains exactly the active record SHA plus final LF.

`previous_record_sha256` binds the immediately preceding successfully committed record.

Every update requires:

```text
expected record_revision
+
expected active record SHA
```

and increments revision by exactly 1.

No write may skip a revision.

For revision 0:

```text
previous_record_sha256 = null
lastgood may be absent
```

For every revision greater than 0, validated active custody requires:

```text
lastgood present
SHA256(lastgood canonical bytes) == previous_record_sha256
lastgood.record_revision == active.record_revision - 1
```

The lastgood record itself must pass exact keyset/domain/version, canonical-byte,
scalar, optionality and state-derived validation. A format-only previous SHA
check is insufficient.

---

## 24. Atomic persistence

Authority persistence is a bounded multi-artifact commit under the exclusive
RuntimeCashAuthority lock. Exact order:

```text
1. validate current active bytes + checksum + lastgood link;
2. CAS expected current revision + active SHA;
3. build canonical next bytes with previous_record_sha256 = current SHA;
4. write/fsync/round-trip-validate unique next-record temp;
5. write/fsync unique next-checksum temp containing next SHA + LF;
6. write/fsync unique lastgood temp containing exact current canonical bytes;
7. atomic-replace lastgood temp -> .lastgood;
8. sync parent directory best effort;
9. atomic-replace next-record temp -> active record;
10. sync parent directory best effort;
11. atomic-replace next-checksum temp -> active .sha256;
12. sync parent directory best effort;
13. re-read and validate active/checksum/previous->lastgood chain;
14. only then return commit success and expose the new in-process state.
```

Initial bootstrap is the only no-current exception. Under the lock it writes
revision 0 with null previous SHA, prepares active/checksum temps, replaces the
active record, replaces its checksum, re-reads the pair and creates no lastgood.
A crash before active replace leaves the all-custody-absent compatibility state;
a mixed active/checksum bootstrap tuple is `RECOVERY_BLOCKED`; a complete exact
pair is `LEGACY_ACTIVE` revision 0.

The repository primitive may be reused only through a wrapper that produces
these canonical bytes and the same observable crash outcomes. Its ordinary
pretty-JSON/checksum behavior is not automatically equivalent.

A process failure before step 9 leaves the old active record, but an interrupted
lastgood replacement may make chain custody recovery-blocked. A failure between
steps 9 and 11 may leave new active bytes with the old checksum. A failure after
step 11 but before successful step 13 may leave new active/checksum with an
invalid or missing previous->lastgood link.

Therefore the exhaustive restart outcomes are:

```text
old active + old checksum + valid old chain -> old state is effective

new active + new checksum + exact previous->lastgood link
    -> new state is effective

every mixed/missing/mismatched combination
    -> RECOVERY_BLOCKED
```

No implementation may claim that a multi-file pair changed atomically.
An operation returns success only for the second outcome. A returned failure or
process crash never grants execution from a partly committed tuple.

No partially parsed record grants execution.

---

## 25. Last-good rule

`.lastgood` is evidence/recovery input only.

After every successful revision greater than 0 it contains the exact canonical
bytes of the immediately previous committed active record. It is replaced
before active bytes only as part of section 24 and is verified through the new
record's `previous_record_sha256` after commit.

CL7 MUST NOT automatically restore `.lastgood`.

In particular:

```text
lastgood = LEGACY_ACTIVE
current corrupt after exact activation
```

MUST NOT silently roll the owner back to legacy.

Any recovery that changes active custody requires an explicit operator command and contract-valid transition.

---

# PART C — PRIVACY AND IDENTITY

## 26. Account scope

Persistent CL7 records MUST NOT contain raw Account ID.

Account identity is:

```text
account_scope_sha256
```

using the accepted CL3 account-scope HMAC exactly.

Every authenticated runtime operation receives ephemeral raw Sandbox Account ID and re-derives the same scope.

Mismatch:

```text
ACCOUNT_SCOPE_INVALID
```

with zero provider mutation.

---

## 27. Identity key

Exact cash mode requires a stable dedicated identity key.

It MUST NOT be derived from:

- T-Bank token;
- Account ID;
- password;
- filesystem path;
- current time.

Version-1 operator convention:

```text
V310_CL_IDENTITY_KEY_HEX
V310_CL_IDENTITY_KEY_ID
```

Key bytes:

```text
32..64 bytes
```

after strict hexadecimal decoding.

Key ID:

```text
[A-Z][A-Z0-9_]{0,63}
```

The key itself is never persisted in:

- authority record;
- ledger export;
- journal;
- fixture outside synthetic KAT;
- logs;
- support artifacts.

Missing/wrong key blocks prepare/activation/exact dispatch.

---

## 28. Environment

CL7 version 1 supports only:

```text
BrokerEnvironment.SANDBOX
```

Production environment:

```text
ENVIRONMENT_UNSUPPORTED
```

No real-account route exists in CL7.

---

# PART D — PROVIDER READ AND LEDGER SYNC

## 29. Additive TBank cursor-read boundary

`tbank_sandbox.py` may add exactly one read-only public primitive needed by CL3 runtime integration.

Semantic contract:

```text
one GetSandboxOperationsByCursor physical HTTP attempt
```

for one supplied request payload and timeout.

The primitive MUST NOT perform its own retry, redirect replay or hedging.

CL3 `collect_tbank_operations` remains the sole retry/pagination policy owner for operation-history reads.

HTTP POST transport of this read-only RPC is not economic provider mutation.

---

## 30. CL3 runtime request

CL7 builds accepted CL3 `BrokerReadRequest` with:

```text
environment = SANDBOX
raw_account_id = ephemeral configured Sandbox account
identity_key / identity_key_id = CL7 supplied
from_inclusive = current operations watermark
to_exclusive = newly frozen sync boundary
limit <= 1000
max_pages <= 100
max_items <= 100000
bounded absolute deadline
accepted RetryPolicy
caller-injected monotonic clock/wait
```

No unbounded history request is permitted.

---

## 31. Opening boundary and first sync

CL7 uses CL4 `FROM_NOW`.

The opening cutoff is not historical backfill.

After accepted opening:

```text
opening_cutoff = C
```

the first operation sync begins strictly after the opening boundary.

Canonical first sync boundary is:

```text
from_inclusive = successor_ns(C)
```

where:

```text
successor_ns(C) = C + 1 nanosecond
```

If successor cannot be represented:

```text
TIMESTAMP_INVALID
```

No operation with:

```text
effective_at <= opening_cutoff
```

may be applied on top of the opening balance.

---

## 32. Contiguous operation watermark

Authority record stores:

```text
operations_complete_through
```

as the next CL3 `from_inclusive` boundary.

For each successful sync:

```text
request.from_inclusive =
record.operations_complete_through

request.to_exclusive =
sync_to_exclusive
```

After and only after the complete batch is locally persisted:

```text
operations_complete_through =
batch.watermark.to_exclusive
```

A crash before watermark update causes the same interval to be replayed through CL2 idempotency rules.

It never skips the interval.

---

## 33. CL3 decision → CL2 persistence mapping

For each exact CL3 `BrokerDecision`:

### TRANSACTION_PROPOSED

```text
append observation
-> append linked transaction
```

using accepted CL2 expected-revision/CAS semantics.

### REVIEW_REQUIRED

```text
append observation
-> append REVIEW_REQUIRED status event
```

No transaction is invented.

### NOT_LEDGER_RELEVANT

```text
append observation
-> append terminal rejection/non-ledger status
```

using accepted finite CL2 status semantics.

Existing exact duplicates are idempotent.

Source/content/economic conflicts fail closed.

---

## 34. Batch persistence crash semantics

CL7 does not require one SQL transaction spanning an entire provider batch.

Instead:

```text
each CL2 append is atomic
+
watermark advances only after full batch completion
```

Crash midway:

```text
watermark remains old
```

and next sync replays the same complete interval.

Exact duplicates converge; conflicts stop progression.

---

## 35. Incomplete operation evidence

Any:

```text
REVIEW_REQUIRED
unresolved Observation
source conflict
ledger graph conflict
CL3 incomplete batch
```

prevents actionable exact cash context.

No pending/unclassified operation is interpreted as zero.

No pending inflow finances BUY.

---

# PART E — CUTOVER PREPARATION

## 36. Cutover Preparation Stage

Before `LEGACY_ACTIVE -> CUTOVER_PREPARED`, operator must supply exact phrase:

```text
PREPARE V3.10 CL7 EXACT CASH CUTOVER
```

The operation:

- acquires the RuntimeCashAuthority lock;
- validates account scope/key/environment;
- writes `CUTOVER_PREPARED` before any provider-read preparation;
- thereby freezes all NEW legacy/diagnostic order creation;
- performs only Sandbox reads and local evidence writes;
- performs zero order POST.

If provider/private access has not been separately authorized, this method MUST NOT be executed against a real connected Sandbox account during development/review.

Synthetic tests are always allowed.

---

## 37. New-order freeze in PREPARED/CONFIRMED

Once durable state is:

```text
CUTOVER_PREPARED
```

or:

```text
CUTOVER_CONFIRMED
```

all repository-owned new provider order paths fail closed before creating a new economic intent.

Existing already-persisted unresolved economic work may only be inspected/reconciled/finalized.

This freeze remains until:

```text
cancel -> LEGACY_ACTIVE
```

or:

```text
activation -> EXACT_CASH_DISARMED
```

---

## 38. Legacy/diagnostic guard placement

`SandboxTradingBot` and `SandboxOrderDiagnostics` MUST consult RuntimeCashAuthority before creating/persisting a NEW execution intent.

Guard acquisition occurs before:

```text
INTENT_SAVED
ORDER_SUBMITTED
provider POST
```

If state is not `LEGACY_ACTIVE`:

```text
new direct legacy/diagnostic provider mutation = FORBIDDEN
```

Read-only recovery/inspection of an existing pending order remains permitted.

---

## 39. CL4 opening creation/reuse

During cutover preparation CL7:

1. opens/creates the accepted CL2 store at `cash_ledger_v3_10.sqlite3`;
2. obtains one fresh caller-supplied Sandbox GetPortfolio response;
3. builds exact CL4 `BrokerCashProof`;
4. if no valid opening exists for the account scope, prepares and accepts one CL4 FROM_NOW opening;
5. if an exact valid opening already exists, reuses it;
6. if a conflicting/multiple/wrong-account opening exists, fails closed.

No opening is silently replaced.

No second generation opening is introduced in CL7 V1.

---

## 40. Opening preparation crash rule

If CL4 opening is committed but authority record update fails:

```text
authority remains CUTOVER_PREPARED
legacy remains cash owner
provider order mutation remains frozen
opening remains shadow evidence
```

Retry must detect/reuse the exact opening.

It MUST NOT append another opening.

---

## 41. Preparation sync

After opening, CL7 synchronizes the contiguous operation interval from the strict post-opening boundary through a bounded `sync_to_exclusive`.

It then builds:

```text
CL4 reconciliation
CL5 CashAvailability
CL6 PortfolioRiskCashContext
```

for preview only.

PREPARED state does not gain exact cash authority.

---

# PART F — QUIESCENCE AND CONFIRMATION

## 42. Quiescence definition

Cutover confirmation/activation require all of:

```text
Central active reservation/intents = none

no Central status in:
QUEUED
IN_FLIGHT
SUBMITTED
UNCERTAIN

legacy robot pending_order = none

diagnostic pending_order = none

authority pending dispatch proof = none

no unresolved CL2/CL3 evidence

CL4 reconciliation = MATCHED / complete / NONE

CL5 availability = READY

CL6 context = READY_FOR_LOCKED_REVALIDATION
```

Any malformed legacy state file is treated as not quiescent.

---

## 43. Legacy pending scan

CL7 performs a bounded canonical JSON scan of the accepted runtime state files used by legacy/diagnostic execution.

Any non-null recognized:

```text
pending_order
```

blocks cutover.

The scan is evidence-only and does not mutate the legacy state.

Unknown/corrupt schema:

```text
LEGACY_PENDING_OPERATION
```

rather than assuming absence.

---

## 44. Confirmation

`CUTOVER_PREPARED -> CUTOVER_CONFIRMED` requires exact operator phrase:

```text
CONFIRM V3.10 CL7 EXACT CASH CUTOVER
```

Immediately before transition CL7 rechecks quiescence and fresh CL1–CL6 evidence.

`CUTOVER_CONFIRMED` still has:

```text
owner = LEGACY_CASH_AUTHORITY
provider new-order execution = frozen
```

---

# PART G — ACTIVATION

## 45. Activation phrase

`CUTOVER_CONFIRMED -> EXACT_CASH_DISARMED` requires exact phrase:

```text
ACTIVATE V3.10 CL7 EXACT CASH AUTHORITY
```

No other text is accepted.

Confirmation matching is exact ASCII after stripping one outer leading/trailing whitespace sequence only.

No case folding.

---

## 46. Activation algorithm

Under one RuntimeCashAuthority lock:

1. revalidate current authority record/CAS;
2. revalidate raw Account ID → account-scope HMAC;
3. recheck legacy/diagnostic/Central quiescence;
4. perform fresh CL3 contiguous sync;
5. freeze/export exact CL2 ledger;
6. read fresh GetPortfolio;
7. read fresh GetSandboxPositions;
8. build exact CL4 reconciliation;
9. build exact CL5 Central projection/availability;
10. build exact Portfolio identity evidence;
11. build exact Risk guard evidence;
12. build exact CL6 PortfolioRiskCashContext;
13. require `READY_FOR_LOCKED_REVALIDATION`;
14. persist exact ledger revision/head/context SHA into authority record;
15. atomically transition to `EXACT_CASH_DISARMED`.

There is no provider order mutation in activation.

---

## 47. Atomic owner switch

The owner switch becomes effective only when the section-24 active record,
checksum and previous->lastgood chain have all committed and exact read-back has
succeeded under the authority lock. The active-record replace is the candidate
state write; it is not by itself a successful multi-artifact commit.

Before successful section-24 commit:

```text
owner = LEGACY_CASH_AUTHORITY
```

After successful section-24 commit:

```text
owner = CL7_EXACT_CASH_AUTHORITY
```

No other file write, log write, provider read or in-memory flag changes owner.

---

## 48. Activation crash matrix

### Before successful section-24 commit

Result:

```text
previous active/checksum/chain exact
    -> CUTOVER_CONFIRMED / legacy owner / all new order POST frozen
    -> retry activation allowed after exact read-back

any mixed/mismatched tuple
    -> RECOVERY_BLOCKED / provider mutation zero
```

### After active-record replace but before command returns

Result after restart:

```text
full active/checksum/previous->lastgood tuple exact
    -> EXACT_CASH_DISARMED / exact owner / execution disarmed

mixed/mismatched tuple
    -> RECOVERY_BLOCKED / provider mutation zero
```

Never retry the owner switch blindly.

Read the durable record first.

---

# PART H — ARMING / DISARMING / CANCEL / ROLLBACK

## 49. Exact execution arming

`EXACT_CASH_DISARMED -> EXACT_CASH_ARMED` requires exact phrase:

```text
ARM V3.10 CL7 SANDBOX EXACT CASH EXECUTION
```

Arming performs zero provider order mutation.

Arming does not make a stale context valid.

Every dispatch still rebuilds/revalidates.

---

## 50. Existing legacy arming is insufficient

After exact activation, any predecessor gate such as:

```text
--execute
ARM_SANDBOX_TRADING=YES
SANDBOX_EXECUTION_CONFIRMATION
```

is insufficient by itself.

It cannot change CL7 authority state.

Legacy `run_bot.py --execute` must fail closed when current effective authority is not `LEGACY_ACTIVE`.

---

## 51. Disarm

`EXACT_CASH_ARMED -> EXACT_CASH_DISARMED` is always a safe operation when no dispatch attempt is currently pending.

It does not require a dangerous-action confirmation phrase.

If state is `EXACT_CASH_DISPATCH_PENDING`:

```text
DISARM requires recovery closure first
```

---

## 52. Cancel before activation

`CUTOVER_PREPARED` or `CUTOVER_CONFIRMED` may be cancelled to `LEGACY_ACTIVE`.

Cancellation:

- performs zero provider order mutation;
- does not delete ledger/opening evidence;
- does not rewrite CashLedger history;
- does not reset revisions.

Local shadow evidence remains available for a future preparation.

---

## 53. Rollback after activation

Bounded rollback is allowed only from:

```text
EXACT_CASH_DISARMED
```

and only if:

```text
post_attempt_count == 0
pending_dispatch_proof_sha256 == null
Central quiescent
legacy/diagnostic pending = none
fresh CL4/CL5/CL6 evidence coherent
```

Exact phrase:

```text
ROLLBACK V3.10 CL7 TO LEGACY CASH AUTHORITY
```

---

## 54. No rollback after provider-attempt marker

Once:

```text
post_attempt_count > 0
```

automatic/bounded owner rollback to legacy is permanently forbidden for the current runtime lineage.

Reason:

the provider may already have observed an exact-mode request.

Allowed response is:

```text
disarm
reconcile
recover
manual review
```

not state rewind.

---

## 55. Rollback is not data rewind

Rollback MUST NOT:

- delete CashLedger rows;
- decrement CL2 revision;
- delete CL4 opening;
- rewrite Central history;
- revert Portfolio;
- revert RiskState;
- remove provider orders;
- reset post attempt count;
- set `ever_exact_activated=false`.

---

# PART I — GLOBAL LOCK ORDER

## 56. RuntimeCashAuthority lock is outermost economic lock

Every repository-owned provider ORDER mutation after CL7 implementation participates in:

```text
RuntimeCashAuthority lock
```

The lock serializes:

- cutover state transition;
- legacy direct execution guard;
- diagnostic direct execution guard;
- exact dispatch;
- rollback;
- dispatch recovery closure.

---

## 57. Frozen multi-store lock order

When locks overlap, exact order is:

```text
1. RuntimeCashAuthority lock

2. PortfolioRepository lock

3. Risk profile lock

4. Risk state lock

5. CashLedgerStore lock

6. CentralOrderStore lock
```

Provider ORDER POST occurs only while the relevant exact-mode lock set is still held.

No CL7 implementation may invert this order.

A lock may be acquired/released alone before the full snapshot set, but overlapping acquisitions must preserve this order.

---

## 58. Provider-read phase and locks

Read-only provider synchronization may run while holding only:

```text
RuntimeCashAuthority lock
```

and temporary CL2 lock for individual local writes.

It MUST NOT hold Central lock across paginated broker history reads.

Final current-cash/positions reads occur under RuntimeCashAuthority lock shortly before final local locked snapshot.

---

## 59. Final local snapshot locks

After final provider reads:

```text
Portfolio
Risk profile
Risk state
CashLedger
Central
```

are frozen in the required order for the final exact proof/dispatch transition.

If any required lock cannot be acquired within bounded timeout:

```text
LOCK_UNAVAILABLE
zero provider mutation
```

---

# PART J — CENTRAL LOCKED DISPATCH API

## 60. Why `prepare_next` alone is insufficient for CL7 exact mode

Existing `prepare_next` may atomically change Central state but does not guarantee that the Central lock remains held through the final provider handoff.

CL7 exact mode requires no Central drift between:

```text
final Central projection
-> QUEUED to IN_FLIGHT
-> provider POST
```

Therefore CL7 may add one bounded Central API for exact mode.

---

## 61. New Central exact dispatch lease

`central_order_manager.py` may add:

```python
locked_dispatch_lease(
    portfolio_repository,
    *,
    expected_intent_id: str,
    locked_portfolio_state: PortfolioState,
    validator: Callable[[CentralOrderState, CentralOrderIntent], T],
) -> ContextManager[LockedCentralDispatch]
```

Naming may differ only if the contract review accepts an exact equivalent API before implementation acceptance.

Semantic contract is frozen here.

---

## 62. LockedCentralDispatch requirements

While the Central store lock is held, the lease:

1. loads exact current state;
2. requires no account blocker;
3. requires queue head == `expected_intent_id`;
4. invokes CL7 validator against pre-transition Central state;
5. requires validator success;
6. transitions exact queue head `QUEUED -> IN_FLIGHT`;
7. persists transition;
8. yields the prepared intent without releasing Central lock.

The lock remains held until provider outcome has been locally classified/persisted or the lease exits fail-closed.

---

## 63. LockedCentralDispatch outcome methods

Inside the held lease exact-mode adapter may persist only accepted Central outcomes:

```text
mark_submitted
mark_uncertain
mark_submission_rejected
mark_pre_submit_failed
```

using existing Central semantics.

No second queue head may be prepared during the lease.

---

## 64. Crash after Central IN_FLIGHT before attempt marker

Ordering is frozen:

```text
locked validation
-> Central QUEUED -> IN_FLIGHT persisted
-> CL7 dispatch-attempt record persisted
-> provider POST invocation
```

Therefore:

```text
IN_FLIGHT
+
no matching CL7 pending dispatch marker
```

proves that CL7 did not invoke provider POST in that dispatch attempt.

Recovery may safely mark pre-submit failure/cancel according to Central contract.

No broker lookup is required for that specific crash point.

---

# PART K — FINAL PRE-POST EVIDENCE

## 65. Exact provider-read order for one dispatch

Under RuntimeCashAuthority lock:

```text
1. contiguous CL3/CL2 operation sync

2. GetPortfolio current-cash response

3. GetSandboxPositions response

4. acquire final local lock set

5. rebuild all local proofs

6. Central locked validator

7. attempt marker

8. provider POST
```

Provider cash/positions reads must be the final broker cash evidence before local locked validation.

---

## 66. Final proof age

CL6 limits remain in force.

CL7 adds stricter final mutation threshold:

```text
MAX_PREPOST_EVIDENCE_AGE_NS = 10_000_000_000
```

At dispatch-attempt marker time each of:

```text
CL6 context evaluated_at
broker cash as_of
broker positions as_of
Central projection evaluated_at
Portfolio captured_at
Risk guard captured_at
```

must be no older than 10 seconds.

Dependency from future remains structural failure.

---

## 67. Full mandatory revalidation set

Immediately before economic mutation CL7 MUST reacquire/recheck:

```text
authority record revision + SHA + state

account scope

Portfolio revision
Portfolio decision checksum
Portfolio document checksum

Risk policy hash
Risk state guard hash

CashLedger revision
CashLedger head

CL4 reconciliation SHA/status/completeness

CL5 CashAvailability SHA/status/reason/freshness

Central revision
Central reservation projection hash

CL6 PortfolioRiskCashContext SHA
CL6 context identity HMAC

all CL6 temporal dependencies
cross-evidence skew

expected Central queue-head intent identity
```

The set cannot be reduced.

---

## 68. Current proof rebuild

A detached saved CL6 context is never sufficient.

Final exact validator reconstructs current:

```text
BrokerCashProof
BrokerPositionsCashProof
CashReconciliation
CentralReservationProjection
CashAvailabilitySnapshot
PortfolioIdentityEvidence
RiskGuardEvidence
PortfolioRiskCashContext
```

and requires:

```text
context.status =
READY_FOR_LOCKED_REVALIDATION
```

at the final pre-transition Central state.

---

## 69. Pre-transition Central semantics

The CL5/CL6 context is built against Central state while the intended order is still:

```text
QUEUED
```

This is intentional.

CL5 V1 proves QUEUED reservations disjoint from provider blocked cash.

After successful validation the same Central lock performs:

```text
QUEUED -> IN_FLIGHT
```

No attempt is made to rebuild CL5 after the transition, because V1 intentionally treats IN_FLIGHT provider overlap as ambiguous.

The already-built locked proof binds the exact pre-transition projection and exact intended transition.

---

## 70. No unrelated Central drift

The provider POST may occur only for the exact intent that was queue head in the proof.

If queue head, reservation amount, Central revision or projection differs:

```text
CENTRAL_CHANGED
zero provider mutation
```

No intent selection fallback is allowed.

---

# PART L — LOCKED DISPATCH PROOF

## 71. Intent privacy identity

Raw Central intent ID is not placed in CL7 proof/export evidence.

Privacy-safe intent scope:

```json
{
  "account_scope_sha256": "<sha>",
  "domain": "v3.10-cl7-intent-scope",
  "intent_id": "<ephemeral raw intent id>",
  "version": 1
}
```

`intent_scope_sha256` is HMAC-SHA-256 with the CL identity key.

Raw intent ID remains only in private Central runtime state/provider request.

---

## 72. LockedDispatchProof exact fields

```text
account_scope_sha256

domain

authority_record_revision
authority_record_sha256

availability_sha256

central_order_revision
central_reservation_projection_hash

cl6_context_identity_sha256
cl6_context_sha256

current_lots
direction
evaluated_at

free_investable_cash

identity_key_id
intent_scope_sha256

ledger_head_sha256
ledger_revision

portfolio_decision_checksum
portfolio_document_checksum
portfolio_revision

reconciliation_sha256

reserved_cash

risk_policy_hash
risk_state_guard_hash

target_lots

proof_identity_sha256

version
```

Canonical domain:

```text
v3.10-cl7-locked-dispatch-proof
```

Identity HMAC domain:

```text
v3.10-cl7-locked-dispatch-proof-identity
```

### 72.1 Exact keysets and scalar encoding

The identity HMAC preimage contains exactly every section-72 field except
`proof_identity_sha256`, with:

```text
domain = v3.10-cl7-locked-dispatch-proof-identity
```

The canonical proof contains exactly every section-72 field, including
`proof_identity_sha256`, with:

```text
domain = v3.10-cl7-locked-dispatch-proof
```

There is no `environment`, raw Account ID, raw intent ID, provider order ID or
additional extension field in either version-1 object.

In-memory plain integers:

```text
authority_record_revision
central_order_revision
ledger_revision
portfolio_revision
current_lots
target_lots
version
```

`bool` is forbidden for each. Range is:

```text
0 <= value <= 9_223_372_036_854_775_807
```

Canonical JSON encoding:

```text
authority_record_revision  decimal string without leading zero
central_order_revision     decimal string without leading zero
ledger_revision            decimal string without leading zero
portfolio_revision         decimal string without leading zero

current_lots               JSON integer
target_lots                JSON integer
version                    JSON integer equal to 1
```

`free_investable_cash` and `reserved_cash` are their complete exact CL1 Money
canonical objects. All SHA/HMAC fields are lowercase 64-hex strings.

Construction order is normative:

```text
identity_preimage = exact identity keyset above
proof_identity_sha256 = HMAC-SHA-256(identity_key, canonical(identity_preimage))
canonical_proof = exact proof keyset above
LockedDispatchProof.sha256 = SHA256(canonical(canonical_proof))
```

The KAT in section 115 freezes both complete preimages; deriving a schema from
the expected digest alone is forbidden.

---

## 73. LockedDispatchProof exact Money

```text
free_investable_cash
reserved_cash
```

are exact CL1 Money.

No float/Decimal inference/kopeck round-trip is allowed.

For current Central V1 reservation storage:

```text
reserved_cash
```

is exact scale-9 conversion of the Central integer kopeck reservation.

---

## 74. Direction/target binding

Version 1 exact direction set:

```text
BUY
SELL
```

`current_lots` and `target_lots` are non-negative plain integers.

The proof is valid only for the exact candidate/intent inspected under the Central lock.

A proof cannot be reused for another intent, target or reservation.

### 74.1 Durable Central proof/request correlation

The same atomic Central state update that performs step U
`QUEUED -> IN_FLIGHT` MUST persist on that exact intent:

```text
cl7_locked_dispatch_proof = complete canonical LockedDispatchProof object
cl7_locked_dispatch_proof_sha256 = LockedDispatchProof.sha256
```

Both values are null before a CL7 exact-mode locked lease first selects the
intent. They are written together with the `IN_FLIGHT` transition, remain
immutable for that intent, and are retained after terminal/reconciled status as
private audit/recovery evidence. Existing pre-CL7 intents may have both null;
exact CL7 dispatch rejects either-one-null and non-exact read-back.

For predecessor Central records, simultaneous absence of both fields is decoded
as both null. This is the only accepted legacy-keyset projection. The next
successful Central write materializes both keys in every serialized intent;
one absent and one present, or any non-null value on an intent never selected by
CL7, is invalid custody.

The provider request identity is frozen as:

```text
PostSandboxOrder.orderId = CentralOrderIntent.intent_id
GetSandboxOrderState.orderId = CentralOrderIntent.intent_id
GetSandboxOrderState.orderIdType = ORDER_ID_TYPE_REQUEST
```

No second generated request ID is allowed.

Before step V, CL7 exact-reads the just-persisted Central intent and requires:

```text
stored canonical proof bytes == proof built under the lease
stored proof SHA == SHA256(stored canonical proof bytes)
stored proof SHA == pending_dispatch_proof_sha256 to be written
HMAC(account scope + raw Central intent_id) == stored intent_scope_sha256
stored central_order_revision == exact pre-transition revision bound by proof
post-transition Central state revision == stored central_order_revision + 1
```

The raw Central intent ID remains private. It is available after restart from
the existing durable Central intent and is never copied into CL7 shareable
evidence.

Recovery locates exactly one Central blocking intent and validates every binding
above against the authority pending SHA before broker lookup. Zero matches,
multiple matches, malformed stored proof, SHA mismatch, intent-scope mismatch or
request-ID mismatch yields:

```text
RECOVERY_BLOCKED
provider lookup = zero when no exact raw request ID is established
provider POST = zero
```

At D3 the Central intent contains the proof while authority has no pending
marker. This exact combination proves pre-submit state; recovery may persist the
Central pre-submit failure and retains the proof as audit evidence. It does not
clear or rewrite any CL7 pending field because none exists.

---

## 75. Strategy dispatch requires full CL6 READY context

CL7 V1 does not define a cash-proof bypass for SELL/reduce-only execution.

Every exact-mode strategy/provider POST requires the complete CL6 context.

A future reduce-only emergency exception would require an explicit successor/rescope contract.

This avoids introducing a second unreviewed authorization lane in CL7.

---

# PART M — ATTEMPT MARKER AND PROVIDER POST

## 76. Durable attempt marker is before transport invocation

After Central is durably `IN_FLIGHT` and LockedDispatchProof is complete:

CL7 MUST atomically write authority record:

```text
state =
EXACT_CASH_DISPATCH_PENDING

pending_dispatch_proof_sha256 =
LockedDispatchProof.sha256

post_attempt_count =
previous + 1

transition_kind =
DISPATCH_ATTEMPT_RECORDED
```

Only after this record is durably committed may transport `post_order` be invoked.

---

## 77. Attempt marker failure

If authority record write fails:

```text
provider POST count = 0
```

Central is returned/marked through the locked lease as pre-submit failure where safely possible.

If local recovery cannot prove that transition:

```text
RECOVERY_BLOCKED
```

No POST is attempted.

---

## 78. Exactly one physical order POST invocation

One CL7 dispatch attempt may invoke:

```text
transport.post_order(...)
```

at most once.

The underlying order transport MUST NOT:

- retry;
- redirect-replay;
- hedge;
- repeat after timeout;
- call an alternate provider mutation endpoint.

Ambiguous network outcome is not retried.

---

## 79. Existing provider mutation owner

In `EXACT_CASH_*` states the only repository-owned path permitted to invoke order `post_order` is:

```text
SandboxExecutionAdapter
```

under a valid CL7 locked dispatch proof and pending attempt marker.

`bot.py` direct execution and `diagnostics.py` direct execution must fail before their own provider POST.

AST/call-graph tests enforce this property.

---

## 80. Provider response classes

### Explicit local/pre-submit validation failure

```text
no provider invocation
Central pre-submit failed
attempt marker absent
```

Every deterministic request/schema/range/market validation MUST finish before
step V. Once the attempt marker is committed, a local validation exception from
inside or below the transport call is classified as uncertain; it cannot be
used to clear the marker.

### Explicit provider rejection proving no acceptance

This class exists iff every condition below is true:

```text
exactly one HTTP response belongs to the one physical PostSandboxOrder request
TBankAPIError.service == SandboxService
TBankAPIError.method == PostSandboxOrder
status_code is a plain integer in [400, 499]
status_code not in {408, 409, 425, 429}
transient == false
the error was constructed directly from that HTTP response
no redirect was followed or replayed
```

No response body text or unknown provider code can widen this class.

```text
Central FAILED/rejected
pending marker may be safely cleared
post_attempt_count remains incremented
```

The marker may be cleared only after the Central rejected/FAILED state is
durably persisted under the same lease and exact proof/request binding remains
valid.

### Provider accepted / correlated response

The success response is accepted as correlated only when:

```text
response is a mapping
orderRequestId is present and exactly equals raw Central intent_id
orderId is a non-empty string of at most 128 characters
the response came from the one physical PostSandboxOrder invocation
```

```text
Central SUBMITTED
pending attempt remains recovery-relevant until safe post-submit classification
```

Missing `orderRequestId` is not repaired from `orderId`. A response with only
`orderId`, mismatched `orderRequestId`, malformed `orderId`, invalid JSON or a
non-mapping body is uncertain.

### Timeout / connection loss / malformed correlation / persistence-after-accept failure

```text
Central UNCERTAIN or existing IN_FLIGHT/SUBMITTED conservative blocker
pending attempt remains
no resubmit
```

This uncertain class is the default after step V. It includes, without
exception:

```text
status_code is null
status_code < 400
status_code >= 500
status_code in {408, 409, 425, 429}
transient == true
TLS/socket/timeout/protocol exception
redirect response or attempted redirect replay
malformed success or error response
missing/mismatched correlation
unknown exception after entering the transport boundary
Central outcome persistence failure
```

Only the exact explicit-rejection predicate above may prove non-acceptance after
the physical request began. Every other post-marker outcome retains the marker
and requires lookup/recovery without resubmit.

---

# PART N — DISPATCH RECOVERY

## 81. Recovery principle

After any durable attempt marker:

```text
automatic resubmit = NEVER
```

Recovery may only:

- inspect existing broker request/order by idempotency/request ID;
- wait for terminal state;
- reconcile Portfolio;
- perform idempotent Risk accounting;
- finalize local state;
- require manual review.

---

## 82. Crash-point matrix — exact dispatch

### D0 — before authority lock

```text
no local transition
no provider mutation
fresh dispatch may be attempted later
```

### D1 — after operation sync/provider reads, before final locks

```text
no provider mutation
evidence discarded
fresh revalidation required
```

### D2 — after final CL6 context, before Central transition

```text
no provider mutation
no Central IN_FLIGHT
fresh revalidation required
```

### D3 — after Central `QUEUED -> IN_FLIGHT`, before attempt marker

```text
provider invocation proven absent
recovery may mark pre-submit failure
no broker lookup required for this attempt
```

### D4 — after attempt marker, before transport call

Conservative result:

```text
provider may have been called from durable evidence perspective
broker lookup required
automatic resubmit forbidden
```

The system intentionally sacrifices availability because process crash cannot prove the exact CPU instruction boundary after durable marker.

### D5 — during provider POST / response lost

```text
lookup existing request
no resubmit
```

### D6 — provider accepted, before Central SUBMITTED persistence

```text
lookup existing request
no resubmit
```

### D7 — Central SUBMITTED persisted, before authority pending clear

```text
lookup/poll existing request
no resubmit
```

### D8 — terminal fill observed, before Portfolio reconciliation

```text
RECONCILE_ONLY
```

### D9 — Portfolio reconciled, before Risk accounting

```text
RISK_ACCOUNT_ONLY
```

### D10 — Risk accounting done, before final pending cleanup

```text
FINALIZE_ACCOUNTED only
```

---

## 83. `EXACT_CASH_DISPATCH_PENDING` startup behavior

On startup:

```text
EXACT_CASH_DISPATCH_PENDING
```

means:

- exact cash owner remains CL7;
- new provider mutation disabled;
- operator `recover` or `inspect` required;
- matching Central blocking intent must be located;
- missing/mismatched Central correlation -> manual review.

It never degrades to `EXACT_CASH_ARMED` automatically.

---

## 84. Clearing pending dispatch state

Pending CL7 attempt can be cleared only after one of:

```text
explicit provider rejection + Central terminal failed state

Central RECONCILED + canonical Portfolio reconciliation complete
    + required Risk accounting complete
```

After an actual attempt marker, `post_attempt_count` never decreases.

Exact clearing transitions:

```text
same uninterrupted dispatch process
    explicit rejection predicate from section 80
    + Central FAILED durably persisted under the same lease
    + exact proof/request correlation
    -> EXACT_CASH_ARMED / DISPATCH_REJECTED_REARMED

same uninterrupted dispatch process
    Central RECONCILED
    + canonical Portfolio reconciliation complete
    + required idempotent Risk accounting complete
    + exact proof/request correlation
    -> EXACT_CASH_ARMED / DISPATCH_ACCOUNTED_REARMED

recover command after any process/restart boundary
    either one of the two complete resolution predicates above
    + exact proof/request correlation
    -> EXACT_CASH_DISARMED / RECOVERY_CLOSED_DISARMED
```

Each transition is a section-24 CAS commit, clears only
`pending_dispatch_proof_sha256`, preserves the Central proof evidence and keeps
`post_attempt_count` unchanged.

Recovery after a process/restart boundary never chooses an armed target. A later
separate exact `arm` command is required.

D3 has no CL7 attempt marker and therefore is not a pending-state clearing case.
It performs only the Central pre-submit resolution specified in section 74.1;
the authority record remains `EXACT_CASH_ARMED` unchanged.

Any incomplete resolution, custody write failure, proof mismatch or unresolved
account blocker remains `EXACT_CASH_DISPATCH_PENDING` or becomes effective
`RECOVERY_BLOCKED`; it never clears optimistically.

---

# PART O — CUTOVER / STATE RECOVERY MATRIX

## 85. Cutover preparation crash points

### C0 — before PREPARED record

```text
LEGACY_ACTIVE
```

### C1 — PREPARED persisted, opening not yet accepted

```text
CUTOVER_PREPARED
provider mutation frozen
retry preparation
```

### C2 — opening accepted, authority record not yet updated with opening identity

```text
CUTOVER_PREPARED
discover/revalidate exact opening on retry
do not append another opening
```

### C3 — operation batch partially persisted, watermark not advanced

```text
CUTOVER_PREPARED
replay same CL3 interval
CL2 idempotency must converge
```

### C4 — CONFIRMED persisted

```text
CUTOVER_CONFIRMED
legacy owner
provider mutation frozen
```

### C5 — exact activation tuple fully committed, process exits before return

```text
EXACT_CASH_DISARMED
exact owner
provider mutation disarmed
```

### C6 — exact arm tuple fully committed, process exits before return

```text
EXACT_CASH_ARMED
fresh per-dispatch revalidation still required
```

---

## 86. Corrupt authority record

If current record/checksum fails:

```text
effective state = RECOVERY_BLOCKED
provider mutation = zero
```

Lastgood may be inspected.

No automatic owner rollback.

---

## 87. Corrupt/missing CashLedger in exact mode

If exact owner is active and CL2 store is unavailable/corrupt:

```text
provider mutation = zero
exact owner remains active
operator recovery required
```

No fallback to legacy cash.

---

## 88. Incomplete broker operation sync

If CL3 pagination/deadline fails or CL2 append cannot complete:

```text
watermark does not advance
CL6 context not actionable
provider mutation = zero
```

---

# PART P — LEGACY AND DIAGNOSTIC PATHS

## 89. Legacy bot behavior

`SandboxTradingBot` keeps predecessor behavior only in effective:

```text
LEGACY_ACTIVE
```

For any other CL7 effective state, new direct execution returns a finite fail-closed result before `INTENT_SAVED` / POST.

Existing recovery of a prior pending legacy order remains permitted and never resubmits automatically.

---

## 90. CLI startup behavior

`run_bot.py --execute`:

### LEGACY_ACTIVE

May retain predecessor double-arm semantics:

```text
--execute
+
ARM_SANDBOX_TRADING=YES
```

### CUTOVER_PREPARED / CUTOVER_CONFIRMED / any EXACT state

Must terminate/fail closed before enabling the legacy direct submission path.

Operator is directed to the CL7 tool.

No exact activation occurs automatically.

---

## 91. Diagnostics behavior

New diagnostic BUY/SELL provider POST is permitted only in `LEGACY_ACTIVE`.

In PREPARED/CONFIRMED/EXACT modes:

```text
new diagnostic mutation = blocked
```

Read-only snapshot/recovery of already-persisted diagnostic state may continue.

No emergency reduce-only bypass is introduced by CL7 V1.

---

# PART Q — OPERATOR SURFACE

## 92. Operator tool

Future implementation path:

```text
current/tools/v3_10_runtime_cash_cutover.py
```

Exact version-1 commands:

```text
status

prepare

confirm

activate

cancel

arm

disarm

rollback

sync

recover

inspect

dispatch
```

No command other than `dispatch` may invoke order POST.

`prepare`, `activate`, `sync`, `inspect` may perform authenticated read-only provider calls when separately authorized.

---

## 93. Operator phrases

Exact phrases:

```text
prepare:
PREPARE V3.10 CL7 EXACT CASH CUTOVER

confirm:
CONFIRM V3.10 CL7 EXACT CASH CUTOVER

activate:
ACTIVATE V3.10 CL7 EXACT CASH AUTHORITY

arm:
ARM V3.10 CL7 SANDBOX EXACT CASH EXECUTION

rollback:
ROLLBACK V3.10 CL7 TO LEGACY CASH AUTHORITY
```

Safe commands:

```text
status
cancel
disarm
inspect
```

do not require dangerous-action phrase.

`dispatch` requires current persistent `EXACT_CASH_ARMED` and explicit expected intent ID.

No command selects a different queue head automatically.

---

## 94. Operator output privacy

CLI output may contain:

- finite status/reason;
- privacy-safe hashes;
- revisions;
- counts;
- timestamps;
- authority state/owner;
- whether execution is armed;
- whether recovery is required.

It MUST NOT print:

- token;
- raw Account ID;
- raw provider payload;
- raw intent/order ID in shareable default output;
- authorization headers;
- identity key;
- filesystem secret content.

Interactive/private mode may accept an intent ID as input but must redact it from default structured evidence.

---

# PART R — PRE-POST EXACT ALGORITHM

## 95. One exact dispatch algorithm

`SandboxExecutionAdapter` exact branch MUST follow this order:

```text
A. acquire RuntimeCashAuthority lock

B. require EXACT_CASH_ARMED

C. validate account scope / identity key

D. contiguous CL3 -> CL2 sync

E. obtain final GetPortfolio response

F. obtain final GetSandboxPositions response

G. acquire Portfolio lock

H. construct/revalidate PortfolioIdentityEvidence

I. acquire Risk profile lock

J. acquire Risk state lock

K. construct/revalidate RiskGuardEvidence
   and existing Risk dispatch guard

L. acquire CashLedger lock

M. export/freeze current ledger

N. enter Central locked dispatch lease

O. build current CentralReservationProjection

P. rebuild CL4 reconciliation

Q. rebuild CL5 CashAvailability

R. rebuild CL6 PortfolioRiskCashContext

S. require READY_FOR_LOCKED_REVALIDATION
   + CL7 <=10s age rules

T. build LockedDispatchProof

U. Central QUEUED -> IN_FLIGHT persist

V. persist CL7 DISPATCH_ATTEMPT_RECORDED

W. exactly one provider POST

X. persist Central outcome under same Central lease

Y. leave locks

Z. recovery/reconciliation continues from durable state
```

An ordinary returned failure at A through U, or a failed step-V commit for which
the writer retains control and exact read-back proves the pending marker absent:

```text
provider POST = zero
```

A process crash, process kill, host loss or indeterminate write outcome after
step V begins is not covered by that statement. If read-back finds the pending
marker committed, even when the last observed program counter was before W, it
is exactly D4:

```text
provider call boundary = not provable from durable evidence
broker lookup required
automatic resubmit forbidden
```

Once control enters W, every exception/result is classified only by section 80.
Tests MUST separately cover an injected in-process failure before the transport
call and a process-boundary D4 crash after the marker.

---

## 96. Market precheck placement

Read-only market-status check may occur before or after A, but it cannot authorize dispatch.

If it is performed before final exact validation, its result must still be valid for the intended order type at provider mutation time according to predecessor bounded market policy.

Market precheck failure:

```text
zero provider POST
```

---

## 97. No proof reuse

`LockedDispatchProof` is one-attempt-only.

After:

- lock release;
- Central revision change;
- authority record revision change;
- provider attempt marker;
- timeout;
- failed call;

the proof cannot be reused.

A retry means an entirely new dispatch cycle with new proof.

---

# PART S — PUBLIC CL7 API

## 98. New core module

`runtime_cash_authority.py` exports only bounded CL7 runtime surfaces, expected to include:

```text
RuntimeCashAuthorityState

RuntimeCashAuthorityOwner

CL7RuntimeReason
CL7RuntimeError

RuntimeCashAuthorityRecord

LockedDispatchProof

RuntimeCashAuthorityStore

RuntimeCashAuthorityManager

legacy_execution_guard
```

Exact symbol names may be changed only by contract correction/review before implementation acceptance.

No wildcard API.

---

## 99. RuntimeCashAuthorityManager responsibilities

The manager owns only:

- authority record;
- CL3→CL2 sync orchestration;
- opening/reconciliation/availability/context orchestration;
- cutover/arm/disarm/rollback state transitions;
- exact locked dispatch proof construction;
- dispatch attempt marker;
- recovery classification.

It does NOT own:

- positions;
- Central reservations;
- RiskState;
- provider orders;
- provider transport session.

---

# PART T — ERROR CONTRACT

## 100. Closed CL7RuntimeReason set

Version 1 exact closed set:

```text
TYPE_INVALID

VERSION_UNSUPPORTED

ENVIRONMENT_UNSUPPORTED

ACCOUNT_SCOPE_INVALID

IDENTITY_KEY_INVALID

TIMESTAMP_INVALID

CONFIRMATION_INVALID

STATE_INVALID

STATE_TRANSITION_INVALID

AUTHORITY_RECORD_MISSING

AUTHORITY_RECORD_CORRUPT

AUTHORITY_CHECKSUM_INVALID

CAS_CONFLICT

CUTOVER_NOT_QUIESCENT

LEGACY_PENDING_OPERATION

LEDGER_UNAVAILABLE

LEDGER_SYNC_INCOMPLETE

BROKER_READ_FAILED

OPENING_INVALID

RECONCILIATION_BLOCKED

AVAILABILITY_BLOCKED

CONTEXT_BLOCKED

CONTEXT_STALE

LOCK_UNAVAILABLE

LOCK_ORDER_VIOLATION

PORTFOLIO_CHANGED

RISK_CHANGED

LEDGER_CHANGED

CENTRAL_CHANGED

DISPATCH_NOT_ARMED

DISPATCH_PENDING

DISPATCH_PROOF_INVALID

ATTEMPT_RECORD_FAILED

PROVIDER_REJECTED

PROVIDER_OUTCOME_UNCERTAIN

RECOVERY_REQUIRED

ROLLBACK_FORBIDDEN_AFTER_ATTEMPT

PRIVACY_BOUNDARY_FAILED

INTERNAL_BOUNDARY_FAILED
```

Adding a reason requires CL7 version transition or rescope.

---

## 101. Error privacy

Public CL7 errors expose only:

- finite CL7 reason;
- optional finite accepted predecessor reason;
- privacy-safe SHA/revision/stage;
- retryable boolean where appropriate.

Never:

- raw Account ID;
- raw provider payload;
- token;
- identity key;
- raw intent/order ID;
- filesystem secret path;
- raw unexpected exception text.

Unexpected exceptions map:

```text
INTERNAL_BOUNDARY_FAILED
```

without public chaining.

---

# PART U — FIRST-FAILURE ORDER

## 102. Authority record load

Exact order:

1. current-file presence/compatibility rule;
2. byte-size/canonical JSON;
3. exact keyset/domain/version;
4. scalar types/ranges;
5. state/field optionality;
6. companion checksum;
7. state-derived invariants;
8. revision-0 null rule or exact previous-record -> canonical lastgood SHA/revision chain;
9. closed `transition_kind` compatibility with current/lastgood state pair.

---

## 103. prepare

1. exact public argument types;
2. current authority custody;
3. state must be LEGACY_ACTIVE;
4. confirmation;
5. environment/key/key-id/account scope;
6. acquire authority lock/CAS;
7. persist PREPARED freeze;
8. CL2 store/opening validation;
9. provider current-cash proof;
10. opening create/reuse;
11. contiguous sync;
12. CL4;
13. CL5;
14. Portfolio/Risk evidence;
15. CL6 preview context;
16. PREPARED custody update.

---

## 104. confirm

1. authority custody;
2. state PREPARED;
3. confirmation;
4. key/account binding;
5. authority lock/CAS;
6. quiescence;
7. contiguous sync;
8. fresh CL4;
9. fresh CL5;
10. fresh CL6 READY;
11. transition CONFIRMED.

---

## 105. activate

1. authority custody;
2. state CONFIRMED;
3. confirmation;
4. account/key binding;
5. authority lock/CAS;
6. quiescence;
7. fresh sync;
8. current broker proofs;
9. local evidence validation;
10. CL4;
11. CL5;
12. CL6 READY;
13. atomic EXACT_CASH_DISARMED owner-switch record.

---

## 106. arm

1. authority custody;
2. state EXACT_CASH_DISARMED;
3. no pending dispatch;
4. confirmation;
5. account/key binding;
6. atomic transition EXACT_CASH_ARMED.

No broker call.

---

## 107. exact dispatch

1. public inputs;
2. authority custody;
3. state EXACT_CASH_ARMED;
4. account/key binding;
5. authority lock/CAS;
6. pending/recovery absence;
7. expected intent identity input;
8. CL3 sync;
9. provider cash/positions reads;
10. Portfolio lock/evidence;
11. Risk profile/state locks/evidence;
12. ledger lock/export;
13. Central lock / exact queue head;
14. Central projection;
15. CL4;
16. CL5;
17. CL6;
18. <=10s freshness/skew;
19. LockedDispatchProof;
20. Central IN_FLIGHT transition;
21. durable attempt marker;
22. exactly one provider POST;
23. Central outcome classification.

---

## 108. rollback

1. authority custody;
2. state EXACT_CASH_DISARMED;
3. post_attempt_count must equal zero;
4. pending dispatch null;
5. confirmation;
6. authority lock/CAS;
7. quiescence;
8. fresh sync;
9. fresh coherent CL4/CL5/CL6;
10. atomic LEGACY_ACTIVE owner-switch record.

---

# PART V — DETERMINISM / CLOCK

## 109. Caller-controlled canonical timestamps

All pure CL7 canonical builders receive timestamps explicitly.

No canonical identity reads system clock internally.

Runtime manager/CLI may obtain current UTC only at the outer I/O boundary and pass the exact normalized timestamp inward.

---

## 110. Controlled-clock qualification

CL7 implementation tests include two synthetic clock families:

```text
CLOCK_A
CLOCK_B
```

They must prove:

- identical evidence + different timestamp changes relevant record/proof hash;
- stale evidence cannot become fresh by mutating only an unbound timestamp;
- future dependency fails closed;
- restart does not use wall-clock inference to reinterpret whether POST occurred;
- attempt marker ordering remains deterministic.

No OS clock manipulation or authenticated Sandbox access is required for CL7 implementation acceptance.

Live controlled-clock qualification, if any, remains separately gated.

---

# PART W — CONTRACT-OWNED KAT

## 111. Common synthetic key

```text
identity_key_hex =
000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f

identity_key_id =
CL5_TEST_KEY_V1

account_scope_sha256 =
15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3
```

This is synthetic test material only.

---

## 112. LEGACY_ACTIVE record KAT

Exact canonical object:

```json
{"account_scope_sha256":null,"activation_context_sha256":null,"cutover_generation":"0","domain":"v3.10-cl7-runtime-cash-authority","environment":"SANDBOX","ever_exact_activated":false,"identity_key_id":null,"ledger_head_sha256":null,"ledger_revision":null,"opening_cutoff":null,"opening_record_sha256":null,"operations_complete_through":null,"pending_dispatch_proof_sha256":null,"post_attempt_count":"0","previous_record_sha256":null,"record_revision":"0","state":"LEGACY_ACTIVE","transition_at":"2026-09-11T10:00:00.000000000Z","transition_kind":"BOOTSTRAP_LEGACY","version":1}
```

Expected:

```text
record_sha256 =
04be4a12386488e9d85a9ae5208c0f120100f75e047da2418ec68fe80d4051bf
```

---

## 113. EXACT_CASH_ARMED record KAT

Exact canonical object:

```json
{"account_scope_sha256":"15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3","activation_context_sha256":"fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9","cutover_generation":"1","domain":"v3.10-cl7-runtime-cash-authority","environment":"SANDBOX","ever_exact_activated":true,"identity_key_id":"CL5_TEST_KEY_V1","ledger_head_sha256":"4444444444444444444444444444444444444444444444444444444444444444","ledger_revision":"5","opening_cutoff":"2026-09-11T10:00:00.000000000Z","opening_record_sha256":"7777777777777777777777777777777777777777777777777777777777777777","operations_complete_through":"2026-09-11T10:00:00.000000001Z","pending_dispatch_proof_sha256":null,"post_attempt_count":"0","previous_record_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","record_revision":"5","state":"EXACT_CASH_ARMED","transition_at":"2026-09-11T10:00:05.000000000Z","transition_kind":"ARM_EXACT","version":1}
```

Revision 5 is the minimum reachable persisted sequence:

```text
0 bootstrap
1 prepare freeze
2 preparation evidence bound
3 confirm
4 activate exact/disarmed
5 arm
```

The synthetic `cccc...` previous SHA freezes record-byte canonicalization only;
the separate custody-chain tests MUST generate a real revision-4 lastgood whose
SHA exactly supplies this field rather than attempt to use `cccc...` as a known
preimage.

Expected:

```text
record_sha256 =
ed8146dd11385e70fb5a0c41b9c93006f8810ab1cf916b3429bf4beb5872f3bf
```

---

## 114. Intent-scope KAT

Synthetic raw intent:

```text
intent-001
```

HMAC preimage:

```json
{"account_scope_sha256":"15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3","domain":"v3.10-cl7-intent-scope","intent_id":"intent-001","version":1}
```

Expected:

```text
intent_scope_sha256 =
b2b1ed88b2fa5b5eb29414fda5990ef241c97d53ff09d8f3a45680aedb999551
```

---

## 115. LockedDispatchProof KAT

Synthetic Money:

```text
free_investable_cash =
50.000000000 RUB

reserved_cash =
10.000000000 RUB
```

Inputs:

```text
authority_record_revision = 5

authority_record_sha256 =
ed8146dd11385e70fb5a0c41b9c93006f8810ab1cf916b3429bf4beb5872f3bf

availability_sha256 =
cd375c47f6dc528ae8e64a13dfb7de9145f5964988893c7ef20561050411e238

central_order_revision = 7

central_reservation_projection_hash =
5555555555555555555555555555555555555555555555555555555555555555

cl6_context_identity_sha256 =
05409978eaff9beafb68c9c4e2a0531c15995e8f337604dbbdc964e12a975bd6

cl6_context_sha256 =
fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9

current_lots = 0
target_lots = 1
direction = BUY

evaluated_at =
2026-09-11T10:00:06.000000000Z

ledger_revision = 5

ledger_head_sha256 =
4444444444444444444444444444444444444444444444444444444444444444

portfolio_revision = 9

portfolio_decision_checksum =
aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

portfolio_document_checksum =
abababababababababababababababababababababababababababababababab

reconciliation_sha256 =
1111111111111111111111111111111111111111111111111111111111111111

risk_policy_hash =
dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd

risk_state_guard_hash =
eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
```

Exact HMAC preimage:

```json
{"account_scope_sha256":"15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3","authority_record_revision":"5","authority_record_sha256":"ed8146dd11385e70fb5a0c41b9c93006f8810ab1cf916b3429bf4beb5872f3bf","availability_sha256":"cd375c47f6dc528ae8e64a13dfb7de9145f5964988893c7ef20561050411e238","central_order_revision":"7","central_reservation_projection_hash":"5555555555555555555555555555555555555555555555555555555555555555","cl6_context_identity_sha256":"05409978eaff9beafb68c9c4e2a0531c15995e8f337604dbbdc964e12a975bd6","cl6_context_sha256":"fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9","current_lots":0,"direction":"BUY","domain":"v3.10-cl7-locked-dispatch-proof-identity","evaluated_at":"2026-09-11T10:00:06.000000000Z","free_investable_cash":{"amount":"50.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"50000000000","scale":9,"version":1},"identity_key_id":"CL5_TEST_KEY_V1","intent_scope_sha256":"b2b1ed88b2fa5b5eb29414fda5990ef241c97d53ff09d8f3a45680aedb999551","ledger_head_sha256":"4444444444444444444444444444444444444444444444444444444444444444","ledger_revision":"5","portfolio_decision_checksum":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","portfolio_document_checksum":"abababababababababababababababababababababababababababababababab","portfolio_revision":"9","reconciliation_sha256":"1111111111111111111111111111111111111111111111111111111111111111","reserved_cash":{"amount":"10.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"10000000000","scale":9,"version":1},"risk_policy_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","risk_state_guard_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","target_lots":1,"version":1}
```

Expected HMAC:

```text
proof_identity_sha256 =
82441ccd5fbc1f0f65e9a81715af753a2a008dc7ea142384a96a3684e64ada9c
```

Exact canonical proof object:

```json
{"account_scope_sha256":"15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3","authority_record_revision":"5","authority_record_sha256":"ed8146dd11385e70fb5a0c41b9c93006f8810ab1cf916b3429bf4beb5872f3bf","availability_sha256":"cd375c47f6dc528ae8e64a13dfb7de9145f5964988893c7ef20561050411e238","central_order_revision":"7","central_reservation_projection_hash":"5555555555555555555555555555555555555555555555555555555555555555","cl6_context_identity_sha256":"05409978eaff9beafb68c9c4e2a0531c15995e8f337604dbbdc964e12a975bd6","cl6_context_sha256":"fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9","current_lots":0,"direction":"BUY","domain":"v3.10-cl7-locked-dispatch-proof","evaluated_at":"2026-09-11T10:00:06.000000000Z","free_investable_cash":{"amount":"50.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"50000000000","scale":9,"version":1},"identity_key_id":"CL5_TEST_KEY_V1","intent_scope_sha256":"b2b1ed88b2fa5b5eb29414fda5990ef241c97d53ff09d8f3a45680aedb999551","ledger_head_sha256":"4444444444444444444444444444444444444444444444444444444444444444","ledger_revision":"5","portfolio_decision_checksum":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","portfolio_document_checksum":"abababababababababababababababababababababababababababababababab","portfolio_revision":"9","proof_identity_sha256":"82441ccd5fbc1f0f65e9a81715af753a2a008dc7ea142384a96a3684e64ada9c","reconciliation_sha256":"1111111111111111111111111111111111111111111111111111111111111111","reserved_cash":{"amount":"10.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"10000000000","scale":9,"version":1},"risk_policy_hash":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","risk_state_guard_hash":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee","target_lots":1,"version":1}
```

Expected canonical proof SHA:

```text
locked_dispatch_proof_sha256 =
28277dfa964d864f9f0e1a1aa4fb61cae99536cb8f0690df20f37f32a32dc2a1
```

---

## 116. Pending-attempt record KAT

After persisting the KAT dispatch attempt:

```text
state =
EXACT_CASH_DISPATCH_PENDING

record_revision =
6

post_attempt_count =
1

pending_dispatch_proof_sha256 =
28277dfa964d864f9f0e1a1aa4fb61cae99536cb8f0690df20f37f32a32dc2a1

previous_record_sha256 =
ed8146dd11385e70fb5a0c41b9c93006f8810ab1cf916b3429bf4beb5872f3bf

transition_at =
2026-09-11T10:00:06.500000000Z

transition_kind =
DISPATCH_ATTEMPT_RECORDED
```

Exact canonical pending record:

```json
{"account_scope_sha256":"15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3","activation_context_sha256":"fd4f5ecda228215f6ac2677d2ac1489a4d47b631258075d67b0fa91165e5d1d9","cutover_generation":"1","domain":"v3.10-cl7-runtime-cash-authority","environment":"SANDBOX","ever_exact_activated":true,"identity_key_id":"CL5_TEST_KEY_V1","ledger_head_sha256":"4444444444444444444444444444444444444444444444444444444444444444","ledger_revision":"5","opening_cutoff":"2026-09-11T10:00:00.000000000Z","opening_record_sha256":"7777777777777777777777777777777777777777777777777777777777777777","operations_complete_through":"2026-09-11T10:00:00.000000001Z","pending_dispatch_proof_sha256":"28277dfa964d864f9f0e1a1aa4fb61cae99536cb8f0690df20f37f32a32dc2a1","post_attempt_count":"1","previous_record_sha256":"ed8146dd11385e70fb5a0c41b9c93006f8810ab1cf916b3429bf4beb5872f3bf","record_revision":"6","state":"EXACT_CASH_DISPATCH_PENDING","transition_at":"2026-09-11T10:00:06.500000000Z","transition_kind":"DISPATCH_ATTEMPT_RECORDED","version":1}
```

Expected record SHA:

```text
a082ea89b08fd86172728b3aef627086a0d59349f699eddc11582c4fa6034829
```

---

## 117. KAT mutation requirement

Future fixture MUST independently mutate every authoritative field in:

```text
RuntimeCashAuthorityRecord
LockedDispatchProof
```

and prove the plain SHA/HMAC changes where applicable.

At minimum:

```text
state
revision
generation
account scope
key id
ledger revision/head
opening cutoff/record
operations watermark
activation context
post attempt count
pending proof
transition timestamp/kind
previous record SHA

intent scope
direction
current/target lots
reservation +/- 1 nano
free cash +/- 1 nano
Portfolio hashes/revision
Risk hashes
Central revision/hash
availability/reconciliation/context hashes
evaluated_at
```

---

# PART X — ACCEPTANCE MATRIX

## 118. Mandatory CL7 implementation cases

Future implementation suite includes at least:

```text
V310-CL7-01
exact predecessor + exact implementation allowlist

V310-CL7-02
bootstrap creates only LEGACY_ACTIVE authority record

V310-CL7-03
bootstrap never activates/arms/exposes identity key

V310-CL7-04
authority canonical/checksum KAT

V310-CL7-05
missing-record compatibility all-artifacts-absent only

V310-CL7-06
missing current + lastgood/ledger present -> RECOVERY_BLOCKED

V310-CL7-07
corrupt current never auto-restores legacy lastgood

V310-CL7-08
CAS conflict + exact previous-record/lastgood revision/SHA chain

V310-CL7-09
exact state/transition_kind/generation/field-mutation matrix

V310-CL7-10
state-derived single-owner mapping

V310-CL7-11
prepare exact confirmation

V310-CL7-12
PREPARED freezes new legacy bot intent before INTENT_SAVED

V310-CL7-13
PREPARED freezes new diagnostic intent before INTENT_SAVED

V310-CL7-14
prepare opening create-once

V310-CL7-15
prepare reuses exact existing opening

V310-CL7-16
conflicting opening blocks

V310-CL7-17
first sync strictly after opening cutoff

V310-CL7-18
CL3 one-attempt transport; no nested retry

V310-CL7-19
CL3 retry count owned only by collect_tbank_operations

V310-CL7-20
CL3->CL2 TRANSACTION_PROPOSED mapping

V310-CL7-21
CL3->CL2 REVIEW_REQUIRED mapping

V310-CL7-22
CL3->CL2 NOT_LEDGER_RELEVANT mapping

V310-CL7-23
mid-batch crash keeps watermark old and replay converges

V310-CL7-24
source/content/economic conflict blocks

V310-CL7-25
quiescence Central QUEUED blocks confirm/activate

V310-CL7-26
IN_FLIGHT/SUBMITTED/UNCERTAIN block confirm/activate

V310-CL7-27
legacy pending state blocks cutover

V310-CL7-28
diagnostic pending state blocks cutover

V310-CL7-29
confirm exact phrase

V310-CL7-30
activation exact phrase

V310-CL7-31
owner linearization only after full authority tuple commit/read-back

V310-CL7-32
activation crash before active replace: old owner or RECOVERY_BLOCKED

V310-CL7-33
activation crash between active/checksum/lastgood boundaries -> RECOVERY_BLOCKED;
fully committed tuple -> exact/disarmed

V310-CL7-34
legacy --execute rejected in exact/prepared state

V310-CL7-35
exact arm exact phrase

V310-CL7-36
disarm is safe/no POST

V310-CL7-37
cancel preparation leaves ledger evidence intact

V310-CL7-38
rollback allowed only before any attempt

V310-CL7-39
rollback after post attempt permanently blocked

V310-CL7-40
rollback never rewinds CL2/Central/Portfolio/Risk

V310-CL7-41
global lock-order adversarial test

V310-CL7-42
lock timeout -> zero POST

V310-CL7-43
Central locked lease holds through POST handoff

V310-CL7-44
queue head drift -> zero POST

V310-CL7-45
Central reservation drift -> zero POST

V310-CL7-46
Portfolio revision/checksum drift -> zero POST

V310-CL7-47
Risk policy/state drift -> zero POST

V310-CL7-48
ledger revision/head drift -> zero POST

V310-CL7-49
CL4 reconciliation drift -> zero POST

V310-CL7-50
CL5 availability non-READY -> zero POST

V310-CL7-51
CL6 context non-READY -> zero POST

V310-CL7-52
>10s final evidence age -> zero POST

V310-CL7-53
future timestamp -> zero POST

V310-CL7-54
cross-evidence skew -> zero POST

V310-CL7-55
intent-scope HMAC KAT

V310-CL7-56
LockedDispatchProof exact identity preimage/canonical object HMAC/SHA KAT

V310-CL7-57
every proof field mutation invalidates identity

V310-CL7-58
Central QUEUED->IN_FLIGHT persists exact proof/request binding before marker

V310-CL7-59
attempt marker persisted before physical POST

V310-CL7-60
attempt-marker persistence failure -> zero POST

V310-CL7-61
one dispatch -> at most one physical POST

V310-CL7-62
provider explicit rejection exact finite predicate + all exclusions

V310-CL7-63
provider timeout -> no retry + recovery required

V310-CL7-64
malformed/mismatched provider response -> uncertain

V310-CL7-65
accepted response + Central persistence failure -> uncertain

V310-CL7-66
D3 crash pre-attempt marker proves no POST

V310-CL7-67
D4 process-boundary uncertainty distinguished from returned pre-W failure;
D4/D5/D6 recovery performs exact correlated lookup only

V310-CL7-68
D8 reconcile-only

V310-CL7-69
D9 Risk-account-only

V310-CL7-70
D10 finalize-only

V310-CL7-71
startup pending attempt never auto-arms

V310-CL7-72
new exact dispatch blocked while pending attempt exists

V310-CL7-73
direct bot POST impossible in exact mode

V310-CL7-74
direct diagnostics POST impossible in exact mode

V310-CL7-75
repository production AST scan: exact mode has one order POST owner

V310-CL7-76
operator output privacy scan

V310-CL7-77
identity key never persisted/logged

V310-CL7-78
raw Account ID absent from CL7 canonical evidence

V310-CL7-79
controlled CLOCK_A/CLOCK_B hash/freshness matrix

V310-CL7-80
full unchanged CL1–CL6 semantic regression

V310-CL7-81
bounded inherited custody-oracle failures only

V310-CL7-82
no authenticated provider access in implementation tests

V310-CL7-83
no GUI/release/workflow file delta

V310-CL7-84
exact implementation path allowlist
```

---

## 119. Inherited CI/custody baseline

CL7 starts from accepted CL6 predecessor whose terminal current-base CI has an explicitly dispositioned inherited custody-oracle failure set.

CL7 full-regression evaluation MUST compare against the exact predecessor baseline.

Rule:

```text
same bounded inherited custody-oracle failures
+
no new failure
=
baseline-compatible

any additional/different failure
=
BLOCK
```

A disappeared inherited failure does not compensate for a new failure.

The exact baseline failure identities must be copied mechanically from accepted CL6 integration evidence into the CL7 implementation review record.

---

# PART Y — ADVERSARIAL MATRIX

## 120. Authority custody adversarial cases

At minimum:

```text
record truncated
record non-canonical
duplicate JSON key
wrong checksum
wrong domain
wrong version
bool revision
negative revision
revision overflow
state/field optionality mismatch
pending proof in non-pending state
pending state without proof
ever_exact false in exact state
previous SHA malformed
previous SHA well-formed but not equal to lastgood hash
lastgood revision not active revision minus one
crash before/after each section-24 replace/fsync boundary
active new + checksum old
active/checksum new + lastgood wrong
current missing + lastgood present
current missing + ledger present
lastgood older LEGACY after exact activation
```

All fail closed as specified.

---

## 121. Cutover race cases

At minimum:

```text
legacy bot reaches intent creation while prepare acquires authority lock

diagnostic reaches intent creation while prepare acquires authority lock

Central becomes QUEUED between preview and confirm

Central becomes QUEUED between confirm and activation

ledger changes after provider read before final lock

RiskState changes after evidence capture before Risk lock

Portfolio changes after evidence capture before Portfolio lock

authority record CAS changes during operator command
```

No race may create double owner or provider mutation from stale proof.

---

## 122. Dispatch race cases

At minimum:

```text
queue-head change
reservation amount change
Central revision change
stored Central proof missing/malformed/non-canonical
stored Central proof SHA differs from authority pending SHA
stored proof intent scope differs from raw Central intent HMAC
provider request ID differs from raw Central intent ID
Portfolio revision-only change
Portfolio checksum-only change
Risk policy change
Risk state guard change
ledger revision change
ledger head mismatch
broker cash change causing reconciliation delta
provider blocked cash change
CL6 context timestamp staleness
authority disarm racing with dispatch
rollback racing with dispatch
second dispatch process racing with first
HTTP 408/409/425/429 classified uncertain
HTTP 5xx classified uncertain
status-less TLS/socket/timeout exception classified uncertain
non-transient direct HTTP 4xx outside the excluded set classified rejection
success response missing/mismatched orderRequestId classified uncertain
returned failure before W with marker absent proves zero POST
D4 process crash with marker present never proves zero POST
```

Exactly one authority lock winner may progress.

---

# PART Z — PRIVACY / SIDE EFFECT BOUNDARY

## 123. Repository implementation tests are synthetic

CL7 implementation/local review MUST use:

- fake provider transport;
- synthetic Account ID;
- synthetic identity key;
- temp runtime directories;
- injected clocks.

No T-Bank token.

No private account.

No Cloud/Sandbox call.

No experiment.

---

## 124. Runtime provider methods allowed by CL7 code

When separately authorized at runtime:

Read-only:

```text
GetSandboxOperationsByCursor
GetPortfolio
GetSandboxPositions
GetOrders / GetOrderState
GetTradingStatus
```

Economic mutation:

```text
PostSandboxOrder
```

only through the exact CL7 `SandboxExecutionAdapter` path.

No sandbox pay-in/open-account is part of CL7 exact dispatch authority.

---

# REVIEW / GOVERNANCE

## 125. Contract review

Exactly:

```text
one independent/adversarial CL7 contract review
```

Result:

```text
PASS
```

or finite fixed set:

```text
CL7-R1-xx
```

At most:

```text
one bounded contract correction batch
```

followed by:

```text
one finding-scoped closure review
```

Material blocker remaining after closure:

```text
RESCOPE / ABORT / DEFER
```

No recursive correction treadmill.

---

## 126. Implementation review

Exactly:

```text
one independent/adversarial implementation review

at most one bounded implementation correction batch

one finding-scoped closure review
```

New material safety/code blocker after closure:

```text
RESCOPE / ABORT / DEFER
```

not another correction loop.

---

## 127. Integration-readiness

Later integration-readiness checks only:

- exact accepted contract identity;
- exact accepted implementation identity;
- exact CL6 predecessor/base/merge-base;
- exact cumulative allowlist;
- frozen contract blob;
- exact-head CI/current-base synthetic merge identity;
- dependency/import/call-graph drift;
- preservation of single-owner/provider-mutation boundary.

It is not another semantic implementation review.

---

## 128. Contract acceptance binding

Explicit CL7 contract acceptance must bind:

```text
exact contract commit
exact tree
exact predecessor
exact merge-base
exact one-file contract delta
```

Acceptance does NOT authorize:

```text
implementation
push
merge
runtime cutover
provider access
Sandbox experiment
provider order POST
release
real-account execution
```

---

## 129. Implementation acceptance binding

Later implementation acceptance must bind:

```text
exact implementation head/tree
exact accepted contract parent/ancestry
exact implementation allowlist
local deterministic verification
review/closure disposition
```

Still no provider/private-account experiment authority is implied.

---

## 130. Merge boundary

Even accepted CL7 implementation + green/bounded CI does not itself authorize stable-line mutation.

Merge requires separate explicit decision.

After merge:

```text
CL7 code exists
but runtime state remains LEGACY_ACTIVE/disarmed unless operator cutover occurs
```

---

## 131. Exit disposition before contract acceptance

Current authority after writing this contract candidate:

```text
CL7 CONTRACT FREEZE CANDIDATE

IMPLEMENTATION BLOCKED

CUTOVER NOT AUTHORIZED

EXACT CASH OWNER NOT ACTIVATED

PROVIDER MUTATION AUTHORITY UNCHANGED

SANDBOX EXPERIMENT NOT AUTHORIZED

REAL-ACCOUNT EXECUTION FORBIDDEN
```

The only currently authorized repository change remains:

```text
docs/project/V3_10_CL7_RUNTIME_CUTOVER_RECOVERY_CONTRACT_RU.md
```
