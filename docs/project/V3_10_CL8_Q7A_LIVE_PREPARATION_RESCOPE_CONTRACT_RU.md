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
2. два взаимно исключающих режима этого entrypoint;
3. отдельный bounded provider-read freshness stage;
4. новый economic-smoke Preparation, связанный с принятым terminal packet
   freshness stage;
5. lock-held pre-POST revalidation принятого CL1–CL7 proof graph;
6. immutable privacy-safe evidence для обеих стадий;
7. fail-closed enforcement одного provider POST attempt.

Он не создаёт нового владельца cash, Portfolio, reservation, Risk, order,
currency, freshness, reconciliation или execution.

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
automatic Stage 1 or Stage 2 execution
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

У него ровно два mutually exclusive modes:

```text
PREFLIGHT_READ
ECONOMIC_SMOKE
```

Любой третий live mode, alias, hidden environment switch, GUI shortcut,
direct helper invocation или alternate provider mutation path запрещён.

Оба режима обязаны:

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

## 7. Stage 1 — bounded provider-read freshness gate

```text
experiment_id =
CL8-Q7-E2E-SMOKE-PREFLIGHT-READ-V1

required operator command =
START EXPERIMENT — CL8-Q7-E2E-SMOKE-PREFLIGHT-READ-V1 — preparation <accepted_exact_sha256>
```

До этой exact команды Stage 1 не выполняется. Preparation должна быть отдельно
сформирована, проверена read-only и явно принята для exact SHA-256.

Stage 1 разрешает только bounded authenticated provider READ для exact
Sandbox account и exact configured set через существующих owners. Допустимый
pipeline:

```text
accepted CL3 bounded operation sync
-> accepted CL2 append/read-back
-> canonical Portfolio refresh owner
-> accepted CL4 current-cash/reconciliation rebuild
-> accepted CL5 CashAvailability rebuild
-> accepted CL6 PortfolioRiskCashContext rebuild
-> CL7 authority read-sync without dispatch attempt marker
```

Если accepted predecessor owner требует `GetOperationsByCursor`,
`GetSandboxPortfolio`, `GetSandboxPositions` или другой уже контрактно
обязательный read для этого pipeline, entrypoint может вызвать его только
через этот owner, только для exact scope и только в пределах его accepted
deadline/pagination/retry semantics. Rescope не добавляет endpoint и не
ослабляет bounded-read limits.

Stage 1 категорически запрещает:

```text
PostSandboxOrder
любой provider mutation
Central intent/reservation/order creation
strategy proposal или economic candidate creation
CL7 dispatch attempt marker
pending dispatch proof
Risk execution registration
automatic transition в ECONOMIC_SMOKE
```

`PREFLIGHT_READ` обязан иметь structurally unreachable POST path. Проверка
только runtime flag после выполнения недостаточна.

---

## 8. Stage 1 Preparation binding

Stage 1 Preparation canonical bytes связывают минимум:

```text
domain/version
experiment_id
candidate commit/tree
accepted contract commit/tree
accepted implementation commit/tree
runtime manifest SHA-256
account_scope_sha256
identity_key_id
environment = SANDBOX
configured_set_sha256
target instrument identity hash
RiskPolicy hash and mode = ENFORCED
RiskState guard hash/revision
CL7 authority record SHA/revision/state
expected post_attempt_count = 0
expected pending proof = absent
Central expected quiescent revision/hash
ledger expected revision/head
Portfolio expected revision/checksum
provider-read absolute deadline
per-owner accepted retry/deadline limits
evidence root identity
B0/B1 or later accepted backup binding
recovery/disarm procedure identity
stop conditions
created_at_utc
preparation_sha256
```

Preparation не содержит token, raw Account ID, raw instrument/provider ID,
raw order/intent ID или private absolute path в shareable части.

Любой drift требует новой Preparation и нового review/acceptance. Начатая
Preparation single-use независимо от PASS/BLOCKED/FAIL.

---

## 9. Stage 1 terminal packet

Stage 1 создаёт immutable terminal packet и manifest. `PASS` допустим только
если packet связывает:

```text
exact Preparation SHA-256
exact candidate/runtime/configured-set identities
provider read receipt set hash
per-read service/method/status/error_class/transient/attempt_count
privacy-safe tracking_id_sha256 where present
sanitized response-content hashes
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
freshness_deadline_utc
terminal status/reason
terminal packet SHA-256
```

`freshness_deadline_utc` равен minimum всех native accepted dependency
deadlines. Нельзя продлевать freshness переписыванием timestamp, copying
packet или повторной локальной сериализацией.

Stage 1 `PASS` не даёт POST authority. `BLOCKED`, `FAIL` или `INDETERMINATE`
terminal packet нельзя использовать в Stage 2.

---

## 10. Separate Stage 1 review and acceptance

Stage 1 terminal packet получает отдельный read-only review. Acceptance
связывается с exact terminal packet SHA-256 и manifest identities.

Review проверяет минимум:

```text
Preparation exact-match
provider-read allowlist and call counts
zero provider mutation
zero Central intent/reservation/order
zero CL7 attempt marker
account/configured-set/target scope
all native freshness and completeness gates
canonical JSON and create-once custody
privacy scan
source/runtime identity
```

Terminal acceptance не переносится на другой packet или более поздний
freshness interval.

---

## 11. Stage 2 — exact controlled economic smoke

```text
experiment_id =
CL8-Q7-E2E-SMOKE-V1

required operator command =
START EXPERIMENT — CL8-Q7-E2E-SMOKE-V1 — preparation <accepted_exact_sha256>
```

Stage 2 требует новую, отдельно reviewed и accepted exact Preparation. Stage 1
команда не разрешает Stage 2, а Stage 2 не запускается автоматически после
Stage 1.

Stage 2 сохраняет принятые Q7A bounds:

```text
exact account-wide economic target count <= 1
requested lots <= 1 and bounded by accepted metadata/Risk
physical PostSandboxOrder attempts <= 1
automatic transport retries = 0
automatic application retries = 0
redirects = 0
legacy/GUI/diagnostic POST paths = 0
```

---

## 12. Stage 2 Preparation binding

Stage 2 Preparation включает все применимые Stage 1 bindings и дополнительно:

```text
accepted Stage 1 terminal packet SHA-256
accepted Stage 1 review/acceptance identity
Stage 1 provider read receipt set hash
Stage 1 CL6 context SHA
Stage 1 freshness_deadline_utc
immutable Q7AControlRecord SHA
proposal marker/admission binding contract version
target runtime identity
target metadata SHA and lot-size evidence SHA
current quote source/as_of hash
max_provider_post_attempts = 1
automatic retries = 0
expected CL7 post_attempt_count = 0
expected pending proof = absent
Central quiescence identity
evidence root identity
backup/recovery binding
absolute experiment deadline
created_at_utc
preparation_sha256
```

Preparation generation обязана fail closed, если Stage 1 terminal не принят,
уже expired, относится к другому account/configured set/runtime/head или не
доказывает zero mutation/quiescence.

---

## 13. Two-stage freshness rule

Stage 2 не может оживить stale Stage 1 evidence.

Перед economic proposal entrypoint проверяет:

```text
now <= Stage 1 freshness_deadline_utc
all Stage 1 revision/hash bindings still equal live read-back
CL7 = EXACT_CASH_ARMED
post_attempt_count = 0
pending proof = absent
Central remains quiescent
```

Затем existing CL7 dispatch owner выполняет свой accepted final pre-POST
rebuild и lock order. Разрешённые в этом rebuild provider reads являются
частью отдельно авторизованного Stage 2 experiment, а не самостоятельным
diagnostic path. Они используют только accepted CL3/CL4 owners и те же exact
account/configured-set bounds.

Внутри final lock-held gate должны быть заново подтверждены:

```text
CL3/CL2 operation completeness and ledger head
canonical Portfolio revision/checksum
CL4 coherent reconciliation
CL5 READY availability
CL6 READY_FOR_LOCKED_REVALIDATION context
RiskPolicy/RiskState guard
Portfolio Risk authorization
Central revision and one-intent budget
Q7A control/proposal/admission identity
CL7 authority revision and attempt budget
all accepted CL7 temporal bounds, including final <=10s gate
```

Если Stage 1 evidence или любой final dependency expired/drifted:

```text
STOP
zero attempt marker
zero provider POST
new Stage 1 Preparation
new Stage 1 review/acceptance
new Stage 1 START EXPERIMENT
new Stage 2 Preparation/review/acceptance
new Stage 2 START EXPERIMENT
```

Никакого refresh-in-place под старой Preparation нет.

---

## 14. Proposal-to-owner and dispatch binding

Stage 2 использует accepted Q7A control/proposal bridge. Exact live entrypoint
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
- новый Stage 1/Stage 2 запуск запрещён до принятого recovery disposition;
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
STAGE1_POST_PATH_REACHABLE
STAGE1_MUTATION_OBSERVED
CL3_SYNC_BLOCKED
PORTFOLIO_REFRESH_BLOCKED
CL4_RECONCILIATION_BLOCKED
CL5_AVAILABILITY_NOT_READY
CL6_CONTEXT_NOT_READY
STAGE1_TERMINAL_NOT_ACCEPTED
STAGE1_TERMINAL_SUBSTITUTED
STAGE1_FRESHNESS_EXPIRED
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
PREFLIGHT_READ cannot reach POST transport
PREFLIGHT_READ leaves Central intent/reservation/order count zero
PREFLIGHT_READ cannot create proposal/candidate/attempt marker
wrong account/configured set/target is rejected before provider call
Preparation SHA substitution is rejected
provider receipt/content substitution is rejected
partial pagination/incomplete watermark is rejected
Stage 1 terminal overwrite/reuse is rejected
Stage 1 freshness timestamp restamping is rejected
Stage 2 without accepted Stage 1 terminal is rejected
Stage 2 with BLOCKED/FAIL/INDETERMINATE Stage 1 is rejected
expired Stage 1 deadline is rejected before proposal
live revision/hash drift is rejected before proposal
final lock-held staleness/drift is rejected before attempt marker
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
обеих Preparation schemas, Stage 1 terminal binding, expiry, account/set
substitution, retry budget и one-POST budget.

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
freeze Stage 1 runtime and Preparation
-> read-only review
-> explicit acceptance exact Preparation SHA
-> exact Stage 1 START EXPERIMENT
-> immutable terminal packet
-> read-only review
-> explicit acceptance exact terminal SHA
-> freeze Stage 2 Preparation bound to that terminal
-> read-only review
-> explicit acceptance exact Stage 2 Preparation SHA
-> exact Stage 2 START EXPERIMENT
-> terminal audit/recovery
-> independent terminal review
-> separate Q7A acceptance decision
```

Любой provider call вне соответствующей exact `START EXPERIMENT` запрещён.
Каждая команда single-use и разрешает только указанный experiment/preparation.

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

Если review фиксирует finite material finding set, возможен ровно один
отдельно авторизованный bounded contract-only correction batch в том же
однофайловом allowlist. После него проводится только finding-scoped closure
review. Второй correction batch не подразумевается:

```text
surviving material blocker -> RESCOPE / ABORT / DEFER
```

---

## 22. Current authority boundary

До explicit acceptance этого exact contract:

```text
contract review = authorized
contract correction = not yet authorized
implementation branch = blocked
implementation = blocked
provider READ = not authorized
provider POST = not authorized
runtime mutation = not authorized
Stage 1 START EXPERIMENT = ineligible
Stage 2 START EXPERIMENT = ineligible
Q7A acceptance = unchanged
Q7B = not authorized
PR Ready / merge / Stable acceptance / publication = not authorized
real-account execution = forbidden
```

Contract acceptance разрешает только создание bounded implementation branch.
Implementation acceptance разрешает только последующую qualification и
Preparation work. Ни один из этих gates не заменяет exact experiment command.
