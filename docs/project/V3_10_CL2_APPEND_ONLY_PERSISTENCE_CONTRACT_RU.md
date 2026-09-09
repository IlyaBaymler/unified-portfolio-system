# v3.10 CL2 — Append-only persistence + OperationInbox contract

Status: `CL2 CONTRACT CANDIDATE / IMPLEMENTATION BLOCKED`.

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

An observation may use only a codec registered explicitly before store creation/open. A registered codec is a pure validator/canonicalizer plus this exact descriptor:

```text
{"codec_id":"<TOKEN>","codec_version":1,"domain":"v3.10-operation-inbox-codec","schema_sha256":"<lowercase-sha256>","version":1}
```

The descriptor SHA is SHA-256 of those canonical bytes. `schema_sha256` binds the codec's declarative sanitized schema; it is not a hash of executable code. Re-registering the same `(codec_id, codec_version)` with different descriptor bytes is `CODEC_UNSUPPORTED`.

The store persists every descriptor used by an accepted observation. Opening, exporting, backing up or restoring a store requires a supplied registry containing byte-identical descriptors for all stored codecs. Missing, newer or changed descriptors fail `CODEC_UNSUPPORTED`; no opaque pass-through is allowed.

Every codec must enforce all of the following before persistence:

- top-level sanitized content is a JSON object;
- allowed keys, types and nesting are finite and exact in the codec schema;
- only JSON object/array/string/Boolean/null and plain integers in `[-9007199254740991, 9007199254740991]` are permitted;
- JSON float, NaN, infinity and negative zero representations are forbidden;
- canonical content is at most `65536` bytes, nesting depth at most `16`, aggregate array/object members at most `1024`, and each string at most `4096` Unicode scalar values;
- keys matching, after ASCII lowercase, `account_id`, `broker_account_id`, `token`, `access_token`, `refresh_token`, `authorization`, `cookie`, `set_cookie`, `headers`, `raw_payload`, `provider_payload`, `secret`, `password`, `api_key` or `credential` are forbidden at every depth;
- raw provider payloads, credentials, transport headers, raw broker Account ID and unbounded provider text are forbidden regardless of key spelling;
- the validator performs no I/O and does not read time, environment or credentials.

The structural denylist is a minimum defense. The registered codec remains responsible for proving that every allowed field is sanitized and bounded. The CL2 production module contains no provider or test codec; acceptance tests supply one public synthetic registry entry and validator. Provider codecs belong to later reviewed scope.

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
2. `content_json_ascii` is ASCII text containing exactly the codec-reproduced canonical bytes;
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
| `LEDGER_CORRECTION_ACCEPTED` | `OBSERVED/REVIEW_REQUIRED -> LEDGER_LINKED` | transaction and bundle non-null |

`event_no` starts at `1` and is contiguous per observation. `LEDGER_LINKED` and `REJECTED` are terminal. Event SHA is SHA-256 of its canonical bytes. A transaction/correction-linked event may be created only atomically by the corresponding ledger operation; the generic status API cannot forge it.

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

The bundle operation accepts one validated CL1 `LedgerCorrectionBundle`, existing observation SHA values for its reversal and correction, and both expected revisions.

Before mutation it requires:

1. the exact original transaction is already present as an ordinary accepted ledger transition;
2. the stored original bytes equal the bundle's original bytes;
3. neither reversal nor correction is already present outside the same exact committed bundle;
4. both new observations exist, are linkable, and exactly match the respective transaction sources;
5. the accepted CL1 finite bundle-set invariant remains valid;
6. all source/economic comparisons against trusted transactions are conflict-free except the exact original relationship required by the bundle.

One successful call atomically inserts:

- the reversal transaction and its postings;
- the correction transaction and its postings;
- both provenance links;
- two `LEDGER_CORRECTION_ACCEPTED` status events referring to the same bundle;
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

Common deterministic decision order is:

1. direct Python type/range/path shape;
2. exact canonical keyset/JSON grammar/hash;
3. version;
4. codec and sanitized-content policy;
5. store existence/header/schema/physical integrity;
6. semantic integrity/replay;
7. lock acquisition;
8. exact duplicate recognition;
9. expected revisions;
10. missing references/status;
11. source/economic/lineage conflicts;
12. transactional I/O/commit.

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
- `decision_cases`: JSON array.

Every vector has common keys `id`, `kind`, `canonical_json_ascii`, `sha256` plus exactly these kind-specific keys:

| kind | additional keys |
|---|---|
| `codec` | none |
| `logical_source` | none |
| `observation` | `logical_source_sha256`, `source_sha256` |
| `status_event` | `event_no`, `from_status`, `observation_sha256`, `to_status` |
| `ledger_head` | `ledger_revision`, `previous_head_sha256`, `transition_kind`, `transition_sha256` |
| `export` | `ledger_head_sha256`, `ledger_revision`, `store_revision` |
| `backup_manifest` | `export_sha256`, `ledger_head_sha256`, `ledger_revision`, `store_revision` |

Every decision case has exact keys `expected_disposition`, `expected_head_sha256`, `expected_ledger_revision`, `expected_reason`, `expected_store_revision`, `id`, `input_ids`, `operation`. `input_ids` is an ordered array of vector IDs. `operation` is one of `APPEND_OBSERVATION`, `APPEND_STATUS_EVENT`, `APPEND_TRANSACTION`, `APPEND_CORRECTION_BUNDLE`. Exactly one of `expected_disposition` and `expected_reason` is non-null. Expected revisions are decimal strings and the head is a lowercase SHA. Unknown keys, kinds, operations or referenced IDs fail fixture verification.

The fixture reuses the exact public synthetic CL1 values and hashes. For account scope `11..11`, source scope `22..22` and source kind `SYNTHETIC`, exact logical-source bytes are:

```text
{"account_scope_sha256":"1111111111111111111111111111111111111111111111111111111111111111","domain":"v3.10-operation-inbox-logical-source","source_kind":"SYNTHETIC","source_scope_sha256":"2222222222222222222222222222222222222222222222222222222222222222","version":1}
```

SHA-256:

`1a8ab1a83f42334472fa30ffa57b921ae6ff9a01702e297acf81278bbd1fba1e`

It must also reproduce the three exact head vectors from section 15 and bind at least one valid observation, status chain, ordinary append, exact retry, source conflict, economic match, correction bundle, backup manifest and restored export.

## 26. Fixed acceptance matrix

The future implementation acceptance suite is frozen to:

- `V310-CL2-01`: import has no I/O/runtime/provider side effect and static dependency boundary passes;
- `V310-CL2-02`: exact clean create, application/schema version, object allowlist and schema fingerprint;
- `V310-CL2-03`: no create-on-open, unknown/older/newer/partial schema and extra objects fail closed;
- `V310-CL2-04`: codec descriptor exact bytes/hash/registration collision and unknown-codec refusal;
- `V310-CL2-05`: sanitized content size/depth/member/string bounds, forbidden keys and raw/private negative vectors;
- `V310-CL2-06`: logical-source and observation canonical keysets/bytes/SHA known answers;
- `V310-CL2-07`: exact observation duplicate no-op, changed same logical source conflict, distinct append;
- `V310-CL2-08`: status-event exact transitions, contiguous numbering, terminal rules, duplicate no-op;
- `V310-CL2-09`: initial revisions/head and inbox-only store-revision behavior;
- `V310-CL2-10`: ordinary CL1 transaction/posting/provenance append and exact byte reproduction;
- `V310-CL2-11`: transaction exact retry no-op, source conflict and economic-match review refusal;
- `V310-CL2-12`: stale store/ledger CAS, duplicate-before-CAS retry, exhaustion and no mutation on failure;
- `V310-CL2-13`: exact genesis/transaction/bundle head bytes/SHA and deterministic replay;
- `V310-CL2-14`: valid correction bundle is one atomic revision/head transition;
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
- `V310-CL2-27`: all closed failure reasons and representative multi-invalid priority vectors;
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

## 29. Contract review and acceptance protocol

The first candidate must receive a separate independent/adversarial read-only review against:

- exact predecessor/head/tree and one-file cumulative diff;
- completeness and internal consistency of sections 7–24;
- preservation of exact CL1 identities and authority;
- deterministic duplicate/CAS/revision/head outcomes;
- atomic correction and crash/restart behavior;
- privacy, codec, path, WAL, corruption, backup and restore negative cases;
- feasibility within the proposed three-file implementation allowlist;
- the fixed acceptance matrix.

The review disposition is either:

- `PASS/APPROVE / MATERIAL FINDINGS 0`; or
- a finite named material finding set requiring one separately authorized bounded correction.

Contract acceptance is a later explicit decision binding one exact commit and tree. Review PASS does not itself authorize implementation. Issue/PR text is status evidence, not canonical contract authority.

Only after explicit acceptance may a separate implementation branch be created from the exact accepted contract head under section 4. Publication, Draft/Ready transition and merge remain later separate decisions.

## 30. Contract exit state

Current authority:

`CL2 CONTRACT CANDIDATE / ONE-FILE CONTRACT DELTA / IMPLEMENTATION BLOCKED`.

This contract candidate performs no implementation, test/fixture creation, database creation, persistence mutation, provider observation, runtime action, experiment, publication, merge or CL3 work.

Next permitted gate: create one exact local contract candidate commit, then conduct an independent final read-only review of that exact predecessor-to-candidate range. Implementation remains blocked until separate explicit CL2 contract acceptance.
