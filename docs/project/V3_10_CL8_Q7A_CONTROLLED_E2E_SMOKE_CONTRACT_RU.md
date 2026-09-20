# CL8 Q7A — controlled Sandbox economic lifecycle smoke: contract candidate

Status: **CANDIDATE / independent contract review pending / no experiment authority**.

This additive contract proposes a separate `CL8-Q7-E2E-SMOKE-V1` qualification
before a *new* natural Q7 burn-in. It does not amend the accepted CL8 Q7 PASS
oracle. Q7A's controlled proposal never substitutes for the naturally
occurring lifecycle required by CL8 sections 71–72, never shortens the
continuous 24–48 hour Q7 window, and never converts the currently running
burn-in into Q7A evidence.

## 1. Exact source and custody

```text
repository = baimleriv/unified-portfolio-system
contract-freeze predecessor = 5cb5b093a8eab3472c7599c882626358c03f5a59
predecessor tree = 039efd92fbb71245a8f4e87d3a9060f013564667
PR #209 head at drafting = b6e2e56360073dad803ffcca5235984d839f74e4
PR #209 base at drafting = 484e4638a4694657d73e78d6f48041b9db044673
contract-only path = docs/project/V3_10_CL8_Q7A_CONTROLLED_E2E_SMOKE_CONTRACT_RU.md
```

PR #209 remains Draft. Before implementation, independently review and
explicitly accept this contract's exact commit/tree. The separately prepared
GUI currency-hydration correction needs its own exact-head review/acceptance
and regression disposition. No PASS transfers between heads, runtimes,
artifacts, experiments, or phases.

## 2. Why Q7A is separate

The active `CL8-SANDBOX-BURNIN-V1` showed natural HOLD and natural BUY blocked
by `PORTFOLIO_RISK_CURRENCY_UNKNOWN`, with zero provider order POST. That is
valid fail-closed pre-dispatch evidence, not an economic lifecycle. Proposed
disposition: `INCOMPLETE / PRE-ECONOMIC BLOCKER OBSERVED`; final classification
belongs to its separate terminal review. This contract does not stop,
restart, modify, or reclassify that live run.

Q7A asks whether one fresh isolated runtime can complete exactly one bounded
Sandbox economic lifecycle through the accepted account-level chain. It keeps
the exact accepted Q7 configured set of 2–3 instruments and nominates exactly
one member as the Q7A economic target. `CONTROLLED_Q7A_ONLY` is provenance in
the Q7A control/evidence record defined below, not a new production proposal
field or execution authority. The controlled candidate enters the existing
proposal/coordination boundary and cannot bypass ConfiguredExecutionSet,
Central, Risk, Portfolio Risk, CL7 or SandboxExecutionAdapter. Q7B remains
natural operation with 2–3 instruments and no manufactured signals.

## 3. Frozen Q7A bounds

```text
environment = T-Invest Sandbox only
protected account = one exact account scope
configured execution set = exact accepted Q7 set of 2–3 instruments
Q7A economic target = exactly one nominated member of that set
requested economic target = at most one lot if accepted metadata/Risk permit
economic lifecycle = at most one account-wide
physical PostSandboxOrder attempts = at most one account-wide
automatic transport/application/redirect retries = zero
diagnostic/GUI/direct legacy mutation paths = zero
```

Non-target configured instruments may perform ordinary market reads,
scheduling, Risk refresh, Portfolio reconciliation and strategy evaluation.
The Q7A harness/control boundary must reject or suppress any non-target
economic coordination request or intent before Central; an unexpected such
request is a fail-closed Q7A result, not permission for a second lifecycle.
This experiment restriction does not change production
`ConfiguredExecutionSet` semantics, Stage A's accepted 2–3-instrument
topology, or the account-level execution path.

The Q7A tool may orchestrate only existing owners. It must not create a new
cash, reservation, order, currency, Portfolio or Risk authority. It must not
alter Risk limits, Sandbox balance, identity keys or instrument selection to
turn a blocked candidate into a pass. Real-account paths are forbidden.

## 4. One coherent pre-POST proof

Before an attempt marker or POST, one fresh account-bound proof set must bind
the exact candidate commit/tree and runtime; accepted CL8, CL7, Q7
preparation and Q7A contract identities; account scope and identity-key ID;
exact configured-set identity, the nominated target and its checked
RUB/lot-size metadata; CL7
`EXACT_CASH_ARMED`, attempt count zero in this fresh runtime and no pending
proof; no blocking Central intent; canonical Portfolio FRESH/coherent;
CashLedger valid; CL4 reconciliation coherent; CL5 READY; CL6
READY_FOR_LOCKED_REVALIDATION; RiskPolicy READY/ENFORCED and its hash;
RiskState guard hash; authorized Portfolio Risk decision; exact current
quote; and a valid CL7 LockedDispatchProof. All revision, scope, hash and
freshness checks remain those of accepted CL7 lock-held revalidation.

Unknown/empty/non-RUB/stale/mismatched currency metadata fails closed.
Account cash, ticker, GUI label, class code and quote alone never infer
instrument currency. The canonical configured-set instrument evidence is
checksummed `portfolio_risk_metadata.json` via its existing loader. An
existing canonical position supplies currency only under the accepted
Portfolio Risk rule; conflict is evaluated as mismatch, never overwritten.

Local preflight grants no provider authority. An independently reviewed
exact Preparation and a later operator command
`START EXPERIMENT — CL8-Q7-E2E-SMOKE-V1 — preparation <sha256>` are required
before any authenticated provider READ or order POST.

### Q7A control/evidence provenance

The immutable `Q7AControlRecord` is owned and validated by the Q7A
control/evidence adapter. Its canonical representation binds at least:

```text
version
domain = CL8_Q7A_CONTROL_RECORD_V1
experiment_id = CL8-Q7-E2E-SMOKE-V1
candidate_commit
candidate_tree
account_scope_sha256
configured_set_sha256
target_instrument_id
target_runtime_key (or exact equivalent configured-runtime identity)
requested_target_lots
max_provider_post_attempts = 1
automatic_retries = 0
control_mode = CONTROLLED_Q7A_ONLY
created_at
record_sha256 = SHA-256(canonical record bytes excluding record_sha256)
```

Canonical record bytes are UTF-8 JSON with ordinally sorted keys, compact
comma/colon separators, integer lot/attempt/retry fields, an explicit UTC
`created_at` timestamp and no floating-number representation. Hash exactly
those bytes before adding `record_sha256`; preserve the original bytes for
read-back. A shareable evidence packet carries only safe identities and the
record hash, not raw private account/order identifiers.

Before constructing the candidate, the adapter verifies record integrity,
exact account/configured-set binding, target membership, lot bound and the
one-lifecycle budget. Immutable Q7A evidence binds the control-record SHA to
the resulting proposal identity and then to the normal Risk, Portfolio Risk,
Central, CL7 proof and SandboxExecutionAdapter outcomes. The label never
grants authorization: Strategy, Central, Risk and CL7 retain their accepted
owners and checks. Do not add a `CONTROLLED_Q7A_ONLY` field to production
`StrategyProposal` or another production economic schema merely to carry
this label. If existing interfaces cannot preserve this evidence binding
without such a schema change, stop implementation and request a separate
explicit contract rescope.

## 5. Dispatch, outcome and restart

The only permitted mutation is one `PostSandboxOrder` through Central's
locked dispatch lease and CL7's durable attempt-before-POST marker. Disable
redirects/retries; no generic retry wrapper, hedge, resubmit after timeout
or second order. An attempt remains in CL7 history even if response is lost.

Q7A PASS requires a broker-confirmed filled lifecycle with definitive order
identity/quantity; one Central intent/reservation and terminal Central state;
resolved CL7 pending proof; canonical Portfolio matching broker state;
exactly one corresponding CashLedger effect with exact Money and preserved
opening lineage; and exactly one Risk execution registration after
reconciliation. All identities must share the same account, intent, order
and proof lineage. Authorization precedes POST. Clean restart/read-back
must show no replay, no ambiguity, valid ledger/Risk and coherent Portfolio.

Explicit rejection, accepted-but-unfilled order, unsupported partial fill,
timeout or ambiguous success is not PASS. Classify by accepted CL7/Central
semantics as `SAFE_REJECTED`, `INCOMPLETE`, `INDETERMINATE` or `FAIL`, with a
finite reason. Ambiguity blocks new dispatch and permits lookup/recovery
only. No fabricated fill, intended-state posting or automatic replay.

## 6. Zero-tolerance invariants

```text
duplicate provider submit = 0
duplicate Central intent/reservation = 0
duplicate CashLedger effect = 0
duplicate Risk accounting = 0
stale-proof dispatch = 0
wrong account/identity/environment = 0
unexplained broker/Portfolio/cash delta = 0
fill without Portfolio and Risk closure = 0
new cash/reservation/execution owner = 0
raw private IDs or secret in shareable evidence = 0
```

Any breach stops the smoke and creates an immutable privacy-safe terminal
record. If safe stop or definitive recovery is impossible, preserve blocking
custody and alert the operator; never perform workaround provider mutation.

## 7. Synthetic implementation gate

Only after separate contract acceptance may a branch be made from its exact
head. Initial state: HEAD = merge-base = accepted contract head, 0/0,
clean, changed paths 0. Initial proposed new-file surface:

```text
current/tools/v3_10_q7a_e2e_smoke.py
current/tests/test_v3_10_q7a_e2e_smoke.py
current/tests/fixtures/v3_10_q7a_e2e_smoke_vectors.json
```

This permits a fake-provider synthetic harness, Q7A control-record handling
and offline Preparation builder only. It must compose the accepted 2–3-member
configured set and nominate one member as the economic target. The initial
surface is the three paths above; a Q7A-specific preparation/control-record
helper requires separate bounded rescope only if it cannot be contained in
the tool. It cannot edit CL1–CL7, Central, Risk, CashLedger, Portfolio
Repository, Portfolio Risk evaluator, SandboxExecutionAdapter, provider
transport, production GUI configured-set topology, release, prior custody
oracles or the live runtime. If existing interfaces cannot enforce target-only
economic coordination while preserving the accepted chain, stop and rescope;
do not create a second execution topology or authority.

Fake-provider matrix: successful one-order fill; explicit rejection; timeout
before definitive response; ambiguous-success transport loss; partial fill
if supported; duplicate callback/read-back; non-target economic
proposal/coordination attempt; restart after POST before
reconciliation; stale proof; Portfolio drift; RiskState drift; Central
revision drift; CashLedger drift; account-scope mismatch; missing currency;
non-RUB currency; and attempted second POST. For each, assert exact CL7
and Central states, physical POST count, pending proof, Portfolio effect,
CashLedger effect and Risk effect. Missing assertion fields are failures.

Run focused currency/Risk, Q7 preparation, CL7 recovery, Central/adapter,
CashLedger/reconciliation tests, full pytest, scoped Ruff, compileall and
`git diff --check`. Compare failed pytest node IDs against exact predecessor,
not merely counts. Any new unexpected failure blocks Preparation. Prior
custody oracles cannot be silently rewritten.

## 8. Preparation and Q7B account disposition

Q7A Preparation binds candidate commit/tree, accepted contract and
source/artifact identities, isolated runtime, account scope, identity key,
configured-set hash, nominated target/metadata checksum, immutable
Q7AControlRecord hash, RiskPolicy hash, RiskState guard,
Portfolio/Central/CashLedger revisions and head, CL7 record revision/SHA,
fresh CL6 context SHA, max POST one, retries zero, evidence root, backup,
recovery/disarm plan and stop conditions. It is ineligible while another
live experiment owns the same protected account, while regressions remain
unresolved, or before independent contract/implementation acceptance and
new exact-candidate Q1/Q4/Q5 acceptance. Earlier source/standalone PASS
does not transfer to the correction successor. Its
SHA cannot be reused after any candidate, policy, runtime, account or
evidence drift.

After a successful one-POST Q7A BUY, broker state may contain an open
position. The preferred clean topology uses **two separately proven Sandbox
accounts**: Account A for Q7A controlled smoke, and Account B for fresh
Stage A → B0 → Stage B → Q7B Preparation → natural burn-in. Account A's Q7A
economic state cannot contaminate Account B's opening state; account-scope
binding and isolation must be proven before either experiment.

For a single Sandbox account, successful Q7A leaves **Q7B eligibility
BLOCKED** until the resulting open position has a separately accepted custody
disposition. Exactly one of these paths must first be accepted: (A) an
ownership-transfer/bootstrap mechanism proving exact Q7A position lineage
into the fresh Q7B runtime, or (B) a separately prepared and authorized
cleanup/close experiment returning the account to a broker-confirmed eligible
state. Neither mechanism is selected or implemented by this contract. If
neither exists in accepted architecture, the single-account path remains
`BLOCKED / FOLLOW-UP RESCOPE REQUIRED`; a fresh runtime alone may classify
the position `UNATTRIBUTED` and is not eligible for Q7B. Do not infer
ownership from ticker/account alone, restore stale B0 over changed broker
state, reset CL7 history, delete economic evidence or silently adopt the
position. Do not expand Q7A into a two-POST BUY→SELL cleanup. Q7A PASS does
not satisfy natural Q7B PASS or Stable acceptance.

## 9. Review and authority

One independent/adversarial review of the exact one-file contract commit is
required. Explicit acceptance only with material findings zero. A material
finding yields a fixed set and separately authorized correction or RESCOPE;
no implementation or provider action follows automatically.

```text
current burn-in = separate immutable evidence; terminal disposition external
currency correction = local candidate, not accepted here
Q7A contract = candidate
Q7A implementation = blocked pending corrected contract review/acceptance
Q7A Preparation = blocked
Q7A provider READ/POST = not authorized
Q7B new burn-in = not authorized
PR #209 = Draft
Stable acceptance/publication = blocked
```
