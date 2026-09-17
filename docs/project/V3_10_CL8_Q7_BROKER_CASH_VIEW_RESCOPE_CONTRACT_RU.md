# v3.10 CL8 Q7 — Broker cash view rescope contract

Статус этого файла: **CORRECTED CONTRACT CANDIDATE / CLOSURE REVIEW REQUIRED /
NOT ACCEPTED**.

Этот contract не разрешает implementation, provider access, B0 restore,
Preparation Stage, `START EXPERIMENT`, burn-in, Stable acceptance, публикацию или
merge. Любая такая authority выдаётся отдельным решением после exact contract
review и explicit acceptance.

## 1. Exact custody

Contract-freeze branch:

```text
agent/v3-10-clean-cl8-q7-broker-cash-view-rescope-contract-freeze
```

Exact predecessor:

```text
commit = ce85bc060e8602c8177072ca92dbaf70f0d15cf1
tree   = 57c5c5f058851a3c3267e87b8dece8405a6b445e
```

Initial gate:

```text
HEAD = merge-base = predecessor
ahead / behind = 0 / 0
worktree clean
changed paths = 0
```

Contract-only allowlist:

```text
docs/project/V3_10_CL8_Q7_BROKER_CASH_VIEW_RESCOPE_CONTRACT_RU.md
```

Все остальные repository paths immutable. Initial contract candidate был одним
локальным commit поверх exact predecessor; этот document разрешает ещё ровно один
authorized correction commit поверх reviewed candidate `e542aac2...`. Его
acceptance связывает exact successor commit и tree, а не имя branch.

## 2. Evidence binding

Этот rescope вызван только следующим принятым evidence packet:

```text
consumed Stage B preparation SHA-256 =
5ba2a540a14f7ff86850eb690878b13528e6abf0d9d4aa257971adb173e1f151

Stage B terminal disposition SHA-256 =
4974a9fbd1c183adf57488ca53a37099a4891cad778c8ac7e21beb6e6f72a84d

post-cancel disposition SHA-256 =
eeb75a12fba6d5256d4bcb97fa4d42e2c40119c85c597109919dbc6ef0fcf2a0

offline broker-view diagnostic SHA-256 =
6935a4c695fb03614238c87c62e315db28c8c69ac1dce690d34860b05978f38d

bounded semantic disposition SHA-256 =
ed0b1aa98971df27e4f0040dc9096422b97b84a3ff3f9c8df1c00702547ba8f7
```

Terminal runtime custody after local-only cancel:

```text
state = LEGACY_ACTIVE
record_revision = 5
authority_record_sha256 =
95253ab00ab10db5cf3faab85b11204ecb1c520f368fb5f41d12ed927d9616c1
ever_exact_activated = false
post_attempt_count = 0
pending_dispatch_proof_sha256 = null
```

No B0 restore and no new Preparation Stage followed that cancellation.

## 3. Fixed finding set

Contract закрывает только следующий fixed set:

```text
CL8-Q7-BVM-D2-01
CL8-Q7-BVM-D2-02
CL8-Q7-BVM-D2-03
CL8-Q7-BVM-D2-04
```

### CL8-Q7-BVM-D2-01 — unsupported cross-RPC equality

Принятый CL5 oracle требует exact:

```text
GetSandboxPortfolio.totalAmountCurrencies
==
GetSandboxPositions.money[RUB] + GetSandboxPositions.blocked[RUB]
```

Два response читаются последовательными RPC. Provider schema описывает поля, но
не объявляет это равенство и не даёт atomic shared snapshot этих RPC. Реальный
accepted evidence дал:

```text
broker total cash = non-zero
positions money RUB = zero
positions blocked RUB = zero
```

Поэтому повтор того же oracle не создаёт нового доказательства readiness.

### CL8-Q7-BVM-D2-02 — absent и explicit zero conflation

Принятый CL5 adapter превращает отсутствие RUB item и explicit RUB zero item в
одинаковый `Money(RUB, 0)`. Privacy-safe HMACs доказывают итоговые Money values,
но не различают эти provider shapes. Новый proof обязан связывать presence flags.

### CL8-Q7-BVM-D2-03 — conservative replacement required

Для Sandbox cash availability нужен один response, нормативно предназначенный
для available/blocked cash. Этим response становится
`SandboxService/GetSandboxWithdrawLimits`.

### CL8-Q7-BVM-D2-04 — frozen semantics changed

Замена READY oracle меняет frozen CL5 semantics. Она требует rescope contract,
exact implementation review, acceptance и полного повторения затронутой
qualification chain. Runtime retry не является correction.

### Contract review correction binding

Первый independent/adversarial review exact candidate зафиксировал:

```text
reviewed candidate =
e542aac2f009840b2e2cf6c41cc51b23138d7a21

reviewed candidate tree =
5aad39ca14a83a6f04f0b36ccba575ad5373986b

review record SHA-256 =
472f32b68e1123ff49fbdd6035cb92d70bde4617cf45346accbf62720b32c902

fixed correction finding set =
CL8-Q7-BVM-C-R1-01
CL8-Q7-BVM-C-R1-02
CL8-Q7-BVM-C-R1-03
CL8-Q7-BVM-C-R1-04
```

Авторизован ровно один contract-only correction commit поверх этого exact
candidate. Correction изменяет только этот файл. После successor commit
contract correction budget исчерпан; разрешён только finding-scoped closure
review перечисленных четырёх findings. Новый общий review или второй correction
round не открываются.

## 4. Normative provider field meanings

Нормативные upstream references:

```text
https://developer.tbank.ru/invest/services/operations/methods
https://developer.tbank.ru/invest/intro/developer/sandbox
https://developer.tbank.ru/invest/api/sandbox-service-get-sandbox-withdraw-limits
```

Contract использует следующие bounded meanings:

```text
PortfolioResponse.totalAmountCurrencies
    total value of currencies in the portfolio

WithdrawLimitsResponse.money
    available currency amount for withdrawal

WithdrawLimitsResponse.blocked
    blocked currency amount

WithdrawLimitsResponse.blockedGuarantee
    currency amount blocked for futures guarantee
```

`WithdrawLimitsResponse` не содержит `accountId`; account provenance должен быть
связан с request на transport boundary. Upstream documentation не объявляет:

```text
totalAmountCurrencies == money + blocked
totalAmountCurrencies == money + blocked + blockedGuarantee
blocked and blockedGuarantee are disjoint
any inequality between PortfolioResponse and WithdrawLimitsResponse
an atomic snapshot shared by those responses
```

Поэтому ни одно из этих отношений не является READY gate. `money[RUB]` из
`GetSandboxWithdrawLimits` принимается только как provider-declared **available
for withdrawal cap**. Это не exact current cash, не exact unblocked cash и не
доказательство полного BUY capacity. Conservative funding lower bound есть
минимум двух independently accepted caps: reconciled CL4 broker total и
WithdrawLimits available. `blocked` и `blockedGuarantee` сохраняются как typed
diagnostic evidence; они не участвуют в funding arithmetic и не суммируются
между собой.

Документация не превращается в authority для network access. Эти references
фиксируют только semantic design input.

## 5. Rescope mission

Разрешённая будущая implementation делает только следующее:

1. сохраняет CL4 `totalAmountCurrencies` proof и CL4 ledger reconciliation;
2. добавляет read-only `GetSandboxWithdrawLimits` adapter и local-only immutable
   request/response observation envelope;
3. создаёт versioned immutable `BrokerWithdrawLimitsCashProof`;
4. строит conservative Sandbox `broker_withdrawable_cash_lower_bound` как lower
   envelope independently accepted CL4 total и `WithdrawLimitsResponse.money[RUB]`;
5. сохраняет CL5 reservation projection и no-double-subtraction invariant;
6. переводит CL5 snapshot и CL6 context на явно versioned withdraw-limits
   identity без positions aliasing;
7. передаёт новый proof через CL6/CL7 initial и final locked rebuild;
8. сохраняет finite privacy-safe error/evidence;
9. не меняет execution ownership или provider POST semantics.

## 6. Explicit exclusions

Вне rescope остаются:

```text
CL4 opening source replacement
CashLedger schema or ownership
Central reservation ownership
Risk ownership
Portfolio ownership
strategy or signal logic
order sizing
provider POST / cancel / replace
automatic retry
automatic B0 restore
automatic cutover or arm
GUI features
release metadata
workflows
real-account support
Stable acceptance / tag / publication
```

`READY` cash evidence по-прежнему не является execution authority.

## 7. Provider call and account binding

Новый adapter имеет ровно один method:

```text
TBankSandboxClient.get_withdraw_limits(account_id)
    -> WithdrawLimitsTransportObservation

    service = SandboxService
    method  = GetSandboxWithdrawLimits
    request = {"accountId": <exact raw account id>}
```

Он выполняется только внутри уже gated CL7 provider-read sequence. Raw account id
не входит в shareable evidence.

`WithdrawLimitsTransportObservation` — exact frozen local-only DTO, создаваемый
adapter в том же call frame, который отправил request. Он содержит ровно:

```text
raw_request_account_id: exact str, local-only/non-serializable
service: exact "SandboxService"
method: exact "GetSandboxWithdrawLimits"
response: detached exact dict
```

DTO запрещено логировать, помещать в canonical/shareable evidence или создавать
из одного response без request account. Normal builder принимает только exact
DTO; отдельная пара `(response, caller supplied account scope)` запрещена.

`build_broker_withdraw_limits_cash_proof` получает observation и independently
выводит `account_scope_sha256` из `observation.raw_request_account_id` через exact
CL3 codec. Он сравнивает результат с expected authority account scope constant
time. Scope mismatch, wrong service/method, forged/subclass observation или
detached response дают `WITHDRAW_LIMITS_OBSERVATION_INVALID` либо
`WITHDRAW_LIMITS_REQUEST_SCOPE_MISMATCH`.

Это request-provenance binding. Provider response сам не self-attests account,
поскольку upstream schema не возвращает `accountId`; contract не заявляет более
сильного доказательства.

`BrokerWithdrawLimitsCashProof` связывает:

```text
domain = v3.10-cl5-broker-withdraw-limits-cash-proof
version = 1
provider = TBANK
account_scope_sha256
environment = SANDBOX
rpc = tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxWithdrawLimits
as_of
response_complete = true
response_canonical_sha256
observation_identity_sha256
identity_key_id
available_rub
blocked_rub
blocked_guarantee_rub
available_rub_present
blocked_rub_present
blocked_guarantee_rub_present
foreign_cash_present
proof_identity_sha256
```

`observation_identity_sha256` есть HMAC-SHA256 exact CL7 identity key от canonical
ASCII JSON со следующими exact keys:

```text
account_scope_sha256
as_of
domain = v3.10-cl5-withdraw-limits-observation-identity
environment
identity_key_id
provider = TBANK
response_canonical_sha256
response_complete
rpc
version = 1
```

`proof_identity_sha256` есть HMAC-SHA256 тем же key от canonical ASCII JSON всех
proof fields кроме `proof_identity_sha256`, с domain
`v3.10-cl5-broker-withdraw-limits-cash-proof-identity`. Request account,
response identity и parsed values поэтому связаны одной accepted identity key.

## 8. Exact bounded response schema

Response проходит существующие bounded snapshot/canonicalization limits. Required
top-level keys:

```text
money
blocked
blockedGuarantee
```

Каждый key имеет exact `list[MoneyValue]`. Каждый MoneyValue имеет exact keys:

```text
currency
units
nano
```

`currency` — exact `str`, `units` — exact decimal string, `nano` — exact `int`.
Каждый список может содержать не более одного item для одной currency. Duplicate
RUB, duplicate foreign currency, bool-as-int, subclass containers, aliases и
unknown MoneyValue keys отклоняются.

Presence semantics field-specific:

```text
money:
    explicit RUB item -> parse exact value, present = true
    missing RUB -> value 0, present = false

blocked:
    explicit RUB item -> parse exact value, present = true
    missing RUB -> value 0, present = false

blockedGuarantee:
    explicit RUB item -> parse exact value, present = true
    missing RUB -> value 0, present = false
```

Presence flags участвуют в proof identity. Missing и explicit zero никогда не
имеют одинаковые proof bytes.

Missing `money[RUB]` не интерпретируется через `broker_total_cash`: cross-RPC
relation запрещена. Оно создаёт exact zero lower bound с `present = false`.
Такой proof может быть READY только с `free_investable_cash = 0`; любой BUY с
positive required cash затем fail closed по обычному sizing/authorization gate.

Любой non-zero foreign-currency item устанавливает `foreign_cash_present = true`
и блокирует READY. Explicit foreign zero также сохраняется в response identity,
но не даёт RUB funding authority.

## 9. Money and arithmetic rules

Все значения проходят exact CL3 `MoneyValue -> CL1 Money` codec. Никаких float,
Decimal quantization, locale parsing или silent currency conversion.

Required currency:

```text
RUB
```

Required non-negativity:

```text
broker_total_cash >= 0
withdraw_available_rub >= 0
blocked_rub >= 0
blocked_guarantee_rub >= 0
queued_local_reservations >= 0
```

Exact scale-9 checked arithmetic:

```text
broker_withdrawable_cash_lower_bound =
    min(broker_total_cash, withdraw_available_rub)

free_investable_cash =
    broker_withdrawable_cash_lower_bound
    - QUEUED_local_reservations
```

`min` — local conservative intersection двух independently valid caps; он не
утверждает provider equality, inclusion, ordering или shared snapshot и не
создаёт mismatch reason. Никакая другая equality, inequality, subtraction или
max между CL4 Portfolio proof и WithdrawLimits proof не допускается. `blocked_rub`
и `blocked_guarantee_rub` в funding formula не участвуют. Provider blocked
amounts не вычитаются из `withdraw_available_rub`, поскольку provider уже
объявил это значение доступным для вывода. Local `QUEUED` reservations ещё не
достигли provider и вычитаются ровно один раз.

Термин `broker_unblocked_cash` удаляется из version-2 snapshot: он утверждал бы
недоказанную exact semantics. Новый field называется
`broker_withdrawable_cash_lower_bound`.

Если итоговый free cash отрицателен:

```text
INSUFFICIENT_AFTER_RESERVATIONS / BLOCKED
```

## 10. GetSandboxPositions disposition

`GetSandboxPositions` сохраняет роль inventory/position evidence. Его `money` и
`blocked` могут сохраняться как privacy-safe diagnostic identities, но:

```text
positions.money + positions.blocked == totalAmountCurrencies
```

больше не является READY gate и не участвует в funding arithmetic.

Нельзя использовать это изменение для игнорирования securities/futures/options,
account mismatch, loading state или других existing Portfolio/Risk blockers.

## 11. Freshness and coherence

Новый proof наследует accepted absolute deadline, timeout, bounded retry и
controlled-clock rules. Automatic experiment retry не открывается.

До READY обязательны:

```text
CL4 reconciliation READY/MATCHED
withdraw-limits proof complete and fresh
withdraw-limits observation request provenance valid
exact account scope match
Central projection fresh
ledger revision/head exact
Portfolio lease exact
Risk inputs exact
cross-proof skew within existing CL5 bound
foreign_cash_present = false
ambiguous Central reservations = 0
```

Provider responses не считаются atomic. Freshness/skew controls ограничивают
временное окно, но не создают между ними provider relation. CL4 reconciliation
и WithdrawLimits proof должны быть independently valid; их values используются
только в local lower-envelope `min`, никогда как equality или mismatch oracle.

## 12. Finite status/reason set

Разрешённые новые finite reasons:

```text
WITHDRAW_LIMITS_OBSERVATION_INVALID
WITHDRAW_LIMITS_REQUEST_SCOPE_MISMATCH
WITHDRAW_LIMITS_RESPONSE_INVALID
WITHDRAW_LIMITS_INCOMPLETE
WITHDRAW_LIMITS_MONEY_INVALID
WITHDRAW_LIMITS_FOREIGN_CASH_PRESENT
```

Existing reasons `CL4_NOT_READY`, `BROKER_PROOF_STALE`,
`CENTRAL_PROJECTION_STALE`, `MIXED_EVIDENCE_SNAPSHOT`,
`CENTRAL_PROVIDER_OVERLAP_UNKNOWN` и `INSUFFICIENT_AFTER_RESERVATIONS` остаются.

Exception text, raw response, raw account id, token и Money numeric values не
входят в shareable CLI evidence. Допустимы только finite reason, response/proof
hashes, presence flags, zero/equality flags и tracking-id hash.

## 13. Canonical schema migration

`BrokerPositionsCashProof` version 1 и `CashAvailabilitySnapshot` version 1
остаются historical accepted evidence, но не могут создать новый CL7 READY
context после этого rescope. Runtime rebuild принимает только exact
`BrokerWithdrawLimitsCashProof` version 1 и `CashAvailabilitySnapshot` version 2.
Cross-version equality, aliasing и silent upgrade запрещены.

Version-2 `CashAvailabilitySnapshot` сохраняет domain
`v3.10-cl5-cash-availability`, получает `version = 2` и заменяет ровно следующие
version-1 fields:

```text
REMOVE broker_positions_as_of
REMOVE broker_positions_cash_proof_sha256
REMOVE broker_blocked_cash
REMOVE broker_unblocked_cash

ADD broker_withdraw_limits_as_of
ADD broker_withdraw_limits_cash_proof_sha256
ADD broker_withdraw_blocked_cash
ADD broker_withdraw_blocked_guarantee_cash
ADD broker_withdrawable_cash_lower_bound
```

Остальные canonical keys version 1 сохраняются byte-for-byte. Для exact valid
withdraw proof все три new Money fields присутствуют даже при zero/absent source
items; presence flags остаются внутри proof identity. Для READY snapshot
`broker_withdrawable_cash_lower_bound` всегда exact Money, а
`free_investable_cash` exact Money и равен lower bound минус queued reservation.
Для invalid/incomplete proof snapshot не создаётся. Для stale/foreign/ambiguous
или insufficient evidence lower bound сохраняется, но `free_investable_cash =
null`.

`PortfolioRiskCashContext` сохраняет domain
`v3.10-cl6-portfolio-risk-cash-context`, получает `version = 2` и заменяет:

```text
REMOVE broker_positions_as_of
ADD broker_withdraw_limits_as_of
```

CL6 exact-type gate принимает `BrokerWithdrawLimitsCashProof`, повторно строит
version-2 CL5 snapshot из него и сравнивает canonical bytes. Context version 1
остаётся historical evidence и не принимается новым CL7 READY rebuild.

Canonical JSON для response, observation identity, proof identity, proof и
snapshot использует accepted CL1 rules: UTF-8, sorted keys, separators `,`/`:`,
`ensure_ascii = true`, no insignificant whitespace, exact strings for revisions
и exact nested CL1 Money. Unknown/missing keys, bool-as-int, subclass containers,
duplicate keys и non-canonical bytes fail closed.

## 14. Frozen implementation allowlist

После explicit acceptance exact contract commit/tree может быть создана новая
isolated implementation branch только от accepted contract head.

Future implementation allowlist содержит ровно одиннадцать paths:

```text
current/trading_robot/tbank_sandbox.py
current/trading_robot/cash_availability.py
current/trading_robot/runtime_cash_authority.py
current/trading_robot/reporting_risk_cash_context.py
current/trading_robot/sandbox_execution_adapter.py
current/tools/v3_10_runtime_cash_cutover.py
current/tests/test_v3_10_cash_availability.py
current/tests/test_v3_10_q7_preparation_runtime.py
current/tests/test_v3_10_reporting_risk_cash_context.py
current/tests/test_v3_10_runtime_cash_cutover_recovery.py
current/tests/fixtures/v3_10_cash_availability_vectors.json
```

Двенадцатый path означает `SCOPE_EXPANSION_REQUIRED -> RESCOPE`.

`sandbox_execution_adapter.py` включён только для замены final locked cash read
`GetSandboxPositions -> GetSandboxWithdrawLimits` и передачи exact observation в
существующий rebuild. Provider POST/cancel/replace, retry ownership, Central/Risk
locking и order semantics в этом path immutable.

## 15. Required adversarial oracle set

Implementation review обязан покрыть минимум:

```text
Q7-BVM-01 exact successful 100/80/20/0/30 vector -> free 50
Q7-BVM-02 provider blocked is not double-subtracted
Q7-BVM-03 blocked-guarantee is identity-bound and excluded from arithmetic
Q7-BVM-04 available above CL4 total is capped by min without mismatch
Q7-BVM-05 blocked and blocked-guarantee overlap is never inferred
Q7-BVM-06 missing available RUB yields absent-zero lower bound
Q7-BVM-07 missing available RUB differs from explicit zero
Q7-BVM-08 missing blocked RUB is bound as absent-zero
Q7-BVM-09 missing guarantee RUB is bound as absent-zero
Q7-BVM-10 duplicate RUB rejects
Q7-BVM-11 foreign non-zero cash blocks
Q7-BVM-12 bool/subclass/custom-equality adversarial values reject
Q7-BVM-13 account substitution rejects
Q7-BVM-14 response/proof tamper rejects
Q7-BVM-15 stale proof blocks
Q7-BVM-16 cross-proof skew blocks
Q7-BVM-17 ambiguous Central reservation remains manual-review
Q7-BVM-18 queued reservation subtracts exactly once
Q7-BVM-19 no provider POST path becomes reachable
Q7-BVM-20 CLI evidence contains no raw values or identifiers
Q7-BVM-21 observation wrong service/method rejects
Q7-BVM-22 detached response/account recombination rejects
Q7-BVM-23 proof/snapshot/context version-1 alias rejects
Q7-BVM-24 CL6 canonical rebuild accepts exact v2 only
Q7-BVM-25 final locked revalidation uses WithdrawLimits and performs one read
```

Contract-owned arithmetic vectors:

```text
Vector A:
total=100, available=80, blocked=20, guarantee=0, queued=30
withdrawable_lower_bound=min(100,80)=80, free=50, READY

Vector B:
total=100, available=70, blocked=20, guarantee=0, queued=30
withdrawable_lower_bound=min(100,70)=70, free=40, READY

Vector C:
total=100, available=110, blocked=20, guarantee=0, queued=0
withdrawable_lower_bound=min(100,110)=100, free=100, READY
note=no provider mismatch or blocked/guarantee arithmetic

Vector D:
total=100, available=70, blocked=20, guarantee=10, queued=10
withdrawable_lower_bound=min(100,70)=70, free=60, READY

Vector E:
total=100, available=absent, blocked=absent, guarantee=absent, queued=0
withdrawable_lower_bound=min(100,0)=0, available_present=false, free=0, READY
```

Contract-owned identity KAT uses:

```text
identity key bytes = 00 01 02 ... 1f
identity_key_id = CL5_TEST_KEY_V1
raw request account = sandbox-account-0001
account_scope_sha256 =
15ef4629fb500c526720663db4c3335cff5ede994c5e23036f4457e71a9101a3

all proof/snapshot timestamps =
2026-09-11T10:00:00.000000000Z

exact response canonical ASCII =
{"blocked":[{"currency":"RUB","nano":0,"units":"20"}],"blockedGuarantee":[],"money":[{"currency":"RUB","nano":0,"units":"80"}]}

broker_total_cash = 100 RUB
available = 80 RUB, present = true
blocked = 20 RUB, present = true
blockedGuarantee = 0 RUB, present = false
foreign_cash_present = false
queued reservation = 30 RUB
ambiguous reservation = 0 RUB
total local reservation = 30 RUB
free_investable_cash = 50 RUB
overlap_disposition = QUEUED_DISJOINT
status / availability_reason = READY / READY

reconciliation_sha256 = 11 repeated 32 times
cl4_adoption_candidate_sha256 = 22 repeated 32 times
ledger_export_sha256 = 33 repeated 32 times
ledger_head_sha256 = 44 repeated 32 times
ledger_revision = 5
central_order_revision = 7
central_reservation_projection_hash = 55 repeated 32 times
central_reservation_projection_sha256 =
24505cbf486c30f5c85a2bb456db3587dc3d97f8ce9f5afc96f9b1405f511048
```

Exact KAT hashes are frozen in the next subsection and must be reproduced by the
fixture; any mismatch is `CONTRACT_KAT_MISMATCH / BLOCKED`.

### 15.1 Contract-owned canonical identity KAT

```text
response_canonical_sha256 =
2e36fc5bc9beaa9fc3eeac990897f6ab894801c09b0a14b65ad1c9d39d919319

observation_identity_sha256 =
b21b64af91a4976216d45978a73cbbafb2033c782e601e191905d1cd0db1f24c

proof_identity_sha256 =
de2b207c43a7dfea9acb9f7a792a24e8375ab8072cfad4fa8f04e63f536d40e5

proof_canonical_sha256 =
f5e03b70096a4e0f9756098d7a1a4e7550f6976aa5017d92aa25ec0f8f812f5b

ready_snapshot_v2_sha256 =
2adb09081ff97c8441f3a5444b11b2dd2afbeeacfbbed1cdabf467e85d540190
```

## 16. Review and correction governance

Sequence:

```text
contract-only commit
-> exact commit/tree custody check
-> one independent/adversarial exact-range contract review
-> PASS / APPROVE / material findings 0
   or one fixed finding set
-> any correction requires separate authorization
-> finding-scoped closure only
-> explicit exact commit/tree acceptance
```

Authorized contract correction batch `CL8-Q7-BVM-C-R1-01..04` использован этим
successor. Contract correction budget после commit равен `EXHAUSTED`. Разрешён
только finding-scoped closure exact successor; surviving material finding ведёт
к `RESCOPE / ABORT / DEFER`.

## 17. Post-acceptance gate sequence

Даже после contract acceptance обязательна последовательность:

```text
isolated implementation branch from exact accepted contract head
-> eleven-path implementation only
-> exact-range independent/adversarial review
-> explicit implementation acceptance
-> full affected Q1 rerun and acceptance
-> deterministic Q4/Q5 rerun and acceptance
-> offline Stage A and new verified B0
-> Stage A/B0 independent review and acceptance
-> verified restore exact accepted B0 to authority revision 0
-> new exact Stage B Preparation SHA-256
-> new explicit START EXPERIMENT command
```

Ни один предыдущий Preparation SHA не reusable.

## 18. Authority summary

До explicit acceptance этого contract candidate:

```text
CONTRACT = CANDIDATE / NOT ACCEPTED
IMPLEMENTATION = BLOCKED
B0 RESTORE = NOT AUTHORIZED
NEW PREPARATION = NOT AUTHORIZED
PROVIDER READ = NOT AUTHORIZED
PROVIDER MUTATION = NOT AUTHORIZED
CL7 ACTIVATE / ARM = NOT AUTHORIZED
Q7 BURN-IN = NOT AUTHORIZED
STABLE ACCEPTANCE = BLOCKED
TAG / PUBLICATION = NOT AUTHORIZED
REAL-ACCOUNT EXECUTION = FORBIDDEN
```
