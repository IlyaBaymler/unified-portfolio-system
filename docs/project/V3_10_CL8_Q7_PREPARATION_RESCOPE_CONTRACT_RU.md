# V3.10 CL8 Q7 — Sandbox burn-in preparation rescope contract

Status:

`CL8 Q7 PREPARATION RESCOPE CONTRACT CORRECTION SUCCESSOR CANDIDATE / IMPLEMENTATION BLOCKED / PROVIDER ACCESS BLOCKED / START EXPERIMENT INELIGIBLE`

Parent program:

`v3.10 stable-line -> CL8 Stable qualification and release -> Q7 Sandbox burn-in`.

This bounded rescope exists only because the accepted Q7 Preparation Stage failed closed before any provider call or provider mutation.

---

## 1. Exact failed-preparation binding

The rescope is bound to the exact Q7 preparation candidate nominated by the operator:

```text
candidate commit =
c45eaa3197093b06ef0d68f4a29729e695cdec0b

candidate tree =
c187c66c0fd9494b7a467855fd57d4a67333dd3a

source tree =
clean
```

Accepted preparation evidence reported:

```text
CL8_Q7_PREPARATION_RECORD.json
SHA-256 =
bde3ec7172cce179f62496e63e5a7681f50ccf504d33ea53e43ecc5939a8ee5a

CL8_Q7_STATIC_PREFLIGHT_AUDIT.json
SHA-256 =
b4c13b887372db641073cfbe6da4b717d8d1ed26851ca961b32f14c04d35ba9d

CL8_Q7_PREPARATION_MANIFEST.json
SHA-256 =
fa85a8e3aa6f70a55386193f4cb1eb854e321763301358376fc5bd416cd47448

CL8_Q7_PREPARATION_VERIFICATION.json
SHA-256 =
efe72191dc6e67868c0bf7bbd6e90f1e1f74c49f515fc433336edb6894166549
```

The failed preparation performed:

```text
provider calls = 0
provider mutations = 0
```

No `START EXPERIMENT` was eligible or issued.

---

## 2. Frozen blocker set

This rescope addresses exactly these preparation blockers:

```text
CL8-Q7-PREP-01
shipped production entrypoints do not invoke GuiRuntimeController.compose;
normal execution fails closed with GUI_RUNTIME_COMPOSITION_REQUIRED

CL8-Q7-PREP-02
no exact Q7 runtime instance is nominated;
the sanitized Q4 standalone artifact contains no authoritative runtime custody

CL8-Q7-PREP-03
the nominated runtime cash authority remains LEGACY_ACTIVE,
record revision 0, ever_exact_activated=false

CL8-Q7-PREP-04
protected secret custody contains no valid
V310_CL_IDENTITY_KEY_HEX / V310_CL_IDENTITY_KEY_ID

CL8-Q7-PREP-05
no verified complete pre-run runtime backup exists
```

The first, second and fourth blockers require bounded source/preparation work.

The third and fifth blockers are later runtime-state gates and MUST NOT be silently "fixed" by offline implementation.

---

# PART A — RESCOPE AUTHORITY

## 3. Purpose

This rescope introduces only the missing production composition and secret-custody preparation required to make a future Q7 runtime *eligible for a separately gated CL7 activation stage*.

It does not authorize the activation stage itself.

The permitted two-stage model is:

```text
STAGE A
offline materialization and protected secret custody
(no provider access)

        ↓ separate acceptance / preparation gate

STAGE B
separately authorized CL7 activation
(authenticated provider reads permitted only after an explicit gate;
provider order POST forbidden)

        ↓ verified EXACT_CASH_ARMED runtime + complete backup

Q7 FINAL PREPARATION PASS

        ↓ separate Q7 burn-in authorization

START EXPERIMENT for CL8-SANDBOX-BURNIN-V1
```

---

## 4. Explicit non-goals

This rescope MUST NOT introduce:

```text
new CashLedger semantics
new opening/backfill semantics
new reconciliation semantics
new CashAvailability semantics
new Risk semantics
new Central reservation semantics
new provider mutation endpoint
new order submission path
new trading strategy
new Portfolio ownership
new GUI economic state owner
new release packaging model
new real-account path
automatic CL7 prepare/confirm/activate
automatic CL7 arm
automatic provider reads
automatic Sandbox burn-in
```

Any implementation requirement outside this set is:

```text
RESCOPE / DEFER
```

not an implicit widening of Q7 preparation.

---

## 5. Contract-freeze one-file allowlist

Before independent review and explicit acceptance of this contract, the only permitted repository path is:

```text
docs/project/V3_10_CL8_Q7_PREPARATION_RESCOPE_CONTRACT_RU.md
```

Any other added, modified, deleted, renamed or untracked repository path:

```text
SCOPE_VIOLATION -> RESCOPE
```

No provider/private-account state is touched by contract work.

---

# PART B — FUTURE IMPLEMENTATION SCOPE

## 6. Frozen implementation allowlist

Only after exact contract acceptance may the dedicated implementation branch change these ten paths:

```text
current/desktop_gui.py
current/trading_robot/gui_runtime_controller.py
current/trading_robot/secret_provider.py
current/tools/v3_10_runtime_cash_cutover.py

current/tools/v3_10_q7_prepare_runtime.py
current/tests/test_v3_10_q7_preparation_runtime.py
current/tests/fixtures/v3_10_q7_preparation_vectors.json

current/tests/test_v3_10_issue72_gui_runtime.py
current/tests/test_v3_10_stable_qualification.py

docs/plans/V3_10_CL8_Q7_PREPARATION_RUNBOOK_RU.md
```

Exact count:

```text
10
```

An allowlisted path is permission, not a requirement.

Any path outside this exact set requires a new explicit rescope.

The implementation branch name is frozen as:

```text
agent/v3-10-clean-cl8-q7-preparation-rescope-implementation
```

The two inherited custody-oracle test paths are allowlisted only to recognize:

```text
the exact accepted Q7 rescope contract commit/tree
the exact implementation branch above
the implementation parent = exact accepted contract head
the implementation merge-base = exact accepted contract head
the exact ten-path implementation allowlist
at most one later separately authorized direct correction successor
```

Their expected regression-failure node set, product assertions, GUI semantics and
runtime assertions are immutable. They MUST NOT accept an arbitrary branch,
descendant, merge-base or changed-path set. A correction successor is recognized
only after its exact parent/head/tree and bounded correction paths have received a
separate governance record.

---

## 7. Immutable accepted owners

This milestone MUST consume, not redefine, the accepted owners and boundaries:

```text
CashLedger:
current/trading_robot/cash_ledger_domain.py
current/trading_robot/cash_ledger_persistence.py

Opening/reconciliation:
current/trading_robot/cash_ledger_opening_reconciliation.py

CashAvailability:
current/trading_robot/cash_availability.py

Central:
current/trading_robot/central_order_manager.py
current/trading_robot/central_order_coordinator.py

Portfolio:
current/trading_robot/portfolio_repository.py
accepted Portfolio domain/read models

Risk:
current/trading_robot/risk.py
current/trading_robot/risk_runtime.py
current/trading_robot/portfolio_risk_runtime.py

CL7 authority:
current/trading_robot/runtime_cash_authority.py

provider mutation:
current/trading_robot/sandbox_execution_adapter.py

provider transport:
current/trading_robot/tbank_sandbox.py
```

No implementation diff in these paths is authorized.

---

# PART C — STAGE A: PRODUCTION COMPOSITION ROOT

## 8. Shipped desktop composition requirement

For the shipped desktop `SANDBOX_EXECUTION` path, exactly one production composition root must be created per GUI process.

Required boundary:

```text
desktop_gui.py
→ GuiRuntimeController.compose(...)
→ one process-local GuiRuntimeController
→ configured account-level runtime set
→ accepted Central / Portfolio / Risk / CL7 owners
```

Composition failure is fail-closed.

The GUI must expose a bounded status such as:

```text
GUI_RUNTIME_COMPOSITION_REQUIRED
```

and disable the account-level economic start path.

It MUST NOT fall back to a legacy single-bot execution owner.

---

## 9. Scope of the composition change

The implementation may change the shipped desktop runtime wiring only.

It MUST NOT:

```text
create a second CentralOrderManager owner
create a second Portfolio owner
create a second Risk owner
create a second CashLedger owner
persist controller-private economic state
reimplement CL7 state transitions
call provider POST from GUI/controller
```

Backtest and non-economic historical research surfaces are outside this rescope unless a minimal import/wiring adjustment is mechanically necessary.

---

## 10. Main SANDBOX_EXECUTION invariant

After implementation:

```text
main account-level SANDBOX_EXECUTION
MUST use GuiRuntimeController composition
```

and:

```text
main account-level SANDBOX_EXECUTION
MUST NOT instantiate SandboxTradingBot
```

A missing/invalid composition is a blocker, never a trigger for a compatibility fallback.

---

## 11. One composition per GUI process

The composition root is created at most once for one active GUI process/runtime selection.

Repeated refreshes, tab changes or Start button clicks MUST NOT create additional controller/owner instances.

If the runtime directory/account selection changes, the old composition must first be explicitly stopped/disposed before a new composition is admitted.

No economic lock is held while waiting for user interaction.

---

# PART D — PROTECTED SECRET CUSTODY

## 12. Canonical Windows secret provider

For Windows Q7 qualification, the canonical protected store is:

```text
provider =
Windows Credential Manager

namespace =
MOEXResearchRobot
```

The existing secret-provider abstraction remains the owner of protected secret retrieval.

The Q7 production path must use the same resolver for token/account/identity inputs rather than directly reading plaintext environment variables as its primary production source.

---

## 13. Frozen protected key names

Existing Sandbox credential keys remain compatible with accepted product behaviour.

CL7 identity custody adds exactly:

```text
V310_CL_IDENTITY_KEY_HEX
V310_CL_IDENTITY_KEY_ID
```

These names are the canonical logical keys regardless of the underlying protected-provider target naming.

---

## 14. Identity-key constraints

`V310_CL_IDENTITY_KEY_HEX` represents exactly 32–64 bytes encoded as strict hexadecimal.

Required properties:

```text
random
persistent
stable across restarts
not derived from token
not derived from raw Account ID
not derived from filesystem path
not derived from timestamps
not derived from Git identity
not derived from runtime state
```

`V310_CL_IDENTITY_KEY_ID` must satisfy the accepted CL7 identity-key ID grammar:

```text
[A-Z][A-Z0-9_]{0,63}
```

---

## 15. Provisioning boundary

Identity provisioning is an explicit local operator action exposed only by the
bounded Q7 preparation tool. Its implementation may delegate protected-store
operations to a bounded helper in the existing secret-provider module, but that
helper is not a second operator entrypoint.

The only contract-owned provisioning entrypoint is:

```text
python tools/v3_10_q7_prepare_runtime.py provision-identity \
  --identity-key-id <ID> \
  --confirmation "PROVISION V3.10 CL7 IDENTITY"
```

`<ID>` is non-secret operator input and must satisfy section 14. Identity-key
plaintext is generated inside the process and MUST NOT be accepted through a
command-line argument, environment variable, stdin, clipboard, log or report.
Generation uses a cryptographically secure operating-system RNG equivalent to:

```text
secrets.token_bytes(32)
```

and stores its exact lowercase 64-character hexadecimal encoding under
`V310_CL_IDENTITY_KEY_HEX`.

Provisioning first acquires the Windows per-user named mutex:

```text
Local\MOEXResearchRobot.V310CLIdentityProvisioning.v1
timeout = 5000 ms
```

The mutex is held from the first protected read through final read-back or
compensation. Timeout or mutex abandonment returns
`IDENTITY_PROVISIONING_LOCK_FAILED`, performs zero secret write and blocks Stage
A. Every accepted product path capable of provisioning these logical keys MUST
use this mutex; no second writer exists in the product.

This mutex is the frozen concurrency boundary for accepted product writers.
Out-of-band mutation through Credential Manager UI or an unrelated process while
provisioning holds the mutex is forbidden operator interference and is outside
the product's serializable writer set. The implementation MUST NOT claim native
Credential Manager compare-and-swap against such an external writer; any
resulting read-back mismatch still fails closed through the guarded compensation
rules below.

After acquiring the mutex and before generating any bytes, the tool reads both
protected logical keys. The finite pre-write states are:

```text
both absent:
  eligible for create-once provisioning

both present and valid:
  ALREADY_PROVISIONED
  zero writes; requested key ID must exactly match the stored key ID,
  otherwise IDENTITY_KEY_MISMATCH

exactly one present:
  IDENTITY_CUSTODY_PARTIAL / MANUAL_RECOVERY_REQUIRED
  zero generation and zero writes

either present but malformed:
  IDENTITY_KEY_INVALID
  zero generation and zero writes
```

Create-once provisioning is a lock-serialized logical transaction over the two
Credential Manager records. Immediately before each write it re-reads the target
and requires it to remain absent. Before the second write it additionally
requires the protected key read-back to equal the just-created value. Any
compare failure performs no overwrite and enters the compensation path.

The transaction writes the newly generated key first, then the operator supplied
key ID, and performs an exact protected read-back of both values before reporting
success. Because Windows Credential Manager does not provide a cross-record
transaction, any write/read failure triggers compensation. Compensation deletes
a target only after an exact protected comparison proves that its current value
equals the value created by this invocation; it MUST NOT delete a missing,
different or pre-existing value.

Successful compensation returns `IDENTITY_PROVISIONING_FAILED` only after a
read-back proves both records absent. Any failed comparison, failed deletion or
non-absent post-compensation state returns
`IDENTITY_CUSTODY_PARTIAL / MANUAL_RECOVERY_REQUIRED`. Neither outcome may
continue Stage A. A first-write failure also verifies both records remain absent;
otherwise it returns the partial/manual-recovery result.

An exact read-back mismatch is handled by the same compensation path. A
successful provisioning result is emitted only when both read-back values are
byte-for-value exact. The output contains metadata only: provider, presence,
`identity_key_id` and a finite status token.

Normal GUI startup and every command other than the exact provisioning entrypoint MUST NOT:

```text
generate identity key material
rotate identity key material
replace an existing identity key
invent a new key ID
write identity plaintext into .env
```

If required identity custody is absent:

```text
IDENTITY_KEY_REQUIRED
```

and the economic Q7 path remains blocked.

---

## 16. Existing-authority protection

Provisioning is strictly create-once. Any existing protected identity-key or
identity-key-ID record, including while CL7 authority is `LEGACY_ACTIVE`, blocks
generation and replacement. A valid complete pair is an idempotent metadata-only
`ALREADY_PROVISIONED` result; it is never rewritten.

If any non-bootstrap CL7 authority custody already exists, provisioning MUST NOT silently replace or regenerate the identity material expected by that authority lineage.

If protected `V310_CL_IDENTITY_KEY_ID` disagrees with persisted authority custody:

```text
IDENTITY_KEY_MISMATCH
```

If the stored key is malformed:

```text
IDENTITY_KEY_INVALID
```

Both cases:

```text
zero CL7 transition
zero provider call
zero provider mutation
```

---

## 17. Secret non-exportability

The following MUST NEVER appear in repository/shareable evidence:

```text
token plaintext
raw Account ID
identity-key plaintext
identity-key hexadecimal value
Authorization header
Credential Manager blob
DPAPI plaintext
```

A support bundle, Q7 B0 evidence binding or qualification summary may contain:

```text
provider name
secure=true/false
logical secret key name
presence status
identity_key_id
privacy-safe account scope after it is validly established
finite error/status tokens
```

The identity secret itself is excluded from runtime backup archives.

---

## 18. Recovery consequence

A runtime backup without the protected identity key is intentionally not a self-contained portable execution backup.

Restoring the runtime on another Windows user/machine requires separately provisioned matching protected identity custody.

The product MUST NOT respond to missing identity custody by creating a different key and continuing.

---

# PART E — STAGE A: Q7 RUNTIME MATERIALIZATION

## 19. Sanitized release artifact is not the runtime

The Q4 source/standalone artifact remains sanitized.

It MUST NOT be modified to ship:

```text
Central state
Portfolio state
Risk state
ConfiguredExecutionSet state
InstrumentRuntime state
CashLedger database
RuntimeCashAuthority record
token
raw Account ID
identity key
```

A Q7 runtime instance is constructed outside the release artifact.

---

## 20. Runtime instance model

The preparation tool nominates one external runtime directory and one generated privacy-safe `runtime_instance_id`.

Shareable evidence records the `runtime_instance_id`.

It MUST NOT record the private absolute filesystem path.

The runtime directory is treated as private operator state.

---

## 21. Required runtime custody

Before Stage A may report `MATERIALIZED`, the nominated runtime must contain or validly initialize the accepted custody required for the future exact-cash workflow.

At minimum, where applicable:

```text
PortfolioRepository custody

CentralOrderManager custody

RiskProfile / RiskState custody

multi_instrument_profiles.json + accepted checksum custody

instrument_runtimes.json + accepted checksum/lastgood custody

cash_ledger_v3_10.sqlite3

runtime_cash_authority.json
runtime_cash_authority.json.sha256
runtime_cash_authority.json.lastgood when required by its revision

accepted operational/event state required by the composed controller
```

Locks are process coordination files, not backup authority.

---

## 22. Configured execution set

Stage A requires exactly:

```text
2 or 3 configured instruments
```

for:

```text
SANDBOX_EXECUTION
```

They must:

```text
bind one account scope
have unique instrument/execution identities
have valid profile hashes
have matching InstrumentRuntime identities
contain no orphan runtime for the nominated mode
```

The tool may bootstrap missing runtime registry rows only through accepted profile/runtime store semantics.

It MUST NOT invent or silently rewrite an operator-nominated instrument profile.

---

## 23. Offline-only materialization

Stage A performs no authenticated provider call.

Allowed work:

```text
read local protected-secret metadata
validate secret presence/format
perform the exact section-15 create-once provisioning action only when invoked
with its exact operator confirmation
validate local configuration
initialize accepted local stores where their accepted APIs allow it
create/open an empty CL2 ledger using accepted codecs
bootstrap revision-0 LEGACY_ACTIVE authority only through accepted compatibility semantics
validate local owner consistency
create verified backup
generate privacy-safe evidence
```

Forbidden:

```text
GetPortfolio
GetSandboxPositions
GetOperationsByCursor
any account-list provider read
any CL7 opening adoption
any CL7 prepare/confirm/activate
any CL7 arm
any provider POST
any Strategy-driven live execution
```

---

## 24. CashLedger creation boundary

If `cash_ledger_v3_10.sqlite3` is absent, the preparation tool may create a new empty ledger only through the accepted CL2 create API and accepted CL3/CL4 codecs.

It MUST NOT:

```text
invent opening balance
backfill history
create synthetic broker operations
claim reconciliation
advance provider watermark without accepted evidence
```

A newly created ledger remains non-actionable until later separately gated CL7 activation establishes the accepted opening/sync/reconciliation chain.

---

## 25. RuntimeCashAuthority Stage-A boundary

A clean runtime may end Stage A as:

```text
LEGACY_ACTIVE
```

with no exact activation.

This is expected and is NOT Q7 Preparation PASS.

Stage A MUST NOT automatically:

```text
PREPARE
CONFIRM
ACTIVATE
ARM
```

CL7 authority.

It only verifies that the authority custody is readable and compatible with a future separately gated activation.

---

# PART F — STAGE-A VERIFIED BACKUP B0

## 26. B0 purpose

After successful offline materialization and before any provider/private activation work, create:

```text
B0 = verified pre-activation backup
```

B0 protects the fully materialized local runtime before CL7 cutover.

---

## 27. B0 required custody

B0 must include the complete materialized authoritative runtime custody according to accepted CL8 Q3 backup semantics, including checksum/lastgood companions where required.

It MUST exclude:

```text
token
raw Account ID credential record
identity-key plaintext
Credential Manager blobs
locks as authoritative state
```

The accepted Q3 backup ZIP and its `manifest.json` schema remain byte-for-contract
unchanged. Secret-prerequisite metadata MUST NOT be injected into that immutable
backup manifest. It is recorded exclusively in the canonical Stage-A
materialization record from section 48, which binds both the verified backup
artifact SHA-256 and the exact B0 `manifest.json` SHA-256.

---

## 28. B0 evidence

Shareable B0 evidence binds:

```text
runtime_instance_id
candidate commit/tree
configured-set identity/hash
b0 manifest SHA-256
backup artifact SHA-256
backup byte size
verification result
identity_key_id
secret-provider type
secret-presence booleans
provider_calls_performed=false
provider_mutations_performed=false
```

B0 creation does not authorize Stage B.

---

# PART G — STAGE-B ACTIVATION GOVERNANCE

## 29. Stage B is a separate private/provider action

CL7 activation is NOT part of Stage A implementation or contract acceptance.

It is a separately gated operator-controlled qualification action.

Frozen activation identifier:

```text
CL8-Q7-CL7-ACTIVATION-V1
```

This identifier is distinct from:

```text
CL8-SANDBOX-BURNIN-V1
```

Authorization for one cannot be reused for the other.

---

## 30. Activation Preparation Stage

Before any authenticated provider read for CL7 activation, create an immutable activation-preparation record binding:

```text
experiment_id = CL8-Q7-CL7-ACTIVATION-V1

exact candidate commit/tree
accepted Q7-rescope contract identity
accepted Q7-rescope implementation identity
runtime_instance_id
B0 backup SHA-256
configured-set identity
account-scope nomination
identity_key_id
protected token/account/key presence
pre-activation authority state/revision/SHA
Central quiescence result
Portfolio custody result
Risk custody result
CashLedger custody result
planned CL7 operator command sequence
provider host/environment proof = Sandbox only
```

The record contains no raw private identifier or credential.

---

## 31. Activation authorization

Only after the activation Preparation Stage has independently passed may the operator explicitly authorize:

```text
START EXPERIMENT
```

for exactly:

```text
CL8-Q7-CL7-ACTIVATION-V1
```

Before that exact authorization:

```text
provider calls = 0
```

---

## 32. Double-gate rule

`START EXPERIMENT` for the activation stage permits only the bounded activation workflow.

Every dangerous CL7 state transition still requires the exact accepted CL7 operator confirmation phrase for that command.

The experiment authorization does not replace CL7 confirmations.

---

## 33. Stage-B provider boundary

During `CL8-Q7-CL7-ACTIVATION-V1`:

Permitted:

```text
authenticated Sandbox provider READS required by accepted CL7
history synchronization
current portfolio/cash/positions reads
accepted reconciliation inputs
accepted CL7 prepare/confirm/activate
accepted CL7 arm after all activation predicates pass
accepted disarm/recovery if required
```

Forbidden:

```text
dispatch command
Strategy-driven economic execution
manual provider order POST
diagnostic provider order POST
account deletion
real-account endpoint
Q7 burn-in
```

Provider order mutation count for Stage B must remain:

```text
0
```

---

## 34. Required CL7 activation result

A successful Stage B ends only when accepted CL7 semantics prove:

```text
RuntimeCashAuthority state =
EXACT_CASH_ARMED

pending dispatch proof =
none

post_attempt_count =
0

Central blocking economic intent =
none

legacy pending operation =
none

diagnostic pending operation =
none

CashLedger =
valid + synchronized through the accepted boundary

opening/reconciliation =
accepted and coherent

CashAvailability =
capable of READY from fresh accepted evidence

CL6 context =
capable of READY_FOR_LOCKED_REVALIDATION
```

No Q7 order has yet been sent.

---

## 35. Activation failure

If Stage B reaches:

```text
EXACT_CASH_DISPATCH_PENDING
```

or any ambiguous provider/order state, Stage B is not a successful preparation path.

Disposition:

```text
BLOCKED / RECOVERY_REQUIRED
```

No burn-in authorization follows until accepted recovery has closed the state and a new preparation record is created.

---

# PART H — VERIFIED PRE-RUN BACKUP B1

## 36. B1 creation

After Stage B reaches the exact successful activation result and before the Q7 burn-in experiment, create:

```text
B1 = verified pre-burn-in backup
```

B1 is the backup bound to the final Q7 Preparation Record.

---

## 37. B1 required state

Before B1 may be accepted:

```text
authority = EXACT_CASH_ARMED
post_attempt_count = 0
pending_dispatch_proof_sha256 = null
Central blocker = none
unresolved pending/uncertain = none
configured set = exact nominated 2–3 instruments
runtime/Portfolio/Risk/CashLedger custody = valid
```

Any drift between activation completion and B1 verification blocks Q7 Preparation.

---

## 38. B1 secret rule

Like B0, B1 excludes secret plaintext.

It binds only metadata such as:

```text
identity_key_id
protected secret provider
required-secret presence status
privacy-safe account scope
```

---

# PART I — FINAL Q7 PREPARATION RECORD

## 39. Final status

Only after:

```text
accepted source rescope
accepted implementation
affected offline qualification reruns
Stage A MATERIALIZED
B0 verified
Stage B separately authorized and successfully completed
B1 verified
```

may Q7 preparation be reconsidered.

---

## 40. Final preparation exact conditions

`CL8 Q7 PREPARATION = PASS` requires:

```text
exact candidate commit/tree
clean source tree
accepted Q4 artifact identities
accepted Q5 privacy/hygiene identities
runtime_instance_id
2–3 configured instruments
same account scope
production compose path proven
protected token/account/identity presence
identity_key_id bound to CL7 authority
EXACT_CASH_ARMED
post_attempt_count = 0
pending dispatch proof absent
Central/Portfolio/Risk/CashLedger valid
reconciliation coherent
B1 backup verified
provider mutation count during preparation/activation = 0
```

---

## 41. Burn-in remains separate

Even a final:

```text
CL8 Q7 PREPARATION = PASS
```

does NOT start Q7.

Q7 burn-in still requires a new explicit authorization:

```text
experiment_id =
CL8-SANDBOX-BURNIN-V1

operator gate =
START EXPERIMENT
```

The activation-stage authorization is not reusable.

---

# PART J — PROTECTED-SECRET RESOLUTION CONTRACT

## 42. Unified resolver

The shipped GUI composition path and `v3_10_runtime_cash_cutover.py` must use one accepted protected-resolution boundary for:

```text
TBANK_SANDBOX_TOKEN
TBANK_SANDBOX_ACCOUNT_ID
V310_CL_IDENTITY_KEY_HEX
V310_CL_IDENTITY_KEY_ID
```

The resolver returns plaintext only in process memory to the immediate trusted caller.

It never serializes plaintext into evidence.

---

## 43. Environment compatibility

Environment variables may remain available only for:

```text
tests
explicit development compatibility
explicit non-production diagnostic invocation
```

They are not the canonical Q7 Windows production custody source.

A Q7 production preparation must prove:

```text
secure protected provider in use
```

for the required secrets.

---

## 44. Secret-probe semantics

Presence probes return only metadata:

```text
provider
secure
available
credential_present
logical key
finite privacy-safe error
```

For the identity key, format validation occurs in memory.

No probe returns the secret value.

---

# PART K — Q7R ACCEPTANCE ORACLE

## 45. Closed acceptance set

The bounded implementation review uses exactly these cases:

```text
Q7R-01
shipped desktop SANDBOX_EXECUTION path invokes GuiRuntimeController.compose exactly once per process/runtime composition

Q7R-02
composition failure produces GUI_RUNTIME_COMPOSITION_REQUIRED and no SandboxTradingBot fallback

Q7R-03
repeated refresh/start actions create no second controller/owner composition

Q7R-04
the exact provision-identity entrypoint holds the frozen named mutex and performs 32-byte CSPRNG create-once provisioning, compare-before-write, exact protected read-back, metadata-only output, no overwrite for every existing-custody state and compare-before-delete compensation after a partial write

Q7R-05
missing identity key outside the exact confirmed provisioning entrypoint fails closed with zero write and zero provider call

Q7R-06
malformed identity key fails closed with zero provider call

Q7R-07
identity key ID mismatch fails closed with zero CL7 transition

Q7R-08
normal GUI startup never auto-generates, replaces or rotates identity key material; complete, partial and malformed existing custody is never overwritten

Q7R-09
identity plaintext absent from logs, reports, support bundles, backups and shareable evidence

Q7R-10
v3_10_runtime_cash_cutover.py uses the same protected-resolution boundary

Q7R-11
Q7 production activation path does not require identity plaintext in environment variables

Q7R-12
v3_10_q7_prepare_runtime.py performs zero provider calls and zero provider mutations

Q7R-13
Stage A requires an exact 2–3-instrument SANDBOX_EXECUTION configured set under one account scope

Q7R-14
Stage A validates required Central/Portfolio/Risk/CL2/CL7 local custody

Q7R-15
Q4 release artifacts remain free of runtime/private state after this rescope

Q7R-16
LEGACY_ACTIVE is reported as ACTIVATION_REQUIRED and is never silently transitioned

Q7R-17
offline preparation tool exposes no prepare/confirm/activate/arm/dispatch side effect

Q7R-18
B0 backup covers the complete materialized runtime custody, excludes secret plaintext, preserves the accepted Q3 backup-manifest schema and binds secret-prerequisite metadata only in Stage-A evidence

Q7R-19
activation preparation binds exact candidate/runtime/B0/config/account-scope/identity metadata

Q7R-20
without activation-specific START EXPERIMENT, authenticated provider call count remains zero

Q7R-21
activation START EXPERIMENT cannot authorize Q7 burn-in

Q7R-22
Stage B exposes no dispatch path and provider order mutation count remains zero

Q7R-23
successful Stage B requires exact accepted CL7 transition sequence and exact operator confirmations

Q7R-24
successful Stage B ends EXACT_CASH_ARMED with post_attempt_count=0 and no pending proof

Q7R-25
pending/uncertain/recovery-required state blocks successful Stage B

Q7R-26
B1 is created only after exact successful activation state and complete custody validation

Q7R-27
B1 excludes secret plaintext and binds identity_key_id/account_scope metadata only

Q7R-28
final Q7 Preparation PASS requires verified B1 and zero provider order mutation

Q7R-29
final Q7 Preparation PASS does not authorize CL8-SANDBOX-BURNIN-V1

Q7R-30
tamper/substitution of candidate, runtime, secret metadata, B0/B1 or activation evidence fails closed
```

All 30 cases are mandatory.

No wildcard, score threshold or reviewer-added substitution is allowed.

---

# PART L — DEDICATED TEST REQUIREMENTS

## 46. Synthetic/offline tests

`test_v3_10_q7_preparation_runtime.py` must execute behaviour, not check prefilled PASS booleans.

At minimum it covers:

```text
composition success
composition missing
composition duplication
legacy fallback rejection

Credential Manager fake provider:
present
missing
malformed
ID mismatch
normal-start no generation
confirmed create-once generation with injected deterministic CSPRNG
lock timeout and abandoned mutex produce zero write
two concurrent accepted provisioning calls yield one exact pair and one idempotent no-write result
complete existing pair idempotent no-write
partial existing pair no-write
second-write failure with successful compensation
two accepted concurrent writers never overwrite or delete the completed pair
read-back mismatch with successful compensation
failed compensation produces MANUAL_RECOVERY_REQUIRED

secret leakage canaries

2-instrument runtime
3-instrument runtime
cross-account runtime
orphan runtime
profile/runtime hash mismatch

empty CL2 create
existing CL2 validate
corrupt CL2 reject

LEGACY_ACTIVE accepted only as ACTIVATION_REQUIRED

offline provider-call counter = 0

B0 accepted-manifest schema immutability, Stage-A metadata binding and secret exclusion

activation-preparation schema/tamper checks

START EXPERIMENT scope separation

Stage-B fake transport:
read-only call set
zero POST
successful exact-state outcome
blocked/recovery outcomes

B1 state predicates

final preparation record tamper checks
```

Synthetic provider fakes are allowed.

Real credentials are forbidden in repository tests.

---

## 47. Fixture boundary

`v3_10_q7_preparation_vectors.json` may contain:

```text
synthetic account IDs
synthetic token canaries
synthetic identity keys
synthetic owner state
negative/tamper vectors
expected finite reason tokens
```

It MUST NOT contain:

```text
real token
real Account ID
real identity key
real private path
prefilled all-green result object used as behavioural proof
```

---

# PART M — EVIDENCE SCHEMAS

## 48. Stage-A materialization record

Canonical shareable Stage-A record must bind at least:

```text
version
domain
candidate_commit
candidate_tree
runtime_instance_id
configured_set_sha256
configured_instrument_count
secret_provider
token_present
account_present
identity_key_present
identity_key_id
authority_state
authority_revision
ledger_present
central_present
portfolio_present
risk_present
b0_backup_sha256
b0_backup_size_bytes
b0_manifest_sha256
b0_verification_status
provider_calls_performed
provider_mutations_performed
overall_status
generated_at
record_sha256
```

Domain:

```text
v3.10-cl8-q7-offline-materialization
```

`provider_calls_performed` and `provider_mutations_performed` must both be false.

---

## 49. Activation-preparation record

Canonical shareable activation-preparation record must bind at least:

```text
version
domain
experiment_id
candidate_commit
candidate_tree
runtime_instance_id
b0_backup_sha256
configured_set_sha256
account_scope_nomination_sha256
identity_key_id
pre_authority_state
pre_authority_revision
pre_authority_sha256
central_quiescent
portfolio_valid
risk_valid
ledger_valid
sandbox_environment_proven
provider_calls_before_authorization
provider_mutations_before_authorization
planned_commands
overall_status
generated_at
record_sha256
```

Domain:

```text
v3.10-cl8-q7-cl7-activation-preparation
```

Before activation authorization:

```text
provider_calls_before_authorization = 0
provider_mutations_before_authorization = 0
```

---

## 50. Final Q7 Preparation Record

Canonical final record must bind at least:

```text
version
domain
candidate_commit
candidate_tree
runtime_instance_id
q4_artifact_identity_sha256
q5_privacy_summary_sha256
configured_set_sha256
configured_instrument_count
account_scope_sha256
identity_key_id
authority_state
authority_revision
authority_record_sha256
ledger_revision
ledger_head_sha256
central_revision
portfolio_revision
risk_policy_hash
risk_state_revision
b1_backup_sha256
b1_backup_size_bytes
post_attempt_count
pending_dispatch_proof_sha256
preparation_provider_order_mutations
overall_status
generated_at
record_sha256
```

Domain:

```text
v3.10-cl8-q7-final-preparation
```

Required terminal values for PASS include:

```text
authority_state = EXACT_CASH_ARMED
post_attempt_count = 0
pending_dispatch_proof_sha256 = null
preparation_provider_order_mutations = 0
configured_instrument_count in {2, 3}
```

---

## 51. Canonical evidence encoding

All three schemas use deterministic canonical JSON:

```text
UTF-8
BOM forbidden
object keys lexicographically sorted
schema-defined array order
separators "," and ":"
insignificant whitespace none
NaN / Infinity forbidden
terminal newline none
record_sha256 = SHA-256 lowercase hex over the exact canonical preimage bytes defined below
```

Secret plaintext is forbidden before serialization.

For each schema in sections 48–50, the `record_sha256` preimage is the exact
canonical JSON object with the top-level `record_sha256` member omitted. The
producer MUST:

```text
1. reject any caller-supplied record_sha256
2. canonicalize the complete validated object without that member
3. calculate SHA-256 over those exact preimage bytes
4. insert the lowercase digest as record_sha256
5. canonicalize the final object once and write it immutably
```

Verification removes exactly the top-level `record_sha256` member, reconstructs
the canonical preimage and requires an exact digest match. Missing, duplicate,
non-lowercase or non-64-hex values fail closed.

The SHA-256 of the final serialized file bytes is a separate external custody
identity. Any CL8 `canonical_summary_sha256` binding uses that final-file digest,
not the internal `record_sha256`. Thus neither digest is self-referential.

---

# PART N — AFFECTED QUALIFICATION RERUNS

## 52. Required reruns after implementation

A source change under this rescope creates a new exact candidate.

Before Stage A is accepted, rerun the affected offline qualifications:

```text
Q1 regression/custody
Q4 standalone/artifact
Q5 privacy/release hygiene
```

Q4/Q5 artifacts used later by Q7 must be built from the new exact candidate.

The Q1 rerun includes both inherited custody-oracle nodes added to the ten-path
allowlist. They must recognize only the exact topology frozen in section 6 and
must leave the accepted PRE_RELEASE_CUT/POST_RELEASE_CUT failure-node sets
unchanged. Either oracle failure is an unexpected Q1 failure and blocks Stage A.

---

## 53. Previously accepted Q2/Q3 evidence

This rescope does not automatically reinterpret or rewrite previously accepted Q2/Q3 summaries.

Their later use in the final CL8 evidence assembly requires an explicit adoption/custody check proving that the Q7 rescope delta did not change the semantic surfaces those accepted summaries qualified.

If such equivalence cannot be defended:

```text
rerun the affected phase
```

No silent carry-forward is allowed.

---

# PART O — REVIEW GOVERNANCE

## 54. Contract review

Exactly:

```text
one independent/adversarial contract review
```

Result:

```text
PASS
```

or finite fixed:

```text
CL8-Q7R-C-R1-xx
```

At most one bounded contract-only correction batch, then one finding-scoped closure.

A remaining/new material blocker after closure:

```text
RESCOPE / DEFER
```

---

## 55. Implementation review

After contract acceptance:

```text
isolated implementation branch
from exact accepted contract head
```

Initial gate:

```text
HEAD = merge-base = accepted contract head
ahead/behind = 0/0
worktree = clean
changed paths = 0
```

Implementation receives one independent/adversarial review.

At most one separately authorized bounded correction batch.

Then one finding-scoped closure.

No recursive correction treadmill.

---

## 56. Implementation acceptance does not authorize Stage B

Even after:

```text
Q7 RESCOPE IMPLEMENTATION = ACCEPTED
```

the following remain blocked until their own gates:

```text
provider access
CL7 activation
CL7 arm
Q7 burn-in
Stable acceptance
publication
```

---

# PART P — PROVIDER / EXPERIMENT SEPARATION

## 57. Three distinct authorities

The project must not conflate:

```text
A. offline Q7 preparation rescope
B. CL8-Q7-CL7-ACTIVATION-V1
C. CL8-SANDBOX-BURNIN-V1
```

Each has separate evidence and authority.

---

## 58. Provider call taxonomy

Stage A:

```text
authenticated provider reads = 0
provider mutations = 0
```

Stage B activation:

```text
authenticated Sandbox reads = permitted only after activation-specific START EXPERIMENT
provider order POST = 0
account mutation = 0
```

Q7 burn-in:

```text
provider reads/mutation only under separately accepted CL8-SANDBOX-BURNIN-V1 protocol
```

Real-account execution is forbidden throughout.

---

# PART Q — FAIL-CLOSED PRECEDENCE

## 59. Stage-A precedence

Before reporting `MATERIALIZED`:

```text
candidate identity
→ source cleanliness
→ protected provider available
→ token/account presence metadata
→ identity-key presence/format
→ configured-set identity
→ owner-store integrity
→ CashLedger integrity
→ authority custody
→ B0 backup verify
→ evidence canonicalization
```

Any earlier failure prevents later PASS.

---

## 60. Stage-B precedence

Before authenticated provider read:

```text
exact candidate
→ accepted rescope implementation
→ Stage-A accepted result
→ B0 verified
→ activation Preparation Stage PASS
→ exact experiment ID
→ START EXPERIMENT
```

Before each CL7 dangerous transition:

```text
accepted CL7 transition predicates
→ exact CL7 operator confirmation
```

Before arm:

```text
accepted CL7 activation/reconciliation state
→ explicit CL7 arm confirmation
```

No dispatch occurs in Stage B.

---

## 61. Final preparation precedence

Before Q7 Preparation PASS:

```text
EXACT_CASH_ARMED
→ zero post attempts
→ zero pending dispatch proof
→ owner-store integrity
→ fresh coherent accepted evidence
→ B1 verified
→ privacy scan
→ final canonical record
```

---

# PART R — CURRENT DISPOSITION

## 62. Frozen authority after contract creation

Until later gates are separately completed:

```text
Q2 = ACCEPTED
Q3 = ACCEPTED

Q6 = DEFERRED / NOT WAIVED

Q7 PREPARATION =
BLOCKED / RESCOPE CONTRACT CORRECTION SUCCESSOR CANDIDATE

CL8-Q7-PREP-01..05 =
OPEN

Q7 RESCOPE IMPLEMENTATION =
BLOCKED

PROVIDER ACCESS =
NOT AUTHORIZED

CL8-Q7-CL7-ACTIVATION-V1 =
NOT AUTHORIZED

CL7 CUTOVER / ACTIVATE / ARM =
NOT AUTHORIZED

CL8-SANDBOX-BURNIN-V1 =
NOT AUTHORIZED

START EXPERIMENT =
INELIGIBLE

STABLE ACCEPTANCE =
BLOCKED

TAG / GITHUB RELEASE =
NOT AUTHORIZED

REAL-ACCOUNT EXECUTION =
FORBIDDEN
```

Any future authority must name the exact accepted commit/tree, exact runtime/evidence identity and exact bounded action.

---

# PART S — R1 CONTRACT CORRECTION CUSTODY

## 63. Fixed correction authority

This successor changes contract semantics only for the fixed independent-review
finding set:

```text
CL8-Q7R-C-R1-01
CL8-Q7R-C-R1-02
CL8-Q7R-C-R1-03
CL8-Q7R-C-R1-04
```

The exact correction parent is:

```text
a0ac11cac7d7b63355272f4daf365d4452097805
tree = 4ce96ca736de26326f0a8fe13a155a482e1db3ee
```

The correction delta is exactly one direct successor commit and exactly the
contract path in section 5. Every other repository path remains immutable.

## 64. Closed correction semantics

The correction is limited to:

```text
CL8-Q7R-C-R1-01:
exact create-once identity provisioning protocol and failure states

CL8-Q7R-C-R1-02:
metadata-only secret prerequisites moved to the Stage-A evidence binding;
accepted Q3 backup manifest remains unchanged

CL8-Q7R-C-R1-03:
non-self-referential record_sha256 preimage and final-file custody distinction

CL8-Q7R-C-R1-04:
two exact inherited custody-oracle paths added with topology-only authority;
implementation allowlist count changes from 8 to 10
```

No other semantic correction is authorized. After this commit the single
contract correction budget is exhausted. Review is finding-scoped only to the
four IDs above. A surviving material finding yields `RESCOPE / DEFER`; it does
not open another correction batch.

## 65. Authority after correction

Until a separate explicit acceptance binds the exact successor commit/tree:

```text
CONTRACT = CORRECTION SUCCESSOR CANDIDATE
IMPLEMENTATION = BLOCKED
PROVIDER ACCESS = NOT AUTHORIZED
CL7 ACTIVATION = NOT AUTHORIZED
CL8-SANDBOX-BURNIN-V1 = NOT AUTHORIZED
START EXPERIMENT = INELIGIBLE
STABLE ACCEPTANCE = BLOCKED
PUBLICATION = NOT AUTHORIZED
```
