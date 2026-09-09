# v3.10 CL2 — Append-only persistence + OperationInbox contract

Status: `CL2 CONTRACT CORRECTION CANDIDATE / CL2-R1-01..04 / IMPLEMENTATION BLOCKED`.

Parent program: Issue #144.

Historical evidence: Issues #50 and #79.
Accepted predecessor: CL1 `ACCEPTED / INTEGRATED / COMPLETED`.

## 1. Exact predecessor and lineage

CL2 contract work starts only from the accepted and integrated CL1 head:

- repository: `baimleriv/unified-portfolio-system`;
- program branch: `program/v3-10-v4-stable-line`;
- exact predecessor commit: `095a24a0d8ad2487f09ec0c5f3473a6c65a84710`;
- exact predecessor tree: `f8ce7df1d59260f6d458533cc5d84e86384dbf53`;
- CL2 contract branch: `agent/v3-10-clean-cl2-contract-freeze`.

The permitted lineage is `v3.9.0 -> accepted CL0 -> accepted CL1` only. Contemporary `main`, historical v3.10/MoneyV2 branches and their commits are evidence sources, not ancestry or integration authority.

The contract-candidate commit and tree are recorded externally after the one-file commit is created. This document does not contain a self-referential candidate SHA.

## 2. Mission

CL2 defines the first local persistent custody layer for the exact CL1 CashLedger domain:

1. one isolated SQLite schema version 1;
2. immutable sanitized `OperationInbox` observations and append-only status events;
3. append-only accepted CL1 transactions, postings and provenance links;
4. exact CL1 source, full, economic and correction-bundle identity indexes;
5. separate monotonic store and ledger revisions with expected-revision CAS;
6. deterministic duplicate, conflict and retry behavior;
7. atomic original -> reversal -> correction persistence;
8. a versioned deterministic ledger head;
9. fail-closed startup, WAL, locking, crash and integrity behavior;
10. deterministic sanitized export;
11. backup, verification and isolated no-clobber restore.

CL2 is generic local evidence custody. It does not observe a provider, classify provider operations, prove current broker cash, or own runtime cash state.

## 3. Contract-freeze allowlist

This contract freeze and any finding-scoped contract correction may change exactly one repository path:

- `docs/project/V3_10_CL2_APPEND_ONLY_PERSISTENCE_CONTRACT_RU.md`.

Any second changed path is `SCOPE_FAILURE -> RESCOPE`. No production code, test, fixture, workflow, CI, release, runtime or accepted CL1 file may change during this gate.

## 4. Proposed future implementation allowlist

If and only if one exact CL2 contract commit/tree receives independent read-only `PASS/APPROVE` and explicit contract acceptance, a later implementation branch must start from that exact accepted contract head and may change exactly these three paths:

1. `current/trading_robot/cash_ledger_persistence.py` — new generic persistence module;
2. `current/tests/test_v3_10_cash_ledger_persistence.py` — new deterministic CL2 suite;
3. `current/tests/fixtures/v3_10_cash_ledger_persistence_vectors.json` — new language-neutral known-answer fixture.

The accepted contract file and all CL1 files are immutable during implementation. Relative to the exact CL1 predecessor, the maximum cumulative CL2 surface is this contract plus those three implementation paths.

A fourth implementation path, a change to an existing path, or a need for migration/provider/runtime wiring stops implementation with `SCOPE_EXPANSION_REQUIRED -> RESCOPE`.

No `__init__.py`, dependency manifest, CI, GUI, Portfolio, Central, Risk, Execution, provider or release path is included.

## 5. Authority and explicit non-goals

CL2 does not authorize or perform:

- provider REST/protobuf adapters, authentication, pagination, retry or normalization — CL3;
- provider-specific operation classification or a T-Bank-specific inbox schema — CL3;
- opening balance, historical backfill, current-cash proof or reconciliation — CL4;
- CashAvailability or reservation projection — CL5;
- reporting, performance or Risk cash context — CL6;
- runtime ownership cutover, startup adoption, GUI/operator wiring or experiment — CL7;
- release, standalone packaging or burn-in — CL8;
- provider POST, order dispatch, pay-in, transfer or any broker mutation;
- private credential access, authenticated observation or real-account action;
- migration of historical MoneyV1/MoneyV2 databases or mixed-version history;
- automatic repair, schema upgrade or downgrade.

`PortfolioRepository`, `CentralOrderManager` and `ExecutionAdapter` retain the accepted ownership boundaries. A store, export or restored copy is evidence/history only and cannot become authoritative current cash without a later accepted cutover.

## 6. Dependency and side-effect boundary

The CL2 production module may import the accepted `cash_ledger_domain.py` and pure Python standard-library facilities needed for SQLite, paths, JSON, hashing, immutable values and explicit locking/error handling.

It must not import or call:

- network clients, provider SDKs or provider payload types;
- GUI/Tkinter;
- Portfolio, Central, Risk, Execution, scheduler or runtime modules;
- credentials, environment-based account selection or secrets;
- system clock, randomness or UUID generation for authoritative identities;
- migration or historical MoneyV1/MoneyV2 modules.

Importing the module performs no I/O, opens no database and registers no runtime behavior. Every store path, observation value, timestamp, revision and codec registry is supplied explicitly by the caller.

## 7. Frozen version axes and primitives

The clean line starts at version 1:

- `CASH_LEDGER_STORE_SCHEMA_VERSION = 1`;
- `OPERATION_INBOX_VERSION = 1`;
- `OPERATION_INBOX_CODEC_VERSION = 1`;
- `OPERATION_INBOX_STATUS_EVENT_VERSION = 1`;
- `LEDGER_HEAD_VERSION = 1`;
- `CASH_LEDGER_EXPORT_VERSION = 1`;
- `CASH_LEDGER_BACKUP_VERSION = 1`;
- `CL2_CROSS_LANGUAGE_FIXTURE_VERSION = 1`.

These axes are independent from all CL1 domain versions. Unknown, missing, Boolean-as-integer, fractional, negative or newer values fail closed; CL2 performs no coercion.

Normative primitives:

- SHA-256 text is exactly 64 lowercase hexadecimal ASCII characters;
- a token is `[A-Z][A-Z0-9_]{0,63}`;
- revisions and ordered sequence numbers are integers in `0..9223372036854775807` in memory and SQLite, but canonical JSON represents them as minimal decimal strings;
- timestamps reuse the exact CL1 UTC nanosecond grammar and Gregorian validation; CL2 never reads a clock;
- canonical JSON is UTF-8/ASCII bytes of sorted object keys, separators `,` and `:`, `ensure_ascii=true`, `allow_nan=false`, no BOM and no trailing newline;
- objects have exact keysets; unknown, missing or duplicate JSON keys fail;
- binary float is forbidden in every authoritative or exported value.

Canonical JSON examples shown below are literal ASCII byte sequences.

## 8. Exact CL1 identity preservation

CL2 parses all supplied CL1 values through the accepted CL1 public parsers and persists their reproduced canonical bytes as BLOBs. It must never normalize, reconstruct with a different schema, or trust a caller-supplied hash without recomputation.

For every accepted transaction CL2 stores and rechecks:

- exact transaction canonical bytes and full SHA;
- exact source canonical bytes and source SHA;
- exact economic canonical bytes and economic SHA;
- exact nested posting canonical bytes and posting SHA values;
- exact correction-bundle canonical bytes and bundle SHA when applicable.

The authoritative relations remain exactly the CL1 `IdentityRelation` values:

- `EXACT_DUPLICATE`;
- `SOURCE_CONFLICT`;
- `ECONOMIC_MATCH`;
- `DISTINCT`.

`ECONOMIC_MATCH` is review evidence and never grants automatic duplicate suppression or economic acceptance.

## 9. Codec registry and sanitized-content boundary

An observation may use only a descriptor registered explicitly before store creation/open. CL2 interprets the descriptor's declarative schema itself; the registry cannot supply or replace validation code. The exact descriptor keyset is:

```text
{"codec_id":"<TOKEN>","codec_version":1,"domain":"v3.10-operation-inbox-codec","schema_json_ascii":"<exact canonical schema JSON text>","schema_sha256":"<lowercase-sha256>","version":1}
```

`schema_json_ascii` is the complete schema preimage. `schema_sha256` must equal `SHA256(ASCII(schema_json_ascii))`; descriptor SHA is SHA-256 of the complete descriptor canonical bytes. The store persists the complete descriptor used by every accepted observation.

The version-1 schema is a canonical JSON object with exact keyset `domain`, `fields`, `version`:

```json
{
  "domain": "v3.10-operation-inbox-codec-schema",
  "fields": [
    {
      "allowed_values": null,
      "key": "operation_kind",
      "kind": "STRING",
      "maximum": null,
      "max_scalars": "64",
      "minimum": null,
      "required": true
    }
  ],
  "version": 1
}
```

Schema rules are exact:

- `schema_json_ascii` is at most `65536` ASCII bytes; `fields` has `1..256` entries ordered by strictly increasing ASCII `key`;
- every entry has exactly `allowed_values`, `key`, `kind`, `maximum`, `max_scalars`, `minimum`, `required`;
- `key` matches `[a-z][a-z0-9_]{0,63}` and is unique;
- `required` is a JSON Boolean;
- `kind` is exactly `STRING`, `INTEGER`, `BOOLEAN` or `NULL`;
- for `STRING`, `max_scalars` is a minimal decimal string in `1..4096`, `minimum`/`maximum` are null, and `allowed_values` is null or an array of `1..256` unique strings ordered by each value's canonical JSON-string ASCII bytes; each value contains only Unicode scalar values and respects the same scalar limit;
- for `INTEGER`, `minimum` and `maximum` are minimal decimal strings in `[-9007199254740991,9007199254740991]` with `minimum <= maximum`, while `max_scalars` and `allowed_values` are null;
- for `BOOLEAN` and `NULL`, all four constraint fields are null;
- no other schema domain/version, key, kind, constraint combination or field order is accepted.

Sanitized content is one flat JSON object. Its keys are exactly all required schema keys plus any subset of optional keys; undeclared keys and missing required keys fail. Values must match the declared JSON primitive type and constraints exactly. Strings contain Unicode scalar values with no normalization; lone surrogates are invalid. Arrays, nested objects, JSON float, NaN, infinity, negative-zero number text and integers outside the safe range are forbidden. Canonical content is at most `65536` ASCII bytes.

The following schema keys are forbidden after ASCII lowercase comparison: `account_id`, `broker_account_id`, `token`, `access_token`, `refresh_token`, `authorization`, `cookie`, `set_cookie`, `headers`, `raw_payload`, `provider_payload`, `secret`, `password`, `api_key`, `credential`. Raw provider payloads, credentials, transport headers, raw broker Account ID and unbounded provider text remain forbidden regardless of key spelling; later provider contract review must prove that its finite field schema contains only sanitized values.

Opening, exporting, backing up or restoring requires a supplied registry containing byte-identical complete descriptors for every stored codec. CL2 first validates the stored and supplied schema bytes/hash itself and then evaluates content through the generic rules above. Missing, newer, changed or internally inconsistent descriptors fail closed; repeating the claimed hash string without its exact preimage cannot pass.

The CL2 production module contains no provider or test codec. Acceptance tests supply one public synthetic descriptor. Provider descriptors belong to later reviewed scope.

Frozen synthetic schema bytes:

```text
{"domain":"v3.10-operation-inbox-codec-schema","fields":[{"allowed_values":null,"key":"operation_kind","kind":"STRING","max_scalars":"64","maximum":null,"minimum":null,"required":true}],"version":1}
```

Schema SHA-256:

`d21206b6c54576f60fa9923817bc4b59f8b8ed04942be4a53ca5c3eb304e858b`

Frozen `SYNTHETIC_CL2` descriptor bytes:

```text
{"codec_id":"SYNTHETIC_CL2","codec_version":1,"domain":"v3.10-operation-inbox-codec","schema_json_ascii":"{\"domain\":\"v3.10-operation-inbox-codec-schema\",\"fields\":[{\"allowed_values\":null,\"key\":\"operation_kind\",\"kind\":\"STRING\",\"max_scalars\":\"64\",\"maximum\":null,\"minimum\":null,\"required\":true}],\"version\":1}","schema_sha256":"d21206b6c54576f60fa9923817bc4b59f8b8ed04942be4a53ca5c3eb304e858b","version":1}
```

Descriptor SHA-256:

`43d6d99e633e8806f7efc11f70816fd8236df824d775b5cdf18626c93abfa1b4`

Frozen sanitized content bytes:

```text
{"operation_kind":"SYNTHETIC"}
```

Content SHA-256:

`ed0306ea59619e88016ad6d2790594b4a5b19ccb9ec5a0a49b746c50b4dc9bc5`

## 10. OperationInbox canonical objects

### 10.1 Logical source identity

`logical_source_sha256` identifies one source slot without its content hash. Its exact canonical object is:

```json
{
  "account_scope_sha256": "<CL1 account-scope SHA>",
  "domain": "v3.10-operation-inbox-logical-source",
  "source_kind": "<CL1 source-kind token>",
  "source_scope_sha256": "<CL1 source-scope SHA>",
  "version": 1
}
```

Its SHA-256 is `logical_source_sha256`. It is unique in one store. It deliberately excludes `source_content_sha256`, so changed content for an already accepted logical source cannot masquerade as a new unrelated observation.

### 10.2 Immutable observation

An `InboxObservationV1` exact keyset is:

```json
{
  "codec_id": "<TOKEN>",
  "codec_schema_sha256": "<lowercase-sha256>",
  "codec_version": 1,
  "content_json_ascii": "<exact canonical sanitized JSON text>",
  "domain": "v3.10-operation-inbox-observation",
  "initial_status": "OBSERVED",
  "logical_source_sha256": "<lowercase-sha256>",
  "observed_at": "<CL1 canonical UTC nanosecond timestamp>",
  "provenance_sha256": "<lowercase-sha256>",
  "source": {
    "account_scope_sha256": "<lowercase-sha256>",
    "domain": "v3.10-cash-ledger-source",
    "source_content_sha256": "<lowercase-sha256>",
    "source_kind": "<TOKEN>",
    "source_scope_sha256": "<lowercase-sha256>",
    "version": 1
  },
  "version": 1
}
```

`observed_at` and `provenance_sha256` are caller-supplied sanitized evidence. They are never generated by CL2.

Validation additionally requires:

1. descriptor fields match a registered codec byte-for-byte;
2. `content_json_ascii` is ASCII text containing exactly the schema-validated canonical bytes reproduced by CL2;
3. `SHA256(ASCII(content_json_ascii)) == source.source_content_sha256`;
4. recomputed logical-source bytes/SHA equal `logical_source_sha256`;
5. `initial_status` is exactly `OBSERVED`;
6. the nested source round-trips through CL1 byte-identically.

`observation_sha256` is SHA-256 of the complete observation canonical bytes. The observation row and its canonical BLOB are immutable after commit.

### 10.3 Append-only status event

Current inbox status is derived from the initial `OBSERVED` value plus ordered immutable events; it is not updated in place. An event exact keyset is:

```json
{
  "domain": "v3.10-operation-inbox-status-event",
  "event_no": "1",
  "from_status": "OBSERVED",
  "observation_sha256": "<lowercase-sha256>",
  "reason": "REVIEW_REQUIRED",
  "related_bundle_sha256": null,
  "related_transaction_sha256": null,
  "to_status": "REVIEW_REQUIRED",
  "version": 1
}
```

Closed status set:

- `OBSERVED`;
- `REVIEW_REQUIRED`;
- `LEDGER_LINKED`;
- `REJECTED`.

Closed reason set and exact transition constraints:

| reason | permitted transition | references |
|---|---|---|
| `REVIEW_REQUIRED` | `OBSERVED -> REVIEW_REQUIRED` | both null |
| `NOT_LEDGER_RELEVANT` | `OBSERVED/REVIEW_REQUIRED -> REJECTED` | both null |
| `INVALID_OBSERVATION` | `OBSERVED/REVIEW_REQUIRED -> REJECTED` | both null |
| `LEDGER_TRANSACTION_ACCEPTED` | `OBSERVED/REVIEW_REQUIRED -> LEDGER_LINKED` | transaction non-null, bundle null |
| `LEDGER_CORRECTION_ACCEPTED` | `OBSERVED/REVIEW_REQUIRED -> LEDGER_LINKED` | transaction null, bundle non-null |

`event_no` starts at `1` and is contiguous per observation. `LEDGER_LINKED` and `REJECTED` are terminal. Event SHA is SHA-256 of its canonical bytes. A transaction/correction-linked event may be created only atomically by the corresponding ledger operation; the generic status API cannot forge it. A correction event names the bundle because the same observation may evidence both of its new transactions; exact transaction links remain in the provenance-link table. If a correction component reuses the original transaction's already `LEDGER_LINKED` observation, the bundle adds its provenance link but no second terminal status event.

An observation is evidence. `OBSERVED` or `REVIEW_REQUIRED` status has no economic effect.

## 11. Duplicate and conflict dispositions

Successful mutators return one exact `PersistenceDisposition`:

- `OBSERVATION_STORED`;
- `OBSERVATION_ALREADY_PRESENT`;
- `STATUS_EVENT_APPENDED`;
- `STATUS_EVENT_ALREADY_PRESENT`;
- `TRANSACTION_APPENDED`;
- `TRANSACTION_ALREADY_PRESENT`;
- `CORRECTION_BUNDLE_APPENDED`;
- `CORRECTION_BUNDLE_ALREADY_PRESENT`.

Duplicate is a successful no-op disposition, not an exception. Exact behavior:

| candidate relation to trusted state | result |
|---|---|
| byte-identical observation SHA and logical source | `OBSERVATION_ALREADY_PRESENT` |
| same logical source, any different observation canonical bytes | `SOURCE_CONTENT_CONFLICT` |
| fully distinct logical source | eligible for `OBSERVATION_STORED` |
| byte-identical next/already committed status event | `STATUS_EVENT_ALREADY_PRESENT` |
| same event number or transition with different bytes | `STATUS_TRANSITION_INVALID` |
| CL1 `EXACT_DUPLICATE` transaction with identical provenance link | `TRANSACTION_ALREADY_PRESENT` |
| same transaction SHA with different provenance link | `SOURCE_CONFLICT` |
| CL1 `SOURCE_CONFLICT` | `SOURCE_CONFLICT` |
| CL1 `ECONOMIC_MATCH` | `ECONOMIC_MATCH_REVIEW_REQUIRED` |
| CL1 `DISTINCT` | eligible for append |
| exact committed correction bundle and identical links | `CORRECTION_BUNDLE_ALREADY_PRESENT` |
| second distinct bundle for one original | `LINEAGE_CONFLICT` |

No duplicate advances a revision or head and no conflict is silently persisted. A later caller may record an explicit review status only as a separate authorized status mutation against an existing observation.

## 12. Store and ledger revisions

Both revisions start at `0`, are monotonic, never wrap and are checked inside the same SQLite write transaction as the mutation.

| committed operation | store revision | ledger revision | ledger head |
|---|---:|---:|---|
| create empty store | `0` | `0` | genesis |
| new observation | `+1` | unchanged | unchanged |
| new non-ledger status event | `+1` | unchanged | unchanged |
| new ordinary transaction plus provenance/status | `+1` | `+1` | one `TRANSACTION` transition |
| new correction bundle plus both transactions/provenance/status events | `+1` | `+1` | one `CORRECTION_BUNDLE` transition |
| exact duplicate/read/export/verify/backup | unchanged | unchanged | unchanged |
| rejected or rolled-back operation | unchanged | unchanged | unchanged |

Every mutator receives `expected_store_revision`; ledger mutators also receive `expected_ledger_revision`. Both are plain integers in range. For a valid, fully verified store, decision order inside `BEGIN IMMEDIATE` is:

1. recognize an exact committed retry and return its duplicate disposition even if supplied expected revisions are now stale;
2. otherwise compare expected revisions and fail `REVISION_MISMATCH` before mutation;
3. evaluate missing references and identity/lineage conflicts;
4. apply the whole mutation and both revision/head updates in one transaction.

The duplicate-before-CAS rule exists only for byte-identical committed retry recovery. It cannot convert a changed request into success. At `9223372036854775807`, an operation that would advance a revision fails `REVISION_EXHAUSTED` with no mutation.

## 13. Ledger append and provenance rules

An ordinary append accepts exactly one validated CL1 `LedgerTransaction`, one existing `observation_sha256`, and both expected revisions.

It must enforce:

- the transaction is not `REVERSAL` and both lineage fields are null;
- the observation current status is `OBSERVED` or `REVIEW_REQUIRED`;
- its nested CL1 SourceIdentity canonical bytes equal the transaction source bytes;
- no trusted transaction has a conflicting source or economic relation;
- the transaction, all postings, the transaction-observation link, one `LEDGER_LINKED` status event, revision updates and head transition commit atomically;
- every accepted transaction has exactly one provenance observation;
- posting rows reproduce the transaction's nested posting order and bytes exactly.

Reversal or correction transactions are never accepted through ordinary append. They enter only through the bundle operation in section 14.

## 14. Atomic correction bundle

The bundle operation accepts one validated CL1 `LedgerCorrectionBundle`, ordered existing observation SHA values for its reversal and correction, and both expected revisions. The two SHA values may be equal.

Before mutation it requires:

1. the exact original transaction is already present as an ordinary accepted ledger transition;
2. the stored original bytes equal the bundle's original bytes;
3. neither reversal nor correction is already present outside the same exact committed bundle;
4. both referenced observations exist and exactly match the respective transaction sources;
5. the accepted CL1 finite bundle-set invariant remains valid;
6. all source/economic comparisons against transactions trusted before this bundle are conflict-free except the exact original relationship required by the bundle.

Reversal and correction are compared as components of their already validated CL1 bundle, not as two independent append candidates. If their SourceIdentity canonical bytes are equal, both must reference the same observation. If their sources differ, each must reference its own matching observation. A referenced observation must be `OBSERVED`/`REVIEW_REQUIRED`, or it may already be `LEDGER_LINKED` only when its existing provenance link is exclusively to this bundle's original transaction. `REJECTED` and every other pre-linked state fail. This bundle path is the only case in which one observation may evidence more than one transaction.

One successful call atomically inserts:

- the reversal transaction and its postings;
- the correction transaction and its postings;
- both provenance links;
- one `LEDGER_CORRECTION_ACCEPTED` status event per distinct referenced observation that was not already `LEDGER_LINKED` through the original, each referring to the bundle and no individual transaction;
- one immutable bundle row;
- one ledger-transition row;
- one store-revision and one ledger-revision advancement;
- one new ledger head.

There is no intermediate trusted revision for reversal alone or correction alone. Exact replay returns `CORRECTION_BUNDLE_ALREADY_PRESENT`; a second distinct bundle for the original fails `LINEAGE_CONFLICT`.

## 15. Versioned ledger-head identity

The exact head object keyset is:

```json
{
  "domain": "v3.10-cash-ledger-head",
  "ledger_revision": "0",
  "previous_head_sha256": null,
  "transition_kind": "GENESIS",
  "transition_sha256": null,
  "version": 1
}
```

Closed transition-kind set:

- `GENESIS` only at revision `0`, with both SHA fields null;
- `TRANSACTION` for one accepted ordinary transaction;
- `CORRECTION_BUNDLE` for one accepted complete bundle.

For revision `n > 0`, canonical head bytes contain decimal string `ledger_revision=str(n)`, `previous_head_sha256=head(n-1)`, the non-genesis kind, and `transition_sha256` equal to the accepted CL1 transaction or bundle SHA. `head(n) = SHA256(canonical_head_bytes(n))`.

The ledger-transition table contains exactly one contiguous row for every revision `1..ledger_revision`. Deterministic replay starts from genesis, recomputes each CL1 identity and each head link, and must reproduce the stored revision/head exactly.

Inbox-only mutations never participate. Wall clock, path, rowid, store revision, lock timing, random value and SQLite physical bytes never participate.

Frozen known answers:

```text
{"domain":"v3.10-cash-ledger-head","ledger_revision":"0","previous_head_sha256":null,"transition_kind":"GENESIS","transition_sha256":null,"version":1}
```

Genesis SHA-256:

`6ee5e86309122771bcaca40bb57771c2378c30d79b079e5431b227300a673d37`

Using the CL1 sample transaction `bb14525732cba2e2050c2743c30c07a7cbbb705ffc3ded42fa8f58368c6a7b53`:

```text
{"domain":"v3.10-cash-ledger-head","ledger_revision":"1","previous_head_sha256":"6ee5e86309122771bcaca40bb57771c2378c30d79b079e5431b227300a673d37","transition_kind":"TRANSACTION","transition_sha256":"bb14525732cba2e2050c2743c30c07a7cbbb705ffc3ded42fa8f58368c6a7b53","version":1}
```

Revision-1 head SHA-256:

`980c7bf507e5724dd9de79492f3f1c2f5f235c1da4f9b31b512997eebc2714c2`

Using the CL1 sample bundle `73a8a1776c5f6e6262bed3a92628fc29f62b11a75d17334d47a12a702351799f` next:

```text
{"domain":"v3.10-cash-ledger-head","ledger_revision":"2","previous_head_sha256":"980c7bf507e5724dd9de79492f3f1c2f5f235c1da4f9b31b512997eebc2714c2","transition_kind":"CORRECTION_BUNDLE","transition_sha256":"73a8a1776c5f6e6262bed3a92628fc29f62b11a75d17334d47a12a702351799f","version":1}
```

Revision-2 head SHA-256:

`b739f6edc1fe1d4bf6feeaf776eaeb9cc45a98fe6be90e4f5b6a27805a730b04`

## 16. SQLite schema and append-only boundary

The store uses SQLite application ID `0x434C3201`, `PRAGMA user_version=1`, and an application meta row independently binding schema version `1`, revisions and head.

Allowed application tables are exactly:

- `cl2_meta`;
- `cl2_codec`;
- `cl2_observation`;
- `cl2_inbox_status_event`;
- `cl2_transaction`;
- `cl2_posting`;
- `cl2_transaction_observation`;
- `cl2_correction_bundle`;
- `cl2_ledger_transition`.

Allowed explicit indexes are exactly:

- `cl2_idx_observation_source`;
- `cl2_idx_status_observation`;
- `cl2_idx_transaction_source`;
- `cl2_idx_transaction_economic`;
- `cl2_idx_posting_transaction`;
- `cl2_idx_link_observation`;
- `cl2_idx_bundle_original`.

SQLite-owned `sqlite_*` objects with `sql IS NULL` are allowed only when generated by constraints on an allowed table. Any other table, index, trigger, view, virtual table, partial object or duplicate name is `SCHEMA_INVALID`. Triggers and views are not used.

Exact SQL spelling, page layout and query plans are implementation details. The implementation and fixture must nevertheless bind the accepted layout with one schema fingerprint: select every allowed application row from `sqlite_schema`, require non-null `sql`, map it to an exact-key object `{"name":...,"sql":...,"tbl_name":...,"type":...}`, order by `(type,name,tbl_name)`, canonicalize the JSON array by section 7 and take SHA-256. Implementation tests must prove that only the object allowlist above plus permitted SQLite-owned autoindexes is present.

The singleton `cl2_meta` semantic row contains exactly one schema-version value, `store_revision`, `ledger_revision`, current head canonical bytes and current head SHA. Those values must agree with `user_version`, the transition replay and each other. Incidental SQL column spelling is not exported or authoritative.

Observation, codec, status, transaction, posting, link, bundle and transition rows are insert-only. `UPDATE` and `DELETE` against those semantic tables are forbidden. Only the singleton meta row may update atomically to advance revisions/head. WAL checkpointing may change physical files but cannot change semantic rows.

## 17. Store-root and path custody

One store is one caller-supplied absolute local directory containing:

- `store.sqlite3`;
- while open, optional SQLite-owned `store.sqlite3-wal` and `store.sqlite3-shm`.

No other child is allowed. Relative paths, URI filenames, UNC/network paths, alternate data streams, symlinks/reparse points, non-regular database files and hard-linked database files are `PATH_INVALID`.

Creation requires an existing safe parent and absent target root. It creates and validates an exclusively owned sibling staging directory named `<target-name>.cl2-create-staging`, then atomically renames it to the still-absent target. Existing target, sidecar or staging names are `PATH_COLLISION`; CL2 never deletes or overwrites them.

Opening requires an existing store root and database file. Missing root/file is `STORE_MISSING`. CL2 never creates a database as a side effect of `open`.

## 18. Transactions, WAL, locking and restart

Every connection must enable and verify:

- `foreign_keys=ON`;
- `journal_mode=WAL` for a live store;
- `synchronous=FULL`;
- `trusted_schema=OFF`;
- caller-supplied `busy_timeout_ms` as a plain integer in `0..60000`.

The implementation requires a SQLite build supporting all mandatory pragmas; otherwise it fails `VERSION_UNSUPPORTED`. Every mutation uses one `BEGIN IMMEDIATE` transaction. Writers serialize through SQLite locking; CL2 performs no hidden retry/backoff. Failure to acquire/retain the lock within the supplied timeout is `STORE_BUSY`.

Readers use one SQLite snapshot and report one internally consistent `(store_revision, ledger_revision, ledger_head)` tuple. A writer committed after that snapshot is not partially visible.

A valid committed WAL may be replayed by SQLite on open. An orphan sidecar, rollback journal in this WAL-only store, invalid WAL header/checksum/frame sequence, database/sidecar ownership mismatch, or unrecoverable hot state is `WAL_SIDECAR_INCONSISTENT`. CL2 does not delete, truncate or replace suspect evidence.

Each mutation has test-only fault-injection points before the transaction, after each semantic write group, before meta/head update, before commit and immediately after commit before return. On restart, validation must expose exactly the previous complete state or the new complete state. An injected pre-commit interruption observed by the caller maps to `INTERRUPTED_TRANSACTION`; a real process loss has no return value. An after-commit retry is recognized by the exact duplicate rule and cannot create a second effect.

## 19. Startup and integrity validation

No store is trusted until startup validation succeeds in this order:

1. path/root/file/sidecar envelope;
2. SQLite header and application ID;
3. exact `user_version` and application schema version;
4. exact schema object allowlist and schema fingerprint;
5. SQLite `PRAGMA integrity_check` returning exactly one row `ok`;
6. empty `PRAGMA foreign_key_check`;
7. codec descriptor availability and identity;
8. canonical BLOB parsing and byte-identical CL1/inbox reconstruction;
9. relational and semantic replay checks;
10. exact revisions and ledger-head reproduction.

Semantic checks include at least:

- singleton meta invariants and revision ranges;
- `ledger_revision == count(cl2_ledger_transition)` and revisions are contiguous;
- `store_revision == count(cl2_observation) + count(non-ledger status events) + ledger_revision`, where non-ledger events are exactly the first three reason rows in section 10.3;
- unique logical source, observation, transaction, posting, bundle and transition identities;
- canonical bytes/hash agreement for every stored object;
- sanitized content/hash/codec agreement;
- contiguous valid status-event chains and terminal-state rules;
- exact posting projection from each transaction;
- exactly one provenance observation per transaction;
- provenance source bytes equal transaction source bytes;
- exactly one transaction-linked status event for an ordinary append and one bundle-linked status event per newly linked distinct correction observation;
- a correction observation already linked exclusively to the original gains no second terminal event; every other pre-linked correction observation is invalid;
- shared correction observation occurs only when reversal/correction source bytes are equal and its additional links are exactly the applicable transactions in one bundle;
- no ordinary append containing correction lineage;
- complete two-transaction correction bundles and one bundle per original;
- contiguous ledger revisions, valid transition kind/target and complete head chain;
- store revision not less than ledger revision.

Missing/older/newer/partial schema is rejected. There is no create-on-open, best-effort read, row skipping, rehashing, default insertion, canonical rewrite, revision reset or automatic migration/repair.

## 20. Deterministic sanitized export

Export runs from one validated read snapshot and changes no revision. Its top-level exact keyset is:

```json
{
  "codec_registry": [],
  "correction_bundles": [],
  "domain": "v3.10-cash-ledger-export",
  "inbox_status_events": [],
  "ledger_head_json_ascii": "<exact current head JSON>",
  "ledger_head_sha256": "<lowercase-sha256>",
  "ledger_revision": "0",
  "ledger_transitions": [],
  "observations": [],
  "provenance_links": [],
  "schema_version": 1,
  "store_revision": "0",
  "transactions": [],
  "version": 1
}
```

Array element exact keysets:

- codec: `canonical_json_ascii`, `sha256`;
- observation: `canonical_json_ascii`, `current_status`, `logical_source_sha256`, `sha256`, `source_sha256`;
- status event: `canonical_json_ascii`, `sha256`;
- transaction: `canonical_json_ascii`, `economic_sha256`, `sha256`, `source_sha256`;
- provenance link: `observation_sha256`, `transaction_sha256`;
- correction bundle: `canonical_json_ascii`, `sha256`;
- ledger transition: `head_json_ascii`, `head_sha256`.

All JSON-in-JSON fields contain the exact canonical ASCII text, not reparsed/reformatted objects.

Sort order is fixed:

1. codecs by SHA;
2. observations by `(logical_source_sha256, sha256)`;
3. status events by `(observation_sha256, numeric event_no)` parsed from canonical bytes;
4. transactions by SHA;
5. provenance links by `(transaction_sha256, observation_sha256)`;
6. bundles by `(original_sha256, bundle_sha256)` parsed from canonical bytes;
7. ledger transitions by numeric ledger revision parsed from head bytes.

The whole export uses the canonical JSON rule in section 7. It contains no path, rowid, file timestamp, SQLite physical metadata, lock state, raw account ID, credential, raw provider payload or binary float. Repeated export of unchanged trusted state is byte-identical.

## 21. Backup and manifest custody

A backup is an absent caller-supplied local directory. Its final exact children are:

- `store.sqlite3` — a self-contained SQLite online-backup snapshot with no WAL/SHM dependency;
- `export.json` — exact deterministic export regenerated from that snapshot;
- `manifest.json` — exact canonical manifest.

The manifest exact keyset is:

```json
{
  "domain": "v3.10-cash-ledger-backup-manifest",
  "export_sha256": "<lowercase-sha256>",
  "files": [
    {"path":"export.json","sha256":"<lowercase-sha256>","size":"<decimal-string>"},
    {"path":"store.sqlite3","sha256":"<lowercase-sha256>","size":"<decimal-string>"}
  ],
  "ledger_head_sha256": "<lowercase-sha256>",
  "ledger_revision": "0",
  "schema_version": 1,
  "store_revision": "0",
  "version": 1
}
```

`files` is sorted by ASCII path and contains exactly those two non-manifest files. `export_sha256` equals the `export.json` entry. The manifest binds the revisions/head inside both the backup database and export. It contains no timestamp, source/destination path, hostname or random identifier.

Backup procedure:

1. validate the source store and acquire one consistent SQLite snapshot;
2. require absent destination and absent exact sibling `<destination-name>.cl2-backup-staging`;
3. create the staging directory exclusively;
4. use SQLite online backup to write `store.sqlite3` without copying live sidecars;
5. open and fully validate the copy with the supplied codec registry;
6. generate `export.json` from the copy;
7. compute file sizes/SHA and write canonical `manifest.json`;
8. verify the complete staging artifact independently;
9. atomically rename staging to the still-absent destination.

The SQLite file's physical bytes need not be identical across separate valid backup operations; each manifest deterministically binds the exact produced bytes and the deterministic semantic export. A failure leaves the source untouched and never promotes or overwrites a destination. An existing destination or staging path is `PATH_COLLISION` and is never removed automatically.

## 22. Backup verification and isolated restore

Backup verification is read-only and requires:

- a safe directory with exactly the three children above and no link/reparse indirection;
- exact manifest canonical bytes/keysets/version;
- exact file size and SHA matches;
- full physical, schema, codec and semantic validation of the backup database;
- byte-identical regeneration of `export.json`;
- exact agreement of schema version, both revisions and ledger head across database, export and manifest.

Missing/extra/tampered files, unsafe entries, non-canonical manifest bytes, unknown backup version or size/hash mismatch is `BACKUP_MANIFEST_INVALID`. Once those checks pass, store/version/codec/physical/semantic failures retain their exact earlier `PersistenceReason`. `RESTORE_VERIFICATION_FAILED` is reserved for a restore staging/promoted copy that no longer reproduces the already verified source backup.

Restore accepts a verified backup and an absent isolated target store root. It:

1. re-verifies the source backup without modifying it;
2. requires absent target and absent exact sibling `<target-name>.cl2-restore-staging`;
3. copies only the verified database into exclusively created staging;
4. opens the staging copy as a live WAL store and fully validates it;
5. regenerates export and proves byte identity with backup `export.json`;
6. proves exact store revision, ledger revision, head and canonical identities;
7. closes/checkpoints safely, leaves no required sidecar, and atomically renames staging to the still-absent target;
8. reopens the promoted target and repeats full validation/read-back.

No-clobber failure, interruption or verification failure leaves the source backup and any existing target untouched. CL2 never merges into, overwrites or repairs a target. Promotion of a restored store grants no runtime adoption authority.

## 23. Closed result and failure vocabulary

`PersistenceDisposition` is exactly the eight success values in section 11.

`PersistenceReason` is the closed version-1 failure set:

- `TYPE_INVALID`;
- `CANONICAL_FORMAT_INVALID`;
- `HASH_INVALID`;
- `VERSION_UNSUPPORTED`;
- `CODEC_UNSUPPORTED`;
- `SANITIZED_CONTENT_INVALID`;
- `SENSITIVE_CONTENT_FORBIDDEN`;
- `PATH_INVALID`;
- `PATH_COLLISION`;
- `STORE_MISSING`;
- `STORE_BUSY`;
- `STORE_CLOSED`;
- `SCHEMA_INVALID`;
- `INTEGRITY_FAILURE`;
- `SEMANTIC_INTEGRITY_FAILURE`;
- `REVISION_MISMATCH`;
- `REVISION_EXHAUSTED`;
- `OBSERVATION_NOT_FOUND`;
- `SOURCE_CONTENT_CONFLICT`;
- `STATUS_TRANSITION_INVALID`;
- `TRANSACTION_NOT_FOUND`;
- `SOURCE_CONFLICT`;
- `ECONOMIC_MATCH_REVIEW_REQUIRED`;
- `LINEAGE_CONFLICT`;
- `INTERRUPTED_TRANSACTION`;
- `WAL_SIDECAR_INCONSISTENT`;
- `BACKUP_MANIFEST_INVALID`;
- `RESTORE_VERIFICATION_FAILED`;
- `IO_FAILURE`.

CL1 `MoneyError`/`LedgerError` reasons propagate unchanged while parsing supplied CL1 objects before persistence. SQLite/OS exception text, locale, path text and errno are not authoritative reason values.

The following tables are normative ordered decision oracles. Rows run top-to-bottom; checks separated by `->` inside a row run left-to-right. The first failing check is the only primary reason.

### 23.1 Canonical and codec inputs

| ordered check | exact failure |
|---|---|
| wrong outer Python type, Boolean where integer is required, non-string text/bytes | `TYPE_INVALID` |
| malformed JSON, duplicate/missing/extra keys, non-canonical JSON text, invalid token/decimal/timestamp grammar | `CANONICAL_FORMAT_INVALID` |
| syntactically invalid lowercase SHA field | `HASH_INVALID` |
| recognized object with unsupported domain or version | `VERSION_UNSUPPORTED` |
| nested CL1 object invalid | propagate the first exact CL1 reason |
| codec schema/descriptor non-canonical or internally inconsistent | `CANONICAL_FORMAT_INVALID` |
| schema/descriptor SHA mismatch | `HASH_INVALID` |
| descriptor absent from registry or registered bytes differ | `CODEC_UNSUPPORTED` |
| forbidden schema/content key or explicit private/raw-content negative vector | `SENSITIVE_CONTENT_FORBIDDEN` |
| content keyset/type/value/size violates the verified schema or global bound | `SANITIZED_CONTENT_INVALID` |
| sanitized content SHA or logical-source SHA does not reproduce its declared value | `HASH_INVALID` |

Observation parsing executes this whole table before any store access. Status-event parsing uses the first four rows; transition semantics are checked later as `STATUS_TRANSITION_INVALID`.

### 23.2 Store/path startup

| ordered check | exact failure |
|---|---|
| path type/absolute-local grammar/safe-parent/link/reparse/hard-link rule | `PATH_INVALID` |
| create/backup/restore target or exact staging name already exists | `PATH_COLLISION` |
| required open/backup source root or database is absent | `STORE_MISSING` |
| forbidden/orphan/inconsistent sidecar or rollback-journal envelope | `WAL_SIDECAR_INCONSISTENT` |
| SQLite header cannot be parsed as a database | `INTEGRITY_FAILURE` |
| application ID, `user_version`, application schema version or required SQLite capability is unsupported | `VERSION_UNSUPPORTED` |
| application schema objects or fingerprint differ | `SCHEMA_INVALID` |
| `integrity_check` is not exactly one `ok` row | `INTEGRITY_FAILURE` |
| `foreign_key_check` is non-empty | `SEMANTIC_INTEGRITY_FAILURE` |
| stored codec descriptor missing from registry or byte-different | `CODEC_UNSUPPORTED` |
| canonical row, relation, revision or replay invariant fails | `SEMANTIC_INTEGRITY_FAILURE` |
| other filesystem/SQLite I/O failure before a lock is requested | `IO_FAILURE` |

Opening through an already closed repository handle is `STORE_CLOSED` before any SQLite action. Startup validation always completes before mutation/duplicate/CAS checks.

### 23.3 Mutators after successful startup/input validation

All mutators first attempt `BEGIN IMMEDIATE`; timeout/lock loss is `STORE_BUSY`. They then apply the following operation-specific order:

| operation | ordered decision after lock |
|---|---|
| observation | exact committed bytes -> duplicate disposition; expected store revision mismatch -> `REVISION_MISMATCH`; same logical source with different bytes -> `SOURCE_CONTENT_CONFLICT`; exhausted store revision -> `REVISION_EXHAUSTED`; write/commit failure -> `IO_FAILURE` |
| non-ledger status event | exact committed event at the same observation/event number -> duplicate disposition; expected store revision mismatch -> `REVISION_MISMATCH`; observation absent -> `OBSERVATION_NOT_FOUND`; current/from/event-number/reason/reference/terminal rule invalid -> `STATUS_TRANSITION_INVALID`; exhausted store revision -> `REVISION_EXHAUSTED`; write/commit failure -> `IO_FAILURE` |
| ordinary transaction | exact committed transaction and identical observation link -> duplicate disposition; expected store then ledger revision mismatch -> `REVISION_MISMATCH`; observation absent -> `OBSERVATION_NOT_FOUND`; reversal/correction lineage presented through ordinary append -> `LINEAGE_CONFLICT`; observation/transaction source bytes differ or trusted source relation conflicts -> `SOURCE_CONFLICT`; trusted economic relation matches -> `ECONOMIC_MATCH_REVIEW_REQUIRED`; observation status not linkable -> `STATUS_TRANSITION_INVALID`; either revision exhausted -> `REVISION_EXHAUSTED`; write/commit failure -> `IO_FAILURE` |
| correction bundle | exact committed bundle and identical ordered links -> duplicate disposition; expected store then ledger revision mismatch -> `REVISION_MISMATCH`; original transaction absent -> `TRANSACTION_NOT_FOUND`; either referenced observation absent -> `OBSERVATION_NOT_FOUND`; stored original/bundle lineage or second branch invalid -> `LINEAGE_CONFLICT`; observation/transaction source mismatch or conflict with a pre-existing trusted non-original transaction -> `SOURCE_CONFLICT`; economic match with a pre-existing trusted non-original transaction -> `ECONOMIC_MATCH_REVIEW_REQUIRED`; a distinct observation is neither linkable nor already linked exclusively to the original -> `STATUS_TRANSITION_INVALID`; either revision exhausted -> `REVISION_EXHAUSTED`; write/commit failure -> `IO_FAILURE` |

An injected pre-commit fault maps to `INTERRUPTED_TRANSACTION` instead of `IO_FAILURE` and rolls back. An injected post-commit/pre-return fault reports `INTERRUPTED_TRANSACTION`, but the committed retry is subsequently recognized as the exact duplicate before CAS.

### 23.4 Backup and restore

| ordered check | exact failure |
|---|---|
| source/target path checks | the exact path reason from section 23.2 |
| backup directory child set, manifest canonical form/version, file size or file SHA invalid | `BACKUP_MANIFEST_INVALID` |
| verified backup database startup/integrity/codec/semantic validation fails | propagate the exact section 23.2 reason |
| verified database export differs from stored `export.json` | `SEMANTIC_INTEGRITY_FAILURE` |
| restore staging or promoted copy differs from the already verified backup identity/export | `RESTORE_VERIFICATION_FAILED` |
| other copy/write/fsync/rename failure | `IO_FAILURE` |

An earlier failure wins. A failure never repairs rows, drops observations, rewrites CL1 bytes, rounds Money, coerces versions, advances a revision/head, creates an economic effect, deletes a sidecar/staging path or overwrites a target.

## 24. Clean schema and migration refusal

CL2 creates only clean schema version 1. It has no normal input format representing an older CashLedger store.

Historical schema-2/schema-3 and MoneyV1/MoneyV2 work supplies invariant evidence only: explicit versions, deterministic heads, transactional changes, no reinterpretation, backup/restore equivalence and unknown/downgrade refusal.

Opening a database with another application ID, `user_version`, schema version, CL1 domain version or codec version fails closed. There is no downgrade reader and no migration command. A future real migration source requires a separate contract, allowlist and explicit `RESCOPE`.

## 25. Frozen cross-language fixture

`current/tests/fixtures/v3_10_cash_ledger_persistence_vectors.json` is the future public synthetic fixture. Production code never reads it.

Top-level exact keyset:

- `domain`: `v3.10-cash-ledger-persistence-fixture`;
- `version`: plain integer `1`;
- `vectors`: JSON array;
- `scenarios`: JSON array.

Every vector has common keys `id`, `kind`, `canonical_json_ascii`, `sha256` plus exactly these kind-specific keys:

| kind | additional keys |
|---|---|
| `codec_schema` | none |
| `codec_descriptor` | `schema_sha256` |
| `logical_source` | none |
| `observation` | `logical_source_sha256`, `source_sha256` |
| `status_event` | `event_no`, `from_status`, `observation_sha256`, `to_status` |
| `cl1_transaction` | `economic_sha256`, `source_sha256` |
| `cl1_bundle` | `correction_sha256`, `original_sha256`, `reversal_sha256` |
| `ledger_head` | `ledger_revision`, `previous_head_sha256`, `transition_kind`, `transition_sha256` |
| `export` | `ledger_head_sha256`, `ledger_revision`, `store_revision` |
| `backup_manifest` | `export_sha256`, `ledger_head_sha256`, `ledger_revision`, `store_revision` |

Vector `id` matches `[a-z0-9][a-z0-9_-]{0,127}`, is unique, and vectors are ordered by strictly increasing ASCII `id`. Each `canonical_json_ascii` independently parses, reproduces byte-identically and hashes to its `sha256`; all additional identities must reproduce from those canonical bytes or from the accepted CL1 parser.

Every scenario has exact keys `id`, `registry_ids`, `steps`. Scenario IDs follow the same grammar, are unique and ASCII-sorted. `registry_ids` is an ASCII-sorted unique array of `codec_descriptor` vector IDs. Every scenario starts from a newly created empty schema-1 store with revisions `0/0` and the frozen genesis head, then executes `steps` strictly in array order.

Every step has exactly:

- `expected_disposition`: one section-11 value or null;
- `expected_head_sha256`: current head after the step;
- `expected_ledger_revision`: decimal ledger revision after the step;
- `expected_reason`: one section-23 reason or null;
- `expected_store_revision`: decimal store revision after the step;
- `input_ids`: ordered vector references;
- `operation`: one closed operation token;
- `supplied_ledger_revision`: decimal CAS input or null;
- `supplied_store_revision`: decimal CAS input.

Exactly one of `expected_disposition` and `expected_reason` is non-null. Failure steps repeat the exact pre-step revisions/head. `supplied_ledger_revision` is null for inbox-only operations and non-null for ledger operations.

Closed operation/input order:

| operation | exact `input_ids` |
|---|---|
| `APPEND_OBSERVATION` | `[observation]` |
| `APPEND_STATUS_EVENT` | `[status_event]` |
| `APPEND_TRANSACTION` | `[cl1_transaction, observation]` |
| `APPEND_CORRECTION_BUNDLE` | `[cl1_bundle, reversal_observation, correction_observation]` |

For a bundle, the last two IDs may be identical exactly when both new CL1 transaction sources are identical. Unknown keys, kinds, operations, referenced IDs, wrong vector kinds, wrong CAS nullability or a step whose declared post-state does not follow from the preceding state fail fixture verification.

The fixture reuses the exact public synthetic CL1 values and hashes. For account scope `11..11`, source scope `22..22` and source kind `SYNTHETIC`, exact logical-source bytes are:

```text
{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111","domain":"v3.10-operation-inbox-logical-source","source_kind":"SYNTHETIC","source_scope_sha256":"2222222222222222222222222222222222222222222222222222222222222222","version":1}
```

SHA-256:

`1a8ab1a83f42334472fa30ffa57b921ae6ff9a01702e297acf81278bbd1fba1e`

Using the frozen `SYNTHETIC_CL2` codec/content, timestamp `2026-01-02T03:04:05.123456789Z`, provenance `44..44` and that logical source, exact observation bytes are:

```text
{"codec_id":"SYNTHETIC_CL2","codec_schema_sha256":"d21206b6c54576f60fa9923817bc4b59f8b8ed04942be4a53ca5c3eb304e858b","codec_version":1,"content_json_ascii":"{\"operation_kind\":\"SYNTHETIC\"}","domain":"v3.10-operation-inbox-observation","initial_status":"OBSERVED","logical_source_sha256":"1a8ab1a83f42334472fa30ffa57b921ae6ff9a01702e297acf81278bbd1fba1e","observed_at":"2026-01-02T03:04:05.123456789Z","provenance_sha256":"4444444444444444444444444444444444444444444444444444444444444444","source":{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111","domain":"v3.10-cash-ledger-source","source_content_sha256":"ed0306ea59619e88016ad6d2790594b4a5b19ccb9ec5a0a49b746c50b4dc9bc5","source_kind":"SYNTHETIC","source_scope_sha256":"2222222222222222222222222222222222222222222222222222222222222222","version":1},"version":1}
```

Observation SHA-256:

`91570ebf94038110b8c3059172c448a105359296118555d7faae3511f3f1e896`

Every `codec_descriptor` schema hash must resolve to exactly one `codec_schema` vector. Every `cl1_bundle` vector's three hashes must resolve to exactly one `cl1_transaction` vector each. The fixture must also reproduce the three exact head vectors from section 15 and bind at least one valid observation, status chain, ordinary append, exact retry, stale CAS, source conflict, economic match, distinct-source correction bundle, shared-new-source/shared-observation bundle, original-source/previously-linked-observation reuse, second-branch lineage conflict, backup manifest and restored export.

## 26. Fixed acceptance matrix

The future implementation acceptance suite is frozen to:

- `V310-CL2-01`: import has no I/O/runtime/provider side effect and static dependency boundary passes;
- `V310-CL2-02`: exact clean create, application/schema version, object allowlist and schema fingerprint;
- `V310-CL2-03`: no create-on-open, unknown/older/newer/partial schema and extra objects fail closed;
- `V310-CL2-04`: codec schema/descriptor exact bytes/preimage/hash, generic evaluation, registration collision and unknown-codec refusal;
- `V310-CL2-05`: flat sanitized content key/type/value/string/total-size bounds, forbidden keys and raw/private negative vectors;
- `V310-CL2-06`: logical-source and observation canonical keysets/bytes/SHA known answers;
- `V310-CL2-07`: exact observation duplicate no-op, changed same logical source conflict, distinct append;
- `V310-CL2-08`: status-event exact transitions, contiguous numbering, terminal rules, duplicate no-op;
- `V310-CL2-09`: initial revisions/head and inbox-only store-revision behavior;
- `V310-CL2-10`: ordinary CL1 transaction/posting/provenance append and exact byte reproduction;
- `V310-CL2-11`: transaction exact retry no-op, source conflict and economic-match review refusal;
- `V310-CL2-12`: stale store/ledger CAS, duplicate-before-CAS retry, exhaustion and no mutation on failure;
- `V310-CL2-13`: exact genesis/transaction/bundle head bytes/SHA and deterministic replay;
- `V310-CL2-14`: distinct-source, shared-new-source and original-source-reuse valid correction bundles each form one atomic revision/head transition with status events only for newly linked distinct observations;
- `V310-CL2-15`: reversal/correction missing evidence, partial/pre-existing rows and ordinary lineage append rejected;
- `V310-CL2-16`: exact bundle retry idempotent and second distinct branch `LINEAGE_CONFLICT`;
- `V310-CL2-17`: physical integrity, foreign keys and every semantic invariant detect tampering;
- `V310-CL2-18`: WAL committed recovery plus orphan/corrupt/mismatched sidecar refusal without deletion;
- `V310-CL2-19`: two-writer serialization, busy timeout, stale writer and snapshot-consistent reader;
- `V310-CL2-20`: fault injection at every mutation boundary yields exact prior or complete successor state;
- `V310-CL2-21`: post-commit/pre-return retry creates no duplicate economic effect;
- `V310-CL2-22`: deterministic export exact schema/order/bytes and absence of path/raw/private/float data;
- `V310-CL2-23`: online backup exact children, manifest/checksums/source identity and destination no-clobber;
- `V310-CL2-24`: tampered/missing/extra backup evidence fails read-only verification;
- `V310-CL2-25`: isolated restore exact revision/head/export equivalence, no source mutation and target no-clobber;
- `V310-CL2-26`: create/backup/restore interruption never promotes partial state and never deletes staging evidence;
- `V310-CL2-27`: all closed failure reasons and operation-specific multi-invalid priority vectors reproduce section 23 exactly;
- `V310-CL2-28`: exact three-path implementation delta, immutable CL1 contract/domain/tests/fixture and complete regression suite.

No vector requires network, provider credentials, private data, GUI, runtime startup, current time, random identity or experiment execution.

## 27. Verification contract for future implementation

Before an implementation candidate may enter independent review it must pass locally and deterministically:

1. dedicated `V310-CL2-01..28` suite;
2. independent canonical bytes/SHA and cross-language fixture verification;
3. fault, restart, WAL, lock, corrupt-store, backup and restore adversarial tests in isolated temporary roots;
4. compile/static/style checks for the new production and test files;
5. the complete unchanged CL1 and v3.9 regression suite;
6. `git diff --check`;
7. exact three-path implementation-delta allowlist check against the accepted contract head;
8. cumulative four-path check against exact CL1 predecessor `095a24a0d8ad2487f09ec0c5f3473a6c65a84710`;
9. complete diff/import/call-graph review proving no provider/runtime/ownership drift.

Tests use only public synthetic stores and fixtures. Green tests do not grant implementation acceptance, publication, runtime, experiment, release or merge authority.

## 28. Historical evidence disposition

Issue #50 is evidence for isolated versioned SQLite, immutable sanitized inbox, separate revisions/CAS, duplicate/conflict behavior, atomic corrections, WAL/fault handling, deterministic export and backup/restore.

Issue #79 is evidence for explicit version identity, deterministic head, transactional change, no silent rewrite, exact backup/restore and unknown/downgrade refusal.

Neither issue, its branch, implementation, schema numbering, fixture bytes nor ancestry is normative for this clean line. In particular, historical mixed MoneyV1/MoneyV2 history and schema migration are excluded.

## 29. Bounded review and correction protocol

The independent/adversarial read-only review of initial exact candidate `8f351feadaec6bbaec9c86d02971779eb0d091f3` / tree `fa6ef5bd15f1908dcadf67e2fd339353893ab9b8` produced the fixed material set:

- `CL2-R1-01 / BLOCKER_CORRECTION_PROVENANCE_CARDINALITY`;
- `CL2-R1-02 / BLOCKER_CODEC_SCHEMA_CUSTODY_UNBOUND`;
- `CL2-R1-03 / BLOCKER_FAILURE_ORACLE_UNFROZEN`;
- `CL2-R1-04 / BLOCKER_FIXTURE_STATE_MODEL_UNREPRODUCIBLE`.

This document is the one authorized bounded correction batch for exactly that set. No second CL2 contract correction batch is authorized.

The closure review is restricted to `CL2-R1-01..04`. It may return only:

- `PASS / CL2-R1-01..04 CLOSED / MATERIAL FINDINGS 0`; or
- `RESCOPE` if any one of those four findings remains material.

No unrelated finding may be introduced into this closure cycle. A separate future concern is deferred unless it proves that one of the four fixed blockers was not actually closed.

### 29.1 `CL2-R1-01 / BLOCKER_CORRECTION_PROVENANCE_CARDINALITY`

Correction: sections 10.3, 14, 19, 25 and 26 now permit identical ordered observation references for reversal/correction when their exact CL1 SourceIdentity bytes are equal, and permit reuse of the original's already linked observation when a component has that source. Bundle persistence writes one provenance link per transaction and one terminal bundle-linked status event per newly linked distinct observation. The pair is validated as one CL1 bundle rather than two independent append candidates.

Closure condition: distinct-source/two-observation, shared-new-source/shared-observation and original-source/pre-linked-observation CL1-valid bundles each have one deterministic atomic persistence result, revision transition, status projection and exact retry behavior.

### 29.2 `CL2-R1-02 / BLOCKER_CODEC_SCHEMA_CUSTODY_UNBOUND`

Correction: section 9 adds the canonical schema preimage to the exact descriptor, freezes its SHA formula and a finite flat schema language, and requires CL2's generic evaluator to validate the preimage and content. The supplied registry can authorize descriptor identities but cannot inject validator code or repeat a hash without the exact schema bytes.

Closure condition: an independent implementation can reproduce schema/descriptor bytes and SHA, reject a changed/missing descriptor, and revalidate every stored/exported/restored observation without trusting an unbound executable validator.

### 29.3 `CL2-R1-03 / BLOCKER_FAILURE_ORACLE_UNFROZEN`

Correction: section 23 now contains ordered per-boundary decision tables for canonical/codec input, startup/path, each mutator, backup and restore, including tie-breaking and exact reason propagation.

Closure condition: representative multi-invalid cases select one exact primary reason without implementation-specific ordering, including hash/content, schema/version, unknown-codec/private-content, duplicate/CAS, missing-reference/conflict and backup/restore combinations.

### 29.4 `CL2-R1-04 / BLOCKER_FIXTURE_STATE_MODEL_UNREPRODUCIBLE`

Correction: section 25 adds codec-schema/descriptor and CL1 transaction/bundle vector kinds plus ordered fresh-store scenarios. Every step freezes input object order, supplied CAS revisions, disposition/reason and exact post-state revisions/head.

Closure condition: the fixture can independently encode and replay exact duplicate, stale CAS, source/economic conflict, distinct/shared-source correction, second-branch lineage and deterministic final-state cases with no implicit setup.

Contract acceptance, if separately granted after closure PASS, binds one exact correction commit and tree. Review PASS does not itself authorize implementation. Issue/PR text is status evidence, not canonical contract authority.

Only after explicit acceptance may a separate implementation branch be created from the exact accepted correction head under section 4. Publication, Draft/Ready transition and merge remain later separate decisions.

## 30. Contract exit state

Current authority:

`CL2 CONTRACT CORRECTION CANDIDATE / CL2-R1-01..04 / ONE-FILE CONTRACT DELTA / IMPLEMENTATION BLOCKED`.

This contract candidate performs no implementation, test/fixture creation, database creation, persistence mutation, provider observation, runtime action, experiment, publication, merge or CL3 work.

Next permitted gate: create one exact local bounded correction commit, then conduct a finding-scoped read-only closure review of `CL2-R1-01..04` against that exact successor head. Implementation remains blocked until separate explicit CL2 contract acceptance.
