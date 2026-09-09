# v3.10 CL1 — Provider-exact Money + balanced CashLedger pure-domain contract

Status: `CL1 CONTRACT FREEZE CANDIDATE / IMPLEMENTATION NOT AUTHORIZED`

Parent program: Issue #144.  
Accepted predecessor: CL0 / M0 `ACCEPTED / INTEGRATED / COMPLETED`.

## 1. Exact predecessor and lineage

CL1 contract work starts only from the accepted/integrated stable-line predecessor:

- repository: `baimleriv/unified-portfolio-system`;
- program branch: `program/v3-10-v4-stable-line`;
- exact predecessor commit: `8f6f60b5d183ae9de50ebc12042879cbf02f9ef4`;
- exact predecessor tree: `93939505e52f62a4ed9b1a5eadb026a7b8c89759`;
- CL1 contract branch: `agent/v3-10-clean-cl1-contract-freeze`.

The predecessor lineage is `v3.9.0 -> accepted/integrated CL0` only. Contemporary `main`, historical v3.10/MoneyV2 branches and their commits are evidence/reference sources, not ancestry.

At this predecessor there is no `current/trading_robot/cash_ledger_domain.py`. CL1 therefore creates the first CashLedger pure-domain module on the clean stable line; it does not create a MoneyV1/MoneyV2 compatibility stack.

## 2. Mission

CL1 shall define one small, deterministic and infrastructure-free domain for:

1. exact T-Bank-compatible RUB Money preserving the complete `units+nano` value;
2. immutable content-bound source identity without raw broker Account ID or payload;
3. immutable ledger postings and balanced transactions;
4. deterministic canonical bytes and SHA-256 identities;
5. source/economic/transaction identity comparison;
6. strict original -> reversal -> correction bundle semantics.

CL1 is additive and non-owning. It must not change any accepted v3.9 runtime owner, cash value, reservation, Risk proof, provider path or persisted state.

## 3. Contract-freeze allowlist

This contract-freeze stage may change exactly one path:

- `docs/project/V3_10_CL1_PROVIDER_EXACT_CASH_LEDGER_CONTRACT_RU.md`.

Any other path change during contract freeze is a hard scope failure.

## 4. Future implementation delta allowlist

If and only if this contract is separately reviewed and explicitly accepted, a later CL1 implementation candidate may change exactly these three paths relative to the accepted contract head:

1. `current/trading_robot/cash_ledger_domain.py` — new pure-domain module;
2. `current/tests/test_v3_10_cash_ledger_domain.py` — new deterministic CL1 suite;
3. `current/tests/fixtures/v3_10_cash_ledger_vectors.json` — new language-neutral known-answer fixture.

The accepted contract file is immutable during implementation. Existing v3.9 source and tests are regression authority and are not implementation scope.

Thus the maximum cumulative CL1 integration allowlist relative to accepted CL0 is four paths: this contract plus the three implementation paths above. A fourth implementation-delta path or any modification of an existing v3.9 file requires `SCOPE_EXPANSION_REQUIRED` and stops CL1 for RESCOPE; it is not folded into a correction batch.

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
- `LEDGER_TRANSACTION_VERSION = 1`;
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
- `MONEY_MIN_MINOR_UNITS = WIRE_UNITS_MIN * NANO_FACTOR + WIRE_NANO_MIN`;
- `MONEY_MAX_MINOR_UNITS = WIRE_UNITS_MAX * NANO_FACTOR + WIRE_NANO_MAX`.

`Money.from_units_nano(units, nano, currency="RUB")` accepts plain integers only; bool is not an integer for this contract. Units are checked against signed int64 before composition, nano against the exact inclusive range, then sign consistency is checked: positive units cannot have negative nano and negative units cannot have positive nano; units zero may carry either nano sign.

The exact value is `units * 1_000_000_000 + nano`. No float, Decimal input, formatted amount or rounding participates in this identity path.

Direct construction from checked `minor_units` is allowed only within the asymmetric bound above.

### 8.3 Canonical Money representation

Exact logical keyset:

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

`minor_units` is a JSON string so cross-language consumers never depend on bounded JSON-number precision. Grammar is `0|-?[1-9][0-9]*`; `-0`, plus signs, leading zeros, exponent notation and whitespace are invalid.

`amount` has exactly nine fractional digits and must be derivable uniquely from `minor_units`. Zero is exactly `0.000000000`; negative zero is forbidden.

Canonical bytes are UTF-8/ASCII JSON with sorted keys, compact separators and no insignificant whitespace. `sha256` is lowercase SHA-256 of those exact bytes.

A convenience exact Decimal view may be derived from the canonical integer/string value, but Decimal input or Decimal formatting is never accepted as authoritative identity.

### 8.4 Arithmetic

`+`, `-` and unary negation are exact integer operations. Operands must be canonical Money with identical currency/scale/version. Result overflow outside the frozen asymmetric bound fails closed. Binary float is never accepted or returned by Money arithmetic.

## 9. Finite Money failure taxonomy

Implementation must expose a finite typed `MoneyReason` (exact names may be represented by a StrEnum) containing at least:

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

Each invalid input produces one deterministic primary reason. Tests assert the typed reason rather than unstable prose. Exception text must not expose raw provider identifiers or payloads.

Validation precedence is part of the contract: container/keyset/type -> version/domain literals -> currency/scale -> numeric grammar/ranges -> cross-field consistency -> arithmetic result.

## 10. SourceIdentity contract

`SourceIdentity` is immutable, content-bound and privacy-safe. Canonical fields are:

- `account_scope_sha256`: lowercase 64-hex opaque stable account-scope identity;
- `source_kind`: uppercase bounded token `[A-Z][A-Z0-9_]{0,63}`;
- `source_scope_sha256`: lowercase 64-hex digest identifying the logical source scope;
- `source_content_sha256`: lowercase 64-hex digest binding exact observed/synthetic content;
- `version = 1`;
- canonical domain `v3.10-cash-ledger-source`.

The pure domain never receives raw provider Account ID, token, operation ID, cursor, payload or authorization data. How an upstream adapter derives privacy-safe scope/content digests is explicitly deferred to CL3.

The exact source canonical bytes and their SHA-256 are deterministic. Source SHA is the authoritative source-idempotency identity supplied to later CL2/CL3 decisions.

## 11. Frozen ledger chart and classifications

### 11.1 Accounts, chart version 1

The initial finite chart is:

- `ASSET_BROKER_CASH`;
- `ASSET_TRADE_CLEARING`;
- `EQUITY_OPENING_BALANCE`;
- `EQUITY_EXTERNAL_FLOW`;
- `INCOME_DIVIDEND`;
- `INCOME_COUPON`;
- `INCOME_INTEREST`;
- `EXPENSE_COMMISSION`;
- `EXPENSE_TAX`.

### 11.2 Classifications, version 1

The initial finite classifications are:

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

CL1 validates structure, exact balance and provenance. It does not define provider-specific operation -> classification/posting-plan mappings; those belong to CL3.

## 12. LedgerPosting contract

`LedgerPosting` is immutable and contains:

- positive plain-integer `line_no`;
- one known `LedgerAccount`;
- one canonical non-zero `Money`.

Zero postings are forbidden. Unknown accounts, wrong Money types and invalid line numbers fail closed.

A transaction may contain multiple currencies in a future version, but CL1 Money currently supports RUB only. Balance is nevertheless defined per currency so the invariant does not need semantic reinterpretation when currency support is expanded.

## 13. LedgerTransaction contract

`LedgerTransaction` is immutable and contains:

- one known `LedgerClassification`;
- `effective_at`: canonical UTC RFC3339 timestamp with exactly nine fractional digits and terminal `Z` (`YYYY-MM-DDTHH:MM:SS.nnnnnnnnnZ`);
- one `SourceIdentity`;
- tuple of `LedgerPosting`;
- `classification_version = 1`;
- `chart_version = 1`;
- optional `reversal_of_sha256`;
- optional `corrects_sha256`;
- transaction domain `v3.10-cash-ledger-transaction`;
- transaction version `1`.

There is deliberately no arbitrary/random transaction UUID in CL1. The exact canonical transaction SHA is the transaction identity.

Requirements:

- at least two postings;
- line numbers positive and unique;
- canonical ordering by `line_no`;
- exact sum of posting minor units equals zero independently for every currency;
- `reversal_of_sha256` and `corrects_sha256` are mutually exclusive lowercase SHA-256 values or null;
- a normal transaction has neither lineage reference;
- `REVERSAL` requires `reversal_of_sha256` and must not contain `corrects_sha256`;
- a non-`REVERSAL` correction may contain `corrects_sha256` but not reversal reference;
- canonical parsing requires exact keysets/versions and byte-stable round-trip.

CL1 does not write or append a ledger history. History consistency beyond one validated correction bundle is CL2.

## 14. Deterministic transaction identities

Every transaction exposes three distinct identities:

1. `source_sha256` — exact `SourceIdentity` SHA;
2. `economic_sha256` — SHA-256 of an economic canonical form containing account scope, classification/version, chart version, effective timestamp and canonical postings, but excluding source evidence and lineage references;
3. `sha256` — SHA-256 of the full canonical transaction including source and lineage.

These axes must never be silently substituted for one another.

A pure comparison result uses a finite relation vocabulary:

- `EXACT_DUPLICATE`: full transaction SHA equal;
- `SOURCE_CONFLICT`: source SHA equal but full transaction SHA differs;
- `ECONOMIC_MATCH`: source SHA differs, full SHA differs, economic SHA equal;
- `DISTINCT`: none of the above.

`ECONOMIC_MATCH` is a review/idempotency signal, not proof that two independent broker observations are the same event. CL2/CL3 will define persistence/adjudication policy; CL1 only produces deterministic identities.

## 15. Reversal and correction bundle

`LedgerCorrectionBundle` is the only CL1 pure-domain object that certifies a correction lineage suitable for later persistence. It contains `(original, reversal, correction)` and validates all of the following:

- `original` is non-REVERSAL and has no lineage reference;
- `reversal.classification == REVERSAL`;
- `reversal.reversal_of_sha256 == original.sha256`;
- reversal has no `corrects_sha256`;
- reversal uses the same account scope as original;
- reversal postings are an exact one-to-one negation of the original canonical postings: same line numbers, accounts, currencies and scale, exact negative minor units;
- `correction` is non-REVERSAL;
- `correction.corrects_sha256 == original.sha256`;
- correction has no reversal reference and uses the same account scope;
- correction economic identity differs from original, so a no-op economic correction is rejected.

A reversal of a reversal is invalid. A correction of a reversal is invalid. A bundle targeting any hash other than the original exact SHA is invalid.

The bundle does not append anything and performs no I/O. CL2 must later reject lineage-bearing persistence writes that are not proven by the accepted bundle semantics.

## 16. Finite ledger failure taxonomy

Implementation must expose a finite typed `LedgerReason` containing at least:

- `TYPE_INVALID`;
- `HASH_INVALID`;
- `ACCOUNT_UNSUPPORTED`;
- `CLASSIFICATION_UNSUPPORTED`;
- `TIMESTAMP_INVALID`;
- `LINE_NUMBER_INVALID`;
- `ZERO_POSTING`;
- `DUPLICATE_LINE`;
- `UNBALANCED`;
- `SOURCE_INVALID`;
- `CANONICAL_FORMAT_INVALID`;
- `LINEAGE_INVALID`.

As with Money, multi-invalid inputs produce one deterministic primary reason and tests bind the reason taxonomy, not incidental exception prose.

## 17. Cross-language known-answer fixture

`current/tests/fixtures/v3_10_cash_ledger_vectors.json` is a future immutable language-neutral fixture for Python and v4 consumers. It must contain only public synthetic values and must not be read by production code.

At minimum it covers:

- Money zero, one nanoruble, one kopeck, negative sub-kopeck and asymmetric min/max;
- exact canonical Money JSON and SHA-256;
- one balanced deposit transaction;
- one trade-settlement transaction;
- one original/reversal/correction bundle;
- exact source/economic/full SHA identities.

All arbitrary-precision integers are represented as decimal strings. Fixture consumers independently reproduce canonical bytes and hashes without binary float.

## 18. Fixed acceptance matrix

The future implementation acceptance suite is frozen to the following vectors:

- `V310-CL1-01`: whole/kopeck/sub-kopeck `units+nano` -> exact scale-9 Money;
- `V310-CL1-02`: signed-int64 units plus nano extrema -> exact asymmetric Money bounds;
- `V310-CL1-03`: invalid types, units/nano bounds and sign combinations -> exact MoneyReason;
- `V310-CL1-04`: currency/scale normalization attempts and foreign currency -> fail closed;
- `V310-CL1-05`: canonical Money grammar/keyset/version/amount mismatch -> fail closed;
- `V310-CL1-06`: Money parse -> canonical bytes -> parse round-trip byte-identical;
- `V310-CL1-07`: exact arithmetic normal cases;
- `V310-CL1-08`: arithmetic overflow/incompatible operand cases;
- `V310-CL1-09`: Money immutability and no binary-float authority;
- `V310-CL1-10`: cross-language Money known-answer vectors and SHA exact;
- `V310-CL1-11`: SourceIdentity canonical bytes/hash and raw-account/payload absence;
- `V310-CL1-12`: account/classification/version unknown values fail closed;
- `V310-CL1-13`: balanced DEPOSIT/WITHDRAWAL transactions accepted;
- `V310-CL1-14`: DIVIDEND/COUPON/INTEREST/COMMISSION/TAX balanced examples accepted;
- `V310-CL1-15`: separate BUY-like and SELL-like TRADE_SETTLEMENT, REFUND and MANUAL_ADJUSTMENT balanced examples accepted;
- `V310-CL1-16`: zero posting, duplicate/invalid line and unbalanced transaction rejected;
- `V310-CL1-17`: effective-at exact nine-digit UTC grammar and invalid timestamp cases;
- `V310-CL1-18`: posting order normalization and repeated transaction serialization/hash deterministic;
- `V310-CL1-19`: exact source/economic/full identity relation matrix;
- `V310-CL1-20`: exact reversal negation and provenance target;
- `V310-CL1-21`: valid original -> reversal -> correction bundle;
- `V310-CL1-22`: reversal-of-reversal, correction-of-reversal, wrong target/account and no-op correction rejected;
- `V310-CL1-23`: AST/import boundary proves no infrastructure/runtime/provider ownership dependency;
- `V310-CL1-24`: exact changed-file allowlist plus complete unchanged v3.9 regression suite.

No acceptance vector requires network, broker credentials, provider calls, filesystem state, current time, GUI or runtime startup.

## 19. Verification contract for future implementation

Before an implementation candidate may enter independent review it must pass, locally and deterministically:

1. dedicated `test_v3_10_cash_ledger_domain.py` acceptance matrix;
2. independent fixture schema/bytes/SHA verification;
3. critical/static/style checks for the new production/test Python files;
4. compile check for the new production module;
5. full unchanged v3.9 regression suite;
6. `git diff --check`;
7. exact three-path implementation-delta allowlist check;
8. complete diff review proving no runtime import/call-graph change.

Green tests do not grant implementation acceptance, publication, runtime, experiment or release authority.

## 20. Historical evidence disposition

Historical #49 and historical MoneyV2 M2-A/M2-B work are reference evidence only. CL1 deliberately retains their validated lessons — exact Money, deterministic canonical bytes, balancing, reversal/correction, typed fail-closed errors and cross-language fixtures — while removing historical dual-version/migration/governance baggage.

In particular:

- there is one clean scale-9 Money domain, not MoneyV1 + MoneyV2;
- there is no persistence/repository/mixed-history implementation in CL1;
- raw broker/account identifiers are absent from canonical source/transaction evidence;
- provider normalization and operation classification are not pulled forward from CL3.

Historical accepted bytes/hashes are not claimed as CL1 bytes and are not copied as authority.

## 21. Bounded review and acceptance protocol

This contract candidate requires one independent/adversarial CL1 contract review before implementation.

Possible review result is either:

- `PASS`; or
- one finite fixed set `CL1-R1-xx` of material findings.

If findings exist, at most one bounded correction batch is authorized after explicit disposition of that fixed set. Closure review is restricted to those IDs. If any material blocker remains after closure, CL1 disposition is `RESCOPE`; a second correction round is forbidden.

Contract acceptance, when separately granted, binds one exact commit and tree. PR/Issue text is status metadata, not canonical authority.

Even accepted contract status does not itself execute implementation. A separate CL1 implementation branch/action must start from the exact accepted contract head under the three-path implementation-delta allowlist.

## 22. Contract exit state

This document is ready only for bounded independent contract review.

Current authority after creation:

`CL1 CONTRACT FREEZE CANDIDATE / IMPLEMENTATION BLOCKED`.

No CL1 code, tests, fixture, provider observation, persistence, runtime action, experiment, release or real-account action is authorized or performed by this contract freeze.
