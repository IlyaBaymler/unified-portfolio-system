# V3.10 CL8 Q7A — live entrypoint and two-stage freshness gate: bounded contract freeze

Статус:

`CONTRACT FREEZE CANDIDATE / IMPLEMENTATION BLOCKED / PROVIDER ACCESS BLOCKED / START EXPERIMENT INELIGIBLE`

Этот additive rescope закрывает только два blocker принятой Q7A lineage:

```text
CL8-Q7A-LIVE-RS-01 = FRESH_CL6_CONTEXT_REQUIRED
CL8-Q7A-LIVE-RS-02 = EXACT_LIVE_Q7A_ENTRYPOINT_NOT_FROZEN
```

Он не меняет принятые CL1–CL7, CL8 Q7/Q7A semantics, Q7A synthetic
acceptance или Q7 PASS oracle. Он не переносит PASS между commit, tree,
runtime, artifact, Preparation или experiment.

---

## 1. Exact predecessor и custody

```text
repository =
baimleriv/unified-portfolio-system

exact predecessor commit =
141416eefefcba2c3d90fc721e52282ea0a1ea42

exact predecessor tree =
4e85875b75184d0895e2cdac9cf6852d60f800b6

contract branch =
agent/v3-10-clean-cl8-q7a-live-preparation-rescope-contract

initial HEAD = merge-base =
141416eefefcba2c3d90fc721e52282ea0a1ea42

ahead / behind = 0 / 0
worktree = clean
changed paths = 0
```

Exact predecessor уже содержит отдельно принятые Q7A contract, currency
correction, synthetic implementation и immutable proposal-to-owner admission
binding. Этот rescope не переопределяет их.

Заблокированный preparation packet:

```text
SHA-256 =
74f31e3c4cafc2c6e3a995cf2634af0f3d7171081627bb6616c8627c20e14aca

disposition =
IMMUTABLE_BLOCKED_EVIDENCE_NOT_REUSABLE_FOR_START
```

Его blockers являются входом этого rescope. Его SHA нельзя использовать в
`START EXPERIMENT`, а его evidence нельзя переписать или заменить новым
packet под прежней identity.

---

## 2. Contract-only allowlist

До independent/adversarial review и explicit acceptance exact contract
commit/tree разрешено менять ровно один repository path:

```text
docs/project/V3_10_CL8_Q7A_LIVE_PREPARATION_RESCOPE_CONTRACT_RU.md
```

Любой другой added, modified, deleted, renamed или untracked repository path:

```text
SCOPE_VIOLATION -> RESCOPE
```

Contract work не трогает provider, protected credentials, private runtime,
backup, B0/B1, GUI, Sandbox account, GitHub или remote refs.

---

## 3. Frozen future implementation allowlist

Только после:

1. independent/adversarial exact-head review этого contract;
2. closure всех material findings;
3. explicit acceptance exact contract commit/tree;

может быть создана отдельная implementation branch непосредственно от exact
accepted contract head:

```text
branch =
agent/v3-10-clean-cl8-q7a-live-preparation-rescope-implementation

required initial state =
HEAD = merge-base = accepted contract head
ahead / behind = 0 / 0
worktree = clean
changed paths = 0
```

Implementation delta ограничен ровно тремя новыми paths:

```text
current/tools/v3_10_q7a_live_entrypoint.py
current/tests/test_v3_10_q7a_live_entrypoint.py
current/tests/fixtures/v3_10_q7a_live_entrypoint_vectors.json
```

Все иные predecessor paths immutable. В частности, запрещено менять accepted
Q7A synthetic tool/test/fixture, CL1–CL7, Central, Portfolio, Risk, Portfolio
Risk, CashLedger, SandboxExecutionAdapter, GUI, provider transport, release,
workflow и historical custody oracles.

Если один exact live entrypoint невозможно реализовать в этих трёх paths
через существующие owners, implementation останавливается:

```text
SCOPE_EXPANSION_REQUIRED -> RESCOPE
```

---

## 4. Bounded mission

Rescope добавляет только:

1. один exact live Q7A CLI entrypoint;
2. один durable/reviewable economic-smoke Preparation, который связывает
   будущую live-acquisition procedure, но не будущие observation values;
3. post-START one-shot `LIVE_ACQUISITION_PRE_ADMISSION` gate;
4. immediate freshness validation до Central admission;
5. post-admission `LOCKED_REVALIDATION_PRE_POST` gate на той же durable intent
   lineage;
6. immutable privacy-safe evidence для обеих live gates;
7. fail-closed enforcement одного provider POST attempt.

Он не создаёт нового владельца cash, Portfolio, reservation, Risk, order,
currency, freshness, reconciliation или execution.

Главный temporal invariant:

```text
no native freshness interval spans a human review/acceptance gate
```

Human review связывает только durable identities и будущую процедуру. Все
expiring Portfolio, cash, quote, RiskState, CL6 и provider observations
получаются после exact `START EXPERIMENT` и используются только внутри одного
непрерывного live run.

---

## 5. Explicit non-goals

Вне scope остаются:

```text
изменение Q7A controlled proposal semantics
изменение Q7 natural burn-in PASS oracle
новая strategy или forced market signal
автоматический candidate generation вне принятого Q7A control record
новый Portfolio/Central/Risk/CashLedger owner
новый provider adapter или endpoint
provider retry / redirect / resubmit
GUI execution path
legacy bot/diagnostic POST path
RiskPolicy weakening
Sandbox balance/account mutation
automatic live acquisition or economic-smoke execution
automatic START EXPERIMENT
Q7B
Stable acceptance, PR Ready, merge, tag или publication
real-account access
```

Contract или implementation acceptance сами по себе не дают provider/runtime
authority.

---

## 6. One exact live entrypoint

Repository получает ровно один Q7A live module:

```text
current/tools/v3_10_q7a_live_entrypoint.py
```

У него ровно один live mode:

```text
ECONOMIC_SMOKE
```

Допустимы offline-only `--help`, schema validation и Preparation validation,
но они structurally не имеют provider/runtime path и не являются live mode.
Отдельный provider-read-only mode не создаёт переносимую freshness authority и
не может быть prerequisite экономического smoke. Любой второй live mode,
alias, hidden environment switch, GUI shortcut, direct helper invocation или
alternate provider mutation path запрещён.

Live mode обязан:

- загрузить exact Preparation bytes и проверить caller-supplied expected
  SHA-256 до private/runtime access;
- проверить experiment ID, exact candidate commit/tree, accepted contract и
  implementation identities;
- проверить exact runtime manifest, account-scope digest, identity-key ID,
  environment `SANDBOX`, configured-set identity и target membership;
- использовать existing protected credential loader; token и raw Account ID
  не принимаются через CLI args, environment echo, stdout или shareable
  evidence;
- получить single-instance account-scoped lock до runtime/provider work;
- создавать новые evidence files create-once; overwrite запрещён;
- завершаться finite status/reason и не продолжать после failed gate.

Import module, `--help`, schema validation и offline dry-run не выполняют
provider call и не меняют runtime.

---

## 7. Durable reviewed Preparation

```text
experiment_id =
CL8-Q7-E2E-SMOKE-V1

required operator command =
START EXPERIMENT — CL8-Q7-E2E-SMOKE-V1 — preparation <accepted_exact_sha256>
```

Preparation формируется offline, проходит отдельный read-only review и explicit
acceptance exact SHA-256 до live execution. Она связывает stable identities и
точную процедуру получения будущего live evidence.

Preparation не доказывает freshness и не содержит ещё не наблюдавшиеся:

```text
current quote/value/hash/as_of
current Portfolio response/hash/revision
current broker cash or withdraw-limits values
current operations response/watermark
current RiskState guard read-back
current Central read-back
current CL4/CL5/CL6 proof values or freshness timestamps
```

Подмена future observation заранее выбранным значением запрещена. Preparation
связывает HOW; post-START evidence связывает WHAT.

---

## 8. Durable Preparation binding

Canonical Preparation bytes связывают минимум:

```text
domain/version
experiment_id
candidate commit/tree
accepted Q7A contract identities
accepted contract-rescope commit/tree
accepted implementation commit/tree
accepted exact-candidate Q1/Q4/Q5 identities where applicable
runtime manifest SHA-256
account_scope_sha256
identity_key_id
environment = SANDBOX
configured_set_sha256
nominated target instrument identity hash
immutable Q7AControlRecord SHA
RiskPolicy hash and mode = ENFORCED
static metadata schema/hash, currency and lot-size evidence identities
live acquisition policy SHA-256
quote acquisition policy SHA-256
native freshness limits and final <=10s CL7 bound
provider READ budget
max_provider_post_attempts = 1
automatic transport/application/redirect retries = 0
pre-admission stop rules
post-admission same-lineage rules
evidence/privacy policy
evidence root identity
B0/B1 or later accepted backup binding
recovery/disarm procedure identity
stop conditions
absolute experiment deadline
created_at_utc
preparation_sha256
```

Preparation не содержит token, raw Account ID, raw instrument/provider ID,
raw order/intent ID или private absolute path в shareable части.

Любой durable binding drift требует новой Preparation и нового
review/acceptance. Начатая Preparation single-use независимо от
PASS/BLOCKED/FAIL/INDETERMINATE.

---

## 9. Frozen live-acquisition policies

Preparation содержит canonical policy records, а не live results.

### 9.1 Account/cash/Portfolio policy

```text
exact account/environment and configured-set scope
existing CL3 GetOperationsByCursor owner and accepted bounded pagination
SandboxService/GetSandboxPortfolio through existing owner
SandboxService/GetSandboxWithdrawLimits through existing owner
canonical Portfolio refresh/read-back through existing owner
accepted CL2/CL4/CL5/CL6 builders
per-owner absolute deadlines
no new endpoint or owner
```

`GetSandboxPositions` допускается только если existing accepted Portfolio owner
для exact candidate требует его для canonical refresh/read-back. Preparation
явно фиксирует это boolean и method identity; implementation не может добавить
его динамически.

### 9.2 Quote acquisition policy

Preparation связывает:

```text
service = MarketDataService
method = GetLastPrices
target = one nominated configured-set instrument
lastPriceType = LAST_PRICE_EXCHANGE
request_count = 1
retry_count = 0
redirect_count = 0
absolute timeout policy
allowed source = existing GuiRuntimeController/provider owner
price field and side semantics = accepted owner semantics
canonicalization = exact non-binary-float price representation
required currency = accepted target metadata currency
maximum quote age = accepted RiskPolicy/native owner limit
timestamp/age evaluation rule
relationship to Q7A proposal, Risk, Portfolio Risk and Central admission
privacy-safe evidence schema
finite failure reasons
```

Preparation не содержит future `current_quote_sha256`, future price или future
quote timestamp.

### 9.3 Closed provider-read budget

Preparation фиксирует exact method/count budget для обеих live gates:

```text
Gate A:
- one logical CL3 GetOperationsByCursor sync session
- one SandboxService/GetSandboxPortfolio
- one SandboxService/GetSandboxWithdrawLimits
- zero or one SandboxService/GetSandboxPositions, fixed by Preparation
- one MarketDataService/GetLastPrices for exactly one target

Gate B:
- one logical CL3 GetOperationsByCursor sync session
- one SandboxService/GetSandboxPortfolio
- one SandboxService/GetSandboxWithdrawLimits
- no GetLastPrices
```

Каждая logical CL3 sync session использует только accepted CL3 pagination,
absolute deadline и exact finite per-page retry policy, чья identity и numeric
limits записаны в Preparation. Остальные reads имеют retry count zero, если
accepted existing owner не имеет более строгого frozen finite policy; в таком
случае Preparation обязана назвать exact policy/version/count. Outer retry,
повтор Gate A/Gate B или automatic reacquisition запрещены. Provider POST
никогда не входит в read budget.

---

## 10. Post-START Gate A — LIVE_ACQUISITION_PRE_ADMISSION

После exact accepted `START EXPERIMENT` один process выполняет bounded live
acquisition через существующих owners. Между START, acquisition, freshness
validation и Central admission нет human review/acceptance gate.

Gate A выполняет один logical acquisition sequence:

```text
accepted CL3 bounded operation sync
-> accepted CL2 append/read-back
-> canonical Portfolio refresh/read-back
-> accepted CL4 current-cash/reconciliation rebuild
-> accepted CL5 CashAvailability rebuild
-> accepted CL6 PortfolioRiskCashContext rebuild
-> one GetLastPrices request for the nominated target
-> existing Q7A proposal/admission bridge preconditions
```

CL3 bounded pagination может содержать несколько page requests только в
рамках accepted CL3 limit; это один logical operation-sync acquisition.
Автоматическая reacquisition всего Gate A, quote retry, indefinite refresh loop
или silent restart запрещены.

Gate A evidence связывает фактически наблюдённые values:

```text
experiment_id and Preparation SHA-256
candidate commit/tree and runtime identity
account_scope_sha256 and configured_set_sha256
target instrument identity hash
live acquisition policy SHA-256
quote acquisition policy SHA-256
per-read service/method/status/error_class/transient/attempt_count
privacy-safe tracking_id_sha256
provider read receipt-set and sanitized content hashes
operations completeness/watermark identity
CL2 ledger revision/head and append identities
Portfolio revision/checksum/as_of
CL4 proof/reconciliation status and SHA
CL5 availability status/reason and SHA
CL6 status/reason/context SHA/evaluated_at
RiskPolicy/RiskState identities
CL7 state/revision/record SHA
post_attempt_count = 0
pending_dispatch_proof = absent
Central intent/reservation/order count = 0
actual quote canonical bytes SHA-256
exact canonical price/value
quote source and provider timestamp
quote acquired_at_utc
quote freshness deadline/age rule
quote request attempt_count = 1
Gate A overall freshness_deadline_utc
evidence record SHA-256
```

Цена сохраняется в существующем exact canonical representation; authoritative
identity не выводится из binary float.

`freshness_deadline_utc` равен minimum всех native dependency deadlines,
включая 120-second CL4/CL5/CL6 bounds и quote-age rule. Нельзя продлевать
freshness restamping, copying или local reserialization.

Если live evidence incomplete, mismatched, non-READY или expired до Central
admission:

```text
terminal reason = LIVE_EVIDENCE_EXPIRED_BEFORE_ADMISSION
or another exact pre-admission finite reason
Central intent/reservation/order creation = 0
CL7 attempt marker = 0
provider POST = 0
```

Это не PASS, provider failure, economic failure или permission to bypass
freshness. Run заканчивается. Новый attempt требует новой Preparation,
review/acceptance и новой exact START command; expired evidence не переносится.

---

## 11. Central admission boundary

До authoritative Central owner durable write run находится в фазе:

```text
PRE_CENTRAL_ADMISSION
Central intent = absent
reservation = absent
provider POST = 0
```

Drift proposal, quote/live evidence, Portfolio, RiskState, CashLedger, CL7,
account scope, configured set или другого required proof в этой фазе приводит
к pre-admission terminal failure с zero Central effect и zero POST.

Irreversible lineage boundary наступает ровно когда existing Central owner
durably сохраняет exact admitted intent/reservation:

```text
Central contains exact admitted intent
intent status = QUEUED
admission binding = exact Q7A control/proposal/owner binding
```

С этого durable write начинается:

```text
POST_CENTRAL_ADMISSION
proposal lineage = immutable
intent/reservation lineage = immutable
```

Никакой новый proposal, marker/control lineage, Central intent или reservation
не может заменить эту lineage.

---

## 12. Post-START Gate B — LOCKED_REVALIDATION_PRE_POST

После Central admission существующий CL7/SandboxExecutionAdapter owner
выполняет accepted final lock order, bounded reads/rebuild и lock-held
revalidation для того же exact queued intent. Между Gate A и Gate B нет human
review или нового `START EXPERIMENT`.

Gate B проверяет:

```text
same accepted Preparation and run identity
same Q7AControlRecord/proposal/admission binding
same durable Central intent/reservation lineage
accepted CL3/CL2 operation completeness and ledger head
canonical Portfolio revision/checksum
CL4 coherent reconciliation
CL5 READY availability
CL6 READY_FOR_LOCKED_REVALIDATION context
RiskPolicy/RiskState guard
Portfolio Risk authorization
Central revision and one-intent budget
CL7 authority revision and attempt budget
all native temporal bounds
final accepted CL7 <=10s freshness/skew gate
```

Provider reads required by accepted CL7 final rebuild are authorized only as
part of this exact live run, only for the same account/intent lineage and only
through existing owners. Они не создают отдельный diagnostic/read authority.

---

## 13. Post-admission drift and same-lineage rule

Если Gate B выявляет drift/expiry после Central durable admission и до attempt
marker:

```text
terminal status = POST_ADMISSION_DRIFT
terminal reason = EXISTING_INTENT_REQUIRES_RECOVERY
provider POST = 0 unless an earlier durable attempt marker proves otherwise
existing Central intent/reservation preserved
new proposal/intent/reservation = forbidden
```

Допустимо только existing same-lineage read-only status/recovery/revalidation,
если оно уже авторизовано accepted architecture. Этот contract не добавляет
automatic resume, cleanup или mutation authority.

После boundary категорически запрещено:

```text
создавать другую Q7A proposal/control/marker lineage
создавать другой Central intent или reservation
автоматически перезапускать live acquisition как новый economic attempt
silent clear/cancel/delete durable intent или reservation
restore старого state поверх durable Central state
automatic intent cleanup или repair
second order или replacement intent
```

Если same-lineage continuation не разрешено existing owners, run остаётся
blocked. Cleanup/mutation требует отдельно принятого и явно авторизованного
recovery/rescope path.

Post-admission terminal evidence связывает original Q7AControlRecord SHA,
proposal hash, admission binding, privacy-safe Central intent identity,
Central revision, reservation state, drift reason, CL7 state, attempt count и
blocking status. Это не Q7A PASS.

---

## 14. Proposal-to-owner and dispatch binding

Live run использует accepted Q7A control/proposal bridge. Exact live entrypoint
не создаёт альтернативный Central/Risk path.

Обязательная цепочка:

```text
accepted Q7AControlRecord
-> canonical public proposal marker
-> immediate pre-Central integrity check
-> detached private proposal graph
-> existing Risk and Portfolio Risk owners
-> existing Central owner admission
-> accepted CL7 locked dispatch proof
-> durable attempt-before-POST marker
-> existing SandboxExecutionAdapter
-> at most one physical PostSandboxOrder
```

Фактический Central input, Risk input и marker должны иметь принятую
immutable admission binding. Mutation public object after hook, field
replacement, custom equality, subclass substitution или direct Central call
не могут породить PASS.

---

## 15. Attempt, outcome and recovery

Durable CL7 attempt marker записывается до физического transport invocation.
После marker:

- `post_attempt_count` никогда не уменьшается;
- timeout, lost response или unknown outcome сохраняют pending proof;
- automatic retry/resubmit запрещён;
- новый live acquisition/economic run запрещён до принятого recovery
  disposition;
- local validation error не может отменить факт возможного POST.

Q7A PASS требует accepted Q7A contract evidence: definitive provider fill,
один Central intent/reservation и terminal state, resolved pending proof,
Portfolio reconciliation, ровно один CashLedger effect и ровно одну Risk
execution registration с общей lineage.

Explicit rejection, unfilled/partial unsupported order, timeout или ambiguity
классифицируются finite reason как `SAFE_REJECTED`, `INCOMPLETE`,
`INDETERMINATE` или `FAIL`; они не являются PASS.

---

## 16. Finite failure taxonomy

Entry point и evidence используют только закрытый набор primary reasons:

```text
PREPARATION_INVALID
PREPARATION_SHA_MISMATCH
PREPARATION_ALREADY_CONSUMED
SOURCE_IDENTITY_MISMATCH
RUNTIME_MANIFEST_MISMATCH
ENVIRONMENT_NOT_SANDBOX
ACCOUNT_SCOPE_MISMATCH
CONFIGURED_SET_MISMATCH
TARGET_NOT_MEMBER
CREDENTIAL_CUSTODY_INVALID
SINGLE_INSTANCE_LOCK_UNAVAILABLE
PROVIDER_READ_FAILED
PROVIDER_READ_SCOPE_INVALID
PROVIDER_READ_INCOMPLETE
LIVE_ACQUISITION_POLICY_MISMATCH
LIVE_ACQUISITION_BUDGET_EXHAUSTED
LIVE_EVIDENCE_EXPIRED_BEFORE_ADMISSION
CL3_SYNC_BLOCKED
PORTFOLIO_REFRESH_BLOCKED
CL4_RECONCILIATION_BLOCKED
CL5_AVAILABILITY_NOT_READY
CL6_CONTEXT_NOT_READY
AUTHORITY_NOT_EXACT_CASH_ARMED
ATTEMPT_BUDGET_EXHAUSTED
PENDING_DISPATCH_PRESENT
CENTRAL_NOT_QUIESCENT
RISK_POLICY_NOT_ENFORCED
RISK_STATE_GUARD_MISMATCH
QUOTE_OR_METADATA_INVALID
CONTROL_RECORD_INVALID
PROPOSAL_ADMISSION_BINDING_INVALID
LOCKED_REVALIDATION_FAILED
POST_ADMISSION_DRIFT
EXISTING_INTENT_REQUIRES_RECOVERY
ATTEMPT_MARKER_FAILED
PROVIDER_SAFE_REJECTED
PROVIDER_OUTCOME_AMBIGUOUS
POSTCONDITION_FAILED
EVIDENCE_WRITE_FAILED
```

Новые причины требуют contract rescope. Free-text provider exception не
становится primary reason и не попадает в shareable evidence.

---

## 17. Privacy and evidence custody

Shareable evidence может сохранять только:

```text
service
method
status_code
finite error code/category
error_class
transient
attempt_count
tracking_id_sha256
account_scope_sha256
configured_set_sha256
safe content/revision hashes
timestamps and deadlines
finite statuses/reasons
```

Запрещено сохранять token, Authorization header, raw Account ID, raw
intent/order/operation/instrument identifiers, full provider payload, private
absolute path, Windows Credential Manager secret или authoritative runtime
file bytes.

Evidence files:

- canonical UTF-8 JSON without BOM;
- ordinally sorted keys and compact separators for hashed bytes;
- create-once with exact read-back;
- immutable manifest with file size and SHA-256;
- new run ID for every rerun;
- timestamps in explicit UTC format;
- no overwrite, symlink, junction or path traversal.

---

## 18. Mandatory adversarial implementation tests

Future implementation acceptance требует минимум:

```text
wrong account/configured set/target is rejected before provider call
Preparation SHA substitution is rejected
Preparation cannot contain a future quote value/hash/timestamp
live acquisition policy tamper is rejected
quote policy method/source/target/age tamper is rejected
actual quote evidence binds exact Preparation and proposal/admission lineage
provider receipt/content substitution is rejected
partial pagination/incomplete watermark is rejected
second Gate A acquisition or quote request is rejected
live evidence overwrite/reuse is rejected
live freshness timestamp restamping is rejected
expired live evidence before Central yields zero intent/reservation/POST
live revision/hash drift is rejected before proposal
post-admission drift preserves the exact queued intent/reservation
post-admission drift cannot create a second proposal/intent
post-admission drift cannot auto-cancel/clear/restore Central state
final lock-held staleness/drift is rejected before attempt marker and remains
attached to the same Central lineage
CL7 non-armed/pending/attempt-count mismatch is rejected
Central non-quiescence is rejected
non-target proposal/intent is rejected
public proposal mutation after hook is rejected
private graph or actual Central input mutation is rejected
direct Central/legacy/GUI/diagnostic bypass cannot produce PASS
second physical POST is rejected
transport/application retry or redirect is rejected
ambiguous outcome preserves pending proof and blocks resubmit
raw secret/private identifiers are absent from shareable evidence
```

Tests должны проверять negative side effects: exact provider read/post counts,
Central state, CL7 marker/pending proof, Portfolio, ledger и Risk state, а не
только exception reason.

Fixture содержит normative valid vectors и adversarial tamper vectors для
durable Preparation schema, live-acquisition/quote policy, post-START evidence,
pre/post-Central expiry, account/set substitution, retry budget и one-POST
budget.

---

## 19. Implementation qualification

После implementation exact successor обязательны:

```text
focused live-entrypoint tests
accepted Q7A synthetic matrix
focused CL3/CL4/CL5/CL6/CL7/Central/Risk regressions
full Q1 exact regression/custody comparison
scoped Ruff check and format check
compileall
git diff --check
Q4 deterministic source/standalone pair if artifact surface includes tool
Q5 privacy/release-hygiene scan of the same artifact bytes
native Windows offline smoke proving import/help/dry-run and zero network
independent/adversarial exact-head review
explicit implementation acceptance exact commit/tree
```

Failure-node identity сравнивается с exact predecessor. Исчезновение known
failure не компенсирует новый node. Existing custody oracle не переписывается
без отдельного disposition.

Green tests, Q4/Q5 или native smoke не дают provider authority.

---

## 20. Preparation and experiment sequence

После принятой implementation/qualification lineage порядок закрыт:

```text
freeze exact runtime and durable Preparation
-> read-only review
-> explicit acceptance exact Preparation SHA
-> exact single-use START EXPERIMENT
-> one-shot LIVE_ACQUISITION_PRE_ADMISSION
-> immediate native freshness validation
-> Central durable admission if and only if fresh
-> irreversible same-lineage boundary
-> LOCKED_REVALIDATION_PRE_POST on the same intent
-> at most one durable attempt marker and provider POST
-> terminal audit/recovery
-> independent terminal review
-> separate Q7A acceptance decision
```

Любой provider call вне этой exact `START EXPERIMENT` запрещён. Команда
single-use и разрешает только указанный experiment/preparation. Ни один native
freshness interval не проходит через human review gate.

---

## 21. Review and correction governance

Contract candidate проходит один independent/adversarial exact-head review.
Explicit acceptance допустим только при:

```text
material findings = 0
exact contract commit/tree verified
cumulative changed-file surface = exactly one contract path
predecessor merge-base exact
worktree clean
```

Этот successor является единственным отдельно авторизованным bounded
contract-only correction batch для fixed finding set
`CL8-Q7A-LIVE-R1-01..03`. После него проводится только finding-scoped closure
review. Contract correction budget исчерпан:

```text
surviving material blocker -> RESCOPE / ABORT / DEFER
```

---

## 22. Current authority boundary

До explicit acceptance этого exact contract:

```text
contract review = authorized
contract correction batch = used / exhausted
implementation branch = blocked
implementation = blocked
provider READ = not authorized
provider POST = not authorized
runtime mutation = not authorized
CL8-Q7-E2E-SMOKE-V1 START EXPERIMENT = ineligible
Q7A acceptance = unchanged
Q7B = not authorized
PR Ready / merge / Stable acceptance / publication = not authorized
real-account execution = forbidden
```

Contract acceptance разрешает только создание bounded implementation branch.
Implementation acceptance разрешает только последующую qualification и
Preparation work. Ни один из этих gates не заменяет exact experiment command.
