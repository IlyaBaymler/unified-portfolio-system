# V3.10 CL8 Q7A — live entrypoint and two-stage freshness gate: bounded contract freeze

Статус:

`LIVE CONTRACT ACCEPTED THROUGH 0f317b6e... / PR224-R1-02 AND PR224-R1-03 R2 CONTRACT RESCOPE CANDIDATE / IMPLEMENTATION BLOCKED / PROVIDER ACCESS BLOCKED / START EXPERIMENT INELIGIBLE`

Этот файл содержит принятый live-preparation contract и additive contract
rescope records. Раздел 26 является новым bounded contract-only candidate.
До independent/adversarial review и explicit acceptance его exact successor
раздел 26 не даёт implementation, provider, runtime или experiment authority.

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
- one logical target-only MarketDataService/GetCandles acquisition session,
  whose exact bounded HTTP request count is fixed by Preparation
- one MarketDataService/GetLastPrices for exactly one target

Gate B:
- one logical CL3 GetOperationsByCursor sync session
- one SandboxService/GetSandboxPortfolio
- one SandboxService/GetSandboxWithdrawLimits
- one target-only MarketDataService/GetTradingStatus through the existing
  SandboxExecutionAdapter market precheck
- no GetCandles
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
-> one target-only StrategyCandleLoader acquisition
-> deterministic controlled-proposal derivation from that exact frame
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
target candle-acquisition policy SHA-256
actual candle request-range set SHA-256 and exact request count
canonical complete-candle frame SHA-256, row count and latest candle time
base strategy proposal SHA-256
controlled proposal SHA-256 and deterministic derivation version
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
CANDLE_ACQUISITION_POLICY_MISMATCH
CANDLE_READ_FAILED
CANDLE_FRAME_INVALID
CANDLE_FRAME_INCOMPLETE
CANDLE_EVIDENCE_STALE
CONTROL_RECORD_INVALID
CONTROLLED_PROPOSAL_DERIVATION_INVALID
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
target candle policy/profile/interval/lookback tamper is rejected
non-target candle acquisition is rejected
candle HTTP request count, retry or redirect drift is rejected
candle frame duplicate/out-of-order/incomplete/non-finite rows are rejected
candle frame substitution after acquisition is rejected
stale or wrong-last-candle evidence is rejected before Central
controlled proposal changes only the frozen PRIMARY signal/target fields
controlled proposal preserves exact base indicators, stop and candle identity
base/controlled proposal hashes bind the same candle-frame identity
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

---

## 23. Additive candle/proposal acquisition rescope

### 23.1 Authority, predecessor and one-file surface

Этот material rescope отдельно авторизован после обнаружения невозможности
реализовать accepted live entrypoint без неучтённого candle input. Он не
является вторым correction batch для `CL8-Q7A-LIVE-R1-01..03` и не переносит
acceptance прежнего exact commit/tree на новый successor.

Раздел 23 имеет precedence только над конфликтующими прежними фразами о
закрытом Gate A read budget и non-goal изменения controlled-proposal
derivation. Все остальные принятые границы разделов 1–22 сохраняются.

```text
rescope predecessor commit =
7e1fd5862c1d5520141af7f22bc94b76876d6b93

rescope predecessor tree =
a3683d42b82fd9404f4077acad5e13e18041490e

contract branch =
agent/v3-10-clean-cl8-q7a-live-candle-proposal-rescope-contract

initial HEAD = merge-base = predecessor
ahead / behind = 0 / 0
worktree = clean
changed paths = 0

contract-only allowlist =
docs/project/V3_10_CL8_Q7A_LIVE_PREPARATION_RESCOPE_CONTRACT_RU.md
```

Fixed rescope blockers:

```text
CL8-Q7A-LIVE-RS2-01 = CANDLE_OWNER_READ_BUDGET_MISSING
CL8-Q7A-LIVE-RS2-02 = CONTROLLED_PROPOSAL_DERIVATION_UNFROZEN
```

Все остальные repository paths immutable. Contract work не выполняет
provider call, runtime mutation, backup/restore, GitHub write или experiment.

### 23.2 Successor implementation lineage

Только после independent/adversarial review и explicit acceptance exact
contract successor разрешено создать новую branch непосредственно от него:

```text
branch =
agent/v3-10-clean-cl8-q7a-live-candle-proposal-implementation

required initial state =
HEAD = merge-base = exact accepted contract successor
ahead / behind = 0 / 0
worktree = clean
changed paths = 0
```

Implementation allowlist остаётся ровно трёхфайловым:

```text
current/tools/v3_10_q7a_live_entrypoint.py
current/tests/test_v3_10_q7a_live_entrypoint.py
current/tests/fixtures/v3_10_q7a_live_entrypoint_vectors.json
```

CL1–CL7, accepted Q7A bridge, Strategy, GUI owner, Central, Portfolio, Risk,
Portfolio Risk, CashLedger, SandboxExecutionAdapter и provider transport не
меняются. Если описанная ниже цепочка не реализуется через их существующие
interfaces в этих трёх paths:

```text
SCOPE_EXPANSION_REQUIRED -> RESCOPE
```

### 23.3 Exact target candle-acquisition policy

Preparation связывает policy, а не future candle values:

```text
owner = existing StrategyCandleLoader
provider service/method = MarketDataService/GetCandles
target = exact nominated configured-set member only
instrument identity = exact target identity from Q7AControlRecord
interval = exact target MultiInstrumentProfile candle_interval
required_bars = exact strategy-suite required_bars_for_suite()
requested lookback_days = exact strategy_lookback_days() result
request mode = date range
candleSourceType = CANDLE_SOURCE_EXCHANGE
logical acquisition sessions = 1
physical HTTP requests = 1
provider retries per HTTP request = 0
redirects followed = 0
automatic reacquisition = 0
non-target GetCandles = 0
```

Preparation дополнительно связывает exact profile/runtime/config hashes,
`required_bars`, `lookback_days`, interval-specific maximum span, deterministic
chunking-policy identity, exact HTTP request count `1`, per-request timeout and
absolute session deadline. До acceptance Preparation обязана доказать, что
target interval присутствует в existing `TBankSandboxClient.CANDLE_MAX_SPAN` и
frozen lookback range помещается в один interval-specific maximum span. Exact
count вычисляется повторно до первого request. Любой второй chunk/request,
retry, redirect, range drift или дополнительный candle request даёт terminal
pre-admission failure.

Live session использует existing `StrategyCandleLoader.load()` и accepted
`TBankSandboxClient` с `max_retries = 0`. Provider telemetry callback является
единственным call-budget receipt collector. Client session имеет redirect
budget zero; redirect response не может быть followed. Нельзя вызывать
`get_candles`, `_post` или HTTP session по альтернативному пути.

### 23.4 Canonical candle evidence and freshness

После read существующий loader обязан вернуть только complete candles. Live
entrypoint проверяет до proposal construction:

```text
frame type = exact pandas.DataFrame owner output
row count >= required_bars
index = unique, strictly increasing, UTC-normalized timestamps
required columns = open/high/low/close/volume/is_complete
all selected rows is_complete = true
OHLC = finite and positive
volume = finite and non-negative
low <= min(open, close) <= max(open, close) <= high
latest candle = exact last complete candle
latest candle close_at = begin timestamp + exact configured interval
age from latest candle close_at <= one configured interval + 300 seconds
future skew <= 5 seconds
```

Canonical frame bytes содержат только normalized selected columns and exact
UTC timestamps. OHLC scalar должен иметь exact built-in `float` или exact
`numpy.float64` type и кодируется как `float.hex(float(value))`; volume имеет
exact built-in `int` или exact `numpy.int64` type и кодируется как
`int(value)`; `is_complete` имеет exact built-in `bool` или exact
`numpy.bool_` type. NaN, infinity, bool-as-number, subclass и alternate
decimal formatting запрещены. Evidence сохраняет frame SHA-256, row count,
earliest/latest begin/close time, requested-range set SHA-256, exact physical
request count and sanitized per-request receipts. Raw target instrument ID не
попадает в shareable evidence.

Любая мутация frame между hash и proposal/admission check даёт
`CANDLE_FRAME_INVALID` до Central effect. Restamping, copying под новым
timestamp или замена frame после read запрещены.

### 23.5 Deterministic controlled-proposal derivation

Live entrypoint не создаёт новый Strategy owner. Он вызывает existing
`build_strategy_proposal(runtime, profile, complete_frame, now=...)` ровно один
раз для target и получает `base_proposal`. Затем выполняется единственная
разрешённая deterministic derivation version:

```text
derivation = CL8_Q7A_CONTROLLED_PROPOSAL_V1

controlled primary decision = dataclasses.replace(
    exact base primary decision,
    signal = 1,
    target_lots = Q7AControlRecord.requested_target_lots  # exact 1
)

controlled decisions = exact base decisions with only PRIMARY replaced
controlled comparison = existing compare_strategy_decisions(
    controlled decisions,
    exact base primary_strategy,
)

controlled proposal = dataclasses.replace(
    exact base proposal,
    primary_target_lots = requested_target_lots,
    decisions = controlled decisions,
    comparison = controlled comparison,
)
```

Все остальные primary decision fields, включая `target_weight`, `reason`,
`indicators`, `bars_used`, `required_bars` и `stop_level`, сохраняются byte-
equivalent после canonical serialization. Shadow decisions не меняются.
Runtime/profile, strategy/config, candle time, generated-at, ticker, interval и
instrument bindings сохраняются. `execution_authorized` остаётся false.

`CONTROLLED_Q7A_ONLY` остаётся только в accepted control/evidence lineage; в
production `StrategyProposal` не добавляется поле или authority label.

### 23.6 Quote/request relationship

После derivation accepted `Q7AControlledHooks` создаёт immutable public marker
и detached private proposal graph. Existing `_ProductionGuiHooks` формирует
`GuiCoordinationRequest` для exact target и выполняет единственный
`GetLastPrices` read.

Live module может обернуть accepted provider только прозрачным call-budget /
evidence adapter. Existing `_ProductionGuiHooks` остаётся единственным caller:
именно hook вызывает обёрнутый existing provider method, проверяет response,
выполняет accepted quotation conversion, создаёт
`PortfolioRiskCandidateQuote` и строит итоговый `GuiCoordinationRequest`.
Evidence adapter сам не выполняет второй `GetLastPrices`, не строит market
value, не выбирает endpoint и не меняет request/response. Он наблюдает ровно
один вызов, проверяет exact `units` string, exact `nano` int, provider time and
target identity, сохраняет private detached raw read-back для immediate
validation и передаёт hook byte-equivalent detached result. Shareable quote
canonical bytes содержат `units`, `nano`, time, source и target identity hash,
но не raw target ID. Любой второй call, adapter modification/retry/reordering
или alternate quote construction даёт `PROVIDER_READ_SCOPE_INVALID`.

Request связывает:

```text
proposal = exact controlled proposal
profile = exact target profile
candles = exact hashed complete frame
lot_size = exact accepted static metadata lot size
portfolio_risk_candidate_quote = exact live GetLastPrices observation
evaluated_at = one controlled UTC clock observation after quote read
```

Legacy Risk price/ATR path сохраняет existing owner semantics: proposal
PRIMARY `indicators.close` и ATR происходят из exact complete candle frame.
Portfolio Risk candidate valuation сохраняет existing owner semantics и
использует exact live last-price quote. Quote запрещено записывать в candle
frame, подменять им PRIMARY indicators или использовать для restamping candle
time. Evidence связывает оба operands и их разные роли.

Перед Central entrypoint повторно проверяет frame SHA, base/controlled proposal
SHA, quote canonical SHA, quote freshness, exact request object identity и
Q7AControlRecord binding. Затем existing
`Q7AControlledHooks.coordinate_marked()` выполняет непосредственную
pre-Central integrity check и передаёт detached private graph existing Central
owner. Direct Central call, reconstructed request или alternate delegate не
может породить PASS.

### 23.7 Revised Gate A order and budget

Нормативный Gate A после этого rescope:

```text
accepted CL3 bounded operation sync
-> accepted CL2 append/read-back
-> canonical Portfolio refresh/read-back
-> accepted CL4 current-cash/reconciliation rebuild
-> accepted CL5 CashAvailability rebuild
-> accepted CL6 PortfolioRiskCashContext rebuild
-> canonical target flat-position proof: current_lots = 0
-> one target-only StrategyCandleLoader logical acquisition
-> one exact base strategy proposal
-> one CL8_Q7A_CONTROLLED_PROPOSAL_V1 derivation
-> existing _ProductionGuiHooks performs one target-only GetLastPrices request,
   accepted validation/conversion and GuiCoordinationRequest construction
-> evidence wrapper validates captured quote/request evidence without a second read
-> immediate canonical Portfolio revision/flat-position revalidation
-> immediate frame/proposal/quote/request freshness and integrity validation
-> Q7AControlledHooks proposal marker/admission bridge
-> existing Central durable QUEUED BUY 0 -> 1 if and only if all checks pass
```

Gate A budget из раздела 9.3 изменён только добавлением candle session. Ни
`GetInstrumentBy`, ни `FindInstrument`, ни trading-status, order, account-list,
GUI loop или non-target market read не разрешены. Lot/currency берутся только
из accepted checksummed static metadata.

Gate B сохраняет post-admission same-lineage contract и дополнительно явно
учитывает mandatory existing `SandboxExecutionAdapter._market_precheck()`:

```text
same durable Central intent
-> existing SandboxExecutionAdapter
-> existing market precheck
-> exactly one target-only GetTradingStatus
-> accepted locked CL7 rebuild/revalidation
-> durable attempt marker
-> at most one physical PostSandboxOrder
```

Entrypoint-side duplicate trading-status read, Gate A reacquisition, new
proposal и new intent запрещены.

### 23.8 Additional adversarial closure matrix

Implementation acceptance дополнительно требует доказать:

```text
wrong candle target/interval/profile rejected before request
request count computed before IO and bounded by Preparation
range gap/overlap or extra chunk rejected
transport retry/redirect rejected with zero Central effect
missing/duplicate/out-of-order/incomplete candle rejected
non-finite/impossible OHLCV rejected
stale/future latest candle rejected before marker/Central
frame mutation after hash rejected before Central
base proposal built exactly once through existing owner
only PRIMARY signal and target_lots differ from base decision
shadow decisions and all preserved PRIMARY fields remain exact
comparison equals existing compare_strategy_decisions output
quote cannot replace candle close, ATR input or candle timestamp
base/frame/controlled/request/marker hashes form one lineage
direct owner call or reconstructed request cannot produce PASS
second candle session, proposal derivation or quote read rejected
current_lots = 1 rejected before Central with zero intent and zero POST
current_lots = 2 rejected before Central with no SELL, zero intent and zero POST
Portfolio revision/position drift before Central rejected with zero intent
successful admission proves current_lots=0, target_lots=1, requested_lots=1,
direction=BUY, one new intent, no replacement and no cancellation
Gate B proves exactly one owner GetTradingStatus and zero duplicate status reads
```

Каждый negative case проверяет provider request receipts, Central intent and
reservation counts, CL7 attempt marker/pending proof, Portfolio, ledger and
Risk state. Exception-only assertion недостаточен.

### 23.9 Review and current authority

Этот rescope candidate требует отдельного independent/adversarial exact-head
review. Explicit acceptance допустим только при:

```text
exact predecessor/merge-base = 7e1fd5862c1d5520141af7f22bc94b76876d6b93
cumulative changed-file surface = exactly the one contract path
CL8-Q7A-LIVE-RS2-01..02 = CLOSED
material findings = 0
exact successor commit/tree and contract blob custody verified
```

До такого acceptance:

```text
candle/proposal rescope contract = CANDIDATE
new implementation branch = BLOCKED
implementation = BLOCKED
provider READ / POST = NOT AUTHORIZED
runtime mutation = NOT AUTHORIZED
Preparation = NOT AUTHORIZED
START EXPERIMENT = INELIGIBLE
Q7A/Q7B/Stable acceptance = unchanged
```

---

## 24. RS2-R1 governance correction and exact BUY binding

Этот раздел имеет precedence над конфликтующими фразами разделов 9.3,
11–12 и 23.6–23.8. Он является одним bounded contract-only correction для
review disposition `CL8-Q7A-LIVE-RS2-R1-01..03` и не открывает implementation,
provider, runtime, Preparation или experiment authority.

### 24.1 `CL8-Q7A-LIVE-RS2-R1-01` — withdrawn review premise

Previous review premise о том, что existing `_ProductionGuiHooks` не выполняет
`GetLastPrices`, противоречит exact production source:

```text
source path = current/trading_robot/gui_runtime_controller.py
source git blob = 062eeef9a0918a9dfdf9527c96967870325e5b07
```

Нормативный disposition:

```text
finding = CL8-Q7A-LIVE-RS2-R1-01
status = MATERIAL_FINDING_WITHDRAWN
reason = REVIEW_PREMISE_INVALID_ON_EXACT_SOURCE
material = false
contract correction required for ownership = no
production correction required = no
```

Это не `CLOSED_BY_CORRECTION`. Frozen correct lineage:

```text
Q7A transparent evidence wrapper observes one existing provider call
-> existing _ProductionGuiHooks performs target-only GetLastPrices
-> existing hook validates target/units/nano/provider time
-> existing hook performs accepted quotation conversion
-> existing hook constructs PortfolioRiskCandidateQuote
-> existing hook constructs GuiCoordinationRequest
-> Q7A bridge validates proposal/request/quote binding
-> existing Central / Risk / Portfolio Risk remain authoritative owners
```

Wrapper связывает, где exact existing telemetry/interception point это
предоставляет, service/method, target identity hash, raw units/nano/provider
time/source, tracking ID hash, attempt count, request/result timestamps,
canonical raw quote SHA, canonical `PortfolioRiskCandidateQuote` binding,
final request SHA and proposal/admission binding. Wrapper не выполняет второй
read, не создаёт alternate quote и не обходит hook ради дополнительных
evidence fields. Недоступное через existing boundary поле не является поводом
менять production owner.

### 24.2 `CL8-Q7A-LIVE-RS2-R1-02` — Gate B trading-status budget

Existing `SandboxExecutionAdapter` остаётся единственным owner market precheck.
Gate B budget включает ровно один target-only:

```text
service = MarketDataService
method = GetTradingStatus
target = nominated Q7A target only
logical calls = 1
physical request budget = 1
automatic application retries = 0
redirect replay = 0
reacquisition = 0
```

Preparation связывает existing transport configuration, которая доказывает
этот bound без изменения `SandboxExecutionAdapter` или provider transport.
Если existing configuration/interface этого не гарантирует:

```text
SCOPE_EXPANSION_REQUIRED -> RESCOPE
```

Evidence связывает privacy-safe target identity hash, service/method,
status code or finite error, attempt/retry count, tracking ID hash,
request/result timestamps, safe response-content hash и exact market/API
availability outcome, использованный existing owner. Full provider payload не
экспортируется.

Если market precheck блокирует execution:

```text
same durable Central intent = preserved
new proposal / new intent = 0 / 0
provider order POST = 0
Gate A restart = forbidden
```

Existing post-admission recovery semantics применяются к той же lineage.

### 24.3 `CL8-Q7A-LIVE-RS2-R1-03` — canonical flat `0 -> 1 BUY`

Q7A live smoke допускает Central admission только при fresh canonical proof:

```text
account scope = exact accepted scope
configured set = exact accepted set
target = exact nominated target
canonical target current_lots = 0
requested_target_lots = 1
expected economic delta = +1 lot
expected direction = BUY
```

Gate A evidence связывает Portfolio revision/checksum, target identity,
`current_lots = 0`, account/configured-set identity, Gate A evidence SHA and
Preparation SHA. Под existing single-instance/account-scoped live lock, после
initial flat proof и до Central запрещён local canonical Portfolio refresh/write,
кроме действий exact accepted owner path.

Immediately before `Q7AControlledHooks.coordinate_marked()` entrypoint повторно
читает canonical Portfolio identity и требует одновременно:

```text
Portfolio revision/checksum = exact initially bound identity
target current_lots = 0
```

Любой drift даёт finite `PORTFOLIO_FLAT_POSITION_DRIFT` до Central:

```text
Central intent / reservation = 0 / 0
CL7 attempt marker = 0
provider POST = 0
```

Controlled proposal до Central обязан доказать:

```text
primary signal = 1
primary target_lots = 1
proposal primary_target_lots = 1
expected delta = +1
expected direction = BUY
```

Successful admission требует exact existing owner fields:

```text
Central result current_lots = 0
Central result proposed_target_lots = 1
Central result approved_target_lots = 1
queued candidate current_lots = 0
queued candidate target_lots = 1
queued candidate requested_lots = 1
queued candidate direction = BUY
new intent count = 1
reservation lineage count = 1
replaced intent = absent
cancelled intent = absent
```

`NO_POSITION_CHANGE`, `CANCELLED_NO_POSITION_CHANGE`, `REAUTHORIZED`,
`REPLACED`, `SELL`, risk-reducing target, approved target other than one or
non-zero current lots не являются Q7A admission PASS. До durable admission они
дают pre-Central fail с zero effect. Если unexpected durable intent уже создан,
run становится `POST_ADMISSION_DRIFT / EXISTING_INTENT_REQUIRES_RECOVERY` без
replacement proposal/intent и следует existing same-lineage rules.

Mandatory adversarial cases:

```text
current_lots = 0 -> controlled BUY path may proceed
current_lots = 1 -> reject before Central; intent/reservation/POST = 0
current_lots = 2 -> reject before Central; SELL/intent/reservation/POST = 0
Portfolio revision or target position drift before Central
  -> PORTFOLIO_FLAT_POSITION_DRIFT; intent/reservation/POST = 0
```

### 24.4 Corrected authority and future implementation surface

Future implementation allowlist не расширяется:

```text
current/tools/v3_10_q7a_live_entrypoint.py
current/tests/test_v3_10_q7a_live_entrypoint.py
current/tests/fixtures/v3_10_q7a_live_entrypoint_vectors.json
```

Production `_ProductionGuiHooks`, `CentralOrderCoordinator`,
`SandboxExecutionAdapter`, Risk, Portfolio Risk, CL7 и `TBankSandboxClient`
остаются immutable. Если exact quote observation, one-call trading-status bound
или flat `0 -> 1 BUY` нельзя доказать в этих трёх paths через existing owners:

```text
IMPLEMENTATION_SURFACE_INSUFFICIENT / RESCOPE REQUIRED
```

До finding-scoped independent closure и explicit acceptance exact successor:

```text
CL8-Q7A-LIVE-RS2-R1-01 = MATERIAL_FINDING_WITHDRAWN / PROPOSED DISPOSITION
CL8-Q7A-LIVE-RS2-R1-02 = PROPOSED_CLOSURE
CL8-Q7A-LIVE-RS2-R1-03 = PROPOSED_CLOSURE
contract = CORRECTION CANDIDATE
implementation = BLOCKED
provider READ / POST = NOT AUTHORIZED
runtime mutation = NOT AUTHORIZED
Preparation = NOT AUTHORIZED
START EXPERIMENT = INELIGIBLE
```

---

## 25. PR224-R1-02 — accepted Portfolio owner broker-order visibility rescope

Этот раздел является отдельно авторизованным bounded contract-only rescope для
одного finding:

```text
PR224-R1-02 = ACCEPTED_PORTFOLIO_OWNER_ORDER_READ_SUPPRESSED
```

Он имеет precedence над конфликтующими запретами order-read в разделах 9.3,
23.7 и 24 только в отношении одного account-scoped Gate A read
`SandboxService/GetSandboxOrders` через existing
`CanonicalPortfolioManager.refresh()`. Он не расходует заново исчерпанный
correction batch раздела 22, не принимает contract successor и не открывает
implementation, provider, runtime, Preparation или experiment authority.

### 25.1 Exact predecessor, surface и source fact

```text
exact predecessor / merge-base =
0ebc2e90e8763d1d46e1d013911557ec8a83bae6

exact predecessor tree =
223fb0bce58f91caf4e14e9ddc74878225c412ac

contract-only changed path =
docs/project/V3_10_CL8_Q7A_LIVE_PREPARATION_RESCOPE_CONTRACT_RU.md

all other repository paths = IMMUTABLE
```

Exact predecessor source фиксирует существующую authoritative chain:

```text
CanonicalPortfolioManager.refresh()
-> api.get_portfolio(exact account)
-> if api exposes get_orders:
     api.get_orders(exact account)
-> BrokerPortfolioAdapter.from_api_portfolio(..., broker_orders=...)
-> canonical Portfolio reconciliation
-> PortfolioRepository publication/read-back
-> PortfolioPreflight
```

Source custody:

```text
current/trading_robot/portfolio_manager.py
git blob = a484cea153f3796dc9db096288375ab0f2e1e440

current/trading_robot/tbank_sandbox.py
git blob = 74f4ab60897a28db43a768f95ae4c90230845ad6
```

`TBankSandboxClient.get_orders()` выполняет exact account-scoped
`SandboxService/GetSandboxOrders`. Его broker orders входят в existing
`PendingOrderState`, `ReconciliationStatus.PENDING_ORDER` и
`ReconciliationStatus.PENDING_ORDER_UNCERTAIN`; это accepted Portfolio owner
semantics, а не новая Q7A economic classification.

Root cause:

```text
accepted owner requires get_orders visibility
+ Q7A evidence wrapper hides get_orders
+ frozen Gate A budget forbids GetSandboxOrders
= canonical pending-order evidence can be silently suppressed
```

### 25.2 One owner-controlled Gate A broker-order read

Gate A authorizes ровно один read:

```text
service = SandboxService
method = GetSandboxOrders
account scope = exact accepted Sandbox account
owner = CanonicalPortfolioManager.refresh

logical calls Gate A = 1
physical requests Gate A = 1
automatic application retries = 0
redirect replay = 0
automatic reacquisition = 0

logical calls Gate B = 0
physical requests Gate B = 0
```

Read остаётся account-wide. Q7A wrapper не фильтрует response по nominated
target, configured-set membership или ожидаемому economic outcome до передачи
existing Portfolio owner. Order другого configured или unexpected instrument
остаётся доступен accepted reconciliation и не может быть отброшен как
non-target.

Normative chain:

```text
existing CanonicalPortfolioManager.refresh
-> existing provider GetSandboxPortfolio
-> existing provider GetSandboxOrders
-> existing BrokerPortfolioAdapter
-> existing canonical reconciliation
-> existing PortfolioRepository publication/read-back
-> existing PortfolioPreflight
```

Q7A evidence wrapper может только:

```text
observe exact call
enforce call/method/account/gate budget
retain a detached private response for immediate custody validation
capture privacy-safe receipt/evidence
pass the unfiltered detached owner input through the accepted interface
```

Он не может:

```text
classify broker order economically
choose an order to ignore
cancel, adopt, repair or replace an order
create order ownership
clear pending/uncertain state
publish parallel Portfolio state
replace reconciliation or PortfolioPreflight
```

### 25.3 Durable Preparation policy

Preparation обязана раздельно связывать Gate A и Gate B authority. Schema
содержит exact equivalent следующего набора; имена полей могут следовать
existing canonical style, но асимметрия `1 / 0` не может быть неоднозначной:

```text
orders_service = SandboxService
orders_method = GetSandboxOrders
orders_owner = CanonicalPortfolioManager.refresh
orders_account_scope_sha256 = exact accepted scope hash

orders_requests_gate_a = 1
orders_requests_gate_b = 0
orders_retries = 0
orders_redirect_replays = 0
orders_automatic_reacquisition = 0
```

Preparation не содержит future response, future order identity или future
Portfolio result. Она фиксирует только durable policy/owner/budget identities.

### 25.4 Corrected Gate A read budget and order

Normative Gate A read budget становится:

```text
one logical accepted CL3 operations-sync session

one physical SandboxService/GetSandboxPortfolio
through the accepted canonical Portfolio owner graph

one physical account-scoped SandboxService/GetSandboxOrders
inside the same CanonicalPortfolioManager.refresh owner invocation

one SandboxService/GetSandboxWithdrawLimits

zero or one SandboxService/GetSandboxPositions
only if already fixed by accepted owner policy and Preparation

one target-only MarketDataService/GetCandles logical session
with the accepted bounded physical request count

one target-only MarketDataService/GetLastPrices

no GetTradingStatus
no account-list read
no instrument lookup
no diagnostic/second GetSandboxOrders
no GetSandboxOrderState
no cancel/replace/post order endpoint
```

Gate A order относительно accepted owner graph:

```text
accepted CL3 bounded operation sync
-> accepted CL2 append/read-back
-> CanonicalPortfolioManager.refresh:
     GetSandboxPortfolio
     GetSandboxOrders
     accepted adapter/reconciliation/publication
-> canonical Portfolio read-back and PortfolioPreflight
-> accepted CL4/CL5/CL6 rebuild
-> accepted target candle/proposal/quote path
-> immediate pre-Central validation
-> Central admission only if all evidence is valid and nonblocking
```

`GetSandboxOrders` не является отдельным diagnostic phase и не может быть
повторён после Portfolio publication, перед Central или при ошибке.

### 25.5 Gate B remains order-read closed

Gate B сохраняет accepted same-lineage post-admission semantics и budget:

```text
accepted CL3 sync session
GetSandboxPortfolio
GetSandboxWithdrawLimits
exactly one target GetTradingStatus through existing owner

GetSandboxOrders = 0
GetCandles = 0
GetLastPrices = 0
```

Любая попытка Gate B вызвать `GetSandboxOrders` блокируется до provider IO и не
может создавать replacement proposal, replacement intent или новый Portfolio
owner. Gate A order-read authority не переносится в Gate B.

### 25.6 Fail-closed acquisition and canonical pending-order semantics

Gate A order receipt является обязательным evidence. Любое из условий:

```text
provider failure or timeout
malformed response
wrong service or method
wrong/unbound account scope
physical request count != 1
attempt_count != 1
retry_count != 0
redirect/replay count != 0
automatic reacquisition
response custody mismatch
wrapper-hidden get_orders on the exact provider
```

даёт:

```text
Gate A = BLOCKED
Central intent = 0
reservation = 0
CL7 attempt marker = 0
provider order POST = 0
```

После provider failure запрещён fallback к `broker_orders = ()`. Различие
нормативно:

```text
method genuinely unavailable in an accepted owner configuration
!= method deliberately hidden by the Q7A wrapper
```

Для exact predecessor provider exposes `get_orders`; wrapper обязан сохранить
его видимость и exact owner invocation.

Accepted result semantics сохраняются без переопределения:

```text
active broker order
-> canonical pending-order state
-> reconciliation / PortfolioPreflight blocking
-> Central admission = 0
-> POST = 0

unknown or uncertain broker order
-> canonical uncertain pending state
-> blocking
-> Central admission = 0
-> POST = 0
```

Эти правила действуют и при `target current_lots = 0`. Flat target proof не
компенсирует существующую external/manual broker order activity.

### 25.7 Response custody, privacy and evidence

Если wrapper удерживает detached private response, обязательна цепочка:

```text
provider response
-> detached private byte-equivalent order collection
-> exact canonical response SHA-256
-> unfiltered existing Portfolio owner input
-> canonical Portfolio result identity
```

Wrapper не изменяет order, lot, direction, status, instrument, ordering или
membership и не строит собственный economic model. Любая mutation,
reordering, filtering или hash/input mismatch блокирует Gate A до Central.

Shareable evidence связывает:

```text
service = SandboxService
method = GetSandboxOrders
owner = CanonicalPortfolioManager.refresh
gate = A
account_scope_sha256
attempt_count
retry_count
redirect_replay_count
status_code or finite error category
tracking_id_sha256
request_started_at
request_completed_at
orders_response_canonical_sha256
canonical Portfolio revision/checksum
accepted aggregate pending/reconciliation identities
```

Raw Account ID, request ID, order ID, broker order ID и full provider payload
не входят в shareable evidence. Existing privacy-safe canonical Portfolio
identities используются вместо parallel order summary/model.

### 25.8 Mandatory adversarial implementation matrix

Future implementation acceptance дополнительно требует доказать:

```text
empty GetSandboxOrders
-> normal accepted owner path may continue

one active target BUY order
-> canonical PENDING_ORDER block
-> Central intent/reservation/POST = 0/0/0

one uncertain target order
-> canonical PENDING_ORDER_UNCERTAIN block
-> Central intent/reservation/POST = 0/0/0

one order for non-target or unexpected instrument
-> unfiltered accepted Portfolio reconciliation semantics preserved
-> never silently discarded merely because non-target

GetSandboxOrders provider failure or malformed response
-> no empty-orders fallback
-> Gate A blocked with zero Central effect

second GetSandboxOrders logical or physical call
-> budget failure before any additional provider IO/effect

retry_count > 0 or redirect/replay > 0
-> acquisition budget failure

Gate B GetSandboxOrders attempt
-> forbidden before provider IO

wrong service/method/account receipt
-> Gate A blocked

response changed, reordered or filtered before owner
-> custody mismatch and zero Central effect

restored get_orders visibility
-> no cancel/get-state/replace/post surfaces opened
```

Tests должны доказывать exact owner call count, response-to-owner binding,
canonical Portfolio blocking result и zero-effect boundary; wrapper-local
synthetic status без canonical owner publication не является PASS.

### 25.9 Ownership boundary and implementation surface

Ownership остаётся:

```text
PortfolioRepository / CanonicalPortfolioManager = canonical Portfolio owner
CentralOrderManager = intent/reservation owner
SandboxExecutionAdapter = provider order-mutation boundary
```

Этот rescope не создаёт нового order, Portfolio, reconciliation, recovery или
execution owner.

Future implementation allowlist не расширяется:

```text
current/tools/v3_10_q7a_live_entrypoint.py
current/tests/test_v3_10_q7a_live_entrypoint.py
current/tests/fixtures/v3_10_q7a_live_entrypoint_vectors.json
```

Production Portfolio owner, provider transport, Central, Risk, CL7 и execution
adapter остаются immutable. Если accepted owner semantics нельзя восстановить в
этих трёх paths:

```text
IMPLEMENTATION_SURFACE_INSUFFICIENT -> separate rescope
```

Этот раздел предлагает closure только `PR224-R1-02`. Он не исправляет и не
закрывает:

```text
PR224-R1-01 = CL3_TELEMETRY_IDENTITY_MISMATCH
PR224-R1-03 = CANDLE_SESSION_GAPS_REJECTED
```

Они остаются implementation findings для отдельного unified correction после
independent acceptance exact contract successor.

### 25.10 Independent review oracle and current authority

Contract successor готов к independent review только при одновременном PASS:

```text
exact predecessor / merge-base = 0ebc2e90e8763d1d46e1d013911557ec8a83bae6
changed repository surface = exactly one contract path

GetSandboxOrders Gate A logical/physical count = 1/1
GetSandboxOrders Gate B count = 0
retry / redirect / reacquisition = 0/0/0

existing CanonicalPortfolioManager semantics preserved
account-wide response unfiltered before owner
active/uncertain order remains canonical blocking evidence
provider/read/custody failure is fail closed
no empty-orders fallback after provider failure
no new economic or mutation owner

future implementation allowlist unchanged
PR224-R1-01 and PR224-R1-03 remain open
```

До finding-scoped independent review и explicit acceptance exact successor:

```text
PR224-R1-02 = PROPOSED_CLOSURE
contract rescope = CANDIDATE
implementation correction = BLOCKED
provider READ / POST = NOT AUTHORIZED
runtime mutation = NOT AUTHORIZED
Preparation = NOT AUTHORIZED
START EXPERIMENT = INELIGIBLE
PR #224 mutation = NOT AUTHORIZED
retarget / merge / publication = BLOCKED
```

---

## 26. PR224 R2 — raw broker-order validation and owner-exact sparse candles

### 26.1 Authority, history and one-file surface

Этот раздел является отдельно авторизованным bounded contract-only rescope
для reopened findings `PR224-R1-02` и `PR224-R1-03`. Он имеет precedence только
над конфликтующими требованиями разделов 23–25 о raw order receipt validation,
future implementation surface и candle-grid completeness. Все остальные
принятые ownership, call-budget, freshness, privacy, zero-effect и same-lineage
границы сохраняются.

```text
accepted authority parent / merge-base =
0f317b6ee18d5bb18ed1dd979731047e1933efaf

accepted authority parent tree =
2f12c27beb07310dd595d1b0c897c43c7d67f271

rejected implementation evidence only =
489ab4148352b2de4922609d5851dbc71a764dcd

contract branch =
agent/v3-10-clean-cl8-q7a-live-orders-candles-r2-contract

initial HEAD = merge-base = accepted authority parent
ahead / behind = 0 / 0
worktree = clean
changed paths = 0

contract-only changed path =
docs/project/V3_10_CL8_Q7A_LIVE_PREPARATION_RESCOPE_CONTRACT_RU.md

all other repository paths = IMMUTABLE
```

Historical dispositions are append-only and remain visible:

```text
PR224-R1-01 = CLOSED

PR224-R1-02 = REOPENED
previous contract closure = accepted at 0f317b6e...
new evidence = production get_orders normalization hides malformed wire response

PR224-R1-03 = REOPENED
new evidence = arbitrary monotonic candle gaps were accepted without a defined
               completeness authority after removal of the over-strict grid check
```

The previous `0f317b6e... / 2f12c27b...` contract acceptance remains historical
authority evidence. This section does not erase, rewrite or retroactively widen
that acceptance. The rejected `489ab414...` implementation may be consulted as
evidence but is not ancestry or implementation authority.

Contract work performs no production implementation, provider call, runtime
mutation, backup/restore, Preparation, experiment, GitHub write or remote-ref
change.

### 26.2 `PR224-R1-02` defect and validation owner

The existing production boundary currently collapses a malformed response and
a legitimate empty response:

```text
TBankSandboxClient.get_orders()
-> _post("SandboxService", "GetSandboxOrders", ...)
-> list(response.get("orders", []))

{"orders": []} -> []
{}             -> []
```

After that normalization the Q7A wrapper cannot reconstruct whether the raw
provider response contained the required `orders` member. The wrapper therefore
MUST NOT be required to prove raw information already discarded by production.

Exact raw response-shape validation belongs to the existing provider trust
boundary:

```text
owner = TBankSandboxClient.get_orders
service = SandboxService
method = GetSandboxOrders

raw response must be Mapping/dict
"orders" key must be present
orders value must be list
every orders item must be Mapping/dict
```

Only the following shape is a legitimate empty broker-order result:

```json
{"orders": []}
```

At minimum all following shapes fail closed at
`TBankSandboxClient.get_orders()` and MUST NOT normalize to `[]`:

```text
{}
{"orders": null}
{"orders": {}}
{"orders": "[]"}
{"orders": [null]}
{"orders": [1]}
```

The validation MUST occur against the raw `_post` result before `.get`, default
substitution, iteration, `list(...)`, filtering or any other normalization.
Malformed response raises through the existing provider/trust boundary. No
parallel provider client, order owner or economic classification is introduced.

### 26.3 Validated owner value and fail-closed chain

After successful raw validation, existing `get_orders()` may return `list[dict]`
to the accepted Portfolio owner. The normative chain is:

```text
TBankSandboxClient.get_orders
-> strict raw provider-response validation
-> validated detached/list result
-> CanonicalPortfolioManager.refresh
-> BrokerPortfolioAdapter
-> canonical Portfolio reconciliation
-> PortfolioRepository publication/read-back
-> PortfolioPreflight
```

`CanonicalPortfolioManager`, `BrokerPortfolioAdapter` and PortfolioPreflight
remain unchanged. Account-wide provider order membership remains unfiltered
before the accepted owner. Active or uncertain target and non-target broker
orders retain the accepted canonical blocking semantics.

Any raw-shape failure, provider failure, wrong receipt identity or custody
mismatch gives:

```text
Gate A = BLOCKED
canonical publication for the failed refresh = 0
Central intent = 0
reservation = 0
CL7 attempt marker = 0
provider order POST = 0
```

After an attempted provider read there is no fallback to:

```text
broker_orders = ()
broker_orders = []
```

unless raw validation has proven the exact legitimate empty shape
`{"orders": []}`.

### 26.4 Post-validation evidence and privacy

The Q7A wrapper collects evidence only after successful production validation.
It may bind:

```text
service = SandboxService
method = GetSandboxOrders
account_scope_sha256
attempt_count = 1
retry_count = 0
tracking_id_sha256
validated_order_count
validated_orders_canonical_sha256
```

The wrapper does not reconstruct or claim custody over discarded raw response
shape. Raw Account ID, request ID, order ID, broker order ID and raw payload do
not enter shareable evidence. The validated collection passed to the owner must
remain byte/semantic-equivalent under the frozen canonicalization; mutation,
reordering, filtering or hash/input mismatch blocks Gate A.

The accepted Gate A and Gate B budgets remain:

```text
Gate A GetSandboxOrders logical / physical = 1 / 1
Gate B GetSandboxOrders logical / physical = 0 / 0
retry / redirect / automatic reacquisition = 0 / 0 / 0
```

### 26.5 Mandatory raw-orders closure matrix

Future implementation acceptance MUST prove at the production API boundary:

```text
{"orders": []}
-> accepted empty list result

missing orders key
-> fail before normalization

orders = null / mapping / scalar
-> fail before normalization

orders contains any non-object member
-> fail before normalization
```

It MUST additionally prove through the Q7A integration:

```text
valid empty orders
-> accepted Portfolio refresh may proceed

active broker order
-> canonical blocking
-> Central intent / reservation / POST = 0 / 0 / 0

uncertain broker order
-> canonical blocking
-> Central intent / reservation / POST = 0 / 0 / 0

malformed raw order response
-> no canonical publication from failed refresh
-> no Central effect
```

Exception-only assertions and wrapper-local synthetic status do not constitute
PASS. Tests must bind the physical transport response to production validation,
the validated owner input and the zero-effect boundary.

### 26.6 `PR224-R1-03` owner-exact sparse candle semantics

Q7A introduces no exchange-calendar or session-schedule owner. This bounded
rescope explicitly forbids adding:

```text
GetTradingSchedules
another provider endpoint
hard-coded MOEX session hours
weekend or holiday tables
external calendar package
synthetic reindex or fill logic
```

For this Q7A smoke, completeness means completeness of the accepted
`StrategyCandleLoader`/provider output, not uninterrupted wall-clock
continuity. A valid canonical frame requires:

```text
index type = DatetimeIndex
timestamps = UTC-normalizable, unique and strictly increasing
required columns = open/high/low/close/volume/is_complete
every returned row is_complete = true
OHLC values = finite, positive and structurally valid
volume = finite and non-negative
complete row count >= exact strategy-suite required_bars
every returned row belongs to the exact authorized request range
latest returned complete candle satisfies the frozen freshness/future-skew rule
```

There is no whole-frame invariant:

```text
timestamp[n+1] - timestamp[n] == configured candle interval
```

Accordingly, a strictly increasing frame may contain overnight, weekend,
holiday or session-break gaps and still be valid. Acceptance of sparse returned
timestamps does not assert that every otherwise expected exchange candle was
present.

### 26.7 Finite missing-candle claims

The ambiguous phrase `missing candle rejection` is superseded for Q7A by only
the following finite and implementable conditions:

```text
MISSING_REQUIRED_HISTORY
complete row count < strategy-suite required_bars

MISSING_OR_INVALID_LATEST_EVIDENCE
latest returned complete candle violates frozen freshness or future-skew rules

CAPTURED_OWNER_RESULT_CHANGED
canonical frame SHA-256 changes after exact owner output was captured/bound

REQUEST_BOUNDARY_MISMATCH
any returned row escapes the exact authorized request interval, or captured
request identity differs from accepted policy
```

Without a frozen market-calendar authority, Q7A MUST NOT claim to distinguish,
from elapsed wall-clock time alone:

```text
legitimate market closure or no-candle interval
from
provider-side omission of one otherwise expected intraday candle
```

If future Stable qualification requires that distinction, it requires a
separate `MARKET_CALENDAR / SESSION_AUTHORITY RESCOPE`. It cannot be inferred or
implemented here.

### 26.8 Candle closure matrix and request-gap terminology

Future tests MUST prove:

```text
strictly increasing frame with overnight gap -> accepted
strictly increasing frame with weekend-like gap -> accepted
duplicate timestamp -> rejected
decreasing or out-of-order timestamp -> rejected
incomplete returned row -> rejected
insufficient complete bar count -> rejected
stale latest candle -> rejected
frame mutation after canonical binding -> rejected
row outside authorized request range -> rejected
captured request identity mismatch -> rejected
```

A synthetic `arbitrary timestamp gap = corruption` assertion is forbidden
without separately accepted calendar authority.

Existing phrases `range gap / overlap rejected` and equivalent wording refer
only to:

```text
authorized request-range mismatch
chunk/range construction defect if chunking is ever separately enabled
```

For the current one-physical-request candle policy they do not require returned
candle timestamps to form an uninterrupted wall-clock grid.

### 26.9 `PR224-R1-01` remains closed

This rescope does not reopen or modify `PR224-R1-01`:

```text
PR224-R1-01 = CLOSED_UNCHANGED
physical CL3 telemetry service = SandboxService
physical CL3 telemetry method = GetSandboxOperationsByCursor
```

Future implementation must preserve that correction exactly. If closing
`PR224-R1-02` or `PR224-R1-03` requires reopening it, this bounded rescope is
`BLOCKED`.

### 26.10 Frozen five-path future implementation surface

Only after independent/adversarial exact-head review, closure of all material
contract findings and explicit acceptance of the exact contract successor may
a new implementation branch be created directly from that successor.

The future unified implementation allowlist is exactly:

```text
current/trading_robot/tbank_sandbox.py
current/tests/test_tbank_sandbox.py
current/tools/v3_10_q7a_live_entrypoint.py
current/tests/test_v3_10_q7a_live_entrypoint.py
current/tests/fixtures/v3_10_q7a_live_entrypoint_vectors.json
```

Total: `5 paths`.

The three-file cumulative delta from rejected `489ab414...` may be used only as
implementation evidence/input. New implementation authority starts from the
newly accepted contract successor. `CanonicalPortfolioManager`,
`BrokerPortfolioAdapter`, PortfolioPreflight, Central, Risk, CL7,
SandboxExecutionAdapter and all other paths remain immutable.

The future successor must prove:

```text
PR224-R1-01 = CLOSED / regression preserved
PR224-R1-02 = raw malformed GetSandboxOrders cannot normalize to legitimate empty
PR224-R1-03 = valid sparse/session-separated frames are accepted while defined
               structural corruption remains blocked
```

If strict raw validation cannot be placed in
`TBankSandboxClient.get_orders()` without modifying another production owner,
if `CanonicalPortfolioManager` must change, if a calendar/new endpoint becomes
necessary, or if more than these five paths are required:

```text
BLOCKED -> separate rescope
```

### 26.11 Qualification non-transfer

No Q1, Q4, Q5, CLI smoke or standalone smoke result from any predecessor or
rejected successor transfers to the future implementation. Only after its
finding-scoped review returns `material findings = 0` must the following be
executed against that exact new implementation head:

```text
Q1 = rerun
Q4 = rerun
Q5 = rerun
native CLI smoke = rerun
native standalone smoke = rerun
```

No qualification runs before implementation material findings return to zero.

### 26.12 Finding map and review oracle

The contract custody packet MUST contain
`PR224_R1_02_R1_03_R2_CONTRACT_RESCOPE_MAP.json` with at least:

```text
PR224-R1-01
status = CLOSED_UNCHANGED

PR224-R1-02
status = REOPENED / PROPOSED_CLOSURE
reason = RAW_GETSANDBOXORDERS_VALIDATION_BOUNDARY

PR224-R1-03
status = REOPENED / PROPOSED_CLOSURE
reason = CANDLE_COMPLETENESS_SEMANTICS
```

The map preserves links to the previous `0f317b6e... / 2f12c27b...`
acceptance and must not rewrite that historical record.

This contract successor is ready for independent review only when all are
true:

```text
parent / merge-base = 0f317b6ee18d5bb18ed1dd979731047e1933efaf
cumulative changed repository surface = exactly one contract path
PR224-R1-01 remains CLOSED_UNCHANGED
raw GetSandboxOrders validation owner = TBankSandboxClient.get_orders
CanonicalPortfolioManager change = 0
new calendar or provider endpoint = 0
future implementation surface = exactly five frozen paths
git diff --check = PASS
worktree = clean
raw Git custody objects and exact file identity are exported
```

Until independent/adversarial review and separate explicit acceptance of the
exact successor:

```text
PR224-R1-01 = CLOSED_UNCHANGED
PR224-R1-02 = REOPENED / PROPOSED_CLOSURE
PR224-R1-03 = REOPENED / PROPOSED_CLOSURE

contract rescope = CANDIDATE
implementation = BLOCKED
provider READ / POST = NOT AUTHORIZED
runtime mutation = NOT AUTHORIZED
Preparation = NOT AUTHORIZED
START EXPERIMENT = INELIGIBLE
PR #224 mutation = NOT AUTHORIZED
retarget / merge / release / Stable publication = BLOCKED
```
