# V3.10 CL4 — Opening proof + shadow reconciliation: bounded contract rescope R1

Статус: `RESCOPE CONTRACT CANDIDATE / IMPLEMENTATION BLOCKED / NO RUNTIME AUTHORITY`.

Этот документ является единственным разрешённым изменением CL4-RS1. Original
accepted contract `4dab154f...` не переписывается: этот successor заменяет его
только для будущей implementation. Rejected candidate `b5d6912f...` остаётся
evidence-only. Документ не является acceptance, implementation, publication,
migration, runtime adoption или разрешением provider/private-account access.

## 1. Exact predecessor и lineage

CL4-RS1 начинается только от original accepted CL4 contract:

```text
branch = agent/v3-10-clean-cl4-contract-rescope-r1
rescope predecessor commit = 4dab154fb6ed096aa23dc3f0fa0987859b598559
rescope predecessor tree = 7f0a73423a4580453f75ebeba979b173b2250e3c
stable-line CL3 ancestor = 4340c5d517dcece4f7db20b7cfc21e602c3efddc
stable-line CL3 tree = bd40cd3b7237434ea2754f6f7c6eaaed2c02637f
HEAD at branch creation = rescope predecessor commit
merge-base(HEAD, rescope predecessor) = rescope predecessor commit
ahead = 0
behind = 0
worktree = clean
```

Governance disposition:

```text
4dab154f... = ACCEPTED CONTRACT / SUPERSEDED FOR IMPLEMENTATION BY CL4-RS1
b5d6912f... = REJECTED / EVIDENCE-ONLY / NOT ACCEPTED
stable line = unchanged at 4340c5d...
fixed implementation finding set = CL4-I-R1-01..04
```

`main`, historical branches and Issue #52 are not integration authority for CL4.
CL1–CL3 source and CL2 persistent schema remain immutable.

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

CL4 contract version 2 обязан дать только:

1. pure snapshot adapter одного caller-supplied `GetPortfolio` response;
2. exact `totalAmountCurrencies -> CL1 Money` через принятый CL3 codec;
3. immutable privacy-safe `BrokerCashProof V2`, HMAC которого напрямую связывает
   cash, `as_of` и все detached authoritative identity fields;
4. единственный opening mode `FROM_NOW`;
5. deterministic create-once opening observation/transaction/record graph;
6. exact byte-equivalent baseline witness без нового CL2 store-id/schema;
7. explicit, confirmation-gated append через существующий CL2 API;
8. deterministic expected-cash reduction из validated CL2 export;
9. exact zero-tolerance broker-vs-ledger shadow reconciliation;
10. finite discrepancy taxonomy, completeness evidence и fail-closed candidate
    только для отдельного adoption review.

Нормативный поток:

```text
caller-supplied GetPortfolio response
-> bounded immutable response snapshot
-> exact CL3 Money codec
-> BrokerCashProof V2
-> FROM_NOW OpeningPlan V2 + exact baseline witness
-> explicit shadow-ledger acceptance through CL2
-> derived OpeningRecord V2
-> LedgerCashProjection V2
-> CashReconciliation V2
-> AdoptionCandidate V2 requiring a separate locked gate
```

Fixed finding closure map:

```text
CL4-I-R1-01 -> sections 10-11: direct detached-proof V2 binding
CL4-I-R1-02 -> sections 12-14: exact baseline witness and graph predicates
CL4-I-R1-03 -> section 20: literal public-boundary first-failure order
CL4-I-R1-04 -> sections 21-22: frozen vectors and full adversarial matrix
CL4-RS1-R1-01 -> sections 7, 13-14, 19-20, 22: pre-write capacity admission
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
CL4_CONTRACT_VERSION = 2
BROKER_CASH_PROOF_VERSION = 2
OPENING_PLAN_VERSION = 2
OPENING_RECORD_VERSION = 2
LEDGER_CASH_PROJECTION_VERSION = 2
CASH_RECONCILIATION_VERSION = 2
ADOPTION_CANDIDATE_VERSION = 2
CL4_OPENING_CONTENT_VERSION = 2
CL4_CROSS_LANGUAGE_FIXTURE_VERSION = 2
CL2 codec descriptor codec_version = 1
CL2 codec descriptor version = 1
MAX_PROOF_AGE_NS = 120000000000
MAX_RESPONSE_DEPTH = 16
MAX_RESPONSE_NODES = 100000
MAX_RESPONSE_CANONICAL_BYTES = 1048576
MAX_MAPPING_KEYS = 4096
MAX_STRING_SCALARS = 4096
MAX_KEY_SCALARS = 128
MAX_LEDGER_EXPORT_BYTES = 16777216
MAX_BASELINE_WITNESS_BYTES = 16777216
MAX_LEDGER_OBJECTS = 100000
CL2_MAX_REVISION = 9223372036854775807
OPENING_STORE_REVISION_RESERVE = 2
OPENING_LEDGER_REVISION_RESERVE = 1
RECONCILIATION_DELTA_MAX_ABS_MINOR_UNITS = 18446744073709551616999999998
```

CL4 V1 artifacts fail closed with `VERSION_UNSUPPORTED`; V1-to-V2 migration or
compatibility parser does not exist. The CL2 codec descriptor remains version 1
because that version is frozen by CL2; V2 separation is expressed by exact
`codec_id = CL4_FROM_NOW_OPENING_V2`, content `contract_version = 2` and the CL4
DTO versions above.

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

`response_canonical_sha256 = SHA256(exact bounded snapshot bytes)`. Detached
proof identity V2 — HMAC exact preimage:

```json
{
  "account_scope_sha256": "<64 lowercase hex>",
  "as_of": "YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ",
  "cash": "<nested exact CL1 Money object>",
  "domain": "v3.10-cl4-broker-cash-proof-identity",
  "environment": "SANDBOX",
  "identity_key_id": "<TOKEN>",
  "provider": "TBANK",
  "response_canonical_sha256": "<SHA-256 exact response snapshot bytes>",
  "rpc": "tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
  "version": 2
}
```

HMAC напрямую связывает domain/version, environment, account scope, key ID,
response SHA, full canonical Money и `as_of`. Изменение cash даже на один nano,
`as_of` или любого другого поля preimage обязано менять HMAC.

Version 2 поддерживает только `BrokerEnvironment.SANDBOX` и CL1 `RUB/scale=9`.
`PRODUCTION`, другая currency/scale и non-positive opening не расширяются
автоматически.

`BrokerCashProof` — frozen/slotted object:

```text
account_scope_sha256: str
environment: BrokerEnvironment
as_of: str
cash: Money
response_canonical_sha256: str
proof_identity_sha256: str
identity_key_id: str
response_complete: bool = True
version: int = 2
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
  "proof_identity_sha256": "<HMAC-SHA-256>",
  "provider": "TBANK",
  "response_canonical_sha256": "<SHA-256>",
  "response_complete": true,
  "rpc": "tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
  "version": 2
}
```

`sha256` равен plain SHA-256 proof canonical bytes. Proof не содержит raw
response/account/token. `as_of` — caller-supplied observation-completion time; он
не является provider timestamp или cryptographic attestation. Build требует:

```text
as_of <= evaluated_at
evaluated_at - as_of <= MAX_PROOF_AGE_NS
```

Сравнение выполняется integer nanoseconds без wall clock read. Все boundaries,
принимающие proof и identity key, воспроизводят V2 HMAC из detached fields.
Mismatch даёт `PROOF_IDENTITY_INVALID`; proof version 1 даёт
`VERSION_UNSUPPORTED` до key/HMAC validation.

## 11. Opening mode, codec и SourceIdentity

`OpeningMode` содержит только `FROM_NOW`. Historical backfill отсутствует.

`CL4_OPENING_CODEC`:

```text
codec_id = CL4_FROM_NOW_OPENING_V2
codec_version = 1
version = 1
schema_sha256 = 1f15c6486dd348bdcf8f2b725111b01e583404efe5717ab5df0b7968d87bbfe3
descriptor_sha256 = d80e3ccde02d469f5bd99d9884cd36524f1d11069d2ce8bab08e9c0b65b3659d
```

Exact `schema_json_ascii`:

```json
{"domain":"v3.10-operation-inbox-codec-schema","fields":[{"allowed_values":null,"key":"account_scope_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"baseline_export_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"broker_cash_proof_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"contract_version","kind":"INTEGER","max_scalars":null,"maximum":"2","minimum":"2","required":true},{"allowed_values":null,"key":"cutoff","kind":"STRING","max_scalars":"30","maximum":null,"minimum":null,"required":true},{"allowed_values":["SANDBOX"],"key":"environment","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"generation","kind":"INTEGER","max_scalars":null,"maximum":"1","minimum":"1","required":true},{"allowed_values":null,"key":"identity_key_id","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"ledger_head_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"ledger_revision","kind":"STRING","max_scalars":"19","maximum":null,"minimum":null,"required":true},{"allowed_values":["FROM_NOW"],"key":"mode","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":["RUB"],"key":"opening_currency","kind":"STRING","max_scalars":"3","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"opening_minor_units","kind":"STRING","max_scalars":"29","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"proof_identity_sha256","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"store_revision","kind":"STRING","max_scalars":"19","maximum":null,"minimum":null,"required":true}],"version":1}
```

Opening sanitized content имеет exact поля schema и значения:

- `contract_version = 2`, `generation = 1`;
- account/proof/proof-identity/baseline-export/head hashes — lowercase SHA-256;
- `cutoff = proof.as_of`;
- `environment = SANDBOX`, `mode = FROM_NOW`;
- `identity_key_id` — exact proof key ID;
- revisions — canonical decimal strings;
- opening currency/minor units — exact CL1 values.

Source scope HMAC preimage:

```json
{"account_scope_sha256":"<hash>","currency":"RUB","domain":"v3.10-cl4-opening-source-scope","environment":"SANDBOX","generation":1,"identity_key_id":"<TOKEN>","mode":"FROM_NOW","version":2}
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
{"account_scope_sha256":"<hash>","baseline_export_sha256":"<hash>","broker_cash_proof_sha256":"<hash>","domain":"v3.10-cl4-opening-provenance","identity_key_id":"<TOKEN>","proof_identity_sha256":"<hash>","source_content_sha256":"<hash>","version":2}
```

`InboxObservation.observed_at = proof.as_of`. Exact logical source включает key
version и уникален per `account/environment/currency/mode/generation/key ID`;
changed content при том же key становится CL2 `SOURCE_CONTENT_CONFLICT`. Key
rotation в CL4 V2 не поддерживается. Независимая cross-key create-once проверка
всего ledger graph запрещает второй opening для account/environment/currency.

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
   `ledger_transitions`, также не более `MAX_LEDGER_OBJECTS`;
5. every nested CL1/CL2 canonical object parse/round-trip byte-identically;
6. wrapper SHA/source/economic/logical fields equal derived values;
7. unique hashes/logical sources/provenance links;
8. complete contiguous inbox event chains и legal terminal status;
9. complete contiguous `LedgerHead` chain from exact CL2 genesis;
10. every ordinary transition resolves one ordinary transaction;
11. every correction transition resolves one valid `LedgerCorrectionBundle`;
12. reversal/correction rows occur only through their exact bundle transition;
13. для target account все V2 CL4 source-scope/provenance HMAC воспроизводятся
    из content и supplied key; V1 codec/content/artifacts отвергаются;
14. final revisions/head equal export top-level values;
15. unreferenced, duplicated, dangling или unknown graph elements fail closed.

CL4 revalidation не меняет CL2 bytes или semantics. Это defense-in-depth для
caller-supplied export, а не второй persistence implementation.

`ledger_export_sha256 = SHA256(exact export bytes)`.

Для acceptance `OpeningPlan V2` несёт exact immutable `baseline_export_bytes`.
Witness обязан пройти все проверки выше, иметь length
`1..MAX_BASELINE_WITNESS_BYTES`, а его SHA-256 обязан byte-for-byte совпасть с
`plan.baseline_export_sha256`. Hash без этих bytes не считается доказательством
baseline lineage. CL4 не добавляет CL2 store-id, table, column или schema version.

Два stores с byte-identical validated export считаются одной semantic baseline;
физическая instance identity намеренно не вводится. Перенос staged observation в
store с любым отличающимся inbox/status/ledger/codec graph не проходит exact
baseline-extension predicate section 14 даже при совпавших revision/head.

## 13. FROM_NOW OpeningPlan

`prepare_from_now_opening` требует fresh positive BrokerCashProof V2, valid export
и отсутствие любого accepted `OPENING_BALANCE` для target account/currency.
Pre-existing non-opening ledger history допускается и входит в frozen cutover
head, но не пересчитывается: это `FROM_NOW`, не historical backfill.

До preparation все target-account observations с `observed_at <= proof.as_of`
обязаны быть terminal `LEDGER_LINKED` или `REJECTED`. `OBSERVED`,
`REVIEW_REQUIRED`, broken status/link или evidence после proof `as_of` дают
`INBOX_INCOMPLETE`; никакой pending amount не добавляется к cash.

`OpeningPlan` — frozen/slotted object с immutable nested `proof`, `observation`,
`transaction` и:

```text
mode: FROM_NOW
generation: 1
pre_store_revision: int
pre_ledger_revision: int
pre_ledger_head_sha256: str
baseline_export_sha256: str
baseline_export_bytes: bytes = field(repr=False)
version: int = 2
```

`baseline_export_bytes` является bounded validation witness. Он участвует в
dataclass equality, но не входит raw bytes в `repr`, error/evidence или canonical
plan. Любая boundary заново проверяет exact witness bytes и их hash. Canonical
identity содержит только stable hashes/values:

```json
{
  "account_scope_sha256": "<hash>",
  "baseline_export_sha256": "<hash>",
  "broker_cash_proof_sha256": "<hash>",
  "domain": "v3.10-cl4-opening-plan",
  "environment": "SANDBOX",
  "generation": 1,
  "mode": "FROM_NOW",
  "observation_sha256": "<hash>",
  "opening_money": "<nested CL1 Money>",
  "pre_ledger_head_sha256": "<hash>",
  "pre_ledger_revision": "<decimal>",
  "pre_store_revision": "<decimal>",
  "transaction_sha256": "<hash>",
  "version": 2
}
```

До возврата plan выполняется pure prospective-capacity admission. Она строит в
memory exact CL2 canonical bytes двух ожидаемых successor states из validated
baseline graph и уже построенных plan artifacts:

```text
prospective STAGED = baseline
  + exact CL4 V2 descriptor, iff его нет в baseline registry
  + exact observation wrapper with current_status = OBSERVED
  + store_revision delta = 1

prospective COMMITTED = baseline
  + exact descriptor-if-absent
  + exact observation wrapper with current_status = LEDGER_LINKED
  + exact CL2 LEDGER_TRANSACTION_ACCEPTED status event
  + exact transaction wrapper and provenance link
  + exact TRANSACTION LedgerHead transition
  + store_revision delta = 2
  + ledger_revision delta = 1
```

Знаки `+ 1/+ 2` выше означают checked integer addition. Admission обязана до
первого durable write доказать одновременно:

1. `pre_store_revision <= CL2_MAX_REVISION - OPENING_STORE_REVISION_RESERVE`;
2. `pre_ledger_revision <= CL2_MAX_REVISION - OPENING_LEDGER_REVISION_RESERVE`;
3. каждая array в prospective STAGED и COMMITTED имеет length
   `0..MAX_LEDGER_OBJECTS`;
4. aggregate seven-array object count каждого prospective state не превышает
   `MAX_LEDGER_OBJECTS`;
5. exact canonical bytes каждого prospective export имеют length
   `1..MAX_LEDGER_EXPORT_BYTES`;
6. prospective head/revisions/wrappers round-trip через принятые CL1/CL2
   canonical constructors и равны exact plan artifacts.

Baseline может находиться на общем CL4 limit только если оба successor states
также помещаются в limit. Inclusive maximum допустим для successor, maximum+1
запрещён. Failure любого capacity predicate даёт
`OPENING_CAPACITY_EXHAUSTED`; plan не возвращается и writes равны нулю.

Эта assembly является только pure admission oracle для двух fixed CL4 deltas: она
не открывает store, не меняет CL2 graph и не создаёт альтернативный persistence
format. `accept_from_now_opening` независимо повторяет admission из plan witness
до первого write, а оба read-back обязаны byte-for-byte совпасть с рассчитанным
STAGED либо COMMITTED export. Любое отличие обрабатывается state/CAS rules section
14, а не расширением preview.

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

Exact string сравнивается byte-for-byte. Это подтверждает только shadow-ledger
opening, не runtime adoption.

Перед вызовом store должен быть создан/открыт с registry, который разрешает exact
`CL4_OPENING_CODEC` вместе со всеми persisted codecs. Отсутствующий или
конфликтующий descriptor нормализуется в `PERSISTENCE_FAILURE`; direct schema
write со стороны CL4 отсутствует.

`OpeningAcceptance` содержит:

```text
disposition: OPENING_APPENDED | OPENING_ALREADY_PRESENT
record: OpeningRecord
store_revision: int
ledger_revision: int
ledger_head_sha256: str
```

До любого write функция валидирует полный plan, exact baseline witness, получает
current `store.export_bytes()` и классифицирует его по полному graph. Разрешены
только три состояния относительно witness:

1. **ABSENT** — current export byte-for-byte равен `baseline_export_bytes`;
   store/ledger revisions и head равны plan pre-fields; target opening отсутствует.
2. **STAGED_OBSERVATION** — current graph является exact baseline graph плюс:
   exact V2 codec descriptor, только если его не было в baseline registry; exact
   plan observation в status `OBSERVED`; `store_revision = pre + 1`. Ledger arrays,
   revision/head и все прочие arrays byte-equivalent baseline. Plan transaction,
   link и LEDGER_LINKED event отсутствуют.
3. **COMMITTED_OPENING** — current graph является exact baseline graph плюс exact
   descriptor-if-needed, plan observation, один CL2-generated LEDGER_LINKED event,
   exact provenance link, exact plan transaction и exact ordinary transition;
   `store_revision = pre + 2`, `ledger_revision = pre + 1`, head равен exact
   transition head. Никакой иной delta не допускается.

Structural comparison нормализует только deterministic CL2 array ordering; он не
игнорирует ни один codec/inbox/status/transaction/link/bundle/transition object.
Совпадения revisions/head без exact graph недостаточно. Любой lower/higher/equal
revision store с иным graph, включая same ledger head с иным inbox/status graph,
даёт `BASELINE_STALE` до нового write.

После exact state classification, opening uniqueness/amount и baseline relation,
но до первого нового write функция повторяет prospective-capacity admission
section 13. Поэтому foreign current graph всегда даёт `BASELINE_STALE` раньше
capacity reason; exact ABSENT/STAGED/COMMITTED с недостаточным headroom даёт
`OPENING_CAPACITY_EXHAUSTED` и zero write этого вызова.

В `COMMITTED_OPENING` exact replay возвращает `OPENING_ALREADY_PRESENT` без write
и без current-age requirement, но V2 proof identity всё равно проверяется.
`STAGED_OBSERVATION` может завершиться с тем же plan после proof expiration:
freshness была проверена перед первым append, а exact staged graph не меняет
cutoff/content. Different proof/plan/opening graph даёт `OPENING_CONFLICT`.

В `ABSENT` непосредственно перед первым append proof обязан быть fresh. Затем
вызывается CL2 `append_observation` с `expected_store_revision =
pre_store_revision`. CAS failure даёт `OPENING_PLAN_STALE` и zero CL4 write.
Read-back обязан byte-for-byte совпасть с prospective STAGED либо prospective
COMMITTED, если same-plan caller уже завершил second step. Из exact STAGED
вызывается `append_transaction` с current store/ledger revisions. Second CAS
failure оставляет только staged observation и zero opening economic effect.
Capacity failure не может впервые возникнуть после observation commit: оба
prospective exports и обе revision additions уже проверены до него. Automatic
retry loop отсутствует.

После второго read-back требуется exact COMMITTED graph и byte-identical derived
record, иначе `POSTCONDITION_FAILED`. Повтор exact acceptance сразу после success
идемпотентен и не пишет.

Допустимые durable states:

```text
exact baseline with no CL4 opening delta
exact staged OBSERVED opening evidence with zero economic effect
exact committed LEDGER_LINKED opening graph
```

Crash до observation commit оставляет baseline; между commits — exact staged;
после transaction commit/lost response — exact committed. Если чужая mutation
произошла до первого append, caller получает `BASELINE_STALE`/CAS stale, затем
строит новый plan от нового baseline. Если чужая mutation сохранилась после exact
CL4 staged append, append-only CL2 не позволяет безопасно удалить/rewrite stage:
CL4 возвращает `BASELINE_STALE`, не пишет transaction и требует отдельного future
remediation authority. Оно не маскируется словом restage.

Edit/delete/reset/reopen/correct-opening API отсутствует. Correction bundle,
reversal или второй `OPENING_BALANCE` target account/currency делает весь CL4
view `OPENING_CONFLICT`.

## 15. Derived OpeningRecord

CL4 не добавляет table/schema. `OpeningRecord V2` всегда заново выводится из exact
validated CL2 graph: one V2 CL4 observation, one exact CL2 provenance link и one
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
  "version": 2
}
```

Record SHA-256 — hash этих bytes. Baseline fields берутся byte-for-byte из plan;
`baseline_ledger_export_sha256 = plan.baseline_export_sha256`. Accepted revision
— exact ordinary transition revision opening transaction. Observation content,
SourceIdentity, proof V2, transaction postings и transition revision обязаны
взаимно совпадать; любое broken/dangling/extra relation fail closed.

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
version: int = 2
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
  "version": 2
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
version: int = 2
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
  "version": 2
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
  "version": 2
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
PROOF_IDENTITY_INVALID
LEDGER_EXPORT_INVALID
LEDGER_GRAPH_INVALID
LEDGER_REVISION_INVALID
RECONCILIATION_INVALID
INBOX_INCOMPLETE
OPENING_AMOUNT_UNSUPPORTED
OPENING_MISSING
OPENING_CONFLICT
BASELINE_STALE
OPENING_CAPACITY_EXHAUSTED
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
baseline_export_sha256, ledger_export_sha256,
ledger_head_sha256, reconciliation_sha256
```

Absent fields опускаются. Raw response/account/token/key/path/SQL/provider text,
object repr, arbitrary exception text, canonical raw response bytes и
`baseline_export_bytes` запрещены во всех errors/evidence/repr.

## 20. Ordered failure priority

Common detached-object order для всех public boundaries:

1. exact outer/container argument types;
2. exact CL4 DTO version, затем mode/environment enum;
3. account/hash/token/scalar grammar и bounded nested canonical shape;
4. timestamp grammar/order, completeness и freshness, когда branch требует age;
5. identity-key bytes grammar;
6. V2 proof/source/provenance HMAC identity;
7. response/export bounds;
8. response/export exact schema/canonical graph;
9. nested CL3 Money / CL1 / CL2 identity;
10. для `prepare_from_now_opening` и `accept_from_now_opening` — prospective
    STAGED/COMMITTED revision, object и byte capacity;
11. opening/projection/reconciliation semantic construction.

Поэтому forged proof `version = 1` вместе с bad key всегда даёт
`VERSION_UNSUPPORTED`. Freshness предшествует HMAC: stale proof с syntactically
valid wrong key/HMAC всегда даёт `PROOF_STALE`. Invalid proof scalar grammar или
invalid timestamp сохраняет более раннюю structural/timestamp reason.

Exact public-boundary refinements:

- `build_broker_cash_proof`: argument types -> environment/account/key-id grammar
  -> timestamp/completeness -> key bytes -> response bounds/schema -> CL3 Money ->
  V2 identity/proof construction;
- `prepare_from_now_opening`: proof type/version -> scalar/nested structure ->
  timestamp/freshness -> key bytes/HMAC -> export bounds/schema/graph -> inbox and
  opening pre-state -> prospective capacity admission -> plan;
- `project_shadow_cash`: argument types/environment/account/timestamp/key grammar
  -> export bounds/schema/graph -> opening uniqueness -> arithmetic/completeness;
- `reconcile_shadow_cash`: proof type/version/structure -> timestamp/freshness ->
  key bytes/HMAC -> export/projection -> reconciliation;
- `build_adoption_candidate`: reconciliation and nested DTO types/versions ->
  recorded timestamp/freshness -> key/HMAC -> export/recomputed graph -> candidate.

Acceptance boundary:

1. confirmation exact string;
2. plan exact type/version and nested DTO versions;
3. plan canonical consistency, baseline witness type/bound/hash;
4. evaluated timestamp grammar;
5. store type/open state и current sanitized export validation;
6. exact ABSENT/STAGED/COMMITTED structural classification;
7. proof freshness only for ABSENT;
8. identity key grammar and V2 proof/source/provenance HMAC;
9. opening uniqueness/amount and exact baseline relation;
10. repeated prospective capacity admission;
11. CL2 observation CAS append/read-back;
12. CL2 transaction CAS append/read-back;
13. exact OpeningRecord/postcondition.

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
baseline export = exact empty CL2 genesis export
post-opening export = baseline + exact V2 opening observation + transaction
```

Exact proof-identity preimage bytes:

```text
{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111","as_of":"2026-01-02T03:04:05.123456789Z","cash":{"amount":"123.500000000","currency":"RUB","domain":"v3.10-money","minor_units":"123500000000","scale":9,"version":1},"domain":"v3.10-cl4-broker-cash-proof-identity","environment":"SANDBOX","identity_key_id":"CL4_TEST_KEY","provider":"TBANK","response_canonical_sha256":"204dba39888167e2384d8188817a5ad4a01f132a405a9b7b06f75f76aceed3ce","rpc":"tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio","version":2}
```

Expected identities:

```text
response canonical SHA-256 = 204dba39888167e2384d8188817a5ad4a01f132a405a9b7b06f75f76aceed3ce
proof identity HMAC-SHA-256 = d21e770a4268fafb4ec39b0cc9d21f3c2155801829cdd5e106f5588dc4e4efb1
broker cash minor_units = 123500000000
broker cash proof SHA-256 = 588abe0b9d3979c300d8f681adccdf2776bc20c8dd4997e66ff8edaaa1e8010f
CL4 V2 codec schema SHA-256 = 1f15c6486dd348bdcf8f2b725111b01e583404efe5717ab5df0b7968d87bbfe3
CL4 V2 codec descriptor SHA-256 = d80e3ccde02d469f5bd99d9884cd36524f1d11069d2ce8bab08e9c0b65b3659d
baseline CL2 export SHA-256 = 9d00fefe18e104500df70063781b708c5ed90816dba8a9058dddc7ecaedf0eb7
baseline ledger head SHA-256 = 6ee5e86309122771bcaca40bb57771c2378c30d79b079e5431b227300a673d37
opening source scope HMAC-SHA-256 = c6c936312caa64f076007b86d13941067ccee1f9e2a2cee06b3db5d272818410
opening content SHA-256 = 9673dea00bc76b4865ceff9424e63a097a0419f2afd29e4475b99e19e4471f3e
opening provenance HMAC-SHA-256 = 9c212565e79474e62260d8da2276339c4509652cfff31d2614cf376beab0e6f7
opening observation SHA-256 = a539c06c4ce0d2c0c1e3c3e29e92b0d322c0b253ed9e0c83d3fa18d4ea955ec3
opening transaction SHA-256 = 7c49d302b3a0cc20e0114ba56529f94dae4fff61501b9f7ce45bb435e1058ee5
opening plan SHA-256 = d0312594d9ca205bc2a1876d4e865e6dc3b020c22992bf220724d4c4b74d6793
staged CL2 export SHA-256 = c5a6ee32a9406f6ddaec9fb2556bf4ad0615710c278bff159a04fdd1e3082550
post-opening CL2 export SHA-256 = f7d0e10b4133ff989425fa276b1b3e18ef440668b5e68fcb0f2df67ad03484a2
post-opening ledger head SHA-256 = 53dcc785fd962de251ce3745138866005584dd6ba179ae3e32a2d97b7fde6999
opening record SHA-256 = 71109fceb92f5f3789e8e323e000fe1226266cb6733bf6072d6d33375a7747ce
complete projection SHA-256 = 022c5f33817808e0dbb54b994d2483fba230e6dec74148cb157100999f6bc791
matched reconciliation SHA-256 = bd8dd76c3525937d121b63f272d67301362446d6835dd64e06e4c1baf5ac77f8
+1 nano proof SHA-256 = 126e873505ee8c50e20ad2e822669938311e5ca392a94060dc776b5c3dd54425
+1 nano reconciliation SHA-256 = 89c795d1827d2b03831fcd96a1c8ac1e54175c3639000f7c2af8e41fe1e7a407
unresolved observation SHA-256 = 19417b801266e83c5a793c128445033409bec5304b0c769536868d523a964d0a
incomplete export SHA-256 = 18883152e238dc07c98dccb96f04a2ba5b853340a9c483a922b888176fc89152
incomplete projection SHA-256 = a3093982a5ecda11bfb8cec1b5e02ee4844fafc5328fec4998025527a80112df
incomplete reconciliation SHA-256 = 3812a53b15181b104849a5622596068aa73898577f300e1c34739f0e23366375
adoption candidate SHA-256 = 017a995a90ab719da91edb0f89fdeecd7350c485f51ac279216512c5ddac20a3
```

Для этого vector pure admission получает exact staged/post-opening export hashes
выше; обе revision additions, все array/aggregate counts и обе byte lengths
находятся внутри inclusive bounds.

Exact canonical response bytes:

```text
{"totalAmountCurrencies":{"currency":"RUB","nano":500000000,"units":"123"}}
```

Fixture version 2 обязана содержать exact canonical bytes/dicts/hashes для proof
V2, codec/content, baseline witness hash, observation, transaction, plan, staged
export, record, matched/discrepancy/incomplete projection/reconciliation и
candidate. Independent fixture construction пересчитывает значения из contract;
runtime output не является oracle.

## 22. Mandatory acceptance/adversarial matrix

- `V310-CL4-01`: exact exports, V2 versions, signatures, immutable/slotted DTOs,
  hidden bounded witness и import side-effect freedom;
- `V310-CL4-02`: one-response snapshot, nested bounds/cycle/alias/custom types,
  mutation-after-call, exact root/child depth, node/key counting, canonical bytes;
- `V310-CL4-03`: exact `totalAmountCurrencies` Money happy/bounds/sign/currency;
- `V310-CL4-04`: V2 HMAC known answer и independent mutations cash `+1/-1 nano`,
  `as_of`, response SHA, account scope, environment, key ID, HMAC and version;
- `V310-CL4-05`: timestamp 9-digit ordering, future/stale/boundary age and exact
  `stale proof + bad HMAC -> PROOF_STALE` precedence;
- `V310-CL4-06`: CL2 export canonical/schema/object/head/link/bundle corruption,
  each-array и aggregate object bounds, exact max/max+1 export bytes;
- `V310-CL4-07`: deterministic V2 plan, witness/hash, codec/content/source/
  provenance, positive amount and exact CL1 postings;
- `V310-CL4-08`: baseline transfer matrix: lower/higher/equal revision with other
  graph; same ledger head with different inbox/status; different export hash;
  every case `BASELINE_STALE` and zero write;
- `V310-CL4-09`: ABSENT -> STAGED -> COMMITTED, faults at both CL2 boundaries,
  exact crash/lost-success replay and immediate repeated acceptance idempotency;
- `V310-CL4-10`: mutation between validation and each CAS; CAS failure creates no
  opening transaction/economic effect; no internal retry;
- `V310-CL4-11`: concurrent same/different plan, create-once, exact staged-only
  recovery and foreign-delta fail-closed behavior;
- `V310-CL4-12`: opening record broken proof/source/observation/transaction/
  revision/head links, extra and dangling graph elements;
- `V310-CL4-13`: projection over every CL1 classification and account scope;
- `V310-CL4-14`: correction bundle net effect once; canonical/bundle corruption;
- `V310-CL4-15`: late pre-cutoff, proof-before-ledger effects/evidence and every
  unresolved/rejected/linked inbox status branch;
- `V310-CL4-16`: exact zero, `+1/-1` minor-unit reconciliation taxonomy;
- `V310-CL4-17`: every authoritative nested DTO field mutated at least once with
  outer hashes recomputed, including witness bytes/hash and proof fields;
- `V310-CL4-18`: adoption booleans/dispositions; no apply/adopt/migrate callable;
  mismatched export/head/key and stale-at-recorded-evaluation rejected;
- `V310-CL4-19`: exact max accepted/max+1 rejected for response depth/nodes/bytes,
  mapping keys, strings, baseline witness and ledger export/object bounds;
- `V310-CL4-20`: representative multi-invalid vector for every public boundary;
  includes `V1 + bad key -> VERSION_UNSUPPORTED` and frozen primary reason;
- `V310-CL4-21`: raw account/token/key/response/witness/sentinels absent from every
  canonical object, error, repr and evidence;
- `V310-CL4-22`: no provider/network/env/clock/random/SQLite/runtime imports;
  only exact CL2 observation and transaction mutators may write;
- `V310-CL4-23`: exact three-file implementation allowlist and byte-identical
  accepted CL1/CL2/CL3 source;
- `V310-CL4-24`: accepted CL1/CL2/CL3 functional suites and full functional suite
  pass excluding only the two documented historical current-HEAD custody nodes;
- `V310-CL4-25`: prospective-capacity matrix: store revision
  `CL2_MAX_REVISION-2` accepted / `CL2_MAX_REVISION-1` rejected; ledger revision
  `CL2_MAX_REVISION-1` accepted / `CL2_MAX_REVISION` rejected;
  descriptor-present/absent;
  each-array, aggregate-object and exact canonical STAGED/COMMITTED byte limit at
  maximum accepted / maximum+1 rejected; every rejection occurs before first
  write with exact `OPENING_CAPACITY_EXHAUSTED`; foreign current graph together
  with exhausted plan capacity returns earlier `BASELINE_STALE` and zero write.

Findings `CL4-I-R1-01..04` имеют отдельные regression tests, способные
воспроизвести исходный defect до correction. Adversarial tests forge/rebuild
outer objects, reorder arrays, duplicate sources, corrupt bundle/canonical bytes,
alter nano/revision/head и проверяют literal first-failure order. Happy-path count
сам по себе не заменяет эту matrix.

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

Final CL4-RS1 contract review выполняется read-only для exact range:

```text
4dab154fb6ed096aa23dc3f0fa0987859b598559 -> <CL4-RS1 candidate head>
```

Reviewer обязан подтвердить:

- exact parent/head/tree, clean worktree и one-file contract delta;
- `4dab154f...` не переписан, `b5d6912f...` не является ancestor implementation;
- consistency V2 schema, canonical preimages и всех known-answer hashes;
- proof HMAC directly binds cash, `as_of`, response/account/env/key/version;
- V1 proof/plan/record/projection/reconciliation/candidate fail closed;
- baseline witness bounded, canonical, hidden from repr/evidence and exact-hashed;
- ABSENT/STAGED/COMMITTED predicates сравнивают весь CL2 graph и не принимают
  same-head/different-inbox transfer;
- crash recovery и CAS branches finite, append-only и не создают partial economic
  opening; post-stage foreign mutation explicitly fails closed;
- revision/object/byte headroom обоих prospective states доказан до первого write,
  exact maximum/max+1 boundaries имеют zero-write regressions;
- first-failure order даёт exact required multi-invalid reasons;
- full adversarial matrix закрывает `CL4-I-R1-01..04`;
- `MATCHED`/candidate не открывают CashAvailability/execution/adoption;
- future implementation allowlist содержит ровно три frozen paths.

Finding именуется `CL4-RS1-R1-NN`. Допустим максимум один bounded correction
commit в этом же one-file allowlist, затем finding-scoped closure review exact
successor. Новый unrelated material finding требует `RESCOPE/ABORT/DEFER`, а не
recursive correction loop.

## 25. Contract acceptance и exit state

Green validation или отсутствие review findings сами по себе не acceptance.
Требуется отдельное явное решение:

```text
CL4 RESCOPE CONTRACT ACCEPTED
commit = <exact 40-hex>
tree = <exact 40-hex>
material findings = 0
supersedes for implementation = 4dab154fb6ed096aa23dc3f0fa0987859b598559
```

Только после этого отдельным решением можно создать новую implementation branch
прямо от exact accepted CL4-RS1 head, replay rejected three-file patch и сделать
один bounded correction commit для fixed finding set `CL4-I-R1-01..04`.

До acceptance:

```text
CL4-RS1 CONTRACT = CANDIDATE
CL4 IMPLEMENTATION = BLOCKED
b5d6912f... = REJECTED / EVIDENCE-ONLY
STABLE LINE = 4340c5d...
PROVIDER ACCESS = NOT AUTHORIZED
RUNTIME ADOPTION = NOT AUTHORIZED
EXPERIMENT = NOT AUTHORIZED
```
