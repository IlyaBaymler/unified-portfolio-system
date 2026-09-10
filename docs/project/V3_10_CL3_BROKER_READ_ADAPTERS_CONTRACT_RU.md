# V3.10 CL3 — Broker read adapters + deterministic classification: bounded contract freeze

Статус документа: `CL3 CONTRACT CORRECTION CANDIDATE / CL3-R1-01..04`.

Этот документ замораживает только наблюдаемое поведение CL3. Он не является
разрешением на реализацию, runtime adoption, сетевой запуск, запись в CL2,
принятие экономических проводок или операции с реальным счётом.

## 1. Exact predecessor и lineage

CL3 contract branch создана непосредственно от accepted и integrated CL2:

- commit: `d684186c0628d27ed452ce4f11311155fdf7a44e`;
- tree: `6afcb9bc169c6ffbf462df918bb5128e0dd9e295`;
- integration line: `program/v3-10-v4-stable-line`;
- branch: `agent/v3-10-clean-cl3-contract-freeze`;
- initial state: `HEAD = merge-base`, `ahead 0`, `behind 0`, clean.

`main` не является predecessor, integration authority или compare base для CL3.

## 2. Contract allowlist

Contract delta имеет однофайловый allowlist:

```text
docs/project/V3_10_CL3_BROKER_READ_ADAPTERS_CONTRACT_RU.md
```

Любой другой изменённый, добавленный, удалённый или переименованный путь делает
contract candidate неприемлемым.

## 3. Замороженный future implementation allowlist

После отдельного exact-head contract acceptance implementation branch может
быть создана только от принятого contract head. Полный implementation delta
ограничен тремя путями:

```text
current/trading_robot/broker_read_adapters.py
current/tests/test_v3_10_broker_read_adapters.py
current/tests/fixtures/v3_10_broker_read_adapters_vectors.json
```

Нельзя менять package initializers, CL1/CL2 implementation, runtime wiring,
dependency manifests, GUI, конфигурацию, CI или другие документы.

## 4. Mission

CL3 задаёт чистую и детерминированную границу между read-only наблюдением
T-Bank Invest API и уже принятыми CL1/CL2 value objects:

1. точный codec `MoneyValue -> CL1 Money`;
2. privacy-safe `SourceIdentity` и `CL2 InboxObservation`;
3. детерминированное решение о классификации и предложение CL1 transaction;
4. ограниченную pagination/retry/deadline машину;
5. completeness watermark с узкой семантикой;
6. закрытые причины ошибок и privacy-safe evidence.

## 5. Authority boundary

CL3 получает только authority читать страницы операций через внедрённый transport,
проверять их, строить immutable objects и возвращать их вызывающей стороне.

CL3 не получает authority:

- создавать HTTP/gRPC session, читать token/environment или выбирать account;
- вызывать provider mutation, order execution или любой write endpoint;
- считать HTTP-метод `POST`, которым провайдер экспонирует read RPC,
  разрешением на произвольный provider POST;
- вызывать `CashLedgerStore` либо иной persistence API;
- добавлять `InboxStatusEvent`, принимать transaction или correction bundle;
- утверждать current cash, opening balance, reconciliation, CashAvailability или Risk;
- менять GUI/runtime, запускать experiment или обращаться к реальному счёту;
- считать provider observation экономическим posting authority.

Возвращённый `LedgerTransaction` называется **proposal**. Его валидность по CL1
не означает принятие, запись, current-cash ownership или разрешение на дальнейшее
действие. Возвращённый `InboxObservation` также не считается сохранённым.

## 6. Нормативные зависимости

CL3 использует без переопределения:

- `current/trading_robot/cash_ledger_domain.py` — `Money`, `SourceIdentity`,
  `LedgerPosting`, `LedgerTransaction` и их причины;
- `current/trading_robot/cash_ledger_persistence.py` — `CodecDescriptor` и
  `InboxObservation`;
- accepted CL1 contract;
- accepted CL2 contract.

Если CL3 и CL1/CL2 расходятся, CL1/CL2 fail-closed rules имеют приоритет.

Provider profile заморожен по официальным `GetOperationsByCursor`,
`OperationItem`, `OperationState`, `OperationType` и `MoneyValue`. Изменение
provider schema не расширяет этот контракт автоматически.

## 7. Versions, tokens и общая canonical форма

Все CL3 canonical objects используют version `1`. JSON формируется как в CL1/CL2:

```python
json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
```

Не принимаются duplicate JSON keys, BOM, whitespace вне строк, non-ASCII bytes,
NaN/Infinity, surrogate code points или неканоническое повторное кодирование.

Общие грамматики:

- SHA-256/HMAC-SHA-256: `[0-9a-f]{64}`;
- token: `[A-Z][A-Z0-9_]{0,63}`;
- provider enum token: `[A-Z][A-Z0-9_]{0,95}`;
- canonical signed decimal: `0|-?[1-9][0-9]*`;
- positive decimal: `[1-9][0-9]*`;
- CL1 timestamp: `YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ` с реальной Gregorian date.

Python `bool` никогда не принимается как integer. Неявные coercions запрещены.

## 8. Public API surface

Implementation должна экспортировать только следующие CL3 symbols:

```text
BrokerEnvironment
BrokerDecisionKind
BrokerDecisionReason
BrokerReadReason
BrokerReadError
RetryPolicy
BrokerReadRequest
BrokerReadTransport
BrokerTransportFailureKind
BrokerTransportFailure
BrokerDecision
CompletenessWatermark
BrokerReadBatch
TBANK_OPERATION_CODEC
money_value_to_money
normalize_provider_timestamp
collect_tbank_operations
```

Другие module-level names являются private. Import модуля не должен читать
environment, файловую систему, сеть, wall clock или secrets и не должен менять
global process state.

Функции имеют exact signatures:

```python
money_value_to_money(value: object) -> Money
normalize_provider_timestamp(value: object) -> str
collect_tbank_operations(request: BrokerReadRequest) -> BrokerReadBatch
```

`BrokerReadBatch` имеет только поля `decisions: tuple[BrokerDecision, ...]` и
`watermark: CompletenessWatermark`.

## 9. Injected read transport

`BrokerReadTransport` — structural protocol/callable:

```python
transport(request_payload: Mapping[str, object], timeout_ns: int) -> Mapping[str, object]
```

Transport получает ephemeral payload с raw `accountId` и raw page cursor. Он
предоставляется вызывающей стороной. CL3 module не импортирует provider SDK,
`requests`, `httpx`, token helpers, `tbank_sandbox` или runtime adapters.

Разрешён единственный семантический RPC:

```text
tinkoff.public.invest.api.contract.v1.OperationsService/GetOperationsByCursor
```

Факт, что REST binding этого read RPC использует HTTP POST, не разрешает модулю
создавать HTTP request самостоятельно. Никакие другие method/service names не
принимаются.

`BrokerTransportFailureKind` имеет закрытые значения:

```text
TIMEOUT
CONNECTION_INTERRUPTED
HTTP_STATUS
```

`BrokerTransportFailure` — exception только с полями `kind` и optional plain
integer `http_status` в диапазоне `100..599`. Status обязателен только для
`HTTP_STATUS`, не может быть `200..299` и запрещён для двух других kinds.
`str/repr` содержат только kind
и status; exception chaining запрещён. Response body, headers, URL, token,
account ID и исходный exception text в CL3 не передаются.

## 10. BrokerReadRequest

`BrokerReadRequest` immutable и содержит:

- `environment`: ровно `PRODUCTION` или `SANDBOX`;
- `raw_account_id`: непустая Unicode string длиной 1..256 scalar values;
- `identity_key`: bytes длиной 32..64, не сохраняется в output;
- `identity_key_id`: token;
- `from_inclusive`, `to_exclusive`: CL1 canonical timestamps;
- `limit`: plain int `1..1000`;
- `max_pages`: plain int `1..100`;
- `max_items`: plain int `1..100000`;
- `absolute_deadline_ns`: positive plain int от injected monotonic clock;
- `retry_policy`: `RetryPolicy`;
- `transport`: `BrokerReadTransport`;
- `monotonic_ns`: injected callable без аргументов;
- `wait_ns`: injected callable, принимающий delay как plain int nanoseconds.

Требуется `from_inclusive < to_exclusive`.
Поля `raw_account_id` и `identity_key` имеют `repr=False`; ни repr request, ни
любой derived repr не раскрывает их. Callables валидируются как callable.

Provider payload каждой страницы имеет exact keys:

```json
{"accountId":"<raw>","cursor":"<raw-or-empty>","from":"<from_inclusive>","limit":1000,"operationTypes":[],"state":"OPERATION_STATE_UNSPECIFIED","to":"<to_exclusive>","withoutCommissions":false,"withoutOvernights":false,"withoutTrades":false}
```

Для первой страницы `cursor` — empty string; затем exact `nextCursor` предыдущей
страницы. Остальные поля неизменны; `limit` равен request limit.

Пустой `operationTypes` и exact значения трёх `without*` обязательны. Watermark
связывает эти bits, но не приписывает им недокументированную completeness или
economic-finality семантику провайдера.

## 11. RetryPolicy и абсолютный deadline

`RetryPolicy` immutable:

- `max_attempts`: plain int `1..4`;
- `per_attempt_timeout_ns`: plain int `1..30000000000`;
- `backoff_ns`: tuple длиной `max_attempts - 1`;
- каждый backoff — plain int `0..10000000000`;
- backoff не убывает, сумма не превышает `20000000000`.

Для каждого page request:

1. прочитать injected monotonic clock;
2. если remaining `<= 0`, завершить `DEADLINE_EXCEEDED` без transport call;
3. передать `timeout_ns = min(per_attempt_timeout_ns, remaining)`;
4. retry разрешён только для `TIMEOUT`, `CONNECTION_INTERRUPTED` и HTTP
   `408, 429, 500, 502, 503, 504`;
5. перед retry заново прочитать clock; если remaining `<= backoff`, завершить
   `DEADLINE_EXCEEDED` без wait и нового call;
6. injected wait вызывается ровно с очередным `backoff_ns`; jitter/random нет;
7. после исчерпания attempts вернуть нормализованную terminal reason.

Terminal mapping exact: exhausted `TIMEOUT` -> `TRANSPORT_TIMEOUT`; exhausted
`CONNECTION_INTERRUPTED` -> `TRANSPORT_CONNECTION_INTERRUPTED`; exhausted
retryable HTTP status -> `TRANSPORT_HTTP_RETRY_EXHAUSTED`; первый non-retryable
HTTP status -> `TRANSPORT_HTTP_PERMANENT`.

Другие HTTP statuses, schema/identity/canonical errors и invalid response не
retry-ятся. Неожиданное transport exception становится `TRANSPORT_PROTOCOL_FAILURE`;
clock/wait exception — `CLOCK_FAILURE`/`WAIT_FAILURE`; non-integer, negative или
убывающее clock value — `CLOCK_INVALID`. Исходное exception не выходит наружу. Deadline общий для всех страниц и попыток. Частичный batch при любой
terminal error не возвращается.

## 12. Exact provider response profile

До field-level validation CL3 выполняет iterative bounded preflight всего
returned object graph: разрешены только acyclic JSON-like `Mapping/list/string`,
`None`, bool и plain int; mapping keys только strings; depth не больше 16;
не больше 200000 nodes; не больше 10000 элементов в одном container; не больше
16384 Unicode scalars в одной string. Cycle, alias container, иной type или
превышение границы даёт `RESPONSE_BOUNDS_EXCEEDED`. Preflight ничего не
сериализует и не включает traversed values в evidence.

Top-level response — Mapping с exact keyset:

```text
hasNext, items, nextCursor
```

- `hasNext`: bool;
- `nextCursor`: string длиной 0..4096 scalars;
- `items`: finite list, длина `0..limit`.

Каждый item — Mapping. Следующие keys обязательны:

```text
brokerAccountId
childOperations
commission
cursor
date
id
payment
quantity
quantityDone
quantityRest
state
type
```

Допустимые optional keys, которые проецируются как non-authoritative и не входят
в sanitized content:

```text
accruedInt, assetUid, cancelDateTime, cancelReason, description, figi,
classCode, instrumentKind, instrumentType, instrumentUid, name,
parentOperationId, positionUid, price, ticker, tradesInfo, yield, yieldRelative
```

Другие keys запрещены и дают `RESPONSE_SCHEMA_INVALID`. Mandatory key нельзя
заменить `null`. Optional provider text никогда не входит в hashes/evidence.

Обязательные scalar rules:

- `brokerAccountId`, `cursor`, `id`: непустые strings, максимум 4096 scalars;
- `brokerAccountId` должен byte-for-byte совпасть с request raw account ID;
- `state`, `type`: provider enum tokens;
- `quantity`, `quantityDone`, `quantityRest`: canonical signed decimal strings,
  диапазон int64;
- `childOperations`: finite list длиной `0..256`;
- `date`: timestamp по section 14;
- `payment`, `commission`: exact `MoneyValue` по section 13.

Optional `parentOperationId` может отсутствовать или быть string длиной 0..4096.
Непустое значение проецируется только как `has_parent_operation = true`; сам ID
никогда не выходит из ephemeral item. После normalization `effective_at` должен
удовлетворять `from_inclusive <= effective_at < to_exclusive`, иначе весь batch
завершается `ITEM_OUTSIDE_WINDOW` без partial result.

Contents `childOperations`, `tradesInfo` и optional nested fields не читаются,
не хешируются и не логируются. Непустой `childOperations` делает event ambiguous.

## 13. Exact T-Bank MoneyValue -> CL1 Money codec

Wire `MoneyValue` имеет exact keyset `currency, nano, units`:

- `currency`: ровно string `RUB`;
- `units`: canonical int64 decimal string;
- `nano`: plain int в `[-999999999, 999999999]`;
- `bool`, float, exponent, whitespace, leading `+`, leading zero и `-0` запрещены.

После wire validation вызывается только:

```python
Money.from_units_nano(units=int(units), nano=nano, currency=currency)
```

Нельзя самостоятельно менять знак, округлять, использовать float/Decimal или
нормализовать non-canonical pair. Все `MoneyReason` сохраняются как safe
`cause_reason` внутри `MONEY_INVALID`.

Codec применим и к `payment`, и к `commission`. CL1 поддерживает только RUB;
другая currency fail-closed, а не становится review item.

## 14. Provider timestamp -> CL1 timestamp

Provider timestamp принимается только в UTC с `Z`:

```text
YYYY-MM-DDTHH:MM:SSZ
YYYY-MM-DDTHH:MM:SS.fffZ
YYYY-MM-DDTHH:MM:SS.ffffffZ
YYYY-MM-DDTHH:MM:SS.fffffffffZ
```

Дата и время должны существовать; `24:00`, leap second, offsets, lowercase `z`,
другая длина fraction и более 9 digits запрещены. Fraction дополняется справа
нулями до 9 digits. Нулевой fraction добавляется как `.000000000`.

## 15. Privacy-safe identities

Поля `raw_account_id` и `identity_key` имеют `repr=False`. Raw account ID,
operation ID, parent ID и cursors являются sensitive ephemeral
provider identifiers. Они не могут появиться в returned objects, canonical bytes,
evidence, exception strings, logs, fixtures с production data или watermark.

Все HMAC ниже используют exact function:

```python
hmac.new(identity_key, canonical_bytes, hashlib.sha256).hexdigest()
```

### 15.1 account scope

Preimage имеет exact keyset:

```json
{"account_id":"<raw_account_id>","domain":"v3.10-cl3-account-scope","environment":"<PRODUCTION|SANDBOX>","identity_key_id":"<token>","provider":"TBANK","version":1}
```

Результат становится `SourceIdentity.account_scope_sha256`.

### 15.2 source scope

Одна CL3 observation представляет payment component одной provider operation.
`source_kind` всегда `TBANK_OPERATION`. Exact HMAC preimage:

```json
{"account_scope_sha256":"<account scope>","component":"PAYMENT","domain":"v3.10-cl3-source-scope","identity_key_id":"<token>","operation_id":"<raw id>","operation_state":"<validated state>","provider":"TBANK","version":1}
```

Результат становится `source_scope_sha256`. State входит в scope, потому что
pending/canceled/final — отдельные provider observations. Поэтому нормальный
переход `PROGRESS -> EXECUTED` не маскируется под изменение immutable CL2
observation. Изменяемость provider operation ID также не скрывается: новый ID
создаёт другой logical source; последующее обнаружение economic match остаётся
обязанностью CL2 review/persistence boundary.

### 15.3 cursor evidence и provenance

Cursor HMAC preimage:

```json
{"cursor":null,"domain":"v3.10-cl3-cursor-evidence","identity_key_id":"<token>","role":"<REQUEST|NEXT|ITEM>","version":1}
```

При наличии cursor вместо `null` используется raw string. `ITEM` всегда имеет
непустой cursor. Item provenance HMAC:

```json
{"account_scope_sha256":"<account scope>","domain":"v3.10-cl3-provenance","identity_key_id":"<token>","operation_id":"<raw id>","source_content_sha256":"<sha256>","version":1}
```

Результат становится `InboxObservation.provenance_sha256`.

`InboxObservation.observed_at` равен normalized provider `effective_at`, а не
collection clock. Поэтому неизменные operation ID и sanitized content дают
byte-identical observation при повторном чтении и перестановке между страницами.
Изменение item/page cursor меняет page-chain evidence, но не создаёт ложный CL2
`SOURCE_CONTENT_CONFLICT`. Изменение content при тех же ID и state меняет
observation и намеренно становится таким conflict. Изменение state или provider
ID создаёт новый logical source и оставляет economic-match решение следующей
CL2 boundary.

Identity key rotation меняет все keyed identities. Caller обязан менять
`identity_key_id`; CL3 не предоставляет migration или cross-key equivalence.

## 16. Frozen CL2 codec и sanitized content

`TBANK_OPERATION_CODEC.codec_id = TBANK_OPERATION_V1`, version `1`.

Exact `schema_json_ascii`:

```json
{"domain":"v3.10-operation-inbox-codec-schema","fields":[{"allowed_values":null,"key":"child_operation_count","kind":"INTEGER","max_scalars":null,"maximum":"256","minimum":"0","required":true},{"allowed_values":null,"key":"commission_minor_units","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true},{"allowed_values":["PAYMENT"],"key":"component","kind":"STRING","max_scalars":"16","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"effective_at","kind":"STRING","max_scalars":"30","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"has_parent_operation","kind":"BOOLEAN","max_scalars":null,"maximum":null,"minimum":null,"required":true},{"allowed_values":["OPERATION_STATE_CANCELED","OPERATION_STATE_EXECUTED","OPERATION_STATE_PROGRESS","OPERATION_STATE_UNSPECIFIED"],"key":"operation_state","kind":"STRING","max_scalars":"32","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"operation_type","kind":"STRING","max_scalars":"96","maximum":null,"minimum":null,"required":true},{"allowed_values":["RUB"],"key":"payment_currency","kind":"STRING","max_scalars":"3","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"payment_minor_units","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"quantity","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"quantity_done","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true},{"allowed_values":null,"key":"quantity_rest","kind":"STRING","max_scalars":"20","maximum":null,"minimum":null,"required":true}],"version":1}
```

`schema_sha256` и descriptor known answer заморожены в section 25.

Sanitized content имеет exact keyset этих двенадцати полей. `payment_minor_units` и
`commission_minor_units` — exact CL1 Money `minor_units` как canonical decimal.
`quantity`, `quantity_done/rest` — validated provider int64 decimal strings.
`has_parent_operation` — bool, полученный только из presence/nonempty parent ID.
`source_content_sha256` равен plain SHA-256 exact ASCII sanitized content.

Raw IDs/cursors, names/descriptions, instrument identifiers, nested trades,
parent/child bodies и provider payload bytes не входят в sanitized content.

## 17. Known provider OperationType profile

Known v1 names:

```text
OPERATION_TYPE_UNSPECIFIED
OPERATION_TYPE_INPUT
OPERATION_TYPE_BOND_TAX
OPERATION_TYPE_OUTPUT_SECURITIES
OPERATION_TYPE_OVERNIGHT
OPERATION_TYPE_TAX
OPERATION_TYPE_BOND_REPAYMENT_FULL
OPERATION_TYPE_SELL_CARD
OPERATION_TYPE_DIVIDEND_TAX
OPERATION_TYPE_OUTPUT
OPERATION_TYPE_BOND_REPAYMENT
OPERATION_TYPE_TAX_CORRECTION
OPERATION_TYPE_SERVICE_FEE
OPERATION_TYPE_BENEFIT_TAX
OPERATION_TYPE_MARGIN_FEE
OPERATION_TYPE_BUY
OPERATION_TYPE_BUY_CARD
OPERATION_TYPE_INPUT_SECURITIES
OPERATION_TYPE_SELL_MARGIN
OPERATION_TYPE_BROKER_FEE
OPERATION_TYPE_BUY_MARGIN
OPERATION_TYPE_DIVIDEND
OPERATION_TYPE_SELL
OPERATION_TYPE_COUPON
OPERATION_TYPE_SUCCESS_FEE
OPERATION_TYPE_DIVIDEND_TRANSFER
OPERATION_TYPE_ACCRUING_VARMARGIN
OPERATION_TYPE_WRITING_OFF_VARMARGIN
OPERATION_TYPE_DELIVERY_BUY
OPERATION_TYPE_DELIVERY_SELL
OPERATION_TYPE_TRACK_MFEE
OPERATION_TYPE_TRACK_PFEE
OPERATION_TYPE_TAX_PROGRESSIVE
OPERATION_TYPE_BOND_TAX_PROGRESSIVE
OPERATION_TYPE_DIVIDEND_TAX_PROGRESSIVE
OPERATION_TYPE_BENEFIT_TAX_PROGRESSIVE
OPERATION_TYPE_TAX_CORRECTION_PROGRESSIVE
OPERATION_TYPE_TAX_REPO_PROGRESSIVE
OPERATION_TYPE_TAX_REPO
OPERATION_TYPE_TAX_REPO_HOLD
OPERATION_TYPE_TAX_REPO_REFUND
OPERATION_TYPE_TAX_REPO_HOLD_PROGRESSIVE
OPERATION_TYPE_TAX_REPO_REFUND_PROGRESSIVE
OPERATION_TYPE_DIV_EXT
OPERATION_TYPE_TAX_CORRECTION_COUPON
OPERATION_TYPE_CASH_FEE
OPERATION_TYPE_OUT_FEE
OPERATION_TYPE_OUT_STAMP_DUTY
OPERATION_TYPE_OUTPUT_SWIFT
OPERATION_TYPE_INPUT_SWIFT
OPERATION_TYPE_OUTPUT_ACQUIRING
OPERATION_TYPE_INPUT_ACQUIRING
OPERATION_TYPE_OUTPUT_PENALTY
OPERATION_TYPE_ADVICE_FEE
OPERATION_TYPE_TRANS_IIS_BS
OPERATION_TYPE_TRANS_BS_BS
OPERATION_TYPE_OUT_MULTI
OPERATION_TYPE_INP_MULTI
OPERATION_TYPE_OVER_PLACEMENT
OPERATION_TYPE_OVER_COM
OPERATION_TYPE_OVER_INCOME
OPERATION_TYPE_OPTION_EXPIRATION
OPERATION_TYPE_FUTURE_EXPIRATION
```

Well-formed token вне списка — `UNKNOWN_OPERATION_TYPE`. Known, но не указанный
в таблице классификации section 19 — `UNSUPPORTED_OPERATION_TYPE`. Ни один из
этих случаев не получает transaction proposal.

## 18. Decision objects

`BrokerDecisionKind`:

```text
TRANSACTION_PROPOSED
REVIEW_REQUIRED
NOT_LEDGER_RELEVANT
```

`BrokerDecisionReason`:

```text
CLASSIFIED
CANCELED
PENDING
STATE_UNSPECIFIED
UNKNOWN_OPERATION_TYPE
UNSUPPORTED_OPERATION_TYPE
ZERO_CASH_EFFECT
AMOUNT_SIGN_AMBIGUOUS
MULTI_COMPONENT_AMBIGUOUS
PARTIAL_EXECUTION_AMBIGUOUS
```

`BrokerDecision` immutable и содержит exact:

- `kind`;
- `reason`;
- `observation: InboxObservation`;
- `transaction_proposal: LedgerTransaction | None`.

`TRANSACTION_PROPOSED` требует reason `CLASSIFIED` и non-null transaction.
Остальные kinds требуют null transaction. Every valid item produces exactly one
observation and one decision. Decision object не создаёт CL2 status event.

Для последующей отдельно авторизованной CL2 boundary `REVIEW_REQUIRED`
соответствует лишь предложению transition reason `REVIEW_REQUIRED`, а
`NOT_LEDGER_RELEVANT` — лишь предложению `NOT_LEDGER_RELEVANT`. CL3 не создаёт
эти events. `TRANSACTION_PROPOSED` не предлагает `LEDGER_LINKED`: такой status
может возникнуть только атомарно с отдельным accepted CL2 ledger append.

## 19. Ordered deterministic classification

После полной wire/content/identity validation применяется первый подходящий rule:

1. `OPERATION_STATE_CANCELED` -> `NOT_LEDGER_RELEVANT / CANCELED`;
2. `OPERATION_STATE_PROGRESS` -> `REVIEW_REQUIRED / PENDING`;
3. `OPERATION_STATE_UNSPECIFIED` -> `REVIEW_REQUIRED / STATE_UNSPECIFIED`;
4. state не из frozen четырёх -> hard `ENUM_TOKEN_INVALID`;
5. unknown type -> `REVIEW_REQUIRED / UNKNOWN_OPERATION_TYPE`;
6. known unsupported type -> `REVIEW_REQUIRED / UNSUPPORTED_OPERATION_TYPE`;
7. nonempty child operations, nonempty parent operation ID или nonzero commission ->
   `REVIEW_REQUIRED / MULTI_COMPONENT_AMBIGUOUS`;
8. BUY/SELL group, если `quantity <= 0`, `quantityDone != quantity` или
   `quantityRest != 0` ->
   `REVIEW_REQUIRED / PARTIAL_EXECUTION_AMBIGUOUS`;
9. zero payment -> `REVIEW_REQUIRED / ZERO_CASH_EFFECT`;
10. знак payment не соответствует table ->
    `REVIEW_REQUIRED / AMOUNT_SIGN_AMBIGUOUS`;
11. иначе -> `TRANSACTION_PROPOSED / CLASSIFIED`.

Classification table:

| Provider types | Required cash sign | CL1 classification | Counterpart |
|---|---:|---|---|
| `INPUT`, `INPUT_SWIFT`, `INPUT_ACQUIRING`, `INP_MULTI` | positive | `DEPOSIT` | `EQUITY_EXTERNAL_FLOW` |
| `OUTPUT`, `OUTPUT_SWIFT`, `OUTPUT_ACQUIRING`, `OUT_MULTI` | negative | `WITHDRAWAL` | `EQUITY_EXTERNAL_FLOW` |
| `DIVIDEND` | positive | `DIVIDEND` | `INCOME_DIVIDEND` |
| `COUPON` | positive | `COUPON` | `INCOME_COUPON` |
| `OVERNIGHT`, `OVER_INCOME` | positive | `INTEREST` | `INCOME_INTEREST` |
| `SERVICE_FEE`, `MARGIN_FEE`, `BROKER_FEE`, `SUCCESS_FEE`, `TRACK_MFEE`, `TRACK_PFEE`, `CASH_FEE`, `OUT_FEE`, `OUTPUT_PENALTY`, `ADVICE_FEE`, `OVER_COM` | negative | `COMMISSION` | `EXPENSE_COMMISSION` |
| `BOND_TAX`, `TAX`, `DIVIDEND_TAX`, `BENEFIT_TAX`, `TAX_PROGRESSIVE`, `BOND_TAX_PROGRESSIVE`, `DIVIDEND_TAX_PROGRESSIVE`, `BENEFIT_TAX_PROGRESSIVE`, `TAX_REPO_PROGRESSIVE`, `TAX_REPO`, `TAX_REPO_HOLD`, `TAX_REPO_HOLD_PROGRESSIVE`, `OUT_STAMP_DUTY` | negative | `TAX` | `EXPENSE_TAX` |
| `TAX_CORRECTION`, `TAX_CORRECTION_PROGRESSIVE`, `TAX_REPO_REFUND`, `TAX_REPO_REFUND_PROGRESSIVE`, `TAX_CORRECTION_COUPON` | positive | `REFUND` | `EXPENSE_TAX` |
| `BUY`, `BUY_MARGIN`, `DELIVERY_BUY` | negative | `TRADE_SETTLEMENT` | `ASSET_TRADE_CLEARING` |
| `SELL`, `SELL_MARGIN`, `DELIVERY_SELL` | positive | `TRADE_SETTLEMENT` | `ASSET_TRADE_CLEARING` |

В таблице подразумевается полный provider enum prefix `OPERATION_TYPE_`.
Положительная fee, отрицательный income, отрицательная tax correction и иная
смена знака никогда не угадывается как refund/reversal.

`BUY_CARD`, `SELL_CARD`, bond repayments, securities transfers, dividend transfer,
variation margin, expirations, `OVER_PLACEMENT` и `UNSPECIFIED` остаются review.

## 20. Exact posting-plan construction

Для classified payment `P`:

- line 1: `ASSET_BROKER_CASH`, Money `P`;
- line 2: table counterpart, Money `-P`;
- effective timestamp: normalized provider `date`;
- source: exact CL3 `SourceIdentity`;
- classification/chart versions: exact CL1 values;
- `corrects_sha256 = None`;
- `reversal_of_sha256 = None`.

Нельзя строить `OPENING_BALANCE`, `MANUAL_ADJUSTMENT`, `REVERSAL` или correction
bundle. Tax correction provider type здесь означает только положительный refund
на tax expense account; он не утверждает lineage к прежней transaction.

Line numbers и posting order заморожены: cash всегда line 1, counterpart line 2.
Любая CL1 `MoneyError`/`LedgerError` завершает item hard error; invalid proposal
нельзя понижать до review result.

## 21. Pagination invariants

Страницы обрабатываются в provider order; items внутри страницы — в list order.
Sorting запрещён.

Для каждой successful page:

- число items не превышает request limit;
- cumulative pages/items не превышают configured maxima;
- каждый item cursor уникален во всём batch;
- каждый raw operation ID уникален во всём batch, проверяется только ephemeral;
- каждый derived source scope уникален во всём batch;
- `hasNext = true` требует nonempty `nextCursor`, nonempty items и новый cursor;
- `hasNext = false` требует empty `nextCursor`;
- request cursor не может повториться или образовать цикл;
- empty intermediate page запрещена;
- collection завершается только первой valid page с `hasNext = false`.

Нарушение возвращает hard error и не возвращает partial decisions/watermark.

## 22. Request fingerprint, page chain и completeness watermark

Request fingerprint — plain SHA-256 canonical bytes exact object:

```json
{"account_scope_sha256":"<account scope>","domain":"v3.10-cl3-read-request","from_inclusive":"<timestamp>","identity_key_id":"<token>","limit":"<positive decimal>","operation_types":[],"state":"OPERATION_STATE_UNSPECIFIED","to_exclusive":"<timestamp>","version":1,"without_commissions":false,"without_overnights":false,"without_trades":false}
```

Initial `previous_page_chain_sha256` — 64 zeroes. Для каждой страницы plain
SHA-256 берётся от canonical object:

```json
{"domain":"v3.10-cl3-page-chain","has_next":false,"item_cursor_evidence_sha256":["<HMAC>","..."],"item_observation_sha256":["<sha256>","..."],"next_cursor_evidence_sha256":"<HMAC>","page_no":"<positive decimal>","previous_page_chain_sha256":"<sha256>","request_cursor_evidence_sha256":"<HMAC>","request_fingerprint_sha256":"<sha256>","version":1}
```

Final `CompletenessWatermark` canonical keyset:

```json
{"account_scope_sha256":"<account scope>","complete":true,"domain":"v3.10-cl3-completeness-watermark","from_inclusive":"<timestamp>","item_count":"<nonnegative decimal>","page_chain_sha256":"<sha256>","page_count":"<positive decimal>","request_fingerprint_sha256":"<sha256>","to_exclusive":"<timestamp>","version":1}
```

`watermark.sha256` — plain SHA-256 canonical bytes. `BrokerReadBatch` содержит
tuple decisions в provider order и final watermark.

`complete=true` означает только: CL3 дошёл до terminal page exact provider
response chain для exact account scope, query window и filters в этом call.
Это не доказывает economic finality, отсутствие поздних/исправленных operations,
current cash, reconciliation или согласие с broker statement. Повторное чтение
того же окна может законно дать новый content/source conflict, который дальше
обрабатывается CL2.

## 23. Closed hard failures и evidence

`BrokerReadReason`:

```text
TYPE_INVALID
CONFIGURATION_INVALID
DEADLINE_EXCEEDED
TRANSPORT_TIMEOUT
TRANSPORT_CONNECTION_INTERRUPTED
TRANSPORT_HTTP_RETRY_EXHAUSTED
TRANSPORT_HTTP_PERMANENT
TRANSPORT_PROTOCOL_FAILURE
CLOCK_INVALID
CLOCK_FAILURE
WAIT_FAILURE
RESPONSE_BOUNDS_EXCEEDED
RESPONSE_SCHEMA_INVALID
PAGE_LIMIT_EXCEEDED
ITEM_LIMIT_EXCEEDED
PAGINATION_INVARIANT_VIOLATION
DUPLICATE_ITEM
ACCOUNT_MISMATCH
ITEM_OUTSIDE_WINDOW
OPERATION_ID_INVALID
CURSOR_INVALID
ENUM_TOKEN_INVALID
TIMESTAMP_INVALID
MONEY_INVALID
QUANTITY_INVALID
CHILD_OPERATIONS_INVALID
IDENTITY_INVALID
INBOX_OBSERVATION_INVALID
LEDGER_PROPOSAL_INVALID
```

`BrokerReadError` имеет `reason` и optional `cause_reason`. `cause_reason` может
быть только exact closed `MoneyReason`, `LedgerReason` или `PersistenceReason`.
`str(error)` содержит только `reason` и, если есть, `cause_reason`; raw exception
text не сохраняется и не chain-ится наружу.

Safe evidence может содержать только:

```text
reason, cause_reason, stage token, attempt_no, page_no,
account_scope_sha256, request_fingerprint_sha256
```

Absent fields опускаются. Нельзя включать raw IDs/cursors/payload, provider text,
URL, headers, token, response body, object repr или stack-local values.

## 24. Ordered failure priority

Top-level порядок:

1. public argument types;
2. request/retry bounds и timestamps;
3. identity key/key ID и derived account scope;
4. deadline before transport;
5. normalized transport terminal failure;
6. response graph bounds/type/cycle preflight;
7. response top-level schema;
8. page bounds/cursor invariants;
9. items последовательно в provider order;
10. page-chain/watermark construction.

Item порядок:

1. Mapping, required/allowed keysets;
2. account match;
3. raw operation/item cursor grammar;
4. state/type token grammar;
5. timestamp;
6. exact request-window membership;
7. payment then commission MoneyValue;
8. quantity, quantityDone, quantityRest;
9. parent/child operation bounds;
10. identities and sanitized content;
11. CL2 observation construction;
12. classification;
13. optional CL1 proposal construction.

При нескольких дефектах возвращается только первая reason этого порядка.

## 25. Synthetic known-answer vector

Тестовый key: 32 zero bytes; `identity_key_id = TEST_KEY_1`; environment
`SANDBOX`; `from_inclusive = 2026-01-01T00:00:00.000000000Z`;
`to_exclusive = 2026-01-03T00:00:00.000000000Z`;
`InboxObservation.observed_at = effective_at`; `limit = 1000`;
одна terminal page (`page_no = 1`, `hasNext = false`, оба page cursors absent); raw account `SYNTHETIC-ACCOUNT-01`; raw operation ID
`SYNTHETIC-OPERATION-01`; item cursor `SYNTHETIC-CURSOR-ITEM-01`.

Provider operation:

```json
{"brokerAccountId":"SYNTHETIC-ACCOUNT-01","childOperations":[],"commission":{"currency":"RUB","nano":0,"units":"0"},"cursor":"SYNTHETIC-CURSOR-ITEM-01","date":"2026-01-02T03:04:05.123Z","id":"SYNTHETIC-OPERATION-01","payment":{"currency":"RUB","nano":567890000,"units":"1"},"quantity":"0","quantityDone":"0","quantityRest":"0","state":"OPERATION_STATE_EXECUTED","type":"OPERATION_TYPE_INPUT"}
```

Normalized sanitized content:

```json
{"child_operation_count":0,"commission_minor_units":"0","component":"PAYMENT","effective_at":"2026-01-02T03:04:05.123000000Z","has_parent_operation":false,"operation_state":"OPERATION_STATE_EXECUTED","operation_type":"OPERATION_TYPE_INPUT","payment_currency":"RUB","payment_minor_units":"1567890000","quantity":"0","quantity_done":"0","quantity_rest":"0"}
```

Frozen outputs (filled only by reproducible canonical calculation):

```text
schema_sha256 = 2b3b7acb6ce2aec48d3c6eda4137a1ca9e9ad5368b961fd556a72767699dd7b9
descriptor_sha256 = 69d60a18b2048c6472fc0b39587ce5332e86d592012c1c8f52ce4183c1342748
account_scope_sha256 = 2f46c3b5dae3b72dd6b0582b4f5f79d8a0479dc93936330f7cd9985b9f63195d
source_scope_sha256 = 29fefc017c4da1d1297bae79d973a0dc2eb24908f3ebafa6adefd5b9b4e1b6e0
source_content_sha256 = 6e58d8b7c4c4acdf6985d43ecd2f0d3494930777b846db041953f0039345a60e
provenance_sha256 = dc7f1830431237411ce8421c08ff8d71303f57803e170173f781a75825383fad
logical_source_sha256 = ee13c8446357e495798e066b8639248f86b47b2faec143fa38e4725895bf4fc6
observation_sha256 = 2bda0a4e13ad9fd415da2c7bcdec5cc1de19179c720f50c4d413cdc6d1e166af
transaction_sha256 = 475affc6fdee262946fee31ac5e1a2eeaceb3a6ca866cefe7367fb72ad913cf3
request_fingerprint_sha256 = a6bfa48de9ef2c52005e136d329d15d1fcf3dbf32d499a0f0e5c893404ee2a5b
initial_request_cursor_evidence_sha256 = 8b4c0aaf5231d284e95d32fb8d66d12f2a10521d349c712ebf713367547a3eb7
terminal_next_cursor_evidence_sha256 = 9e756e87f5f547972ac4908a4ec53ff031d869829f40d1a2492f2f53d9724d4a
item_cursor_evidence_sha256 = 1bf84e1169ffda5a960e4d27a6f1865ddc43e0ad1fb118fd542656327fe02976
page_chain_sha256 = a1e8523aadecc2d0e83a8fbca1ce2b3ffca7e6da52064cf061e18c1a443b8c06
watermark_sha256 = 5a29828e93e2a0038bcf00609acca7dcf413ecfeb98463ed1ac70f239aba7f00
```

Transaction proposal: `DEPOSIT`, cash `+1.567890000`, external equity
`-1.567890000`, exact source above, no lineage fields.

Любое отличие exact bytes/SHA делает known-answer test failed.

## 26. Mandatory test matrix

- `V310-CL3-01`: exact module exports и import side-effect freedom;
- `V310-CL3-02`: MoneyValue happy paths, zero, bounds и all rejection grammars;
- `V310-CL3-03`: timestamp 0/3/6/9 fractions и calendar/offset negatives;
- `V310-CL3-04`: HMAC account/source/cursor/provenance known answers;
- `V310-CL3-05`: codec schema, descriptor, content, observation known answers и
  byte-stable repeat при изменившейся pagination;
- `V310-CL3-06`: every supported type/sign -> exact CL1 proposal/postings;
- `V310-CL3-07`: unknown, unsupported, pending, unspecified, canceled outcomes;
- `V310-CL3-08`: embedded commission/children and partial trade ambiguity;
- `V310-CL3-09`: response bounds/keysets/types, parent operation, account/window
  mismatch и error priority;
- `V310-CL3-10`: multi-page order, cursor progress, duplicates and caps;
- `V310-CL3-11`: retry status table, attempts/backoff and no invalid retry;
- `V310-CL3-12`: absolute deadline before call/wait/next page;
- `V310-CL3-13`: no partial batch on every page/item/transport failure;
- `V310-CL3-14`: page-chain/watermark known answers and narrow semantics;
- `V310-CL3-15`: evidence/error strings contain no raw synthetic secrets;
- `V310-CL3-16`: no filesystem/env/network/persistence/runtime imports or calls;
- `V310-CL3-17`: implementation delta exact three-file allowlist;
- `V310-CL3-18`: full accepted CL1/CL2 regression suites stay green.

Negative tests должны использовать sentinels для raw account, operation ID,
cursor, provider description, URL, token и body и искать их во всех outputs,
canonical bytes, exceptions и evidence.

## 27. Implementation verification commands

Implementation review должен запускать как минимум:

```powershell
git diff --name-status <accepted-cl3-contract-head>..HEAD
git diff --check <accepted-cl3-contract-head>..HEAD
python -m pytest current/tests/test_v3_10_broker_read_adapters.py -q
python -m pytest current/tests/test_v3_10_cash_ledger_domain.py current/tests/test_v3_10_cash_ledger_persistence.py -q
python -m pytest current/tests -q
```

Также обязательны static scans production module на provider SDK/network,
filesystem, environment, persistence, GUI/runtime imports и secret-like strings.

## 28. Independent/adversarial contract review protocol

Review выполняется read-only для exact range:

```text
d684186c0628d27ed452ce4f11311155fdf7a44e -> <candidate head>
```

Reviewer обязан подтвердить:

- exact parent/head/tree и clean worktree;
- delta ровно один разрешённый файл;
- canonical schemas/known-answer bytes и hashes воспроизводимы;
- нет provider write/order/runtime/persistence authority;
- raw identifiers не проходят privacy boundary;
- Money/timestamp/sign/canonical edge cases закрыты;
- unknown/pending/canceled/ambiguous cases fail closed;
- pagination/retry/deadline имеют конечные bounds и no-partial semantics;
- watermark не утверждает finality/current cash;
- observation/proposal не выдаются за economic acceptance;
- future implementation allowlist ровно три замороженных пути.

Finding должен ссылаться на exact candidate и именоваться `CL3-R1-NN`.
Correction требует отдельного bounded successor commit в том же однофайловом
allowlist и finding-scoped closure review successor head.

Initial exact candidate `bf78b15c62f6a29ec7a6789a708cdd07d04915f9`
получил `CL3-R1-01..04`: incomplete trade quantity invariant, неучтённый parent
operation, отсутствие local half-open-window check и избыточное утверждение о
provider filter semantics. Этот документ содержит bounded corrections; closure
и acceptance относятся только к exact successor commit/tree.

## 29. Contract acceptance и exit state

Green validation или отсутствие diff warnings не является acceptance.
Требуется отдельное явное решение:

```text
CL3 CONTRACT ACCEPTED
commit = <exact 40-hex>
tree = <exact 40-hex>
material findings = 0
```

Только после этого отдельным решением можно создать implementation branch прямо
от exact accepted head. До такого решения implementation branch отсутствует,
implementation не начата, runtime/experiment/provider access не разрешены.
