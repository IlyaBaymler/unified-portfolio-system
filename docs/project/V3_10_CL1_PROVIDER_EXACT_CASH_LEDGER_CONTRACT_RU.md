# v3.10 CL1 — Provider-exact Money + balanced CashLedger pure-domain contract

Status: `CL1 CONTRACT CORRECTION CANDIDATE / CL1-R1-01..05 / IMPLEMENTATION NOT AUTHORIZED`

Parent program: Issue #144.  
Tracker: Issue #147.  
Accepted predecessor: CL0 / M0 `ACCEPTED / INTEGRATED / COMPLETED`.

## 1. Exact predecessor and lineage

CL1 contract work starts only from the accepted/integrated stable-line predecessor:

- repository: `baimleriv/unified-portfolio-system`;
- program branch: `program/v3-10-v4-stable-line`;
- exact predecessor commit: `8f6f60b5d183ae9de50ebc12042879cbf02f9ef4`;
- exact predecessor tree: `93939505e52f62a4ed9b1a5eadb026a7b8c89759`;
- CL1 contract branch: `agent/v3-10-clean-cl1-contract-freeze`;
- initial CL1 contract candidate: `b0cb73262408ccdb13ade0e083c0b8d25e71d90b`;
- initial candidate tree: `5936c5794e7936cfb10fff1229c5ea46ec1e0502`.

The predecessor lineage is `v3.9.0 -> accepted/integrated CL0` only. Contemporary `main`, historical v3.10/MoneyV2 branches and their commits are evidence/reference sources, not ancestry.

At this predecessor there is no `current/trading_robot/cash_ledger_domain.py`. CL1 therefore creates the first CashLedger pure-domain module on the clean stable line; it does not create a MoneyV1/MoneyV2 compatibility stack.

## 2. Mission

CL1 shall define one small, deterministic and infrastructure-free domain for:

1. exact T-Bank-compatible RUB Money preserving the complete `units+nano` value;
2. immutable content-bound source identity without raw broker Account ID or payload;
3. immutable ledger postings and balanced transactions;
4. deterministic canonical bytes and SHA-256 identities;
5. source/economic/transaction identity comparison;
6. strict original -> reversal -> correction bundle semantics;
7. a pure finite-set correction-lineage conflict check for later CL2 persistence.

CL1 is additive and non-owning. It must not change any accepted v3.9 runtime owner, cash value, reservation, Risk proof, provider path or persisted state.

## 3. Contract-freeze allowlist

This contract-freeze and its single authorized correction batch may change exactly one path:

- `docs/project/V3_10_CL1_PROVIDER_EXACT_CASH_LEDGER_CONTRACT_RU.md`.

Any other path change during contract freeze/correction is a hard scope failure.

## 4. Future implementation delta allowlist

If and only if this corrected contract is finding-scoped reviewed and explicitly accepted, a later CL1 implementation candidate may change exactly these three paths relative to the accepted contract head:

1. `current/trading_robot/cash_ledger_domain.py` — new pure-domain module;
2. `current/tests/test_v3_10_cash_ledger_domain.py` — new deterministic CL1 suite;
3. `current/tests/fixtures/v3_10_cash_ledger_vectors.json` — new language-neutral known-answer fixture.

The accepted contract file is immutable during implementation. Existing v3.9 source and tests are regression authority and are not implementation scope.

Thus the maximum cumulative CL1 integration allowlist relative to accepted CL0 is four paths: this contract plus the three implementation paths above. A fourth implementation-delta path or any modification of an existing v3.9 file requires `SCOPE_EXPANSION_REQUIRED` and stops CL1 for `RESCOPE`; it is not folded into a correction batch.

No `__init__.py`, runtime entrypoint, CI, release, GUI, provider, Portfolio, Central, Risk or persistence file is in scope.

## 5. Authority and explicit non-goals

Contract preparation and future CL1 implementation do NOT authorize or perform:

- SQLite, filesystem persistence, repository append state, CAS/revisions, backup, restore or migration — CL2;
- provider/API client calls, REST/protobuf decoding, pagination, retries or provider normalization — CL3;
- opening balance, historical backfill, current-cash proof or reconciliation — CL4;
- CashAvailability or reservation projection — CL5;
- performance/reporting or Risk cash context — CL6;
- runtime ownership cutover, GUI/operator wiring, restart qualification or experiment — CL7;
- release/standalone/burn-in — CL8;
- provider POST, order dispatch, pay-in, transfer or any economic mutation;
- authenticated/private-input access or real-account action.

`PortfolioRepository`, `CentralOrderManager` and `ExecutionAdapter` retain the v3.9 ownership boundaries. CL1 objects are evidence/value objects only and cannot become current-cash, reservation or execution owners.

The v3.9 `CashBalance` float representation remains unchanged in CL1. No CL1 API converts authoritative exact Money to or from that float representation. Compatibility/cutover is a later separately reviewed boundary.

## 6. Pure-domain dependency boundary

The production CL1 module must remain deterministic and side-effect-free.

Forbidden production dependencies or actions include:

- filesystem/path/temp/SQLite/process/network/environment APIs;
- requests/http clients, broker SDKs or provider payload classes;
- GUI/Tkinter;
- Portfolio, Central, Risk, Execution, scheduler, runtime or EventJournal modules;
- system clock reads, randomness, UUID generation, logging of domain values;
- file/network/process writes.

Pure standard-library facilities needed for immutable values, enums, regex validation, JSON, SHA-256 and exact arithmetic are permitted.

All time/source/money values are caller-supplied. Importing the CL1 module must perform no I/O and no runtime registration.

## 7. Frozen version axes

The clean-line domain starts at version 1; it does not inherit historical MoneyV1/MoneyV2 numbering.

- `MONEY_DOMAIN_VERSION = 1`;
- `SOURCE_IDENTITY_VERSION = 1`;
- `LEDGER_POSTING_VERSION = 1`;
- `LEDGER_TRANSACTION_VERSION = 1`;
- `LEDGER_ECONOMIC_VERSION = 1`;
- `LEDGER_CORRECTION_BUNDLE_VERSION = 1`;
- `LEDGER_CHART_VERSION = 1`;
- `LEDGER_CLASSIFICATION_VERSION = 1`;
- `CROSS_LANGUAGE_FIXTURE_VERSION = 1`.

Schema/version fields participate in canonical identities. Unknown versions fail closed. Later semantic changes require an explicit version transition; silent reinterpretation of version-1 bytes is forbidden.

## 8. Provider-exact Money contract

### 8.1 Representation

`Money` is an immutable frozen/slotted value object with canonical fields:

- `currency: str`;
- `minor_units: int`;
- `scale: int = 9`.

For CL1 the only supported canonical currency is exact uppercase literal `RUB`. Pure-domain constructors do not trim, uppercase or otherwise normalize currency; `rub`, mixed case, whitespace and foreign currencies fail closed. Provider normalization belongs to CL3.

Canonical scale is exactly `9`, where one minor unit is one nanoruble (`10^-9 RUB`). No scale-2 authoritative Money exists in this clean CL1 domain.

### 8.2 T-Bank `units+nano` boundary

Frozen constants:

- `NANO_FACTOR = 1_000_000_000`;
- `WIRE_UNITS_MIN = -9_223_372_036_854_775_808`;
- `WIRE_UNITS_MAX = 9_223_372_036_854_775_807`;
- `WIRE_NANO_MIN = -999_999_999`;
- `WIRE_NANO_MAX = 999_999_999`;
- `MONEY_MIN_MINOR_UNITS = -9_223_372_036_854_775_808_999_999_999`;
- `MONEY_MAX_MINOR_UNITS = 9_223_372_036_854_775_807_999_999_999`;
- `LEDGER_POSTING_MIN_MINOR_UNITS = -MONEY_MAX_MINOR_UNITS`;
- `LEDGER_POSTING_MAX_MINOR_UNITS = MONEY_MAX_MINOR_UNITS`.

The exact Money bounds are equivalent to:

`MONEY_MIN_MINOR_UNITS = WIRE_UNITS_MIN * NANO_FACTOR + WIRE_NANO_MIN`

`MONEY_MAX_MINOR_UNITS = WIRE_UNITS_MAX * NANO_FACTOR + WIRE_NANO_MAX`.

`Money.from_units_nano(units, nano, currency="RUB")` accepts plain integers only; bool is not an integer for this contract. Units are checked against signed int64 before composition, nano against the exact inclusive range, then sign consistency is checked: positive units cannot have negative nano and negative units cannot have positive nano; units zero may carry either nano sign.

The exact value is `units * 1_000_000_000 + nano`. No float, Decimal input, formatted amount or rounding participates in this identity path.

Direct construction from checked `minor_units` is allowed within the full asymmetric Money bound. The full minimum is therefore valid standalone Money.

Ledger postings intentionally use the narrower symmetric reversible range `[-MONEY_MAX_MINOR_UNITS, +MONEY_MAX_MINOR_UNITS]`. This guarantees that exact one-to-one negation of every accepted posting is itself valid Money. A valid standalone Money below `LEDGER_POSTING_MIN_MINOR_UNITS` fails closed when used as a posting.

### 8.3 Canonical Money representation

Exact keyset and JSON types:

```json
{
  "amount": "<canonical decimal with exactly 9 fractional digits>",
  "currency": "RUB",
  "domain": "v3.10-money",
  "minor_units": "<canonical signed decimal integer string>",
  "scale": 9,
  "version": 1
}
```

`minor_units` is always a JSON string. Grammar is `0|-?[1-9][0-9]*`; `-0`, plus signs, leading zeros, exponent notation and whitespace are invalid.

`amount` is always a JSON string with exactly nine fractional digits and must be derivable uniquely from `minor_units`. Zero is exactly `0.000000000`; negative zero is forbidden.

Canonical bytes for all CL1 canonical forms are UTF-8 bytes of `json.dumps(..., ensure_ascii=True, sort_keys=True, separators=(",", ":"))` semantics: sorted object keys, compact separators, no insignificant whitespace, JSON lowercase `null`, and ASCII escaping where required. Arrays preserve the contract-defined order.

`sha256` is lowercase SHA-256 of those exact bytes.

A convenience exact Decimal view may be derived from the canonical integer/string value, but Decimal input or Decimal formatting is never accepted as authoritative identity.

### 8.4 Arithmetic

`+`, `-` and unary negation are exact integer operations. Operands must be canonical Money with identical currency/scale/version. Result overflow outside the full asymmetric Money bound fails closed. In particular negating `MONEY_MIN_MINOR_UNITS` fails with `ARITHMETIC_OVERFLOW`; this does not affect ledger reversibility because that standalone value cannot enter a posting.

Binary float is never accepted or returned by Money arithmetic.

## 9. Closed Money failure taxonomy and decision order

Version-1 `MoneyReason` is the exact closed set below. Adding a reason requires a version transition; implementation may not add convenience reasons under version 1.

- `TYPE_INVALID`;
- `CURRENCY_UNSUPPORTED`;
- `SCALE_INVALID`;
- `WIRE_UNITS_OUT_OF_RANGE`;
- `WIRE_NANO_OUT_OF_RANGE`;
- `WIRE_SIGN_NON_CANONICAL`;
- `MINOR_UNITS_OUT_OF_RANGE`;
- `CANONICAL_FORMAT_INVALID`;
- `INCOMPATIBLE_OPERAND`;
- `ARITHMETIC_OVERFLOW`.

Every invalid input produces one deterministic primary reason. Exception text is non-authoritative and must not expose raw provider identifiers or payloads.

Ordered public-boundary decision tables:

### 9.1 `Money(currency, minor_units, scale=9)`

1. currency must be string literal `RUB`; any other type/value -> `CURRENCY_UNSUPPORTED`;
2. scale must be a plain integer and exactly `9`; any bool/non-int/other integer -> `SCALE_INVALID`;
3. `minor_units` must be a plain integer; bool/non-int -> `TYPE_INVALID`;
4. full Money bound -> `MINOR_UNITS_OUT_OF_RANGE`.

### 9.2 `Money.from_units_nano(...)`

1. currency literal -> `CURRENCY_UNSUPPORTED`;
2. units plain integer -> `TYPE_INVALID`;
3. nano plain integer -> `TYPE_INVALID`;
4. signed-int64 units range -> `WIRE_UNITS_OUT_OF_RANGE`;
5. nano inclusive range -> `WIRE_NANO_OUT_OF_RANGE`;
6. sign consistency -> `WIRE_SIGN_NON_CANONICAL`;
7. composed full Money bound -> `MINOR_UNITS_OUT_OF_RANGE`.

### 9.3 `Money.from_canonical_dict(...)`

1. Mapping/container and exact six-key keyset -> `CANONICAL_FORMAT_INVALID`;
2. exact `domain="v3.10-money"` and plain-int `version=1` -> `CANONICAL_FORMAT_INVALID`;
3. exact currency literal -> `CURRENCY_UNSUPPORTED`;
4. plain-int scale exactly 9 -> `SCALE_INVALID`;
5. `minor_units` JSON string grammar -> `CANONICAL_FORMAT_INVALID`;
6. parsed full Money bound -> `MINOR_UNITS_OUT_OF_RANGE`;
7. `amount` JSON string grammar with exactly nine fractional digits -> `CANONICAL_FORMAT_INVALID`;
8. amount equality to the unique value derived from `minor_units` -> `CANONICAL_FORMAT_INVALID`.

### 9.4 Arithmetic

1. operand must be Money with same currency/scale/version -> `INCOMPATIBLE_OPERAND`;
2. exact integer result/negation full bound -> `ARITHMETIC_OVERFLOW`.

Representative multi-invalid vectors must assert this exact first-failure order.

## 10. SourceIdentity canonical contract

`SourceIdentity` is immutable, content-bound and privacy-safe. Object fields are:

- `account_scope_sha256`: lowercase 64-hex opaque stable account-scope identity;
- `source_kind`: exact uppercase bounded token `[A-Z][A-Z0-9_]{0,63}`;
- `source_scope_sha256`: lowercase 64-hex digest identifying the logical source scope;
- `source_content_sha256`: lowercase 64-hex digest binding exact observed/synthetic content.

The exact canonical keyset is:

```json
{
  "account_scope_sha256": "<64 lowercase hex>",
  "domain": "v3.10-cash-ledger-source",
  "source_content_sha256": "<64 lowercase hex>",
  "source_kind": "<uppercase token>",
  "source_scope_sha256": "<64 lowercase hex>",
  "version": 1
}
```

There are no optional/null source fields in version 1. Missing/extra keys fail closed. No normalization is performed on `source_kind`.

The pure domain never receives raw provider Account ID, token, operation ID, cursor, payload or authorization data. How an upstream adapter derives privacy-safe scope/content digests is deferred to CL3. The derivation must bind account/environment semantics strongly enough that two distinct provider account scopes cannot intentionally share the same accepted account-scope identity without an explicit CL3 collision/failure disposition.

The exact source canonical bytes and their SHA-256 are deterministic. Source SHA is the source-idempotency identity supplied to later CL2/CL3 decisions.

## 11. Frozen ledger chart, sign semantics and classifications

### 11.1 Accounts, chart version 1

The exact finite chart is:

- `ASSET_BROKER_CASH`;
- `ASSET_TRADE_CLEARING`;
- `EQUITY_OPENING_BALANCE`;
- `EQUITY_EXTERNAL_FLOW`;
- `EQUITY_MANUAL_ADJUSTMENT`;
- `INCOME_DIVIDEND`;
- `INCOME_COUPON`;
- `INCOME_INTEREST`;
- `EXPENSE_COMMISSION`;
- `EXPENSE_TAX`.

### 11.2 Posting sign convention

For version 1 a posting's Money sign is the signed change applied to that ledger account by the event. For `ASSET_BROKER_CASH`, positive means broker cash increases and negative means broker cash decreases. Counter-account signs are the exact balancing opposite required by the classification matrix below. This convention is semantic and versioned; it is not provider-specific debit/credit text.

### 11.3 Classifications, version 1

The exact finite classifications are:

- `OPENING_BALANCE`;
- `DEPOSIT`;
- `WITHDRAWAL`;
- `DIVIDEND`;
- `COUPON`;
- `INTEREST`;
- `COMMISSION`;
- `TAX`;
- `TRADE_SETTLEMENT`;
- `REFUND`;
- `MANUAL_ADJUSTMENT`;
- `REVERSAL`.

The presence of `OPENING_BALANCE` reserves a pure-domain classification only; it does not implement or authorize an opening procedure before CL4.

### 11.4 Exact version-1 non-reversal accounting matrix

For every row, `A` is a strictly positive Money value within the reversible posting range. Account order in the tuple is semantic, not tied to line number. Every non-REVERSAL version-1 transaction contains exactly two postings and must match exactly one allowed pattern:

| Classification | Posting pattern |
|---|---|
| `OPENING_BALANCE` | `ASSET_BROKER_CASH +A`; `EQUITY_OPENING_BALANCE -A` |
| `DEPOSIT` | `ASSET_BROKER_CASH +A`; `EQUITY_EXTERNAL_FLOW -A` |
| `WITHDRAWAL` | `ASSET_BROKER_CASH -A`; `EQUITY_EXTERNAL_FLOW +A` |
| `DIVIDEND` | `ASSET_BROKER_CASH +A`; `INCOME_DIVIDEND -A` |
| `COUPON` | `ASSET_BROKER_CASH +A`; `INCOME_COUPON -A` |
| `INTEREST` | `ASSET_BROKER_CASH +A`; `INCOME_INTEREST -A` |
| `COMMISSION` | `ASSET_BROKER_CASH -A`; `EXPENSE_COMMISSION +A` |
| `TAX` | `ASSET_BROKER_CASH -A`; `EXPENSE_TAX +A` |
| `TRADE_SETTLEMENT` BUY-like | `ASSET_BROKER_CASH -A`; `ASSET_TRADE_CLEARING +A` |
| `TRADE_SETTLEMENT` SELL-like | `ASSET_BROKER_CASH +A`; `ASSET_TRADE_CLEARING -A` |
| `REFUND` commission | `ASSET_BROKER_CASH +A`; `EXPENSE_COMMISSION -A` |
| `REFUND` tax | `ASSET_BROKER_CASH +A`; `EXPENSE_TAX -A` |
| `MANUAL_ADJUSTMENT` increase | `ASSET_BROKER_CASH +A`; `EQUITY_MANUAL_ADJUSTMENT -A` |
| `MANUAL_ADJUSTMENT` decrease | `ASSET_BROKER_CASH -A`; `EQUITY_MANUAL_ADJUSTMENT +A` |

`TRADE_SETTLEMENT` must never use `EQUITY_EXTERNAL_FLOW`, income or expense accounts. Trade principal is therefore structurally distinct from external contribution and P&L.

Provider-specific operation -> classification selection remains CL3. CL1 only defines which already-classified accounting shape is semantically valid.

`REVERSAL` has no independent account/sign matrix. A reversal transaction is only lineage-eligible when a `LedgerCorrectionBundle` proves exact one-to-one negation of its original. Later CL2 persistence must reject lineage-bearing writes that lack accepted bundle proof.

If future versions require a multi-leg non-reversal transaction, that is a versioned contract change rather than silent widening of version 1.

## 12. LedgerPosting canonical contract

`LedgerPosting` is immutable and contains:

- positive plain-integer `line_no` in `1..2_147_483_647`;
- one known `LedgerAccount`;
- one canonical non-zero `Money` whose `minor_units` lies in the symmetric reversible posting range.

The exact canonical keyset is:

```json
{
  "account": "<LedgerAccount value>",
  "domain": "v3.10-cash-ledger-posting",
  "line_no": "<canonical positive decimal integer string>",
  "money": {"<exact Money canonical object>": "..."},
  "version": 1
}
```

The illustrative placeholder above denotes that `money` is the complete nested six-key Money object from section 8.3, not a SHA or partial projection.

Canonical `line_no` is always a JSON string with grammar `[1-9][0-9]*`, numeric value at most `2_147_483_647`. JSON-number `line_no`, plus sign, whitespace and leading zeros fail closed. This makes the canonical form portable even for consumers with limited JSON integer precision.

Zero postings are forbidden. Standalone Money below `LEDGER_POSTING_MIN_MINOR_UNITS`, although valid Money, fails with `POSTING_OUT_OF_REVERSIBLE_RANGE` when used as a posting.

## 13. LedgerTransaction and exact timestamp contract

`LedgerTransaction` is immutable and contains:

- one known `LedgerClassification`;
- `effective_at`: canonical timestamp string;
- one `SourceIdentity`;
- tuple of `LedgerPosting`;
- `classification_version = 1`;
- `chart_version = 1`;
- optional `reversal_of_sha256`;
- optional `corrects_sha256`.

There is deliberately no arbitrary/random transaction UUID in CL1. The exact canonical transaction SHA is the transaction identity.

### 13.1 Timestamp grammar and calendar semantics

The only accepted canonical timestamp grammar is exactly:

`YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ`

with ASCII digits and exactly nine fractional digits.

Semantic validation uses the proleptic Gregorian calendar with years `0001..9999`; month/day must be a real Gregorian date including normal leap-year rules; hour `00..23`; minute `00..59`; second `00..59`. `24:00`, offsets, missing/extra fractional digits and leap-second `SS=60` are rejected. CL1 therefore has no leap-second normalization policy: leap seconds fail closed.

The canonical field remains the exact validated string so nanosecond precision is not lost to a language runtime whose datetime type supports fewer digits. A convenience parsed time view may exist but is never canonical authority.

### 13.2 Full transaction canonical form

The exact keyset is:

```json
{
  "chart_version": 1,
  "classification": "<LedgerClassification value>",
  "classification_version": 1,
  "corrects_sha256": null,
  "domain": "v3.10-cash-ledger-transaction",
  "effective_at": "YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ",
  "postings": ["<complete posting objects sorted by numeric line_no>"],
  "reversal_of_sha256": null,
  "source": {"<complete SourceIdentity canonical object>": "..."},
  "version": 1
}
```

The placeholders denote full nested objects, not strings. The serialized top-level keyset is exactly the ten keys shown. Both lineage keys are always present: either JSON `null` or one lowercase 64-hex string. Omission is invalid.

Requirements:

- at least two postings;
- line numbers positive, unique and canonicalized by numeric `line_no` ascending;
- exact sum of posting minor units equals zero independently for every currency;
- non-REVERSAL transaction has exactly two postings and matches section 11.4;
- `reversal_of_sha256` and `corrects_sha256` are mutually exclusive lowercase SHA-256 values or null;
- a normal transaction has neither lineage reference;
- `REVERSAL` requires `reversal_of_sha256` and null `corrects_sha256`;
- a non-`REVERSAL` correction may contain `corrects_sha256` but null reversal reference;
- canonical parsing requires exact nested keysets/versions and byte-stable round-trip.

CL1 does not write or append a ledger history. History consistency beyond validated pure bundles/set checks is CL2.

## 14. Exact economic form and deterministic identity axes

Every transaction exposes three distinct identities:

1. `source_sha256` — exact SourceIdentity SHA;
2. `economic_sha256` — SHA-256 of the exact economic canonical form below;
3. `sha256` — SHA-256 of the exact full transaction canonical bytes from section 13.2.

The exact economic canonical keyset is:

```json
{
  "account_scope_sha256": "<source.account_scope_sha256>",
  "chart_version": 1,
  "classification": "<LedgerClassification value>",
  "classification_version": 1,
  "domain": "v3.10-cash-ledger-economic",
  "effective_at": "YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ",
  "postings": ["<complete posting canonical objects sorted by numeric line_no>"],
  "version": 1
}
```

The economic form excludes SourceIdentity evidence, `corrects_sha256` and `reversal_of_sha256`. It includes account scope, exact timestamp, classification/version, chart version and complete postings. No fields are optional or null in the economic form.

These identity axes must never be silently substituted for one another.

A pure comparison result uses the exact finite relation vocabulary:

- `EXACT_DUPLICATE`: full transaction SHA equal;
- `SOURCE_CONFLICT`: source SHA equal but full transaction SHA differs;
- `ECONOMIC_MATCH`: source SHA differs, full SHA differs, economic SHA equal;
- `DISTINCT`: none of the above.

`ECONOMIC_MATCH` is a review/idempotency signal, not proof that two independent broker observations are the same event. CL2/CL3 will define persistence/adjudication policy; CL1 only produces deterministic identities.

## 15. Reversal, correction bundle and branch conflict contract

`LedgerCorrectionBundle` is the only CL1 pure-domain object that certifies a correction lineage suitable for later persistence. It contains `(original, reversal, correction)` and validates all of the following:

- `original` is non-REVERSAL and has no lineage reference;
- `reversal.classification == REVERSAL`;
- `reversal.reversal_of_sha256 == original.sha256`;
- reversal has null `corrects_sha256`;
- reversal uses the same account scope as original;
- reversal postings are exact one-to-one negation of the original canonical postings: same line numbers, accounts, currencies and scale, exact negative minor units;
- `correction` is non-REVERSAL;
- `correction.corrects_sha256 == original.sha256`;
- correction has null reversal reference and uses the same account scope;
- correction itself satisfies its classification/account/sign matrix;
- correction economic identity differs from original, so a no-op economic correction is rejected.

Because every posting is constrained to the symmetric reversible range, the required exact reversal is always representable.

A reversal of a reversal is invalid. A correction of a reversal is invalid. A bundle targeting any hash other than the original exact SHA is invalid.

### 15.1 Canonical bundle identity

The exact bundle canonical keyset is:

```json
{
  "correction_sha256": "<correction.sha256>",
  "domain": "v3.10-cash-ledger-correction-bundle",
  "original_sha256": "<original.sha256>",
  "reversal_sha256": "<reversal.sha256>",
  "version": 1
}
```

No nested transaction bytes, timestamps, source evidence or optional/null fields appear in the bundle identity because those are already transitively bound by the three exact transaction hashes. Bundle `sha256` is SHA-256 of these canonical bytes.

### 15.2 Pure finite-set uniqueness rule

CL1 exposes a pure order-independent correction-bundle set check. Before grouping, every supplied bundle must individually validate. Bundles are then compared by `(original_sha256, bundle.sha256)` semantics:

- one unique bundle for an original -> accepted unique lineage;
- repeated byte-identical/equal-SHA bundle for the same original -> deterministic `EXACT_DUPLICATE` relation with no second lineage;
- any second distinct `bundle.sha256` for the same `original_sha256` -> `LINEAGE_CONFLICT` and the set is rejected regardless of input order.

This check performs no persistence and owns no history. CL2 must apply the same invariant under its repository lock/CAS boundary so a persisted original can never acquire two distinct accepted correction branches.

## 16. Closed ledger failure taxonomy and ordered decision tables

Version-1 `LedgerReason` is the exact closed set below. Adding a new reason requires a version transition.

- `TYPE_INVALID`;
- `HASH_INVALID`;
- `ACCOUNT_UNSUPPORTED`;
- `CLASSIFICATION_UNSUPPORTED`;
- `TIMESTAMP_INVALID`;
- `LINE_NUMBER_INVALID`;
- `ZERO_POSTING`;
- `POSTING_OUT_OF_REVERSIBLE_RANGE`;
- `DUPLICATE_LINE`;
- `UNBALANCED`;
- `SOURCE_INVALID`;
- `CANONICAL_FORMAT_INVALID`;
- `ACCOUNTING_PATTERN_INVALID`;
- `LINEAGE_INVALID`;
- `LINEAGE_CONFLICT`.

Nested Money parsing may return the closed `MoneyReason` from section 9; it is not wrapped into an unstable Ledger reason. All other ledger boundaries return exactly one LedgerReason.

### 16.1 `SourceIdentity(...)`

1. `account_scope_sha256` exact lowercase 64-hex -> `SOURCE_INVALID`;
2. `source_kind` exact uppercase token -> `SOURCE_INVALID`;
3. `source_scope_sha256` exact lowercase 64-hex -> `SOURCE_INVALID`;
4. `source_content_sha256` exact lowercase 64-hex -> `SOURCE_INVALID`.

`SourceIdentity.from_canonical_dict(...)` first validates Mapping/exact keyset/domain/version -> `CANONICAL_FORMAT_INVALID`, then applies the four field checks above in that order.

### 16.2 `LedgerPosting(...)`

1. `line_no` plain integer, not bool, range `1..2_147_483_647` -> `LINE_NUMBER_INVALID`;
2. account must be known LedgerAccount -> `ACCOUNT_UNSUPPORTED`;
3. money must be Money -> `TYPE_INVALID`;
4. posting reversible range -> `POSTING_OUT_OF_REVERSIBLE_RANGE`;
5. money must be non-zero -> `ZERO_POSTING`.

`LedgerPosting.from_canonical_dict(...)` first validates Mapping/exact five-key keyset/domain/version and canonical string grammar/range for `line_no` -> `CANONICAL_FORMAT_INVALID`/`LINE_NUMBER_INVALID` as applicable, then account, then nested Money parser. Nested MoneyReason propagates unchanged.

### 16.3 `LedgerTransaction(...)`

1. classification type/value -> `CLASSIFICATION_UNSUPPORTED`;
2. `classification_version` plain integer exactly 1 -> `CANONICAL_FORMAT_INVALID`;
3. `chart_version` plain integer exactly 1 -> `CANONICAL_FORMAT_INVALID`;
4. effective timestamp grammar/calendar -> `TIMESTAMP_INVALID`;
5. SourceIdentity type -> `SOURCE_INVALID`;
6. postings must be a non-string finite sequence of LedgerPosting objects -> `TYPE_INVALID`;
7. minimum posting count -> `ACCOUNTING_PATTERN_INVALID`;
8. duplicate line numbers -> `DUPLICATE_LINE`;
9. exact per-currency balance -> `UNBALANCED`;
10. lineage hash type/format/mutual exclusion/classification compatibility -> `HASH_INVALID` or `LINEAGE_INVALID`;
11. non-REVERSAL exact two-posting accounting matrix -> `ACCOUNTING_PATTERN_INVALID`.

`LedgerTransaction.from_canonical_dict(...)` first validates top-level Mapping/exact ten-key keyset/domain/version -> `CANONICAL_FORMAT_INVALID`; then steps 1–4; then parses SourceIdentity; then parses posting objects in serialized array order, propagating the first nested Source/Ledger/Money reason; finally steps 7–11. Extra/missing nested keys fail before semantic validation of those nested values.

### 16.4 `LedgerCorrectionBundle(...)`

1. original/reversal/correction must each be LedgerTransaction -> `TYPE_INVALID`;
2. original must be non-REVERSAL with both lineage fields null -> `LINEAGE_INVALID`;
3. reversal classification/reference/null-correction-field -> `LINEAGE_INVALID`;
4. reversal account scope -> `LINEAGE_INVALID`;
5. reversal exact posting negation -> `LINEAGE_INVALID`;
6. correction non-REVERSAL/reference/null-reversal-field/account scope -> `LINEAGE_INVALID`;
7. correction valid accounting matrix is already guaranteed by transaction construction; if a malformed unchecked object is supplied -> `ACCOUNTING_PATTERN_INVALID`;
8. correction economic SHA must differ from original -> `LINEAGE_INVALID`.

`LedgerCorrectionBundle.from_canonical_dict(...)` validates exact five-key bundle keyset/domain/version and three lowercase hashes first -> `CANONICAL_FORMAT_INVALID`/`HASH_INVALID`, then requires supplied transaction objects whose SHA values match all three canonical hashes -> `LINEAGE_INVALID`, then applies steps 2–8.

### 16.5 Finite bundle-set validator

1. input must be a finite non-string iterable of valid LedgerCorrectionBundle -> `TYPE_INVALID` or the first bundle validation reason;
2. normalize deterministically by original SHA then bundle SHA;
3. repeated same bundle SHA for one original -> relation `EXACT_DUPLICATE`;
4. second distinct bundle SHA for one original -> `LINEAGE_CONFLICT`;
5. otherwise unique accepted set.

Representative multi-invalid vectors for every public boundary must bind the exact first reason defined above.

## 17. Cross-language known-answer fixture and frozen canonical sample

`current/tests/fixtures/v3_10_cash_ledger_vectors.json` is a future immutable language-neutral fixture for Python and v4 consumers. It contains only public synthetic values and is never read by production code.

### 17.1 Exact fixture schemas

Top-level exact keyset:

- `domain`: literal `v3.10-cash-ledger-fixture`;
- `version`: plain integer `1`;
- `vectors`: JSON array.

Every vector has exact common keys `id`, `kind`, `canonical_json_ascii`, `sha256`; extra/missing keys fail fixture verification. For `kind="transaction"`, exact additional keys are `economic_json_ascii`, `economic_sha256`, `source_sha256`. For `kind="bundle"`, exact additional keys are `original_sha256`, `reversal_sha256`, `correction_sha256`. Other kinds have no additional keys.

All canonical JSON payloads and arbitrary-precision values inside them are represented as strings where frozen above. Fixture consumers parse `canonical_json_ascii` as ASCII/UTF-8, independently reconstruct canonical objects/bytes, and verify SHA without binary float.

### 17.2 Mandatory vectors

At minimum the fixture covers:

- Money zero, one nanoruble, one kopeck, negative sub-kopeck, full asymmetric standalone min/max, and symmetric posting limits;
- a standalone Money minimum that is rejected as a posting;
- SourceIdentity;
- positive and negative LedgerPosting;
- one balanced DEPOSIT original transaction;
- one BUY-like and one SELL-like TRADE_SETTLEMENT;
- exact full/economic/source identities;
- one exact original/reversal/correction bundle;
- exact duplicate bundle and distinct-branch conflict cases.

### 17.3 Frozen known-answer sample

Synthetic source uses:

- account scope = `1111111111111111111111111111111111111111111111111111111111111111`;
- source scope = `2222222222222222222222222222222222222222222222222222222222222222`;
- source content = `3333333333333333333333333333333333333333333333333333333333333333`;
- source kind = `SYNTHETIC`.

Exact canonical Money for `1.000000000 RUB`:

```text
{"amount":"1.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"1000000000","scale":9,"version":1}
```

SHA-256:

`84f2a0a835a9925f376b0f8deb78df59665c4f7cb66bad5b46ee7d88044050ed`

Exact SourceIdentity canonical bytes:

```text
{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111","domain":"v3.10-cash-ledger-source","source_content_sha256":"3333333333333333333333333333333333333333333333333333333333333333","source_kind":"SYNTHETIC","source_scope_sha256":"2222222222222222222222222222222222222222222222222222222222222222","version":1}
```

SHA-256:

`37b37643d08b5e591bf184f95542dfe20c0d627427cc4757c7e5255158d7ee20`

Exact positive cash posting canonical bytes:

```text
{"account":"ASSET_BROKER_CASH","domain":"v3.10-cash-ledger-posting","line_no":"1","money":{"amount":"1.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"1000000000","scale":9,"version":1},"version":1}
```

SHA-256:

`5764735e2fbb39adb0118772020e5cde8ce978497058270ecafd0613ff77fa20`

The paired line 2 is `EQUITY_EXTERNAL_FLOW -1.000000000`. With effective timestamp `2026-01-02T03:04:05.123456789Z`, classification `DEPOSIT`, null lineage fields and the exact source above, the full canonical transaction bytes are:

```text
{"chart_version":1,"classification":"DEPOSIT","classification_version":1,"corrects_sha256":null,"domain":"v3.10-cash-ledger-transaction","effective_at":"2026-01-02T03:04:05.123456789Z","postings":[{"account":"ASSET_BROKER_CASH","domain":"v3.10-cash-ledger-posting","line_no":"1","money":{"amount":"1.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"1000000000","scale":9,"version":1},"version":1},{"account":"EQUITY_EXTERNAL_FLOW","domain":"v3.10-cash-ledger-posting","line_no":"2","money":{"amount":"-1.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"-1000000000","scale":9,"version":1},"version":1}],"reversal_of_sha256":null,"source":{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111","domain":"v3.10-cash-ledger-source","source_content_sha256":"3333333333333333333333333333333333333333333333333333333333333333","source_kind":"SYNTHETIC","source_scope_sha256":"2222222222222222222222222222222222222222222222222222222222222222","version":1},"version":1}
```

Full transaction SHA-256:

`bb14525732cba2e2050c2743c30c07a7cbbb705ffc3ded42fa8f58368c6a7b53`

Its exact economic canonical bytes are:

```text
{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111","chart_version":1,"classification":"DEPOSIT","classification_version":1,"domain":"v3.10-cash-ledger-economic","effective_at":"2026-01-02T03:04:05.123456789Z","postings":[{"account":"ASSET_BROKER_CASH","domain":"v3.10-cash-ledger-posting","line_no":"1","money":{"amount":"1.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"1000000000","scale":9,"version":1},"version":1},{"account":"EQUITY_EXTERNAL_FLOW","domain":"v3.10-cash-ledger-posting","line_no":"2","money":{"amount":"-1.000000000","currency":"RUB","domain":"v3.10-money","minor_units":"-1000000000","scale":9,"version":1},"version":1}],"version":1}
```

Economic SHA-256:

`02f37175436a6e25fefdfc0b3750713572a486e056bb6b32cd13be959b666788`

For the mandatory sample correction lineage, the exact transaction SHA values are:

- original: `bb14525732cba2e2050c2743c30c07a7cbbb705ffc3ded42fa8f58368c6a7b53`;
- reversal: `8207bbda82b045350235f78e0a2e132f142b98c006d254ff08d25ceb39d8c86b`;
- correction: `77b098c218c50faa4f2d205bf903ce216e6d3c45935c00805477b839d9738004`.

The exact bundle canonical bytes are:

```text
{"correction_sha256":"77b098c218c50faa4f2d205bf903ce216e6d3c45935c00805477b839d9738004","domain":"v3.10-cash-ledger-correction-bundle","original_sha256":"bb14525732cba2e2050c2743c30c07a7cbbb705ffc3ded42fa8f58368c6a7b53","reversal_sha256":"8207bbda82b045350235f78e0a2e132f142b98c006d254ff08d25ceb39d8c86b","version":1}
```

Bundle SHA-256:

`73a8a1776c5f6e6262bed3a92628fc29f62b11a75d17334d47a12a702351799f`

The fixture must reproduce these exact sample values independently; any mismatch is contract failure, not implementation freedom.

## 18. Fixed acceptance matrix

The future implementation acceptance suite is frozen to the following vectors:

- `V310-CL1-01`: whole/kopeck/sub-kopeck `units+nano` -> exact scale-9 Money;
- `V310-CL1-02`: signed-int64 units plus nano extrema -> exact asymmetric standalone Money bounds; standalone MIN accepted, but rejected as posting; every accepted posting exact-negatable;
- `V310-CL1-03`: exact closed MoneyReason and ordered multi-invalid direct/wire cases;
- `V310-CL1-04`: canonical Money exact keyset/types/grammar/version/amount and byte-identical round-trip;
- `V310-CL1-05`: exact SourceIdentity keyset/bytes/hash, raw-account/payload absence and ordered Source failure cases;
- `V310-CL1-06`: exact Posting keyset, line-number string/range, nested Money bytes and reversible-range enforcement;
- `V310-CL1-07`: timestamp exact grammar, real Gregorian dates, nanosecond preservation, offset/24:00/leap-second rejection;
- `V310-CL1-08`: exact full Transaction canonical keyset/null policy/nested objects/bytes/SHA;
- `V310-CL1-09`: exact economic canonical keyset/bytes/SHA and source/economic/full identity separation;
- `V310-CL1-10`: exact arithmetic normal/overflow/incompatible cases and no binary-float authority;
- `V310-CL1-11`: DEPOSIT/WITHDRAWAL exact polarity accepted; inverted patterns rejected;
- `V310-CL1-12`: DIVIDEND/COUPON/INTEREST/COMMISSION/TAX exact account/sign patterns accepted; inverted/wrong counterpart rejected;
- `V310-CL1-13`: BUY-like and SELL-like TRADE_SETTLEMENT only through `ASSET_TRADE_CLEARING`; external-flow/income/expense trade principal rejected;
- `V310-CL1-14`: OPENING_BALANCE/REFUND/MANUAL_ADJUSTMENT exact patterns and distinct counterpart accounts;
- `V310-CL1-15`: zero posting, duplicate/invalid line, wrong posting type, unbalanced and non-reversal multi-leg/wrong accounting patterns rejected;
- `V310-CL1-16`: posting order normalization by numeric line number and repeated serialization/hash deterministic;
- `V310-CL1-17`: exact source/economic/full identity relation matrix including non-authoritative `ECONOMIC_MATCH`;
- `V310-CL1-18`: exact reversal negation/provenance/account scope and representability for every accepted original posting;
- `V310-CL1-19`: valid original -> reversal -> correction, no-op/wrong-target/reversal-of-reversal/correction-of-reversal rejected;
- `V310-CL1-20`: exact correction-bundle canonical keyset/bytes/SHA known-answer;
- `V310-CL1-21`: finite bundle-set check is order-independent; repeated same bundle -> `EXACT_DUPLICATE`; distinct second bundle for original -> `LINEAGE_CONFLICT`;
- `V310-CL1-22`: exact closed LedgerReason and ordered representative multi-invalid cases for Source, Posting, Transaction, Bundle and bundle-set boundaries;
- `V310-CL1-23`: AST/import boundary proves no infrastructure/runtime/provider ownership dependency;
- `V310-CL1-24`: exact three-path implementation-delta allowlist plus complete unchanged v3.9 regression suite and exact frozen fixture sample hashes.

No acceptance vector requires network, broker credentials, provider calls, filesystem state, current time, GUI or runtime startup.

## 19. Verification contract for future implementation

Before an implementation candidate may enter independent review it must pass, locally and deterministically:

1. dedicated `test_v3_10_cash_ledger_domain.py` acceptance matrix `V310-CL1-01..24`;
2. independent fixture schema/bytes/SHA verification, including all frozen sample hashes from section 17.3;
3. critical/static/style checks for the new production/test Python files;
4. compile check for the new production module;
5. full unchanged v3.9 regression suite;
6. `git diff --check`;
7. exact three-path implementation-delta allowlist check;
8. complete diff review proving no runtime import/call-graph change.

Green tests do not grant implementation acceptance, publication, runtime, experiment or release authority.

## 20. Historical evidence disposition

Historical #49 and historical MoneyV2 M2-A/M2-B work are reference evidence only. CL1 retains validated lessons — exact Money, deterministic canonical bytes, balancing, reversal/correction, typed fail-closed errors and cross-language fixtures — while removing historical dual-version/migration/governance baggage.

In particular:

- there is one clean scale-9 Money domain, not MoneyV1 + MoneyV2;
- there is no persistence/repository/mixed-history implementation in CL1;
- raw broker/account identifiers are absent from canonical source/transaction evidence;
- provider normalization and operation classification are not pulled forward from CL3;
- persistence/CAS is not pulled into CL1 merely to enforce bundle-set uniqueness; CL1 supplies the pure invariant, CL2 later enforces it atomically.

Historical accepted bytes/hashes are not claimed as CL1 bytes and are not copied as authority. The section 17.3 known answers are newly frozen clean-line version-1 bytes.

## 21. Bounded review and correction protocol

The independent/adversarial review of initial exact candidate `b0cb73262408ccdb13ade0e083c0b8d25e71d90b` produced the fixed material set:

- `CL1-R1-01 / BLOCKER_CANONICAL_FORMS_UNFROZEN`;
- `CL1-R1-02 / BLOCKER_ASYMMETRIC_MIN_NOT_REVERSIBLE`;
- `CL1-R1-03 / BLOCKER_ACCOUNTING_POLARITY_UNDEFINED`;
- `CL1-R1-04 / BLOCKER_CORRECTION_BRANCHING_AND_BUNDLE_IDENTITY`;
- `CL1-R1-05 / BLOCKER_FAILURE_ORACLE_AMBIGUOUS`.

This document is the only authorized bounded correction batch for that fixed set. No second CL1 contract correction batch is authorized.

The required closure review is restricted to exactly `CL1-R1-01..05`. It may return only:

- `PASS / CL1-R1-01..05 CLOSED / MATERIAL FINDINGS 0`; or
- `RESCOPE` if any one of those five remains material.

No new unrelated finding may be admitted into the CL1 closure cycle. A separate future concern is deferred unless it proves that one of the five fixed blockers was not actually closed.

Contract acceptance, when separately granted after closure PASS, binds one exact commit and tree. PR/Issue text is status metadata, not canonical authority.

Even accepted contract status does not itself execute implementation. A separate CL1 implementation branch/action must start from the exact accepted contract head under the three-path implementation-delta allowlist.

## 22. Correction mapping and closure conditions

### `CL1-R1-01 / BLOCKER_CANONICAL_FORMS_UNFROZEN`

Correction: sections 10, 12, 13, 14, 15 and 17 now freeze exact keysets, JSON types, nesting, null/omission policy, canonical encoding, line-number portable representation, timestamp calendar semantics and clean-line known-answer bytes/SHA.

Closure condition: an independent consumer can reproduce SourceIdentity, Posting, full Transaction, economic Transaction and Bundle bytes/SHA without implementation-specific choices; unknown/extra/missing keys and unsafe line numbers fail closed.

### `CL1-R1-02 / BLOCKER_ASYMMETRIC_MIN_NOT_REVERSIBLE`

Correction: full asymmetric Money remains valid standalone, while posting amounts are constrained to the symmetric range `[-MONEY_MAX_MINOR_UNITS, +MONEY_MAX_MINOR_UNITS]` with exact `POSTING_OUT_OF_REVERSIBLE_RANGE` failure.

Closure condition: every accepted posting can be exactly negated into valid Money; standalone Money minimum remains representable but cannot enter a posting.

### `CL1-R1-03 / BLOCKER_ACCOUNTING_POLARITY_UNDEFINED`

Correction: section 11 freezes sign meaning and the exact two-posting classification/account/sign matrix; trade principal is structurally restricted to `ASSET_TRADE_CLEARING`, manual adjustments use a dedicated account.

Closure condition: inverted DEPOSIT/WITHDRAWAL and trade settlement through external-flow/income/expense accounts fail deterministically with `ACCOUNTING_PATTERN_INVALID`.

### `CL1-R1-04 / BLOCKER_CORRECTION_BRANCHING_AND_BUNDLE_IDENTITY`

Correction: section 15 freezes bundle domain/version/keyset/SHA plus a pure order-independent finite-set invariant: one distinct bundle per original, exact repeats are duplicate, second distinct branch is `LINEAGE_CONFLICT`.

Closure condition: known-answer bundle bytes/SHA reproduce exactly; two distinct bundles for one original cannot both be accepted under CL1 semantics regardless of order.

### `CL1-R1-05 / BLOCKER_FAILURE_ORACLE_AMBIGUOUS`

Correction: MoneyReason and LedgerReason are exact closed version-1 sets; sections 9 and 16 freeze ordered decision tables for every public constructor/parser/arithmetic/bundle boundary and nested-error propagation.

Closure condition: representative multi-invalid inputs have one exact primary reason independent of implementation ordering.

## 23. Contract exit state

Current authority after this single correction batch:

`CL1 CONTRACT CORRECTION CANDIDATE / CL1-R1-01..05 / IMPLEMENTATION BLOCKED`.

No CL1 code, tests, fixture, provider observation, persistence, runtime action, experiment, release or real-account action is authorized or performed by this correction batch.

Next permitted gate: finding-scoped closure review of `CL1-R1-01..05` only.