# v3.10 CL8 Q7 — Broker cash view rescope contract

Статус этого файла: **CONTRACT CANDIDATE / REVIEW REQUIRED / NOT ACCEPTED**.

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

Все остальные repository paths immutable. Contract candidate должен быть одним
локальным commit поверх exact predecessor. Его acceptance связывает exact commit
и tree, а не имя branch.

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

Документация не превращается в authority для network access. Эти references
фиксируют только semantic design input.

## 5. Rescope mission

Разрешённая будущая implementation делает только следующее:

1. сохраняет CL4 `totalAmountCurrencies` proof и CL4 ledger reconciliation;
2. добавляет read-only `GetSandboxWithdrawLimits` adapter;
3. создаёт immutable `BrokerWithdrawLimitsCashProof`;
4. строит conservative Sandbox `broker_unblocked_cash`;
5. сохраняет CL5 reservation projection и no-double-subtraction invariant;
6. передаёт новый proof через существующий CL6/CL7 rebuild;
7. сохраняет finite privacy-safe error/evidence;
8. не меняет execution ownership или provider POST path.

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
    service = SandboxService
    method  = GetSandboxWithdrawLimits
    request = {"accountId": <exact raw account id>}
```

Он выполняется только внутри уже gated CL7 provider-read sequence. Raw account id
не входит в shareable evidence.

`BrokerWithdrawLimitsCashProof` связывает:

```text
account_scope_sha256
environment = SANDBOX
rpc = tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxWithdrawLimits
as_of
response_complete = true
response_canonical_sha256
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

Account scope выводится из того же raw account id, который передан adapter call,
через принятый CL3 account-scope codec. Подмена request account между read и proof
construction должна fail closed.

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
    missing RUB and broker_total_cash != 0 -> AVAILABLE_RUB_MISSING / BLOCKED
    missing RUB and broker_total_cash == 0 -> value 0, present = false

blocked:
    explicit RUB item -> parse exact value, present = true
    missing RUB -> value 0, present = false

blockedGuarantee:
    explicit RUB item -> parse exact value, present = true
    missing RUB -> value 0, present = false
```

Presence flags участвуют в proof identity. Missing и explicit zero никогда не
имеют одинаковые proof bytes.

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
portfolio_bound =
    broker_total_cash
    - blocked_rub
    - blocked_guarantee_rub
```

Если `portfolio_bound < 0`:

```text
PORTFOLIO_BOUND_UNDERFLOW / BLOCKED
```

Если:

```text
withdraw_available_rub > portfolio_bound
```

то результат:

```text
PROVIDER_AVAILABLE_EXCEEDS_PORTFOLIO_BOUND / BLOCKED
```

Иначе:

```text
broker_unblocked_cash = withdraw_available_rub

free_investable_cash =
    broker_unblocked_cash
    - QUEUED_local_reservations
```

Blocked provider amounts **не вычитаются повторно** из
`withdraw_available_rub`. Они используются только для независимого conservative
portfolio bound.

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
exact account scope match
Central projection fresh
ledger revision/head exact
Portfolio lease exact
Risk inputs exact
cross-proof skew within existing CL5 bound
foreign_cash_present = false
ambiguous Central reservations = 0
provider_available <= portfolio_bound
```

Provider responses не считаются atomic. Freshness/skew controls ограничивают
временное окно, а conservative inequality не требует недокументированного exact
cross-RPC equality.

## 12. Finite status/reason set

Разрешённые новые finite reasons:

```text
WITHDRAW_LIMITS_RESPONSE_INVALID
WITHDRAW_LIMITS_INCOMPLETE
AVAILABLE_RUB_MISSING
WITHDRAW_LIMITS_MONEY_INVALID
WITHDRAW_LIMITS_FOREIGN_CASH_PRESENT
PORTFOLIO_BOUND_UNDERFLOW
PROVIDER_AVAILABLE_EXCEEDS_PORTFOLIO_BOUND
```

Existing reasons `CL4_NOT_READY`, `BROKER_PROOF_STALE`,
`CENTRAL_PROJECTION_STALE`, `MIXED_EVIDENCE_SNAPSHOT`,
`CENTRAL_PROVIDER_OVERLAP_UNKNOWN` и `INSUFFICIENT_AFTER_RESERVATIONS` остаются.

Exception text, raw response, raw account id, token и Money numeric values не
входят в shareable CLI evidence. Допустимы только finite reason, response/proof
hashes, presence flags, zero/equality flags и tracking-id hash.

## 13. Frozen implementation allowlist

После explicit acceptance exact contract commit/tree может быть создана новая
isolated implementation branch только от accepted contract head.

Future implementation allowlist содержит ровно семь paths:

```text
current/trading_robot/tbank_sandbox.py
current/trading_robot/cash_availability.py
current/trading_robot/runtime_cash_authority.py
current/tools/v3_10_runtime_cash_cutover.py
current/tests/test_v3_10_cash_availability.py
current/tests/test_v3_10_q7_preparation_runtime.py
current/tests/fixtures/v3_10_cash_availability_vectors.json
```

Восьмой path означает `SCOPE_EXPANSION_REQUIRED -> RESCOPE`.

## 14. Required adversarial oracle set

Implementation review обязан покрыть минимум:

```text
Q7-BVM-01 exact successful 100/80/20/0/30 vector -> free 50
Q7-BVM-02 provider blocked is not double-subtracted
Q7-BVM-03 blocked-guarantee participates in portfolio bound
Q7-BVM-04 available above portfolio bound blocks
Q7-BVM-05 portfolio bound underflow blocks
Q7-BVM-06 missing available RUB with non-zero total blocks
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
```

Contract-owned arithmetic vectors:

```text
Vector A:
total=100, available=80, blocked=20, guarantee=0, queued=30
portfolio_bound=80, broker_unblocked=80, free=50, READY

Vector B:
total=100, available=70, blocked=20, guarantee=0, queued=30
portfolio_bound=80, broker_unblocked=70, free=40, READY

Vector C:
total=100, available=90, blocked=20, guarantee=0, queued=0
portfolio_bound=80, PROVIDER_AVAILABLE_EXCEEDS_PORTFOLIO_BOUND / BLOCKED

Vector D:
total=100, available=70, blocked=20, guarantee=10, queued=10
portfolio_bound=70, broker_unblocked=70, free=60, READY
```

## 15. Review and correction governance

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

Contract correction budget сейчас равен `0 authorized`. Этот файл сам не
авторизует correction batch.

## 16. Post-acceptance gate sequence

Даже после contract acceptance обязательна последовательность:

```text
isolated implementation branch from exact accepted contract head
-> seven-path implementation only
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

## 17. Authority summary

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
