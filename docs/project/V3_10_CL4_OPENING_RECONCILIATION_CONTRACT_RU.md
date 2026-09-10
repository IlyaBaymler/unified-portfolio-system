# V3.10 CL4 — Opening proof + shadow reconciliation: bounded contract freeze

Статус: `CONTRACT CANDIDATE / IMPLEMENTATION BLOCKED / NO RUNTIME AUTHORITY`.

Этот документ является единственным разрешённым изменением CL4 contract-freeze.
Он не является acceptance, implementation, publication, migration, runtime
adoption или разрешением provider/private-account access.

## 1. Exact predecessor и lineage

CL4 начинается только от принятого и интегрированного CL3:

```text
branch = agent/v3-10-clean-cl4-contract-freeze
predecessor commit = 4340c5d517dcece4f7db20b7cfc21e602c3efddc
predecessor tree = bd40cd3b7237434ea2754f6f7c6eaaed2c02637f
HEAD at branch creation = predecessor commit
merge-base(HEAD, predecessor) = predecessor commit
ahead = 0
behind = 0
worktree = clean
```

`main`, исторические ветки и прежний Issue #52 не являются integration authority
для CL4. Исторический #52 используется только как negative/lessons evidence;
его schema migration, PortfolioRepository coupling и runtime wiring не переносятся.

## 2. Contract allowlist

До отдельного explicit contract acceptance разрешён только:

```text
docs/project/V3_10_CL4_OPENING_RECONCILIATION_CONTRACT_RU.md
```

Любой другой changed/untracked path означает `SCOPE_VIOLATION`. Contract review
выполняется read-only по exact predecessor -> candidate head.

## 3. Замороженный future implementation allowlist

Только после explicit acceptance exact contract commit/tree отдельная
implementation branch может быть создана прямо от accepted head. Весь future
implementation delta ограничен тремя путями:

```text
current/trading_robot/cash_ledger_opening_reconciliation.py
current/tests/test_v3_10_cash_ledger_opening_reconciliation.py
current/tests/fixtures/v3_10_cash_ledger_opening_reconciliation_vectors.json
```

Изменения CL1/CL2/CL3 source, SQLite schema, package/runtime wiring, GUI, Risk,
Central, Execution, build/release и других tests запрещены. Расширение allowlist
требует отдельного re-scope решения, а не correction внутри CL4.

## 4. Bounded mission

CL4 version 1 обязан дать только:

1. pure snapshot adapter одного caller-supplied `GetPortfolio` response;
2. exact `totalAmountCurrencies -> CL1 Money` через принятый CL3 codec;
3. immutable privacy-safe `BrokerCashProof`;
4. единственный opening mode `FROM_NOW`;
5. deterministic create-once opening observation/transaction/record graph;
6. explicit, confirmation-gated append через существующий CL2 API;
7. deterministic expected-cash reduction из validated CL2 export;
8. exact zero-tolerance broker-vs-ledger shadow reconciliation;
9. finite discrepancy taxonomy и completeness evidence;
10. fail-closed immutable candidate только для отдельного adoption review.

Нормативный поток:

```text
caller-supplied GetPortfolio response
-> bounded immutable response snapshot
-> exact CL3 Money codec
-> BrokerCashProof
-> FROM_NOW OpeningPlan
-> explicit shadow-ledger acceptance through CL2
-> derived OpeningRecord
-> LedgerCashProjection
-> CashReconciliation
-> AdoptionCandidate requiring a separate locked gate
```

## 5. Authority boundary и explicit non-goals

CL4 получает authority только на pure/read-only interpretation и на явно
подтверждённый append одного shadow opening graph через уже принятые CL2
mutators. Этот append не меняет broker, Portfolio, reservations или runtime owner.

CL4 не получает authority:

- открывать сеть, читать credentials/env или вызывать T-Bank SDK/API;
- выполнять provider POST, order placement/cancel или economic broker mutation;
- объявлять caller-supplied response broker attestation;
- читать `GetPositions.money/blocked` либо выводить available/investable cash;
- создавать CashAvailability, reservation projection или funding decision;
- менять CL1 semantics, CL2 schema/WAL/backup/restore или CL3 classification;
- выполнять historical backfill, reset, reopen, second opening или correction
  opening transaction;
- менять PortfolioRepository, Central, Risk, Execution, GUI или runtime state;
- автоматически применять `AdoptionCandidate`;
- разрешать BUY, SELL, real-account mutation или experiment.

Invariant:

> `MATCHED` reconciliation означает только согласованность shadow evidence.
> Она не передаёт CashLedger current-cash ownership и не даёт execution authority.

## 6. Нормативные зависимости и side-effect boundary

Implementation может импортировать только standard library и публичные symbols:

- CL1 `Money`, `SourceIdentity`, `LedgerPosting`, `LedgerTransaction`,
  `LedgerCorrectionBundle`, enums/reasons и canonical identities;
- CL2 `CashLedgerStore`, `CodecDescriptor`, `InboxObservation`, `LedgerHead`,
  dispositions/reasons и canonical JSON/hash helpers;
- CL3 `BrokerEnvironment`, `money_value_to_money` и соответствующие closed errors.

CL4 module не импортирует `sqlite3`, provider SDK, requests/http clients,
credential/token helpers, `portfolio*`, Central, Risk, Execution, GUI, scheduler,
runtime или EventJournal. Он не читает filesystem/environment/clock/randomness и
не создаёт threads/processes. Единственный durable side effect — вызовы
`CashLedgerStore.append_observation` и `append_transaction` внутри explicit
`accept_from_now_opening`; direct database/file access отсутствует.

Все response, timestamps, account scope, identity key, confirmation и store
handle передаются caller. Import модуля side-effect free.

## 7. Versions, bounds и canonical primitives

Frozen constants:

```text
CL4_CONTRACT_VERSION = 1
BROKER_CASH_PROOF_VERSION = 1
OPENING_PLAN_VERSION = 1
OPENING_RECORD_VERSION = 1
LEDGER_CASH_PROJECTION_VERSION = 1
CASH_RECONCILIATION_VERSION = 1
ADOPTION_CANDIDATE_VERSION = 1
CL4_OPENING_CODEC_VERSION = 1
CL4_CROSS_LANGUAGE_FIXTURE_VERSION = 1
MAX_PROOF_AGE_NS = 120000000000
MAX_RESPONSE_DEPTH = 16
MAX_RESPONSE_NODES = 100000
MAX_RESPONSE_CANONICAL_BYTES = 1048576
MAX_MAPPING_KEYS = 4096
MAX_STRING_SCALARS = 4096
MAX_KEY_SCALARS = 128
MAX_LEDGER_EXPORT_BYTES = 16777216
MAX_LEDGER_OBJECTS = 100000
RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS = 18446744073709551616999999998
```

Все CL4 canonical bytes используют exact CL1/CL2 rule:

```python
json.dumps(
    value,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("ascii")
```

При parsing запрещены BOM, duplicate keys, внешние whitespace bytes,
non-canonical re-encoding, NaN/Infinity и Unicode surrogates. SHA-256 — lowercase
hex exact canonical bytes. HMAC-SHA-256:

```python
hmac.new(identity_key, canonical_preimage, hashlib.sha256).hexdigest()
```

Общие грамматики:

- hash: `[0-9a-f]{64}`;
- token/key ID: `[A-Z][A-Z0-9_]{0,63}`;
- canonical signed decimal: `0|-?[1-9][0-9]*`;
- revision decimal: `0|[1-9][0-9]*` в CL2 range;
- timestamp: CL1 `YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ` с реальной UTC date.

`bool` не является integer. Неявные coercions, float, Decimal input, rounding,
locale formatting и normalization строк запрещены.

## 8. Frozen public API surface

Implementation экспортирует только:

```text
OpeningMode
ReconciliationStatus
DiscrepancyKind
AdoptionDisposition
CL4Reason
CL4Error
BrokerCashProof
OpeningPlan
OpeningRecord
LedgerCashProjection
CashReconciliation
AdoptionCandidate
OpeningAcceptance
CL4_OPENING_CODEC
build_broker_cash_proof
prepare_from_now_opening
accept_from_now_opening
project_shadow_cash
reconcile_shadow_cash
build_adoption_candidate
```

Exact signatures:

```python
build_broker_cash_proof(
    response: object,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    evaluated_at: str,
    response_complete: bool,
    identity_key: bytes,
    identity_key_id: str,
) -> BrokerCashProof

prepare_from_now_opening(
    ledger_export_bytes: bytes,
    proof: BrokerCashProof,
    *,
    evaluated_at: str,
    identity_key: bytes,
) -> OpeningPlan

accept_from_now_opening(
    store: CashLedgerStore,
    plan: OpeningPlan,
    *,
    confirmation: str,
    evaluated_at: str,
    identity_key: bytes,
) -> OpeningAcceptance

project_shadow_cash(
    ledger_export_bytes: bytes,
    *,
    account_scope_sha256: str,
    environment: BrokerEnvironment,
    as_of: str,
    identity_key: bytes,
) -> LedgerCashProjection

reconcile_shadow_cash(
    ledger_export_bytes: bytes,
    proof: BrokerCashProof,
    *,
    evaluated_at: str,
    identity_key: bytes,
) -> CashReconciliation

build_adoption_candidate(
    reconciliation: CashReconciliation,
    *,
    ledger_export_bytes: bytes,
    identity_key: bytes,
) -> AdoptionCandidate
```

Другие module-level names private. Public functions не принимают callbacks,
paths, URLs, raw account IDs, credentials или transport objects.

## 9. One-response GetPortfolio snapshot boundary

CL4 не вызывает transport. `response` — результат ровно одного завершённого
caller-owned semantic RPC:

```text
tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio
```

`response_complete` обязан быть exact `True`; `False` и non-bool дают
`PROOF_INCOMPLETE`. Этот bit является caller assertion, а не доказательством
transport completeness.

Для исключения mutable/custom-object ambiguity response graph принимает только
exact built-in `dict`, `list`, `str`, plain `int`, `bool`, `None`. Tuple,
Mapping subclass, dataclass, protobuf object, bytes, float и Decimal запрещены.
Graph preflight до semantic extraction:

1. cycle и repeated container identity запрещены;
2. root response имеет depth `1`; child value имеет parent depth `+1`, maximum
   inclusive depth равен `MAX_RESPONSE_DEPTH`;
3. node — каждое occurrence value, включая root, container и scalar; dict key не
   является node; traversal идёт dict insertion order и list order, а limit
   проверяется после каждого increment;
4. каждый dict имеет не более `MAX_MAPPING_KEYS`; key проверяется отдельно;
5. depth, total nodes, keys/container и string scalar bounds применяются ко всему
   graph, включая ignored provider fields;
6. dict keys — exact strings, не более `MAX_KEY_SCALARS`, без surrogate;
7. integer — plain signed int64;
8. canonical response bytes не превышают `MAX_RESPONSE_CANONICAL_BYTES`;
9. serialization и immediate parse/re-serialize обязаны быть byte-identical.

Все limits inclusive. Container identity добавляется в global seen-set при первом
посещении; повтор, включая cycle, отклоняется до обхода children. String key
участвует в key bounds, string value — в node и value bounds.

После snapshot дальнейшее чтение идёт только из parsed immutable logical value;
mutation исходного object не может изменить proof.

Top-level должен содержать `totalAmountCurrencies`. Другие keys разрешены только
как bounded identity material: CL4 не интерпретирует и не экспортирует их.
`totalAmountCurrencies` имеет exact keyset:

```json
{"currency":"RUB","nano":0,"units":"0"}
```

`currency`, `nano`, `units` проходят без локальной нормализации через exact CL3
`money_value_to_money`. Другой cash field, `GetPositions.money/blocked`,
`totalAmountPortfolio`, float-derived Portfolio cash и caller-supplied override
не являются substitute.

## 10. Privacy-safe response identity и BrokerCashProof

`identity_key` — exact bytes длиной `32..64`, `identity_key_id` — token. Key не
входит в result/error/repr и не хранится. CL4 не выводит/ротирует key.

`response_canonical_sha256 = SHA256(exact bounded snapshot bytes)`. Response
identity — HMAC exact preimage:

```json
{
  "account_scope_sha256": "<64 lowercase hex>",
  "domain": "v3.10-cl4-getportfolio-response-identity",
  "environment": "SANDBOX",
  "identity_key_id": "<TOKEN>",
  "provider": "TBANK",
  "response_canonical_sha256": "<SHA-256 exact response snapshot bytes>",
  "rpc": "tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
  "version": 1
}
```

Version 1 поддерживает только `BrokerEnvironment.SANDBOX` и CL1 `RUB/scale=9`.
`PRODUCTION`, другая currency/scale и non-positive opening не расширяются
автоматически.

`BrokerCashProof` — frozen/slotted object:

```text
account_scope_sha256: str
environment: BrokerEnvironment
as_of: str
cash: Money
response_canonical_sha256: str
response_identity_sha256: str
identity_key_id: str
response_complete: bool = True
version: int = 1
```

Exact canonical keyset:

```json
{
  "account_scope_sha256": "<64 lowercase hex>",
  "as_of": "YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ",
  "cash": "<nested exact CL1 Money object>",
  "cash_field": "totalAmountCurrencies",
  "domain": "v3.10-cl4-broker-cash-proof",
  "environment": "SANDBOX",
  "identity_key_id": "<TOKEN>",
  "provider": "TBANK",
  "response_complete": true,
  "response_canonical_sha256": "<SHA-256>",
  "response_identity_sha256": "<HMAC-SHA-256>",
  "rpc": "tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
  "version": 1
}
```

`sha256` равен plain SHA-256 proof canonical bytes. Proof не содержит raw
response/account/token. Наличие `response_canonical_sha256` позволяет всем
принимающим proof boundaries заново проверить keyed response identity, не сохраняя
response. `as_of` — caller-supplied observation-completion time; он не объявляется
provider timestamp или cryptographic attestation. Build требует:

```text
as_of <= evaluated_at
evaluated_at - as_of <= MAX_PROOF_AGE_NS
```

Сравнение выполняется integer nanoseconds без wall clock read.
`prepare_from_now_opening`, `accept_from_now_opening` и
`reconcile_shadow_cash` обязаны воспроизвести response identity из proof и
supplied key; mismatch даёт `RESPONSE_IDENTITY_INVALID` до ledger mutation.

## 11. Opening mode, codec и SourceIdentity

`OpeningMode` содержит только `FROM_NOW`. Historical backfill отсутствует.

`CL4_OPENING_CODEC`:

```text
codec_id = CL4_FROM_NOW_OPENING_V1
codec_version = 1
version = 1
schema_sha256 = 917b0b3a98748c2279ed9efb1007fddec6375afd3da7003143690544b7336d78
descriptor_sha256 = 159ebf8e748b104bf691a0da2cda4d6a94866417b1c0330124f406f35d17e6c2
```

Exact `schema_json_ascii`:

```json
{"domain":"v3.10-operation-inbox-codec-schema","fields":[{"allowed_values":null,"key":"account_scope_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"broker_cash_proof_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"cutoff","kind":"STRING","max_scalars":"30","maximum":null,"minimum":null,"required":true},{"allowed_values":["SANDBOX"],"key":"environment","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"generation","kind":"INTEGER","max_scalars":null,"maximum":"1","minimum":"1","required":true},{"allowed_values":null,"key":"identity_key_id","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"ledger_export_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"ledger_head_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"ledger_revision","kind":"STRING","max_scalars":"19","maximum":null,"minimum":null,"required":true},{"allowed_values":["FROM_NOW"],"key":"mode","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":["RUB"],"key":"opening_currency","kind":"STRING","max_scalars":"3","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"opening_minor_units","kind":"STRING","max_scalars":"29","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"response_identity_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"store_revision","kind":"STRING","max_scalars":"19","maximum":null,"minimum":null,"required":true}],"version":1}
```

Opening sanitized content имеет exact поля schema и значения:

- account/proof/response/export/head hashes — lowercase SHA-256;
- `cutoff = proof.as_of`;
- `environment = SANDBOX`, `mode = FROM_NOW`, `generation = 1`;
- `identity_key_id` — exact proof key ID;
- revisions — canonical decimal strings;
- opening currency/minor units — exact CL1 values.

Source scope HMAC preimage:

```json
{"account_scope_sha256":"<hash>","currency":"RUB","domain":"v3.10-cl4-opening-source-scope","environment":"SANDBOX","generation":1,"identity_key_id":"<TOKEN>","mode":"FROM_NOW","version":1}
```

`SourceIdentity`:

```text
account_scope_sha256 = proof.account_scope_sha256
source_kind = CL4_FROM_NOW_OPENING
source_scope_sha256 = HMAC(preimage above)
source_content_sha256 = SHA256(exact sanitized content bytes)
```

Provenance HMAC preimage:

```json
{"account_scope_sha256":"<hash>","broker_cash_proof_sha256":"<hash>","domain":"v3.10-cl4-opening-provenance","identity_key_id":"<TOKEN>","ledger_export_sha256":"<hash>","response_identity_sha256":"<hash>","source_content_sha256":"<hash>","version":1}
```

`InboxObservation.observed_at = proof.as_of`. Exact logical source включает key
version и уникален per `account/environment/currency/mode/generation/key ID`;
changed content при том же key становится CL2 `SOURCE_CONTENT_CONFLICT`. Key
rotation меняет keyed identities и в CL4 v1 не поддерживается. Независимая
cross-key create-once проверка всего ledger graph всё равно запрещает второй
opening для account/environment/currency.

## 12. CL2 export validation и bounded ledger view

CL4 принимает только bytes exact deterministic CL2 export version/schema `1`.
Он не принимает dict, file path, database handle или independently assembled
transactions как projection input. Перед любым opening/projection:

1. bytes length `1..MAX_LEDGER_EXPORT_BYTES`;
2. CL2 canonical parse и byte-identical re-encoding;
3. exact top-level/element keysets и versions из CL2 contract;
4. каждая export array имеет длину `0..MAX_LEDGER_OBJECTS`; aggregate count —
   exact сумма длин `codec_registry`, `observations`, `inbox_status_events`,
   `transactions`, `provenance_links`, `correction_bundles` и
   `ledger_transitions`, также не более `MAX_LEDGER_OBJECTS`; top-level object,
   wrapper fields и nested JSON-in-JSON CL1/CL2 objects отдельно не считаются;
5. every nested CL1/CL2 canonical object parse/round-trip byte-identically;
6. wrapper SHA/source/economic/logical fields equal derived values;
7. unique hashes/logical sources/provenance links;
8. complete contiguous inbox event chains и legal terminal status;
9. complete contiguous `LedgerHead` chain from exact CL2 genesis;
10. every ordinary transition resolves one ordinary transaction;
11. every correction transition resolves one valid `LedgerCorrectionBundle`;
12. reversal/correction rows occur only through their exact bundle transition;
13. для target account все CL4 source-scope/provenance HMAC воспроизводятся из
    content и supplied key; их key ID обязан быть единым; non-target CL4 rows
    проходят полный CL2/schema/hash graph validation, но не проверяются чужим key;
14. final revisions/head equal export top-level values;
15. unreferenced, duplicated, dangling или unknown graph elements fail closed.

CL4 revalidation не меняет CL2 bytes или semantics. It is defense-in-depth for a
caller-supplied export, not a second persistence implementation.

`ledger_export_sha256 = SHA256(exact export bytes)`.

## 13. FROM_NOW OpeningPlan

`prepare_from_now_opening` требует fresh positive proof, valid export и отсутствие
любого accepted `OPENING_BALANCE` для target account/currency. Pre-existing
non-opening ledger history допускается и входит в frozen cutover head, но не
пересчитывается: это и есть `FROM_NOW`, не historical backfill.

До preparation все target-account observations с `observed_at <= proof.as_of`
обязаны быть terminal `LEDGER_LINKED` или `REJECTED`. `OBSERVED`,
`REVIEW_REQUIRED`, broken status/link или evidence после proof `as_of` дают
`INBOX_INCOMPLETE`; никакой pending amount не добавляется к cash.

`OpeningPlan` содержит immutable nested `proof`, `observation`, `transaction` и:

```text
mode: FROM_NOW
generation: 1
pre_store_revision
pre_ledger_revision
pre_ledger_head_sha256
pre_ledger_export_sha256
version: 1
```

Его canonical identity содержит только stable hashes/values:

```json
{
  "account_scope_sha256": "<hash>",
  "broker_cash_proof_sha256": "<hash>",
  "domain": "v3.10-cl4-opening-plan",
  "environment": "SANDBOX",
  "generation": 1,
  "mode": "FROM_NOW",
  "observation_sha256": "<hash>",
  "opening_money": "<nested CL1 Money>",
  "pre_ledger_export_sha256": "<hash>",
  "pre_ledger_head_sha256": "<hash>",
  "pre_ledger_revision": "<decimal>",
  "pre_store_revision": "<decimal>",
  "transaction_sha256": "<hash>",
  "version": 1
}
```

Opening transaction exact material:

```text
classification = OPENING_BALANCE
effective_at = proof.as_of
source = opening observation SourceIdentity byte-for-byte
line 1 = ASSET_BROKER_CASH      +proof.cash
line 2 = EQUITY_OPENING_BALANCE -proof.cash
reversal_of_sha256 = null
corrects_sha256 = null
```

Proof cash обязан быть strictly positive и в reversible posting range. Zero и
negative cash дают `OPENING_AMOUNT_UNSUPPORTED`; CL1 zero-posting invariant не
ослабляется. Один exact input создаёт byte-identical plan/observation/transaction.

## 14. Explicit opening acceptance и create-once state machine

Единственная accepted confirmation:

```text
ACCEPT V3.10 CL4 FROM_NOW OPENING <opening_plan_sha256>
```

Exact string сравнивается byte-for-byte; whitespace/case/другой hash запрещены.
Это подтверждает только shadow-ledger opening, не runtime adoption.

Перед вызовом store должен быть создан/открыт с registry, который содержит exact
`CL4_OPENING_CODEC` вместе со всеми уже persisted codecs. Отсутствующий или
конфликтующий descriptor нормализуется в `PERSISTENCE_FAILURE` без direct schema
write со стороны CL4.

`OpeningAcceptance` содержит:

```text
disposition: OPENING_APPENDED | OPENING_ALREADY_PRESENT
record: OpeningRecord
store_revision: int
ledger_revision: int
ledger_head_sha256: str
```

Перед первым write функция получает `store.export_bytes()`, валидирует весь graph и
допускает только:

1. **ABSENT** — exact pre-export/head/revisions из plan, opening отсутствует;
2. **STAGED_OBSERVATION** — current export является valid append-only descendant
   plan pre-state: ledger chain содержит exact `pre_ledger_head_sha256` на exact
   `pre_ledger_revision`, exact plan observation существует в status `OBSERVED`,
   plan transaction отсутствует и другого target opening нет;
3. **COMMITTED_OPENING** — complete exact observation/link/transaction graph,
   из которого выводится byte-identical expected `OpeningRecord`.

В `COMMITTED_OPENING` возвращается `OPENING_ALREADY_PRESENT` без write и без
проверки старых pre-revisions или current proof age. Exact
`STAGED_OBSERVATION` также можно завершить после expiration: первый write уже
зафиксировал fresh-at-preparation exact plan, а recovery не меняет его cutoff или
content. Для `ABSENT` proof всё ещё обязан быть fresh непосредственно перед
первым write. Different proof/plan/graph даёт `OPENING_CONFLICT`.

Здесь append-only descendant означает: current `CashLedgerStore` прошёл полную
CL2 export/schema/graph validation, frozen plan ledger head найден на exact
revision его contiguous head chain, а все последующие ledger transitions являются
valid suffix этой chain. CL2 не имеет delete/update/reset API; inbox-only suffix не
имеет экономического эффекта и не обязан сохранять plan `pre_store_revision`.
`pre_ledger_export_sha256` остаётся exact preparation evidence и требуется как
current equality только в `ABSENT`, а не как equality predicate recovery branch.

В `ABSENT` вызывается существующий CL2 `append_observation` с exact CAS revision.
После read-back допускается exact `STAGED_OBSERVATION` либо exact
`COMMITTED_OPENING`, если concurrent same-plan caller уже завершил second step.
Во втором случае возвращается `OPENING_ALREADY_PRESENT`. Из staged state
вызывается CL2 `append_transaction` с current store revision и неизменными plan
transaction bytes. CL2 CAS получает current store revision и current ledger
revision, прочитанные из validated descendant export. После второго read-back exact
graph обязан быть complete, иначе `POSTCONDITION_FAILED`.

В `STAGED_OBSERVATION` первая операция не повторяет write; выполняется только
точно такой же second step. Automatic retry loop отсутствует.

Допустимые durable состояния:

```text
no opening graph
exact staged OBSERVED opening evidence with zero economic effect
one complete LEDGER_LINKED opening graph
```

Crash/fault до observation commit оставляет первый state; между двумя CL2 commits
— второй; после transaction commit/lost response — третий. Staged observation не
является opening record и не влияет на expected cash. Exact caller replay может
завершить staged state даже после unrelated valid inbox или ledger appends. Такие
appends не меняют frozen baseline plan; их target-account cash effects позже входят
в projection относительно baseline. Concurrent mutation между read и append даёт
CL2 CAS failure, нормализованный в `OPENING_PLAN_STALE`; caller может повторно
вызвать функцию, internal retry loop отсутствует. Изменённый status exact plan
observation, competing target opening, отсутствующий baseline head в exact chain
или иная несовместимая lineage дают `OPENING_CONFLICT` и zero additional writes.

Edit/delete/reset/reopen/correct-opening API отсутствует. Correction bundle,
reversal или второй `OPENING_BALANCE` target account/currency делает весь CL4
view `OPENING_CONFLICT`.

## 15. Derived OpeningRecord

CL4 не добавляет table/schema. `OpeningRecord` всегда заново выводится из exact
validated CL2 graph: one CL4 observation, one exact CL2 provenance link и one
ordinary `OPENING_BALANCE` transaction transition.

Exact canonical keyset:

```json
{
  "accepted_ledger_revision": "<positive decimal>",
  "account_scope_sha256": "<hash>",
  "baseline_ledger_export_sha256": "<hash>",
  "baseline_ledger_head_sha256": "<hash>",
  "baseline_ledger_revision": "<decimal>",
  "baseline_store_revision": "<decimal>",
  "broker_cash_proof_sha256": "<hash>",
  "cutoff": "<CL1 timestamp>",
  "domain": "v3.10-cl4-opening-record",
  "environment": "SANDBOX",
  "generation": 1,
  "mode": "FROM_NOW",
  "observation_sha256": "<hash>",
  "opening_money": "<nested CL1 Money>",
  "transaction_sha256": "<hash>",
  "version": 1
}
```

Record SHA-256 — hash этих bytes. Baseline fields берутся byte-for-byte из plan и
обязаны совпасть с exact ancestor ledger head/revision; accepted revision — exact
ordinary transition revision opening transaction. Observation content,
SourceIdentity, transaction postings и transition revision обязаны взаимно
воспроизводить все fields. Inference из missing/corrupt/legacy data запрещён.

## 16. Deterministic shadow cash projection

`project_shadow_cash` валидирует full export, воспроизводит CL4 source/provenance
HMAC из opening content и supplied key, находит ровно один target opening и
начинает с `opening_money`. Для target account proof/content/source/provenance
обязаны иметь один exact key ID и проходить HMAC supplied key; target mixed-key или
key-rotation state не поддерживается. Non-target CL4 identities, созданные с
другими keys, не препятствуют projection после полного graph validation. Затем в
ascending ledger revision учитываются все accepted target-account effects строго
после `baseline_ledger_revision`, кроме exact opening transaction:

- ordinary transaction — exact posting `ASSET_BROKER_CASH`;
- correction-bundle transition — exact sum reversal and correction cash postings;
- original effect уже был учтён в его earlier ordinary revision и не добавляется
  повторно;
- other account scope не влияет на target sum после полного graph validation.

Любой target cash effect с `effective_at <= opening.cutoff`, appended после
baseline revision, помечает projection incomplete как
`LATE_PRE_CUTOFF_LEDGER_EFFECT` и не
может дать `MATCHED`. Он всё равно включается в deterministic arithmetic, чтобы
`expected_cash` оставался воспроизводимым; automatic repair/subtraction forbidden.

Если target ledger effect имеет `effective_at > as_of`, projection incomplete как
`PROOF_BEFORE_LEDGER_EFFECT`. Target observation с `observed_at > as_of` даёт
`PROOF_BEFORE_LEDGER_EVIDENCE`.

Current target observations `OBSERVED`/`REVIEW_REQUIRED` дают sorted
`unresolved_observation_sha256`. `REJECTED` завершён без cash effect;
`LEDGER_LINKED` требует exact link. Pending/unclassified amounts никогда не
угадываются и не добавляются.

Каждый accepted target cash effect после baseline ledger revision входит в
`expected_cash` ровно один раз независимо от `effective_at`. Timestamp
нарушения меняют только `complete/incompleteness_kinds`; при `INCOMPLETE`
получившаяся сумма и delta являются воспроизводимым evidence, а не экономическим
утверждением или основанием для repair.

`LedgerCashProjection` immutable public fields имеют exact types:

```text
account_scope_sha256: str
environment: BrokerEnvironment
currency: str
as_of: str
opening_record_sha256: str
ledger_export_sha256: str
ledger_revision: int
ledger_head_sha256: str
expected_cash: Money
complete: bool
incompleteness_kinds: tuple[DiscrepancyKind, ...]
unresolved_observation_sha256: tuple[str, ...]
version: int = 1
```

Exact canonical keyset:

```json
{
  "account_scope_sha256": "<hash>",
  "as_of": "<CL1 timestamp>",
  "complete": true,
  "currency": "RUB",
  "domain": "v3.10-cl4-ledger-cash-projection",
  "environment": "SANDBOX",
  "expected_cash": "<nested CL1 Money>",
  "incompleteness_kinds": ["<DiscrepancyKind>", "..."],
  "ledger_export_sha256": "<hash>",
  "ledger_head_sha256": "<hash>",
  "ledger_revision": "<decimal>",
  "opening_record_sha256": "<hash>",
  "unresolved_observation_sha256": ["<hash>", "..."],
  "version": 1
}
```

`incompleteness_kinds` — unique subset первых четырёх `DiscrepancyKind` в frozen
precedence порядка section 17; это не ASCII sort.
`unresolved_observation_sha256` — unique ASCII-sorted hashes.
`complete` равен `true` iff оба массива пусты. Projection SHA-256 — hash exact
canonical bytes.

`UNRESOLVED_OBSERVATION` присутствует в `incompleteness_kinds` iff
`unresolved_observation_sha256` не пуст; остальные три incomplete kinds
присутствуют iff соответствующее condition section 16 встретилось хотя бы один
раз. Ни один amount/count не кодируется в kind list.

Integer summation проверяется после каждого addition против full CL1 Money bound;
overflow fail-closed. Порядок input rows не влияет на canonical result.

## 17. Exact shadow reconciliation

`reconcile_shadow_cash` повторно валидирует proof freshness на `evaluated_at`,
строит projection на `as_of = proof.as_of` и требует exact account/environment/
currency equality. Proof `identity_key_id` обязан совпасть с opening content;
target mixed-key reconciliation запрещена, valid non-target CL4 rows с другими
key IDs не проверяются target key и не запрещены.

```text
delta_minor_units = broker_cash.minor_units - expected_cash.minor_units
tolerance_minor_units = 0
```

`delta_minor_units` — exact Python `int` в inclusive range
`[-RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS,
RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS]`; overflow невозможен для двух valid
CL1 Money, но проверяется явно. В canonical JSON он является signed decimal
string, потому что difference может быть шире одного CL1 Money.

Closed `ReconciliationStatus`:

```text
MATCHED
DISCREPANCY
INCOMPLETE
```

Closed `DiscrepancyKind` и precedence:

```text
LATE_PRE_CUTOFF_LEDGER_EFFECT
PROOF_BEFORE_LEDGER_EFFECT
PROOF_BEFORE_LEDGER_EVIDENCE
UNRESOLVED_OBSERVATION
BROKER_ABOVE_EXPECTED
BROKER_BELOW_EXPECTED
NONE
```

Первый присутствующий incompleteness kind в этом списке даёт `INCOMPLETE` и
соответствующий `DiscrepancyKind`; delta сохраняется как evidence, но не меняет
status. При complete projection: positive delta -> `DISCREPANCY /
BROKER_ABOVE_EXPECTED`; negative -> `DISCREPANCY / BROKER_BELOW_EXPECTED`; exact
zero -> `MATCHED / NONE`.

`CashReconciliation` immutable public fields имеют exact types:

```text
proof: BrokerCashProof
projection: LedgerCashProjection
evaluated_at: str
broker_cash: Money
expected_cash: Money
delta_minor_units: int
status: ReconciliationStatus
discrepancy_kind: DiscrepancyKind
version: int = 1
```

Exact canonical keyset:

```json
{
  "account_scope_sha256": "<hash>",
  "broker_cash": "<nested CL1 Money>",
  "broker_cash_proof_sha256": "<hash>",
  "currency": "RUB",
  "delta_minor_units": "<signed decimal>",
  "discrepancy_kind": "<DiscrepancyKind>",
  "domain": "v3.10-cl4-cash-reconciliation",
  "environment": "SANDBOX",
  "evaluated_at": "<CL1 timestamp>",
  "expected_cash": "<nested CL1 Money>",
  "ledger_cash_projection_sha256": "<hash>",
  "status": "<ReconciliationStatus>",
  "tolerance_minor_units": "0",
  "version": 1
}
```

Nested proof/projection, repeated public Money fields и все canonical hash/value
fields обязаны совпадать byte-for-byte. Status/discrepancy consistency следует
только deterministic table выше. Reconciliation SHA-256 — hash exact canonical
bytes.

No configurable epsilon, float comparison, pending-netting или silent rounding.

## 18. Fail-closed AdoptionCandidate

`AdoptionDisposition`:

```text
SEPARATE_LOCKED_REVIEW_REQUIRED
BLOCKED
```

Функция byte-identically revalidates nested reconciliation, proof и projection,
затем заново вызывает `reconcile_shadow_cash(ledger_export_bytes, proof,
evaluated_at=reconciliation.evaluated_at, identity_key=identity_key)`. Переданный
export обязан иметь exact hash/head/revision projection, target CL4 HMAC должны
воспроизводиться supplied key, а recomputed reconciliation canonical bytes обязаны
совпасть с input. Только после этой independent revalidation exact `MATCHED/NONE`,
fresh-at-recorded-evaluation proof и complete projection создают
`SEPARATE_LOCKED_REVIEW_REQUIRED`. Любой valid другой reconciliation создаёт
`BLOCKED`; forged, stale-at-evaluation или mismatched graph отклоняется error и не
создаёт candidate. Candidate не продлевает freshness proof.

Canonical/nested/recomputed mismatch самого reconciliation даёт
`RECONCILIATION_INVALID`; lower-level proof/export/key/freshness failures сохраняют
свой более ранний exact `CL4Reason` по section 20.

Canonical candidate обязательно содержит:

```json
{
  "automatic_adoption": false,
  "disposition": "<SEPARATE_LOCKED_REVIEW_REQUIRED|BLOCKED>",
  "domain": "v3.10-cl4-adoption-candidate",
  "ledger_head_sha256": "<hash>",
  "reconciliation_sha256": "<hash>",
  "requires_locked_revalidation": true,
  "runtime_cash_owner_changed": false,
  "version": 1
}
```

CL4 не экспортирует `adopt`, `apply`, `migrate`, callback или mutation method.
Любой будущий adopter обязан в отдельном accepted milestone получить locks,
заново получить current broker proof, ledger export и owner state, пересчитать
reconciliation и проверить exact candidate/revisions immediately before its own
mutation. CL4 не утверждает, что такой gate существует или разрешён.

## 19. Closed errors и privacy-safe evidence

`CL4Reason` — exact finite set:

```text
TYPE_INVALID
VERSION_UNSUPPORTED
ENVIRONMENT_UNSUPPORTED
MODE_UNSUPPORTED
ACCOUNT_SCOPE_INVALID
IDENTITY_KEY_INVALID
TIMESTAMP_INVALID
PROOF_FROM_FUTURE
PROOF_STALE
PROOF_INCOMPLETE
RESPONSE_BOUNDS_EXCEEDED
RESPONSE_SCHEMA_INVALID
MONEY_INVALID
RESPONSE_IDENTITY_INVALID
LEDGER_EXPORT_INVALID
LEDGER_GRAPH_INVALID
LEDGER_REVISION_INVALID
RECONCILIATION_INVALID
INBOX_INCOMPLETE
OPENING_AMOUNT_UNSUPPORTED
OPENING_MISSING
OPENING_CONFLICT
OPENING_PLAN_STALE
CONFIRMATION_INVALID
PERSISTENCE_FAILURE
ARITHMETIC_OVERFLOW
POSTCONDITION_FAILED
INTERNAL_BOUNDARY_FAILED
```

`CL4Error` экспортирует только `reason`, optional `cause_reason` и read-only
`evidence`. Cause может быть exact `MoneyReason`, `LedgerReason`,
`PersistenceReason` или `BrokerReadReason`. `str/repr` содержат только finite
tokens; raw exception chaining наружу запрещён.

Safe evidence keyset ограничен:

```text
reason, cause_reason, stage, account_scope_sha256,
broker_cash_proof_sha256, opening_plan_sha256,
ledger_export_sha256, ledger_head_sha256, reconciliation_sha256
```

Absent fields опускаются. Raw response/account/token/key/path/SQL/provider text,
object repr, arbitrary exception text и canonical raw response bytes запрещены во
всех public objects/errors/evidence.

## 20. Ordered failure priority

Top-level public boundary:

1. exact argument/container types;
2. enum/version/mode/environment;
3. account/hash/token/key grammar and bounds;
4. timestamp grammar and ordering;
5. completeness/freshness;
6. response/export byte and graph bounds;
7. response/export exact schema/canonical form;
8. nested CL3 Money / CL1 / CL2 object validation;
9. response/proof or ledger graph identities;
10. opening uniqueness/amount/pre-state;
11. arithmetic/window/completeness projection;
12. reconciliation/adoption construction.

Acceptance boundary:

1. confirmation exact string;
2. plan/nested object byte-identical revalidation;
3. identity key grammar, proof response identity и opening HMAC;
4. evaluated timestamp grammar;
5. store type/open state and sanitized export;
6. committed exact replay либо exact staged append-only descendant branch;
7. proof freshness for absent state;
8. absent state plan revisions/head/export equality;
9. CL2 observation append/read-back;
10. staged baseline ancestry, competing-opening absence и current CAS revisions;
11. CL2 transaction append/read-back;
12. exact OpeningRecord/postcondition.

При multi-invalid input возвращается только первая reason. Dependency error
нормализуется без private context; unexpected error -> `INTERNAL_BOUNDARY_FAILED`.

## 21. Synthetic known-answer vector

Frozen synthetic input:

```text
account_scope_sha256 = 1111111111111111111111111111111111111111111111111111111111111111
environment = SANDBOX
identity_key = bytes(range(32))
identity_key_id = CL4_TEST_KEY
as_of = 2026-01-02T03:04:05.123456789Z
evaluated_at = 2026-01-02T03:04:06.123456789Z
response_complete = true
response = {"totalAmountCurrencies":{"currency":"RUB","nano":500000000,"units":"123"}}
ledger export = exact empty CL2 genesis export
post-opening export = genesis + exact opening observation + exact opening transaction
```

Expected identities:

```text
response canonical SHA-256 = 204dba39888167e2384d8188817a5ad4a01f132a405a9b7b06f75f76aceed3ce
response identity HMAC-SHA-256 = 78fbeb0620bd7fd086fb4c39e3a205d16ecd85e366c3bd77f28cbe9f45ac2190
broker cash minor_units = 123500000000
broker cash proof SHA-256 = 8c693dc23481c63acda38e51ad6e7efda549976ff76b8b79efc083dc2562860d
genesis CL2 export SHA-256 = 9d00fefe18e104500df70063781b708c5ed90816dba8a9058dddc7ecaedf0eb7
genesis ledger head SHA-256 = 6ee5e86309122771bcaca40bb57771c2378c30d79b079e5431b227300a673d37
opening source scope HMAC-SHA-256 = d2b0be487681ab01e541fd8e594db29350da7401450efef7870a8217a28b9584
opening content SHA-256 = 91ba372c45fe11317a39a1a474f3a9f4490d9fd664d2fc4add8cb0a648ceaf91
opening provenance HMAC-SHA-256 = cb2313c95fb36f6beae746b5b4a449f0daa41b0b70e1a5538039ba442bf71507
opening observation SHA-256 = d9467aa68811d79288aa44040df798770b57ae7080bd823b50469cf77ab616d5
opening transaction SHA-256 = 4f824590773db04fe95f91852182b70ae4fb0dbc92bb6d397306963d277a1a3a
opening plan SHA-256 = d5b351c9e7132ff60fd0d20e452996932a6df992bc4ecff73772e7e1c4f65df2
post-opening CL2 export SHA-256 = 6c2e5d71a02d5ea07b8a18bcd6ac89d8f4b67ad2c6e73352f47d09c7f111942d
post-opening ledger head SHA-256 = adcfaa4c9d4c0460b96ca1845882c748f4531b79c27cd807b03577576d23a892
opening record SHA-256 = 9544462db024ed8d3a0eae9da5b0b037467565fb360925421821f8120e855cc1
complete projection SHA-256 = 9a13af4d874d1f6f4adf4f3ee36cf2a368252a4dac2c040cdf49b98283f708e8
matched reconciliation SHA-256 = 0abaaacfa0c8a607848b54a90f140ce8bb2a59eb02cecaaca87b17c21d24ebd7
adoption candidate SHA-256 = 8de3f3e4cdbed751d7593dbcb96730f1a1ac5552a450f75869fe9f955a4f3304
```

Exact canonical response bytes:

```text
{"totalAmountCurrencies":{"currency":"RUB","nano":500000000,"units":"123"}}
```

Fixture обязана содержать exact canonical bytes/dicts/hashes для proof, codec,
content, observation, transaction, plan, record, matched/discrepancy/incomplete
reconciliation и adoption candidate. Independent implementation должен
пересчитать их, а не копировать runtime output как oracle.

## 22. Mandatory acceptance/adversarial matrix

- `V310-CL4-01`: exact exports, versions, signatures, immutable/slotted DTOs и
  import side-effect freedom;
- `V310-CL4-02`: one-response snapshot, nested bounds/cycle/alias/custom types,
  mutation-after-call, root/child depth, exact node/key counting boundaries и
  canonical identity;
- `V310-CL4-03`: exact totalAmountCurrencies Money happy/bounds/sign/currency и
  rejection of every substitute/float/rounding path;
- `V310-CL4-04`: account/response/proof HMAC known answers, response snapshot
  hash, key/key-id changes, forged proof и privacy scans;
- `V310-CL4-05`: timestamp 9-digit ordering, future/stale/boundary age;
- `V310-CL4-06`: CL2 export canonical/schema/object/head/link/bundle adversarial
  corruption, each-array и exact seven-array aggregate size/count bounds;
- `V310-CL4-07`: deterministic FROM_NOW plan, codec/content/source/provenance,
  positive amount and exact CL1 opening postings;
- `V310-CL4-08`: wrong confirmation/stale head/revision/export -> zero writes;
- `V310-CL4-09`: ABSENT -> STAGED -> COMMITTED, fault at both CL2 boundaries,
  exact resume after proof expiry, unrelated inbox/ledger append между steps,
  CAS race replay and lost-success replay;
- `V310-CL4-10`: concurrent same/different plan, one opening, no second effect;
- `V310-CL4-11`: derived record graph and missing/extra/dangling/wrong-source/
  wrong-link/wrong-revision/key negatives;
- `V310-CL4-12`: projection over every CL1 classification and account scope;
- `V310-CL4-13`: correction bundle net effect once; opening reversal/correction
  rejected;
- `V310-CL4-14`: late pre-cutoff and proof-before-ledger effects/evidence;
- `V310-CL4-15`: unresolved/rejected/linked inbox status semantics;
- `V310-CL4-16`: exact zero, +1 and -1 minor-unit reconciliation taxonomy;
- `V310-CL4-17`: adoption candidate booleans/dispositions and absence of any
  apply/adopt/migrate callable; forged nested/outer-rehashed reconciliation,
  mismatched export/head/key и stale-at-recorded-evaluation rejected;
- `V310-CL4-18`: raw account/token/key/response/sentinels absent from every
  canonical object, error, repr and evidence;
- `V310-CL4-19`: no provider/network/env/clock/random/SQLite/runtime imports or
  calls; only two named CL2 mutators may write;
- `V310-CL4-20`: implementation delta exact frozen three-file allowlist and
  accepted CL1/CL2/CL3 sources byte-identical;
- `V310-CL4-21`: accepted CL1/CL2/CL3 functional suites and full functional suite
  pass excluding only two exact historical current-HEAD custody node IDs below;
  predecessor file custody проверяется отдельными immutable Git comparisons.

Adversarial tests обязаны forge/mutate frozen DTO nested values, recompute outer
hashes, reorder arrays, duplicate logical sources, alter one nano/revision/head,
inject multiple simultaneous faults и проверять exact first-failure priority.

## 23. Implementation verification commands

Future implementation review запускает минимум:

```powershell
git diff --name-status <accepted-cl4-contract-head>..HEAD
git diff --check <accepted-cl4-contract-head>..HEAD
git diff --name-only 4340c5d517dcece4f7db20b7cfc21e602c3efddc..HEAD
git diff --quiet 095a24a0d8ad2487f09ec0c5f3473a6c65a84710..HEAD -- current/trading_robot/cash_ledger_domain.py current/tests/test_v3_10_cash_ledger_domain.py current/tests/fixtures/v3_10_cash_ledger_vectors.json
git diff --quiet d684186c0628d27ed452ce4f11311155fdf7a44e..HEAD -- current/trading_robot/cash_ledger_persistence.py current/tests/test_v3_10_cash_ledger_persistence.py current/tests/fixtures/v3_10_cash_ledger_persistence_vectors.json
git diff --quiet 4340c5d517dcece4f7db20b7cfc21e602c3efddc..HEAD -- current/trading_robot/broker_read_adapters.py current/tests/test_v3_10_broker_read_adapters.py current/tests/fixtures/v3_10_broker_read_adapters_vectors.json
Push-Location current
try {
  python -m pytest tests/test_v3_10_cash_ledger_opening_reconciliation.py -q -p no:cacheprovider
  python -m pytest tests/test_v3_10_cash_ledger_domain.py tests/test_v3_10_cash_ledger_persistence.py tests/test_v3_10_broker_read_adapters.py --deselect=tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_28_three_path_delta_and_immutable_predecessor_files --deselect=tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_17_exact_three_path_delta -q -p no:cacheprovider
  python -m pytest tests --deselect=tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_28_three_path_delta_and_immutable_predecessor_files --deselect=tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_17_exact_three_path_delta -q -p no:cacheprovider
} finally {
  Pop-Location
}
```

Оба deselected tests — исторические self-custody oracles: они намеренно сравнивают
свои accepted contract heads с current `HEAD`, поэтому после любого successor
milestone их pytest predicates ложны. Они не являются functional regression
tests. Их invariant на CL4 successor заменён exact `git diff --quiet` checks выше;
каждый command обязан завершиться exit `0`, а cumulative `4340c5d...HEAD` name set
обязан быть ровно CL4 contract plus frozen three implementation paths.

Static checks подтверждают отсутствие provider SDK/network/env/clock/random/
direct SQLite/runtime imports, отсутствие forbidden public methods и ровно два
разрешённых CL2 mutator call sites внутри `accept_from_now_opening`.

## 24. Independent/adversarial contract review protocol

Final contract review выполняется read-only для exact range:

```text
4340c5d517dcece4f7db20b7cfc21e602c3efddc -> <candidate head>
```

Reviewer обязан подтвердить:

- exact parent/head/tree, clean worktree и one-file contract delta;
- consistency всех schemas/known-answer bytes/hashes;
- GetPortfolio response не подменён CL3 operation output или GetPositions;
- proof snapshot bounded, immutable, privacy-safe и не назван attestation;
- create-once graph реально выражается существующими CL1/CL2 contracts без
  schema change и partial economic state;
- opening replay/concurrency/CAS branches finite и fail closed;
- projection правильно обрабатывает CL2 bundle revision и cutover boundary;
- unresolved/late/future evidence не может дать `MATCHED`;
- `MATCHED`/candidate не открывают CashAvailability/execution/adoption;
- future implementation allowlist содержит ровно три frozen paths.

Finding именуется `CL4-R1-NN`. Допустим максимум один bounded correction commit
в этом же one-file allowlist, затем finding-scoped closure review exact successor.
Новый unrelated material finding после closure требует `RESCOPE/ABORT/DEFER`, а
не recursive correction loop.

## 25. Contract acceptance и exit state

Green validation или отсутствие review findings сами по себе не acceptance.
Требуется отдельное явное решение:

```text
CL4 CONTRACT ACCEPTED
commit = <exact 40-hex>
tree = <exact 40-hex>
material findings = 0
```

Только после этого отдельным решением можно создать implementation branch прямо
от exact accepted contract head и заморозить указанный three-file allowlist.

До acceptance:

```text
CL4 CONTRACT = CANDIDATE
CL4 IMPLEMENTATION = BLOCKED
PROVIDER ACCESS = NOT AUTHORIZED
RUNTIME ADOPTION = NOT AUTHORIZED
EXPERIMENT = NOT AUTHORIZED
```
