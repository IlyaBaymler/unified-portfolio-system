# V3.10 CL5 — CashAvailability + Central reservation projection: bounded contract freeze

Статус:

`CL5 CONTRACT FREEZE CANDIDATE / IMPLEMENTATION BLOCKED / SHADOW READ-ONLY`

Parent program: stable-line v3.10 → v4.

Historical evidence: Issue #53.

Accepted predecessor:

`CL4 = ACCEPTED / INTEGRATED / COMPLETED`.

---

## 1. Exact predecessor и lineage

CL5 contract work начинается только от exact integrated CL4 stable-line head:

```text
repository = baimleriv/unified-portfolio-system
program branch = program/v3-10-v4-stable-line

exact predecessor commit =
c3befe877e5f0d0058fbf485cb42ff54d57da7de

exact predecessor tree =
4eb657c48437a851951c8fe5ed48ab8d85e730a1

contract branch =
agent/v3-10-clean-cl5-contract-freeze

HEAD at branch creation = exact predecessor
merge-base = exact predecessor
ahead = 0
behind = 0
working tree = clean
```

Допустимая ancestry:

```text
v3.9 oracle
-> accepted CL0
-> accepted CL1
-> accepted CL2
-> accepted CL3
-> accepted CL4
-> CL5 contract candidate
```

`main`, historical v3.10/MoneyV2 branches и старые Issue implementations являются только evidence/reference и не являются ancestry или integration authority.

---

## 2. Contract-freeze allowlist

До отдельного exact-head CL5 contract acceptance разрешено менять ровно один repository path:

```text
docs/project/V3_10_CL5_CASH_AVAILABILITY_CONTRACT_RU.md
```

Любой другой changed, added, deleted, renamed или untracked repository path:

```text
SCOPE_VIOLATION -> RESCOPE
```

Во время contract freeze запрещено менять:

- CL1–CL4 implementation;
- CentralOrderManager;
- Portfolio;
- Risk;
- Execution;
- GUI/runtime;
- tests;
- fixtures;
- workflows;
- dependency manifests;
- release files.

---

## 3. Frozen future implementation allowlist

Только после:

1. independent/adversarial contract review;
2. contract closure;
3. explicit acceptance exact contract commit/tree;

может быть создана отдельная implementation branch непосредственно от accepted contract head.

Future implementation delta ограничен ровно тремя paths:

```text
current/trading_robot/cash_availability.py

current/tests/test_v3_10_cash_availability.py

current/tests/fixtures/v3_10_cash_availability_vectors.json
```

Accepted CL5 contract file immutable во время implementation.

CL1–CL4 files и `central_order_manager.py` immutable.

Относительно exact CL4 predecessor максимальный cumulative CL5 surface:

```text
1 contract path
+
3 implementation paths
=
4 paths
```

Четвёртый implementation path или изменение существующего predecessor file:

```text
SCOPE_EXPANSION_REQUIRED -> RESCOPE
```

---

## 4. Bounded mission

CL5 создаёт только immutable read-only proof:

```text
CashAvailabilitySnapshot
```

из согласованного набора уже существующих evidence:

```text
accepted CL4 cash reconciliation
+
exact CL2 ledger export used to revalidate CL4
+
caller-supplied read-only GetSandboxPositions response
+
immutable CentralOrderState reservation projection
->
CashAvailabilitySnapshot
```

CL5 обязан:

1. получить exact RUB provider blocked-cash evidence;
2. связать его с exact CL4 broker cash;
3. построить privacy-safe projection Central reservations;
4. разделить local reservations по lifecycle;
5. доказать отсутствие double subtraction до вычисления investable cash;
6. вычислять exact scale-9 free cash только при полностью доказанной семантике;
7. fail closed / manual-review при неизвестном overlap;
8. выдавать deterministic canonical snapshot/hash.

CL5 не создаёт новый cash owner.

---

## 5. Fundamental ownership invariant

CL5 — derived proof layer.

Он не владеет:

- broker current cash;
- CashLedger;
- Central reservations;
- Portfolio cash;
- execution budget;
- orders;
- Risk authority.

Принятые ownership boundaries сохраняются:

```text
PortfolioRepository
    = existing canonical portfolio/current-cash owner
      until a later accepted cutover

CentralOrderManager
    = sole reservation / queue / intent owner

ExecutionAdapter
    = provider mutation boundary

CL5 CashAvailabilitySnapshot
    = immutable derived evidence only
```

Invariant:

> `CashAvailabilitySnapshot.free_investable_cash` не является execution authorization.

Даже:

```text
status = READY
```

не разрешает:

- enqueue;
- reservation mutation;
- BUY;
- provider POST;
- Risk PASS;
- runtime cutover.

---

## 6. Explicit non-goals

CL5 не реализует и не авторизует:

- provider transport;
- credentials/token/env loading;
- provider POST;
- order placement/cancel/replace;
- broker mutation;
- запись в CL2;
- новую OperationInbox classification;
- изменение CL4 opening/reconciliation;
- изменение Central state;
- reservation create/replace/release;
- Portfolio mutation;
- Risk cash authorization;
- execution authorization;
- GUI/runtime adoption;
- recovery/startup wiring;
- real-account action;
- experiment;
- release.

Reporting/Risk integration остаётся CL6.

Runtime ownership/cutover остаётся CL7.

---

## 7. Normative dependencies

CL5 использует без переопределения accepted public semantics:

### CL1

```text
Money
MoneyReason
```

и canonical scale-9 RUB arithmetic.

### CL2

CL2 export используется только через принятый CL4 validation path.

CL5 самостоятельно не открывает SQLite и не изменяет store.

### CL3

```text
BrokerEnvironment
money_value_to_money
BrokerReadReason
```

CL5 также воспроизводит exact accepted CL3 account-scope identity formula.

### CL4

```text
CashReconciliation
AdoptionCandidate
AdoptionDisposition
build_adoption_candidate
```

CL5 не переопределяет CL4 cash/reconciliation semantics.

### Central

Read-only imports:

```text
CentralOrderState
RESERVATION_STATUSES
central_reservation_projection_hash
```

CL5 не импортирует `CentralOrderManager` для mutation.

---

## 8. Frozen version axes and constants

Version 1:

```text
CL5_CONTRACT_VERSION = 1

BROKER_POSITIONS_CASH_PROOF_VERSION = 1

CENTRAL_RESERVATION_PROJECTION_VERSION = 1

CASH_AVAILABILITY_SNAPSHOT_VERSION = 1

CL5_CROSS_LANGUAGE_FIXTURE_VERSION = 1
```

Money remains CL1 scale 9.

Frozen constants:

```text
KOPECK_TO_NANO = 10_000_000

MAX_PROOF_AGE_NS = 120_000_000_000

MAX_CROSS_PROOF_SKEW_NS = 10_000_000_000

MAX_RESPONSE_DEPTH = 16

MAX_RESPONSE_NODES = 100_000

MAX_RESPONSE_CANONICAL_BYTES = 1_048_576

MAX_MAPPING_KEYS = 4096

MAX_STRING_SCALARS = 4096

MAX_KEY_SCALARS = 128

MAX_CENTRAL_INTENTS = 100_000
```

Revision bounds reuse accepted Central/CL2 integer ranges.

Python `bool` is never accepted as integer.

No float participates in authoritative arithmetic.

---

## 9. Canonical primitives

CL5 canonical JSON:

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
- float;
- surrogate code points;
- non-canonical reserialization.

SHA-256:

```text
lowercase 64 hex
```

HMAC-SHA-256:

```python
hmac.new(
    identity_key,
    canonical_preimage,
    hashlib.sha256,
).hexdigest()
```

Timestamp:

```text
YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ
```

with real Gregorian UTC date.

---

## 10. Frozen public API surface

CL5 production module exports only:

```text
AvailabilityStatus

AvailabilityReason

OverlapDisposition

CL5Reason

CL5Error

BrokerPositionsCashProof

CentralReservationProjection

CashAvailabilitySnapshot

build_broker_positions_cash_proof

project_central_reservations

build_cash_availability
```

Exact public signatures:

```python
build_broker_positions_cash_proof(
    response: object,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    evaluated_at: str,
    response_complete: bool,
    identity_key: bytes,
    identity_key_id: str,
) -> BrokerPositionsCashProof
```

```python
project_central_reservations(
    state: CentralOrderState,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    evaluated_at: str,
    identity_key: bytes,
    identity_key_id: str,
) -> CentralReservationProjection
```

```python
build_cash_availability(
    ledger_export_bytes: bytes,
    reconciliation: CashReconciliation,
    positions: BrokerPositionsCashProof,
    reservations: CentralReservationProjection,
    *,
    evaluated_at: str,
    identity_key: bytes,
) -> CashAvailabilitySnapshot
```

Другие module-level names private.

Import модуля выполняет zero I/O.

---

## 11. Environment scope

CL5 version 1 поддерживает только:

```text
SANDBOX
RUB
TBANK
```

`PRODUCTION`:

```text
ENVIRONMENT_UNSUPPORTED
```

Никакой silent promotion SANDBOX → PRODUCTION нет.

---

## 12. Provider positions snapshot boundary

CL5 не вызывает provider transport.

`response` — caller-supplied result одного semantic read:

```text
tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxPositions
```

CL5 не принимает raw account ID отдельным аргументом.

Raw account ID может существовать только внутри caller-supplied response и исчезает после account-scope verification.

`response_complete` обязан быть exact `True`.

`False`:

```text
PROOF_INCOMPLETE
```

---

## 13. Response bounds

До semantic interpretation CL5 выполняет bounded traversal всего response graph.

Разрешены только exact built-ins:

```text
dict
list
str
plain int
bool
None
```

Запрещены:

- Mapping subclass;
- tuple;
- dataclass;
- protobuf object;
- bytes;
- float;
- Decimal;
- arbitrary objects.

Bounds:

```text
depth <= MAX_RESPONSE_DEPTH

nodes <= MAX_RESPONSE_NODES

dict keys <= MAX_MAPPING_KEYS

string scalars <= MAX_STRING_SCALARS

key scalars <= MAX_KEY_SCALARS

canonical bytes <= MAX_RESPONSE_CANONICAL_BYTES
```

Cycle и repeated container identity запрещены.

Ignored provider fields также участвуют в bounds и canonical response hash.

---

## 14. PositionsResponse V1 provider profile

Required top-level keys:

```text
accountId

money

blocked

limitsLoadingInProgress
```

Permitted ignored provider arrays:

```text
securities

futures

options
```

Unknown top-level keys fail closed for version 1.

`accountId`:

- non-empty string;
- no surrogate;
- bounded;
- privacy-sensitive;
- never returned or logged.

`money` and `blocked`:

```text
list
```

`limitsLoadingInProgress`:

```text
bool
```

and must be exact:

```text
False
```

`True`:

```text
POSITIONS_LOADING_IN_PROGRESS
```

because availability cannot be derived from incomplete limits.

---

## 15. RUB extraction

Each `money` / `blocked` element must have the provider MoneyValue structural keyset required for identification.

For exact:

```text
currency == RUB
```

the value is parsed through accepted CL3:

```text
money_value_to_money
```

There may be at most one RUB entry in each array.

Duplicate RUB entries:

```text
RESPONSE_SCHEMA_INVALID
```

No RUB entry means exact zero RUB Money.

Foreign currency entries:

- remain part of exact response canonical bytes/hash;
- are not converted to CL1 Money;
- set:

```text
foreign_cash_present = true
```

A version-1 `READY` CashAvailability is forbidden when foreign cash is present.

This preserves RUB-only CL1 semantics without pretending that non-RUB provider values were interpreted.

---

## 16. BrokerPositionsCashProof

Immutable fields:

```text
account_scope_sha256

environment

as_of

positions_money_rub

blocked_rub

foreign_cash_present

response_canonical_sha256

proof_identity_sha256

identity_key_id

response_complete

version
```

`blocked_rub` must be non-negative.

The complete provider response SHA is:

```text
SHA256(exact bounded canonical response bytes)
```

---

## 17. Account-scope verification

CL5 MUST reproduce the accepted CL3 account-scope HMAC exactly.

Preimage:

```json
{
  "account_id": "<raw response accountId>",
  "domain": "v3.10-cl3-account-scope",
  "environment": "SANDBOX",
  "identity_key_id": "<TOKEN>",
  "provider": "TBANK",
  "version": 1
}
```

Computed HMAC must equal supplied:

```text
account_scope_sha256
```

Otherwise:

```text
ACCOUNT_SCOPE_INVALID
```

This creates no new account identity version.

---

## 18. BrokerPositionsCashProof identity

Exact HMAC preimage:

```json
{
  "account_scope_sha256": "<sha>",
  "as_of": "<timestamp>",
  "blocked_rub": "<exact nested CL1 Money canonical object>",
  "domain": "v3.10-cl5-broker-positions-cash-proof",
  "environment": "SANDBOX",
  "foreign_cash_present": false,
  "identity_key_id": "<TOKEN>",
  "positions_money_rub": "<exact nested CL1 Money canonical object>",
  "provider": "TBANK",
  "response_canonical_sha256": "<sha>",
  "rpc": "tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxPositions",
  "version": 1
}
```

Changing any authoritative field changes proof identity.

Proof must not contain:

- raw account ID;
- provider payload;
- credentials;
- token;
- headers;
- URLs.

---

## 19. Broker proof freshness

At proof creation:

```text
as_of <= evaluated_at
```

and:

```text
evaluated_at - as_of <= MAX_PROOF_AGE_NS
```

Future:

```text
PROOF_FROM_FUTURE
```

Stale:

```text
PROOF_STALE
```

`as_of` is caller-supplied observation timing evidence, not provider-signed time.

---

## 20. Central reservation source semantics

Accepted Central defines active cash reservations for exactly:

```text
QUEUED

IN_FLIGHT

SUBMITTED

UNCERTAIN
```

Terminal:

```text
RECONCILED
FAILED
CANCELLED
```

do not reserve cash.

Existing Central invariant:

```text
SELL -> reserved_cash_kopecks == 0
```

remains authoritative.

CL5 does not redefine Central lifecycle.

---

## 21. Central state validation

`project_central_reservations` accepts exact `CentralOrderState`.

Before projection the implementation must:

1. require exact accepted DTO type;
2. serialize through existing `to_dict`;
3. reconstruct through accepted `CentralOrderState.from_dict`;
4. require semantically/structurally identical reconstruction;
5. enforce `len(intents) <= MAX_CENTRAL_INTENTS`;
6. recompute:
   `central_reservation_projection_hash(state, excluded_reservation_ids=())`;
7. use exact `state.revision`;
8. use full-account projection only.

No reservation exclusions are permitted in CL5.

---

## 22. Central account-scope binding

Raw:

```text
state.account_id
```

must reproduce the same accepted CL3 account-scope HMAC from section 17.

Mismatch:

```text
CENTRAL_ACCOUNT_MISMATCH
```

Raw Central account ID never appears in output.

---

## 23. Exact kopeck → Money conversion

Central reservations are integer kopecks.

Exact conversion:

```text
minor_units =
reserved_cash_kopecks * KOPECK_TO_NANO
```

where:

```text
KOPECK_TO_NANO = 10_000_000
```

No rounding is allowed.

Overflow:

```text
ARITHMETIC_OVERFLOW
```

Every derived Central reservation Money is therefore an exact multiple of one kopeck while retaining CL1 scale 9.

---

## 24. Reservation lifecycle partition

Projection computes exact mutually exclusive sums:

```text
queued_reserved_cash
```

from:

```text
QUEUED
```

and:

```text
ambiguous_reserved_cash
```

from:

```text
IN_FLIGHT
SUBMITTED
UNCERTAIN
```

and:

```text
total_reserved_cash =
queued_reserved_cash
+
ambiguous_reserved_cash
```

Terminal states contribute zero.

Counts are frozen separately:

```text
queued_count
ambiguous_count
```

---

## 25. CentralReservationProjection

Canonical fields:

```text
account_scope_sha256

environment

central_order_revision

central_reservation_projection_hash

queued_reserved_cash

ambiguous_reserved_cash

total_reserved_cash

queued_count

ambiguous_count

evaluated_at

identity_key_id

projection_identity_sha256

version
```

Exact identity HMAC binds all fields above except `projection_identity_sha256` itself.

No:

- raw account ID;
- intent ID;
- instrument ID;
- broker order ID;

appears in canonical CL5 projection.

Detailed Central topology remains represented indirectly by accepted:

```text
central_reservation_projection_hash
```

---

# 26. ADR-CL5-01 — broker blocked ↔ Central reservations overlap

## Decision

CL5 version 1 MUST NOT guess overlap between provider aggregate blocked cash and local Central reservations.

Provider evidence is aggregate.

Central evidence is intent-lifecycle based.

There is no accepted order-level provider→Central blocked-cash mapping in CL5.

Therefore overlap is decided solely where the existing lifecycle proves it.

---

## 26.1 QUEUED

A `QUEUED` Central reservation is local and has not crossed the provider submission boundary represented by later Central states.

Therefore:

```text
QUEUED reservation
=
DISJOINT from provider blocked cash
```

for CL5 V1.

It must be subtracted in addition to provider blocked cash.

Overlap disposition:

```text
QUEUED_DISJOINT
```

---

## 26.2 IN_FLIGHT / SUBMITTED / UNCERTAIN

For:

```text
IN_FLIGHT
SUBMITTED
UNCERTAIN
```

CL5 cannot prove whether provider `blocked`:

- already includes the reservation;
- includes only part;
- includes none;
- contains unrelated external/manual blocked cash as well.

Therefore:

```text
ambiguous_reserved_cash > 0
```

forces:

```text
MANUAL_REVIEW_REQUIRED
```

with:

```text
overlap_disposition =
AMBIGUOUS_PROVIDER_OVERLAP
```

and:

```text
free_investable_cash = null
```

CL5 MUST NOT:

- double-subtract;
- assume complete overlap;
- assume disjointness;
- use `max(blocked, reservations)`;
- use `min(...)`;
- invent proportional allocation.

---

## 26.3 No local reservation

If:

```text
total_reserved_cash == 0
```

overlap:

```text
NO_LOCAL_RESERVATION
```

---

## 26.4 Provider blocked cash

Provider blocked cash is subtracted exactly once from CL4 broker total cash.

It may include:

- unrelated orders;
- external/manual provider-side holds;
- other broker restrictions.

CL5 does not attempt attribution.

---

## 27. CL4 readiness gate

`build_cash_availability` MUST revalidate CL4 through its accepted public boundary:

```python
build_adoption_candidate(
    reconciliation,
    ledger_export_bytes=ledger_export_bytes,
    identity_key=identity_key,
)
```

CL5 never treats the resulting AdoptionCandidate as adoption authority.

It is used only to prove:

- reconciliation DTO validity;
- exact CL4 BrokerCashProof validity;
- current ledger export binding;
- ledger head/revision consistency;
- reconciliation completeness.

CL5 stores only canonical hashes/derived evidence, not raw ledger export.

---

## 28. Pending / unresolved cash policy

CL5 does not implement a second operation-classification engine.

CL3 decisions such as:

```text
PENDING
STATE_UNSPECIFIED
UNKNOWN_OPERATION_TYPE
PARTIAL_EXECUTION_AMBIGUOUS
...
```

remain governed through CL2/CL4 completeness.

A CL4 reconciliation that is not:

```text
MATCHED
+
projection.complete == True
+
discrepancy_kind == NONE
```

cannot produce a READY CL5 snapshot.

Consequences:

### Pending inflow

Never increases:

```text
free_investable_cash
```

before it becomes accepted ledger state and CL4 becomes complete again.

### Pending outflow

Is not independently subtracted by CL5 because that could duplicate:

- Central reservation;
- provider blocked cash;
- future ledger effect.

Instead incomplete evidence blocks READY status.

This is the version-1 anti-double-counting rule.

---

## 29. Broker view correlation

CL5 correlates:

### View A

CL4:

```text
reconciliation.broker_cash
```

from accepted GetPortfolio proof.

### View B

CL5:

```text
positions_money_rub
blocked_rub
```

from GetSandboxPositions.

Exact relation required for READY:

```text
positions_money_rub + blocked_rub
==
reconciliation.broker_cash
```

All arithmetic exact scale-9 integer arithmetic.

If not equal:

```text
BROKER_VIEW_MISMATCH
```

and:

```text
status = BLOCKED
free_investable_cash = null
```

No tolerance.

No rounding.

---

## 30. Cross-proof skew

Absolute difference:

```text
abs(
    reconciliation.proof.as_of
    -
    positions.as_of
)
```

must not exceed:

```text
MAX_CROSS_PROOF_SKEW_NS
```

for READY.

Otherwise:

```text
MIXED_BROKER_SNAPSHOT
```

and status:

```text
BLOCKED
```

Exact amount equality does not override an excessive timestamp skew.

---

## 31. Freshness at snapshot evaluation

At `build_cash_availability(... evaluated_at=...)`:

both:

```text
reconciliation.proof.as_of
positions.as_of
```

must still satisfy:

```text
not future
age <= MAX_PROOF_AGE_NS
```

A proof may have been fresh when created and stale when CL5 snapshot is built.

CL5 rechecks freshness.

---

## 32. Derived broker unblocked cash

Only after broker-view equality is proven:

```text
broker_unblocked_cash =
reconciliation.broker_cash
-
positions.blocked_rub
```

Then invariant:

```text
broker_unblocked_cash
==
positions.positions_money_rub
```

must hold exactly.

Otherwise:

```text
BROKER_VIEW_MISMATCH
```

This cross-check is required before any local reservation subtraction.

---

## 33. READY availability formula

READY preconditions:

```text
CL4 adoption candidate =
SEPARATE_LOCKED_REVIEW_REQUIRED

CL4 reconciliation =
MATCHED / complete / NONE

broker proofs fresh

cross-proof skew within bound

account/environment/currency equal

foreign_cash_present = false

broker view equality proven

ambiguous_reserved_cash = 0
```

Then:

```text
candidate_free_cash =
broker_unblocked_cash
-
queued_reserved_cash
```

If:

```text
candidate_free_cash < 0
```

snapshot becomes:

```text
BLOCKED
INSUFFICIENT_AFTER_RESERVATIONS
free_investable_cash = null
```

Otherwise:

```text
status = READY
availability_reason = READY
free_investable_cash = candidate_free_cash
```

---

## 34. No double subtraction invariant

For READY snapshot, exact formula is mathematically equivalent to:

```text
free =
broker_total_cash
-
provider_blocked_cash
-
QUEUED_local_reservations
```

and MUST NOT include another subtraction for:

```text
IN_FLIGHT
SUBMITTED
UNCERTAIN
```

because those states cannot be proven disjoint from provider blocked cash.

If such states exist, READY is forbidden.

---

## 35. Foreign cash

If GetSandboxPositions contains any non-RUB cash entry:

```text
foreign_cash_present = true
```

CL5 V1 cannot prove account-wide availability under RUB-only CL1 semantics.

Disposition:

```text
BLOCKED
FOREIGN_CASH_PRESENT
free_investable_cash = null
```

Foreign values remain in provider response hash but are not converted or rounded.

---

## 36. Availability enums

### AvailabilityStatus

Exact closed set:

```text
READY

MANUAL_REVIEW_REQUIRED

BLOCKED
```

### OverlapDisposition

Exact closed set:

```text
NO_LOCAL_RESERVATION

QUEUED_DISJOINT

AMBIGUOUS_PROVIDER_OVERLAP
```

### AvailabilityReason

Exact closed set:

```text
READY

CL4_NOT_READY

BROKER_PROOF_STALE

MIXED_BROKER_SNAPSHOT

BROKER_VIEW_MISMATCH

FOREIGN_CASH_PRESENT

CENTRAL_PROVIDER_OVERLAP_UNKNOWN

INSUFFICIENT_AFTER_RESERVATIONS
```

---

## 37. Deterministic disposition precedence

After structural validation succeeds, valid-but-unsafe evidence is resolved in this exact order:

1. CL4 candidate not separately reviewable:
   `CL4_NOT_READY / BLOCKED`

2. either broker proof stale at snapshot time:
   `BROKER_PROOF_STALE / BLOCKED`

3. proof skew above bound:
   `MIXED_BROKER_SNAPSHOT / BLOCKED`

4. provider cash views do not match:
   `BROKER_VIEW_MISMATCH / BLOCKED`

5. foreign cash present:
   `FOREIGN_CASH_PRESENT / BLOCKED`

6. ambiguous Central reservation amount > 0:
   `CENTRAL_PROVIDER_OVERLAP_UNKNOWN / MANUAL_REVIEW_REQUIRED`

7. known-disjoint subtraction would become negative:
   `INSUFFICIENT_AFTER_RESERVATIONS / BLOCKED`

8. otherwise:
   `READY / READY`

Exactly one primary reason is emitted.

---

## 38. CashAvailabilitySnapshot canonical contract

Exact fields:

```text
account_scope_sha256

environment

currency

evaluated_at

reconciliation_sha256

cl4_adoption_candidate_sha256

ledger_export_sha256

ledger_revision

ledger_head_sha256

broker_positions_cash_proof_sha256

broker_total_cash

broker_blocked_cash

broker_unblocked_cash

central_reservation_projection_sha256

central_order_revision

central_reservation_projection_hash

central_queued_reserved_cash

central_ambiguous_reserved_cash

central_total_reserved_cash

overlap_disposition

status

availability_reason

free_investable_cash

version
```

Canonical domain:

```text
v3.10-cl5-cash-availability
```

`free_investable_cash`:

- exact nested CL1 Money when READY;
- JSON `null` otherwise.

`broker_unblocked_cash`:

- exact Money only after broker-view proof succeeds;
- otherwise null.

All revision values use canonical decimal-string encoding.

---

## 39. Snapshot identity

`sha256` is SHA-256 exact canonical snapshot bytes.

Snapshot SHA binds:

- exact CL4 reconciliation;
- exact current ledger export identity;
- exact CL4 ledger revision/head;
- exact positions proof;
- exact Central revision/projection hash;
- all exact monetary components;
- overlap disposition;
- final availability status/reason;
- evaluated_at.

Changing any authoritative evidence changes snapshot SHA.

---

## 40. Read-only race semantics

CL5 never acquires Central lock and never performs CAS.

Therefore snapshot is a point-in-time proof only.

If Central changes after projection:

```text
snapshot becomes stale evidence
```

but CL5 performs no automatic mutation/recomputation.

Any future execution authorization using CL5 must revalidate:

```text
central revision
reservation projection hash
ledger revision/head
broker proof freshness
```

under a later separately accepted locked boundary.

That boundary is NOT CL5.

---

## 41. Closed CL5 error taxonomy

`CL5Reason` exact version-1 set:

```text
TYPE_INVALID

VERSION_UNSUPPORTED

ENVIRONMENT_UNSUPPORTED

CURRENCY_UNSUPPORTED

ACCOUNT_SCOPE_INVALID

IDENTITY_KEY_INVALID

TIMESTAMP_INVALID

PROOF_FROM_FUTURE

PROOF_STALE

PROOF_INCOMPLETE

RESPONSE_BOUNDS_EXCEEDED

RESPONSE_SCHEMA_INVALID

POSITIONS_LOADING_IN_PROGRESS

MONEY_INVALID

PROOF_IDENTITY_INVALID

CENTRAL_STATE_INVALID

CENTRAL_ACCOUNT_MISMATCH

CENTRAL_PROJECTION_INVALID

CL4_EVIDENCE_INVALID

ARITHMETIC_OVERFLOW

CANONICAL_FORMAT_INVALID

INTERNAL_BOUNDARY_FAILED
```

Adding a reason requires CL5 version transition.

---

## 42. Failure vs disposition

Malformed, forged, structurally invalid or privacy-unsafe inputs raise:

```text
CL5Error(CL5Reason...)
```

Economically valid but unsafe evidence returns a valid:

```text
CashAvailabilitySnapshot
```

with:

```text
MANUAL_REVIEW_REQUIRED
```

or:

```text
BLOCKED
```

This distinction is frozen.

---

## 43. First-failure order

Public boundaries use deterministic primary-reason order.

### build_broker_positions_cash_proof

1. exact container/public scalar types;
2. version/environment/account/key inputs;
3. timestamps;
4. response completeness;
5. graph bounds;
6. exact provider schema;
7. `limitsLoadingInProgress`;
8. account-scope derivation;
9. RUB Money parsing;
10. blocked non-negative;
11. canonical response identity;
12. freshness;
13. proof construction.

### project_central_reservations

1. exact public argument types;
2. environment/account/key/timestamp;
3. exact Central DTO round-trip;
4. intent count bounds;
5. account-scope match;
6. exact Central projection hash;
7. lifecycle partition;
8. exact kopeck conversion;
9. arithmetic overflow;
10. projection identity.

### build_cash_availability

1. exact public argument types;
2. evaluated timestamp/key;
3. CL4 revalidation through public CL4 boundary;
4. positions proof reconstruction/HMAC;
5. Central projection reconstruction/HMAC;
6. account/environment/currency correlation;
7. structural arithmetic;
8. deterministic disposition precedence from section 37.

Representative multi-invalid cases are mandatory tests.

---

## 44. Privacy boundary

CL5 output/errors/repr MUST NOT expose:

- raw broker account ID;
- raw Central account ID;
- provider payload;
- credentials;
- tokens;
- Authorization header;
- response text;
- raw intent IDs;
- broker order IDs;
- instrument IDs;
- filesystem paths;
- raw exception text.

Allowed evidence consists only of:

- finite reason/status tokens;
- version;
- bounded counters;
- privacy-safe SHA/HMAC identities;
- revisions.

Exception chaining across the public boundary is forbidden.

---

## 45. Determinism

Identical validated inputs produce byte-identical:

```text
BrokerPositionsCashProof

CentralReservationProjection

CashAvailabilitySnapshot
```

and identical SHA/HMAC values.

No authoritative identity contains:

- system clock;
- randomness;
- UUID;
- memory address;
- dict insertion accident;
- SQLite rowid;
- filesystem path.

All timestamps are caller supplied.

---

## 46. Frozen known-answer fixture

Future:

```text
current/tests/fixtures/v3_10_cash_availability_vectors.json
```

contains public synthetic values only.

Production code never reads fixture.

Required known-answer cases include:

1. zero cash / zero blocked / zero reservations;
2. total `100 RUB`, blocked `20 RUB`, no reservation → free `80 RUB`;
3. total `100`, blocked `20`, queued reservation `30` → free `50 RUB`;
4. exact sub-kopeck broker cash plus whole-kopeck Central reservation;
5. exact kopeck→nano conversion;
6. one `IN_FLIGHT` reservation → manual review;
7. one `SUBMITTED` reservation → manual review;
8. one `UNCERTAIN` reservation → manual review;
9. provider blocked unrelated to queued reservation → subtract each exactly once;
10. broker total != positions money + blocked → blocked;
11. foreign cash present → blocked;
12. CL4 incomplete → blocked;
13. stale positions proof → blocked;
14. proof timestamp skew above bound → blocked;
15. reservation exceeds unblocked cash → blocked;
16. canonical proof/snapshot bytes and SHA;
17. account-scope HMAC known answer;
18. positions-proof HMAC known answer;
19. Central projection HMAC known answer.

All arbitrary Money integers are decimal strings in fixture.

No float.

---

## 47. Fixed acceptance matrix

Future implementation must satisfy at least:

```text
V310-CL5-01
exact predecessor + cumulative allowlist

V310-CL5-02
positions response graph bounds

V310-CL5-03
Sandbox account-scope identity exact

V310-CL5-04
RUB money/blocked exact extraction

V310-CL5-05
limitsLoadingInProgress fail closed

V310-CL5-06
foreign-cash detection

V310-CL5-07
BrokerPositionsCashProof canonical/HMAC vectors

V310-CL5-08
proof future/stale/age edges

V310-CL5-09
Central exact DTO round-trip

V310-CL5-10
exact existing reservation projection hash binding

V310-CL5-11
QUEUED / ambiguous lifecycle partition

V310-CL5-12
kopeck -> nanoruble exact conversion

V310-CL5-13
SELL reservation invariant inherited unchanged

V310-CL5-14
CL4 public revalidation and ledger-export binding

V310-CL5-15
broker proof cross-skew boundaries

V310-CL5-16
broker total = money + blocked exact correlation

V310-CL5-17
no-reservation READY case

V310-CL5-18
QUEUED reservation exact free-cash formula

V310-CL5-19
IN_FLIGHT/SUBMITTED/UNCERTAIN -> MANUAL_REVIEW_REQUIRED

V310-CL5-20
explicit zero-double-subtraction adversarial matrix

V310-CL5-21
pending/unresolved CL4 evidence cannot fund BUY

V310-CL5-22
insufficient-after-reservation blocked case

V310-CL5-23
forged DTO + multi-invalid exact reason precedence

V310-CL5-24
privacy/repr/error boundary

V310-CL5-25
immutability + canonical bytes/SHA determinism

V310-CL5-26
AST/import boundary: zero provider/runtime/Central mutation

V310-CL5-27
full unchanged CL1–CL4 regression

V310-CL5-28
exact three-path implementation delta
```

---

## 48. Mandatory overlap adversarial matrix

Tests must explicitly cover:

```text
provider blocked = 0
Central queued > 0

provider blocked > 0
Central queued = 0

provider blocked > 0
Central queued > 0

provider blocked > 0
Central IN_FLIGHT > 0

provider blocked > 0
Central SUBMITTED > 0

provider blocked > 0
Central UNCERTAIN > 0

provider blocked = Central ambiguous reservation

provider blocked < Central ambiguous reservation

provider blocked > Central ambiguous reservation
```

Last six ambiguous-overlap cases MUST NOT calculate free investable cash.

All produce:

```text
MANUAL_REVIEW_REQUIRED
```

regardless of arithmetic coincidence.

---

## 49. No hidden authority through READY

Tests must prove that building READY snapshot performs zero calls to:

- Central mutation APIs;
- CashLedgerStore mutation APIs;
- provider transport;
- PortfolioRepository;
- Risk;
- ExecutionAdapter.

No reservation or broker state changes.

---

## 50. Future implementation local verification

Before independent implementation review:

1. dedicated CL5 suite;
2. fixture known-answer reproduction;
3. AST/import boundary check;
4. Ruff;
5. format check;
6. compile;
7. `git diff --check`;
8. exact three-path implementation allowlist;
9. complete unchanged CL1–CL4 regression;
10. zero runtime call-graph changes.

Green tests do not grant acceptance.

---

## 51. Historical evidence disposition

Historical Issue #53 remains reference evidence for:

- immutable CashAvailability snapshot;
- broker blocked vs Central reservation overlap;
- pending-flow safety;
- stale/mixed evidence rejection;
- zero-double-subtraction requirement;
- read-only shadow boundary.

Its historical implementation assumptions are not inherited automatically.

In particular clean CL5 uses:

- exact CL1 Money scale 9;
- accepted CL2 revisions/head;
- accepted CL4 broker/reconciliation proof;
- accepted existing Central projection semantics.

Historical MoneyV1/MoneyV2 migration architecture is not part of CL5.

---

## 52. Provider-profile disposition

CL5 V1 freezes the current T-Bank PositionsResponse profile needed only for:

```text
money
blocked
limitsLoadingInProgress
accountId
```

Provider schema change does not silently modify CL5 V1.

Incompatible provider change requires:

```text
VERSION TRANSITION
or
RESCOPE
```

not permissive parsing.

---

## 53. Review protocol

CL5 contract:

```text
one independent/adversarial review
```

Result:

```text
PASS
```

or one finite fixed set:

```text
CL5-R1-xx
```

At most:

```text
one bounded contract correction batch
```

then:

```text
one finding-scoped closure review
```

If a material blocker remains:

```text
RESCOPE / ABORT / DEFER
```

No recursive contract correction treadmill.

Implementation follows the same one-review / one-correction / one-closure rule.

---

## 54. Contract acceptance

Contract acceptance must bind:

```text
exact commit
exact tree
exact predecessor
exact merge-base
exact one-file delta
```

Issue/PR text is metadata only.

Contract acceptance does not automatically authorize implementation.

---

## 55. Exit state

After creating the first one-file candidate, authority remains:

```text
CL5 CONTRACT FREEZE CANDIDATE
/
IMPLEMENTATION BLOCKED
/
SHADOW READ-ONLY
/
NO RESERVATION OWNERSHIP
/
NO EXECUTION AUTHORITY
```

No provider access, persistence mutation, runtime action, experiment, merge or release is authorized by this contract freeze.