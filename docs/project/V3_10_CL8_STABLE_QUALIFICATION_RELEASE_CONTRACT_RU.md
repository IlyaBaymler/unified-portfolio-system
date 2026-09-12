# V3.10 CL8 — Stable qualification and release: bounded contract freeze

Статус:

`CL8 CONTRACT FREEZE CANDIDATE / QUALIFICATION IMPLEMENTATION BLOCKED / EXPERIMENT NOT AUTHORIZED / RELEASE NOT AUTHORIZED`

Parent program: stable-line v3.10 → v4.

Historical evidence: accepted CL0–CL7, immutable v3.9 oracle, historical Issue #56 and accepted v3.9 Stable qualification/recovery/standalone tooling.

Accepted predecessor:

`CL7 = ACCEPTED / INTEGRATED / COMPLETED`.

---

## 1. Exact predecessor и lineage

CL8 contract work начинается только от exact accepted/integrated CL7 stable-line head:

```text
repository = baimleriv/unified-portfolio-system
program branch = program/v3-10-v4-stable-line
exact predecessor commit = ef2eba758bbffb587dbe237a96372b2273fdee03
exact predecessor tree = f55788ff362a24d368b0d01dc0d68e85d8183199
contract branch = agent/v3-10-clean-cl8-contract-freeze
HEAD at branch creation = ef2eba758bbffb587dbe237a96372b2273fdee03
merge-base = ef2eba758bbffb587dbe237a96372b2273fdee03
ahead = 0
behind = 0
worktree = clean
changed paths = 0
```

Допустимая stable-line ancestry:

```text
v3.9.0 oracle
-> accepted/integrated CL0
-> accepted/integrated CL1
-> accepted/integrated CL2
-> accepted/integrated CL3
-> accepted/integrated CL4
-> accepted/integrated CL5
-> accepted/integrated CL6
-> accepted/integrated CL7
-> CL8 contract candidate
```

`main`, historical MoneyV1/MoneyV2 work, archived RC branches and old release branches are evidence/reference only and are not CL8 ancestry or release authority.

---

## 2. Immutable v3.9 regression oracle

```text
tag = v3.9.0
commit = 412e126166831a8bac0435d61932ab328fc61f12
tree = 18c2822d2aa7f174a99d7d7dc5f5a5851dc59eba
```

CL8 may supersede release/version metadata, package names and Stable documentation, but MUST NOT silently redefine v3.9 behavioural safety semantics.

---

## 3. Contract-freeze allowlist

Until explicit CL8 contract acceptance, exactly one repository path may change:

```text
docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
```

Any other changed, added, deleted, renamed or untracked repository path:

```text
SCOPE_VIOLATION -> RESCOPE
```

No application code, tests, fixtures, release scripts, runtime files, tags or GitHub release state may change during contract freeze.

---

# PART A — CL8 PURPOSE AND AUTHORITY

## 4. Mission

CL8 is a qualification-and-release milestone. It MUST answer one bounded question:

> Is one exact v3.10 release candidate, derived from accepted CL7 without new trading semantics, sufficiently verified to be explicitly accepted and published as `v3.10.0 Stable`?

CL8 is NOT a feature-development milestone.

## 5. Frozen qualification surfaces

```text
Q0  GUI_RUNTIME_PREREQUISITE
Q1  REGRESSION_AND_CUSTODY
Q2  CORRUPTION_RESTART_RECOVERY
Q3  INSTALL_UPGRADE_ROLLBACK_BACKUP_RESTORE
Q4  STANDALONE_AND_RELEASE_ARTIFACTS
Q5  PRIVACY_SUPPORT_RELEASE_HYGIENE
Q6  CONTROLLED_CLOCK
Q7  LIVE_SANDBOX_BURNIN
Q8  INDEPENDENT_RELEASE_REVIEW
Q9  EXPLICIT_STABLE_ACCEPTANCE_AND_PUBLICATION
```

No phase automatically authorizes the next.

## 6. Explicit non-goals

CL8 MUST NOT introduce:

```text
new CashLedger accounting semantics
new CashAvailability semantics
new Portfolio Risk semantics
new Central reservation semantics
new execution authorization lane
new broker mutation endpoint
new trading strategy
new backfill algorithm
new autonomous GUI business owner
new real-account execution
production trading
v4 Portfolio Manager functionality
```

If qualification demonstrates that any of these is required:

```text
CL8_BLOCKED -> RESCOPE / DEFER
```

not an in-scope release fix.

## 7. Authority boundaries

CL8 contract acceptance does NOT authorize:

```text
qualification implementation
provider/private-account access
runtime cutover
Sandbox order execution
OS clock manipulation
tag creation
GitHub Release creation
stable publication
issue closure
real-account execution
```

CL8 implementation acceptance does NOT authorize live experiments.

Live experiment authority requires its own Preparation Stage and exact user experiment gate.

Stable publication requires separate explicit Stable acceptance and separate publication decision.

## 7.1. Q0 GUI/runtime prerequisite

Q0 is a hard prerequisite inherited from Issue #56 and Issue #72. Q0 is `PASS` only when one immutable accepted-#72 disposition binds:

```text
issue = 72
accepted commit
accepted tree
independent review verdict = PASS
review evidence SHA-256
explicit acceptance record
```

The accepted commit/tree must be an ancestor of the exact CL8 release candidate. The review evidence and acceptance record must cover the existing GUI/runtime environment, account-wide configured multi-instrument workflow and the authority boundaries inherited by CL8.

An open, blocked, unresolved, deferred, waived, unbound or merely planned #72 disposition is not acceptance:

```text
Q0 != PASS
CL8 = BLOCKED
Q8 = FORBIDDEN
Q9 = FORBIDDEN
```

The current Issue #72 governance record is `BLOCKED — FINAL GUI/RUNTIME REVIEW AFTER ACCEPTED CASH PIPELINE`; CL8 does not reinterpret it as accepted evidence. Closing Q0 grants no GUI feature-development authority. If existing behaviour cannot satisfy the frozen qualification surface, the result is `RESCOPE`, not an implicit CL8 implementation fix.

---

# PART B — TWO REPOSITORY CHANGE PHASES

## 8. Why CL8 has two code-bearing phases

CL8 distinguishes:

```text
PHASE I  qualification infrastructure
PHASE II release-metadata/package cut
```

Qualification infrastructure may improve backup/integrity/support/release verification coverage but MUST NOT change trading semantics or the product version.

The release cut changes only bounded version/package/docs surfaces after qualification infrastructure has passed independent review.

## 9. Frozen future qualification-infrastructure allowlist

Only after explicit CL8 contract acceptance may a qualification implementation branch change these paths:

```text
current/trading_robot/runtime_backup.py
current/trading_robot/runtime_integrity.py
current/trading_robot/support_bundle.py
current/trading_robot/readiness.py
current/trading_robot/runtime_bootstrap.py
current/rc_tool.py
current/runtime_tool.py
current/tools/build_release.py
current/tools/release_cleanup.py
current/tools/verify_standalone_layout.py
current/tools/v3_10_stable_qualification.py
current/tests/test_v3_10_stable_qualification.py
current/tests/fixtures/v3_10_stable_qualification_vectors.json
.github/workflows/ci.yml
```

Count: `14 paths`.

All CL1–CL7 domain, accounting, Risk, Central and execution code not listed above remains immutable.

## 10. Qualification-infrastructure semantic restriction

Changes in the 14-path allowlist may only:

- include CL2/CL7 runtime artifacts in backup/integrity/support/readiness inventory;
- add privacy-safe qualification reporting;
- add deterministic artifact verification;
- add offline corruption/recovery/install/upgrade/rollback test orchestration;
- add qualification CLI;
- make runtime bootstrap validate/preserve CL7 release-required custody without changing CL7 state semantics;
- add dedicated CL8 tests/fixtures.

The `.github/workflows/ci.yml` authority is narrower than the general qualification-infrastructure authority. It permits exactly one correction for `CL8-I-PR177-01`: keep `actions/checkout@v7` and add `fetch-depth: 0` to that checkout step so pull-request CI materializes the complete predecessor ancestry required by the frozen regression identity oracle. It MUST NOT change workflow triggers, permissions, credentials handling, runner selection, jobs, matrices, commands, dependencies, publication behaviour or any other workflow field.

This bounded rescope is an additive governance supplement to the accepted semantic/KAT contract identity `a315b9b919a075966a3be898420d37be8ab7b65d / 035f0a4a824001ee2c794d92bb500e206a0123f0`. It does not replace that identity in qualification evidence, does not change any contract-owned KAT and grants no authority outside the single workflow correction above. The rescope successor commit/tree and its finding-scoped acceptance evidence MUST additionally be bound by the final CL8 acceptance record.

They MUST NOT:

- alter RuntimeCashAuthority transitions;
- alter CashLedger transactions;
- alter CashAvailability arithmetic;
- alter Risk decisions;
- alter Central state transitions;
- call a new provider mutation;
- auto-activate exact cash;
- auto-arm Sandbox execution.

## 11. Frozen release-cut allowlist

After the qualification-infrastructure implementation is separately accepted, one bounded release-cut candidate may change only:

```text
current/trading_robot/__init__.py
current/trading_robot/tbank_sandbox.py
current/desktop_gui.py
current/build_manifest.json
current/MOEXResearchRobot.spec
current/BUILD_RELEASE.bat
current/BUILD_STANDALONE.bat
current/portable_launcher.bat
current/initialize_runtime.bat
current/install_and_run_gui.bat
current/run_gui.bat
current/run_risk_lab.bat
current/run_risk_report.bat
current/restore_stable_default_risk_profile.bat
current/README.md
current/START_HERE_WINDOWS.md
current/CHANGELOG_V3_10_0_STABLE_RU.md
current/RELEASE_MANIFEST_V3_10_0_STABLE.txt
current/UPDATE_TO_V3_10_0_STABLE.md
current/V3_10_0_STABLE_ARCHITECTURE_RU.md
current/V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md
current/V3_10_0_STABLE_TEST_PLAN_RU.md
current/VERIFY_V3_10_0_STABLE.bat
current/install_and_verify_v3_10_0.bat
docs/releases/V3_10_0_STABLE_QUALIFICATION_RU.md
current/CHANGELOG_V3_9_0_STABLE_RU.md
current/MASTER_UPDATE_2026-08-15_V3_9_0_STABLE_RU.md
current/RELEASE_MANIFEST_V3_9_0_STABLE.txt
current/UPDATE_TO_V3_9_0_STABLE.md
current/V3_9_0_STABLE_ARCHITECTURE_RU.md
current/V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md
current/V3_9_0_STABLE_TEST_PLAN_RU.md
current/VERIFY_V3_9_0_STABLE.bat
current/install_and_verify_v3_9_0.bat
```

Count: `34 paths`.

The bounded release-review rescope for fixed findings `CL8-REL-R1-01..05`
adds exactly these seven paths to the cumulative release-cut allowlist:

```text
docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
current/tools/release_cleanup.py
current/tools/build_release.py
current/tools/v3_10_stable_qualification.py
current/tests/test_release_hygiene.py
current/tests/test_v3_10_stable_qualification.py
current/tests/test_v3_10_issue72_gui_runtime.py
```

Effective cumulative release-cut allowlist: `41 paths`.

These paths may change only to close the fixed five findings: align cleanup
with the active v3.10 root, preserve and verify required empty standalone
directories, make the regression comparator account for pytest setup errors,
update the exact post-cut node oracle, and extend the two inherited custody
oracles to the bounded release-cut successor. They grant no trading, cash,
Risk, Central, provider, experiment or publication authority.

The final nine v3.9 root-release paths may only be deleted or moved out of the active release root by the accepted release process; their historical Git content is not rewritten.

## 12. Release-cut code restrictions

`current/trading_robot/__init__.py` may change only the public version constant to:

```text
0.3.10
```

Frozen human release identity:

```text
v3.10.0 Stable
```

`tbank_sandbox.py` may change only release-identifying application metadata such as the default `x-app-name` string.

`desktop_gui.py` may change only static/user-facing v3.10 release labels and manifest-derived display identity. It MUST NOT change calculations, state reads/writes, refresh ownership, provider clients, mutation paths, Risk/Central/execution logic, threading or control flow.

`restore_stable_default_risk_profile.bat` may change only v3.10 release identity and explanatory text. Its invoked command, arguments, Risk ownership and mutation behaviour remain unchanged.

Transport, retry, provider method, TLS, account, read or mutation semantics MUST remain byte-for-semantic unchanged.

Any other code-flow diff in `__init__.py`, `tbank_sandbox.py`, `desktop_gui.py` or the restore script:

```text
RELEASE_SCOPE_VIOLATION -> RESCOPE
```

## 13. No GUI behaviour changes

Outside the static/display-only release cut in section 12, CL8 does not authorize modification of:

```text
current/desktop_gui.py
```

or GUI business logic.

Existing GUI behaviour is qualification input. If the existing GUI cannot meet a mandatory Stable criterion without code change:

```text
INDETERMINATE / FAIL -> RESCOPE
```

## 14. Cumulative CL8 repository surface

Before publication, the phase allowlists are:

```text
1 contract path
+ 14 qualification-infrastructure paths
+ 34 base release-cut paths
+ 7 bounded release-review rescope paths
```

The sets overlap: the rescope reuses the contract path and four qualification
paths, while the qualification-adoption correction already owns the Issue #72
custody test. The maximum unique CL8 repository surface is therefore exactly
`51 paths`, with `current/tests/test_release_hygiene.py` as the only newly
unique path introduced by this release-review rescope.

The exact actual changed set may be smaller. An allowlisted path is permission, not a requirement.

Any path outside these frozen sets requires explicit CL8 rescope.

---

# PART C — QUALIFICATION STATUS MODEL

## 15. PhaseStatus

Closed version-1 status set:

```text
NOT_RUN
PASS
FAIL
INDETERMINATE
EVIDENCE_GAP
```

Semantics:

```text
PASS = mandatory evidence proves the frozen acceptance condition
FAIL = evidence proves a contract violation or unsafe state
INDETERMINATE = evidence is insufficient/corrupt/unavailable or cannot distinguish safe/unsafe
EVIDENCE_GAP = only a contract-explicit non-core observational condition was not naturally observed
NOT_RUN = phase has not been executed
```

## 16. Stable-ready status rule

Stable readiness requires:

```text
Q0 PASS
Q1 PASS
Q2 PASS
Q3 PASS
Q4 PASS
Q5 PASS
Q6 PASS
Q7 PASS or the narrow EVIDENCE_GAP exception in section 88
Q8 PASS
```

`FAIL` or `INDETERMINATE` in any mandatory core safety surface blocks Stable acceptance.

`EVIDENCE_GAP` is not a generic waiver.

Q0 cannot use `EVIDENCE_GAP`. Q0 must remain `PASS` through Q8 and Q9; a later loss or mismatch of #72 custody blocks Stable acceptance.

---

# PART D — EVIDENCE CUSTODY

## 17. Evidence root

Every qualification execution writes only to a dedicated external evidence root, e.g.:

```text
qualification_output/v3_10_0/<candidate-short-sha>/
```

The evidence root is NOT part of the source release tree and MUST NOT be committed automatically.

## 18. Evidence immutability rule

Each phase produces:

```text
one canonical summary JSON
one SHA-256 digest
zero or more sanitized referenced artifacts
```

After a phase is declared complete, its canonical summary bytes are immutable.

A rerun creates a new run ID and new summary. No result is overwritten in place.

## 19. Canonical evidence JSON

```python
json.dumps(
    value,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("ascii")
```

Forbidden:

- BOM;
- duplicate keys;
- external whitespace;
- NaN/Infinity;
- binary float in canonical release evidence;
- Unicode surrogate;
- non-canonical integer string.

## 20. QualificationEvidenceEnvelope

Version-1 exact keyset:

```text
candidate_commit
candidate_tree
contract_sha256
domain
generated_at
oracle_v39_commit
oracle_v39_tree
overall_status
phase_results
release_version
version
```

Domain:

```text
v3.10-cl8-qualification-evidence
```

`phase_results` exact keys:

```text
GUI_RUNTIME_PREREQUISITE
REGRESSION
CORRUPTION_RECOVERY
INSTALL_UPGRADE_ROLLBACK
STANDALONE
ARTIFACTS
PRIVACY
CONTROLLED_CLOCK
SANDBOX_BURNIN
```

Every `phase_results` value is an exact object with this keyset:

```text
canonical_summary_sha256
run_id
status
```

`canonical_summary_sha256` is exactly 64 lowercase hexadecimal characters and equals SHA-256 of the referenced phase's exact canonical summary bytes. `run_id` is a non-empty privacy-safe identifier matching `[a-z0-9][a-z0-9._-]{0,127}` and must equal the run ID inside that summary. All phase `run_id` values are pairwise distinct. `status` is one exact `PhaseStatus` token.

The phase summary must bind the same candidate commit/tree and accepted contract SHA as the envelope. A missing summary, unknown/duplicate phase, run-ID mismatch, digest mismatch, candidate mismatch or contract mismatch makes the envelope invalid and prevents `overall_status = PASS`.

`GUI_RUNTIME_PREREQUISITE` binds Q0. The remaining eight entries bind every Q1–Q7 result; Q4 is deliberately represented by separate `STANDALONE` and `ARTIFACTS` summaries.

No raw Account ID, token, intent ID, order ID, identity key or filesystem secret path may appear.

## 21. Evidence envelope identity

```text
qualification_evidence_sha256 = SHA256(exact canonical QualificationEvidenceEnvelope bytes)
```

The digest is public/shareable evidence. No secret HMAC key participates.

Because the canonical envelope contains every phase's status, run ID and summary SHA-256, replacing any underlying phase summary necessarily changes or invalidates `qualification_evidence_sha256`.

## 22. ReleaseArtifactManifest

Version-1 exact keyset:

```text
artifacts
candidate_commit
candidate_tree
domain
qualification_evidence_sha256
release_version
runtime_private_files_present
source_tree_clean
version
```

Domain:

```text
v3.10-cl8-release-artifact-manifest
```

Each artifact entry exact keys:

```text
name
sha256
size_bytes
```

`size_bytes` is a canonical unsigned decimal string. Artifact entries are sorted lexicographically by `name`.

## 23. Artifact-manifest identity

```text
release_artifact_manifest_sha256 = SHA256(exact canonical ReleaseArtifactManifest bytes)
```

The artifact manifest does not contain timestamps and is deterministic for identical candidate/artifact bytes.

## 24. Evidence privacy boundary

Shareable evidence MUST NOT contain:

```text
raw Sandbox Account ID
token
Authorization header
identity key
.env content
raw provider payload
private SQLite database
raw Central intent ID
raw broker order ID
local username/home path
OneDrive path
temporary-directory absolute path
raw unexpected exception text
```

Permitted:

```text
privacy-safe account_scope_sha256
candidate Git SHA/tree
artifact SHA/size
finite status/reason tokens
revisions
counts
normalized timestamps
redacted relative paths
```

---

# PART E — BASELINE ORACLES

## 25. Three baseline layers

```text
A. immutable v3.9 behavioural oracle
B. accepted CL7 predecessor source/custody oracle
C. exact CL8 release-candidate oracle
```

They MUST NOT be conflated.

## 26. v3.9 behavioural oracle

The v3.9 oracle protects:

- no production trading route;
- recovery no-resubmit behaviour;
- persistence/integrity semantics;
- portfolio/risk safety behaviour;
- dry-run isolation;
- release security properties still applicable.

CL8 may intentionally supersede only version/package/release metadata.

## 27. Accepted CL7 predecessor oracle

```text
commit = ef2eba758bbffb587dbe237a96372b2273fdee03
tree = f55788ff362a24d368b0d01dc0d68e85d8183199
```

All CL1–CL7 semantic modules outside frozen CL8 qualification allowlists remain immutable.

## 28. Inherited CL7 current-base custody failures

The accepted CL7 integration evidence dispositions these six historical shallow/cumulative custody assertions:

```text
tests/test_v3_10_cash_ledger_persistence.py::test_v310_cl2_28_three_path_delta_and_immutable_predecessor_files
tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_17_exact_three_path_delta
tests/test_v3_10_runtime_cash_cutover_recovery.py::test_exact_implementation_allowlist
tests/test_v3_10_cash_ledger_opening_reconciliation.py::test_v310_cl4_20_three_path_delta_and_predecessor_custody
tests/test_v3_10_cash_availability.py::test_exact_successor_custody_and_three_path_delta
tests/test_v3_10_reporting_risk_cash_context.py::test_v310_cl6_01_exact_contract_lineage_and_three_path_delta
```

The PRE_RELEASE_CUT identity set above includes the narrowly approved
`CL8-I-REG-01` regression-oracle rescope:

```text
REMOVE = tests/test_v3_10_broker_read_adapters.py::test_v310_cl3_18_cl1_cl2_sources_unchanged
ADD = tests/test_v3_10_runtime_cash_cutover_recovery.py::test_exact_implementation_allowlist
classification = ORACLE_EVOLUTION_DUE_TO_EXPLICIT_CL8_SURFACE
```

The removed CL3 assertion now passes and continues to prove that the frozen
CL1/CL2 sources are unchanged. The added CL7 exact-delta assertion fails only
because the explicitly frozen CL8 qualification surface is present. This
identity substitution does not classify either result as a semantic runtime
regression. Every other PRE_RELEASE_CUT node ID remains byte-for-value
unchanged, and the expected set cardinality remains exactly six.

CL8 does not silently expand this set.

Any additional semantic/custody failure before the intentional release-metadata cut:

```text
BLOCK
```

## 29. Historical release-oracle supersession

After the v3.10 release-metadata cut, only the following closed set of exact pytest node IDs is `SUPERSEDED_RELEASE_METADATA_ORACLE`:

```text
tests/test_stable_release_v3_9.py::test_stable_candidate_version_manifest_and_exact_baseline_are_consistent
tests/test_stable_release_v3_9.py::test_only_v3_9_root_release_documents_are_current
tests/test_stable_release_v3_9.py::test_release_candidate_does_not_claim_manual_m6_acceptance
tests/test_v3_9_source_artifact_qualification.py::test_valid_source_artifacts_are_byte_identical_and_sandbox_only
tests/test_v3_9_source_artifact_qualification.py::test_artifact_manifest_and_zip_contents_are_covered
tests/test_v3_9_stable_preflight.py::test_repository_source_preflight_passes_without_claiming_manual_gates
tests/test_standalone_rc1.py::test_standalone_sources_are_present_and_use_portable_environment
tests/test_observability.py::test_cycle_has_timing_session_and_decision_is_not_an_order
```

The effective set contains exactly eight nodes. The bounded release-review
rescope removes the two cleanup tests because cleanup itself is now a current
v3.10 oracle and adds the exact v3.9 qualification-manifest-shape node that is
superseded by a truthful v3.10 candidate. The path-traversal and secret-canary
verifier tests remain mandatory and passing. There are no regexes, file-wide
exemptions, wildcard node IDs or reviewer-added entries.

Regression equality is evaluated in the terminal current-base PR CI synthetic-merge context used for integration readiness. The two exact gates are:

```text
PRE_RELEASE_CUT:
actual failing node IDs == the six exact inherited custody node IDs in section 28

POST_RELEASE_CUT:
actual failing node IDs ==
the six exact inherited custody node IDs in section 28
+ the eight exact SUPERSEDED_RELEASE_METADATA_ORACLE node IDs above
```

Local/non-shallow supporting runs may cause a known shallow custody assertion to pass, but they never authorize an additional failure and do not redefine either CI equality set.

Before release-candidate acceptance, CL8 MUST mechanically compare the actual failing node-ID set with the applicable exact gate above.

Each of the eight frozen post-cut nodes is classified:

```text
SUPERSEDED_RELEASE_METADATA_ORACLE
```

No other node may receive this classification. Any missing known failure is reported but does not compensate for any new failure.

Any failure that touches execution, Risk, cash, persistence or recovery semantics remains material.

## 30. No pass-count arithmetic

A newly passing old test does not compensate for a newly failing test.

Acceptance compares exact test identities and semantic dispositions, not only totals.

---

# PART F — Q1 REGRESSION AND CUSTODY

## 31. Q1 required checks

At minimum:

```text
exact candidate commit/tree
exact accepted contract ancestry
exact changed-file allowlist
git diff --check
Ruff
format check
compile
full pytest collection
full regression execution
all CL1–CL7 dedicated suites
v3.9 behavioural oracle suites
AST/provider-mutation ownership scan
release metadata diff-shape validation
```

## 32. Q1 provider mutation invariant

Source analysis must prove:

```text
real-account provider mutation route = 0
exact-mode Sandbox order mutation owner = SandboxExecutionAdapter only
no CL8 qualification tool calls provider mutation
```

Qualification tooling may call no authenticated provider method unless inside separately authorized live phase.

## 32.1. Existing GUI/runtime qualification surface

Q1 must exercise the accepted existing GUI/runtime boundary without modifying GUI behaviour. Synthetic/read-only qualification proves:

```text
one account-level Start Sandbox / Stop Sandbox workflow
the complete ConfiguredExecutionSet is shown together
per-instrument timeframe/profile/actual lots/target lots/status is canonical
multiple simultaneous canonical positions are representable
Central reservations and pending/uncertain states are visible
Portfolio Risk state, halt and resync state are visible
restart and disconnect do not create a second owner
OPEN -> MARKET_IDLE -> OPEN remains account-wide
duplicate refresh/provider client path = 0
popup storm under repeated transient failure = 0
GUI/direct provider POST path = 0
widget-owned Risk/Central/Cash calculation = 0
```

The evidence must identify the read-model/service owner for each displayed safety field and prove that GUI widgets do not read/write authoritative JSON directly. Existing behaviour that fails any mandatory item produces `Q0/Q1 FAIL -> RESCOPE`; it does not authorize a CL8 feature correction.

---

# PART G — Q2 CORRUPTION / RESTART / RECOVERY

## 33. Q2 rule

Q2 is offline/synthetic. It uses temp directories, fake provider transport, synthetic IDs and injected clocks.

No token/private account/provider access.

## 34. CashLedger corruption matrix

At minimum:

```text
SQLite header corruption
schema/version mismatch
row/content corruption detectable by accepted graph validation
ledger head mismatch
revision mismatch
WAL present
WAL truncated/corrupt
SHM present/missing combinations
DB locked
backup while locked
large-history bounded validation
partial batch replay
unresolved OperationInbox
```

Expected unsafe result:

```text
zero economic mutation
no silent repair
finite failure
```

## 35. RuntimeCashAuthority corruption matrix

At minimum:

```text
active record truncated
active JSON non-canonical
checksum mismatch
lastgood missing
lastgood wrong revision
lastgood wrong hash
active new/checksum old
active/checksum new/lastgood wrong
pending dispatch proof mismatch
authority record missing while CL2/CL7 artifacts remain
post_attempt_count inconsistency
```

Expected:

```text
RECOVERY_BLOCKED
zero provider mutation
no fallback to LEGACY_ACTIVE
```

## 36. Central / Portfolio / Risk corruption matrix

At minimum:

```text
Central checksum mismatch
Central malformed CL7 proof
Portfolio checksum mismatch
Portfolio stale/corrupt snapshot
RiskPolicy checksum/profile mismatch
RiskState corrupt or kill-switch/resync state
cross-store account-scope mismatch
```

All fail closed before provider mutation.

## 37. CL7 crash-point replay

Q2 MUST re-execute the accepted CL7 crash semantics:

```text
D0..D10
C0..C6
```

and verify:

```text
D3 -> no POST proven
D4+ -> never claim no POST from missing process-local knowledge
D4/D5/D6 -> lookup only, never resubmit
D8 -> reconcile only
D9 -> Risk accounting only
D10 -> finalize only
```

## 38. Persistence failure invariant

No crash/recovery test may:

- decrement an authoritative revision;
- remove a provider-attempt marker;
- create a second order POST;
- convert UNKNOWN/UNCERTAIN to success without evidence;
- silently restore old lastgood as current authority;
- create a second CashLedger.

## 38.1. Multi-session import and recovery matrix

Q2 must cover, with synthetic provider transport and controlled IDs:

```text
session A -> observations/effects -> clean stop -> session B replay
session A -> pending/uncertain -> crash -> session B recovery
parallel distinct session IDs under one account scope
sequential distinct session IDs under one account scope
same operation replayed across sessions
same content under a different source identity
wrong account scope in either session
stale watermark followed by a fresh monotonic watermark
```

Required outcome:

```text
duplicate CashLedger cash effect = 0
duplicate Central/order effect = 0
provider resubmit caused by import/replay = 0
wrong-account adoption = 0
account-scope crossing = 0
OperationInbox/source identity remains exact
ledger/Central/authority revisions remain monotonic
watermarks remain monotonic
pending/uncertain recovery remains explicit and fail-closed
```

The live Q7 matrix repeats session A -> restart/session B and sequential session-ID recovery through the accepted read/sync path. It does not manufacture an economic order or permit a second authenticated provider client.

---

# PART H — Q3 BACKUP / RESTORE / INSTALL / UPGRADE / ROLLBACK

## 39. Runtime backup inventory

A v3.10 backup must explicitly account for all authoritative runtime custody, including when present:

```text
portfolio_state.json + checksum/lastgood custody
central_order_state.json + checksum/lastgood custody
risk_profiles.json
risk_state.json
strategy_profiles.json
multi_instrument_profiles.json + custody
instrument_runtimes.json + custody
robot_state.json
sandbox_diagnostic_state.json
trading_events.db
runtime_cash_authority.json
runtime_cash_authority.json.sha256
runtime_cash_authority.json.lastgood
cash_ledger_v3_10.sqlite3
cash_ledger_v3_10.sqlite3-wal
cash_ledger_v3_10.sqlite3-shm
```

Locks are not restored as authoritative data. Secrets are never copied into a shareable backup.

## 40. Transactional backup rule

Backup must:

1. acquire accepted non-inverting locks or use accepted SQLite-safe backup primitive;
2. snapshot one coherent runtime custody set;
3. calculate per-file SHA-256 and size;
4. write manifest last;
5. verify the completed archive;
6. never alter authoritative source files.

An incomplete backup is never marked valid.

## 41. Restore rule

Qualification restore is:

```text
isolated
no-clobber
```

by default and restores into a new empty directory.

It MUST NOT overwrite the active qualification runtime in place.

## 42. Clean install protocol

One clean-install qualification uses:

```text
empty destination directory
exact release candidate source/standalone artifact
no existing .env
no runtime state
no system-wide project checkout
no private token
```

Expected:

- package starts/validates;
- no provider mutation;
- bootstrap defaults safe;
- exact-cash execution not automatically armed;
- no private file appears in release source.

## 43. Accepted v3.9 -> v3.10 upgrade protocol

Upgrade qualification starts from a copy of accepted v3.9 runtime fixtures.

Steps:

1. verify source v3.9 fixture against accepted v3.9 oracle;
2. create a verified pre-upgrade backup;
3. overlay/install exact v3.10 candidate code into a separate upgrade workspace;
4. execute only accepted bootstrap/migration paths;
5. require existing v3.9 user state preserved;
6. require CL7 authority creation/default to safe legacy/disarmed semantics;
7. require no provider call;
8. require no exact cutover;
9. verify runtime integrity;
10. verify repeated bootstrap is idempotent.

No historical CashLedger backfill is invented.

## 44. Backfill disposition

CL8 V1 does not add a backfill algorithm.

If a Stable requirement cannot be met using accepted FROM_NOW opening + existing CL1–CL7 semantics:

```text
INDETERMINATE -> RESCOPE / DEFER
```

## 45. Rollback protocol before exact attempts

Rollback qualification is code/package rollback, not state rewind.

Safe v3.9 rollback is qualified only from a verified pre-upgrade backup or isolated v3.9 runtime copy.

It must not reinterpret v3.10 exact-state custody.

## 46. Rollback after CL7 activation/attempt

If:

```text
ever_exact_activated == true
```

or especially:

```text
post_attempt_count > 0
```

CL8 MUST NOT claim that in-place downgrade to v3.9 is supported.

Safe disposition:

```text
remain on v3.10
disarm
recover/reconcile
manual operator review
```

A v3.9 code rollback after exact-mode economic attempts is:

```text
UNSUPPORTED / FAIL-CLOSED
```

---

# PART I — Q4 STANDALONE AND RELEASE ARTIFACTS

## 47. Source release artifact

Frozen name:

```text
moex_trading_robot_source_v3_10_0.zip
```

Requirements:

- exact candidate source;
- deterministic sorted member order;
- fixed archive timestamps;
- fixed compression settings;
- no runtime/private files;
- no legacy root-release files;
- `ZIP_CONTENTS.txt`;
- clean extraction.

## 48. Standalone artifact

Frozen name:

```text
moex_trading_robot_standalone_v3_10_0.zip
```

Must run on the supported clean Windows qualification environment without system Python.

Standalone packaging may not contain:

```text
.env
token
runtime JSON/SQLite
logs
backups
support bundles
pytest cache
Git metadata
identity key
raw qualification evidence
```

## 49. Standalone clean-machine check

At minimum:

```text
extract to new path
system Python unavailable from PATH
launch application
open offline/read-only UI path
run offline runtime integrity/readiness
exit cleanly
no provider mutation
```

Provider credential is not required for standalone structural qualification.

## 50. Deterministic source ZIP

Two builds from byte-identical clean source trees must produce:

```text
identical source ZIP SHA-256
identical source ZIP size
identical member list
identical per-member hashes
```

Any difference is `FAIL`.

## 51. Deterministic standalone claim

The release process may claim `deterministic standalone artifact` only if two clean builds using the frozen toolchain produce byte-identical final standalone ZIPs.

If PyInstaller/toolchain nondeterminism prevents this:

```text
FAIL / RESCOPE
```

The contract does not weaken "deterministic" into "looks similar".

## 52. Toolchain identity

Qualification evidence records privacy-safe versions/hashes of:

```text
Python build
pip freeze or locked dependency manifest
PyInstaller
OS edition/build
architecture
build scripts
spec file
```

No absolute private paths.

## 53. Artifact manifest

After final artifact construction:

```text
ReleaseArtifactManifest
```

is generated and hashed.

The candidate source tree MUST be clean.

`runtime_private_files_present` MUST be false.

---

# PART J — Q5 PRIVACY / SUPPORT / RELEASE HYGIENE

## 54. Runtime private-file release denylist

At minimum release scans reject:

```text
.env
robot_state.json
portfolio_state.json
sandbox_diagnostic_state.json
strategy_profiles.json
multi_instrument_profiles.json
instrument_runtimes.json
central_order_state.json
risk_profiles.json
risk_state.json
trading_events.db
cash_ledger_v3_10.sqlite3
runtime_cash_authority.json
runtime_cash_authority.json.sha256
runtime_cash_authority.json.lastgood
*.lock
*.wal
*.shm
runtime_bootstrap_report.json
backups/
logs/
reports/
support/
qualification_output/
verification_output/
```

## 55. Secret/privacy scans

Every source release, standalone package, support bundle and shareable evidence set is scanned for:

```text
known token canary
Authorization: Bearer
raw account-id canary
identity-key canary
raw intent-id canary
raw order-id canary
private absolute-path canary
```

Tests use synthetic canaries. No real secret is placed in a scanner fixture.

## 56. Support bundle policy

Support bundle is sanitized evidence only.

Default support bundle MUST NOT embed entire authoritative runtime databases/state files.

It may include:

- schema/version;
- integrity result;
- privacy-safe hashes;
- revisions;
- finite failure statuses;
- redacted exception categories;
- sanitized recent event summaries if existing support contract permits.

Unexpected exception paths receive the same privacy scan.

## 57. Stale release-label rule

The v3.10 Stable release candidate must have no active user-facing/build-path labels for:

```text
v3.6
v3.7
v3.8
v3.9 Stable Candidate
v3.9.0 Stable Candidate
```

inside active v3.10 launch/build/update/README/release paths.

Historical docs outside active release root may retain historical text.

This rule does not authorize GUI business-logic changes.

## 58. v3.9 root-release cleanup

The old v3.9 root release documents/scripts listed in section 11 are removed from the active v3.10 release root.

They remain available in Git history/tag `v3.9.0`.

The v3.10 source ZIP must not contain two competing Stable instruction sets.

---

# PART K — Q6 CONTROLLED CLOCK

## 59. Two-level controlled-clock qualification

Q6 contains:

```text
Q6A injected-clock offline qualification
Q6B disposable OS-clock qualification
```

Both are required for Stable readiness.

Q6A requires no experiment authority.

Q6B is a separately gated experiment because it changes the environment clock.

## 60. Q6A injected clock

Using synthetic runtime data and injected/caller clocks, verify:

```text
future evidence fails closed
stale evidence remains stale
10-second CL7 final-age boundary
cross-evidence skew boundary
authority timestamps affect identities where specified
attempt-marker recovery does not infer POST from wall clock
restart does not auto-arm because time advanced
clock moves backward -> fail closed where dependency ordering breaks
```

## 61. Q6B environment

Q6B must use a disposable Windows VM or equivalent disposable qualification machine.

Forbidden:

```text
real token
raw Sandbox account
provider/private data
economic provider mutation
```

After required binaries/artifacts are copied in, network is disabled before clock manipulation.

Time synchronization is disabled for the qualification window.

## 62. Frozen Q6B run structure

Two independent clocks:

```text
CLOCK_A
CLOCK_B
```

Each clock performs:

```text
3 fresh runs
```

with a clean runtime directory per run.

Between CLOCK_A and CLOCK_B:

```text
VM restore/recreate
or verified clean system-clock/state reset
```

No state directory is reused across clock families.

## 63. Q6B acceptance

Q6B passes only if:

- all six runs complete the same structural qualification matrix;
- only contract-expected timestamps/hashes vary;
- no hidden local-time/timezone dependency changes authority semantics;
- no stale proof becomes valid;
- no provider call occurs;
- no automatic execution arming occurs.

## 64. Controlled-clock experiment gate

Before Q6B:

```text
PREPARATION STAGE = PASS
experiment_id = CL8-CONTROLLED-CLOCK-V1
exact release candidate commit/tree frozen
artifacts copied
network-disable plan frozen
rollback/recreate plan frozen
```

Then and only then the user may authorize this experiment using the exact governance token:

```text
START EXPERIMENT
```

That authorization applies only to `CL8-CONTROLLED-CLOCK-V1` and does not authorize Sandbox burn-in.

---

# PART L — Q7 LIVE SANDBOX BURN-IN

## 65. Live burn-in is a separate experiment

CL8 contract/implementation/release candidate acceptance does not authorize provider/private-account access.

Before any connected Sandbox action:

```text
Preparation Stage
```

must be completed for exactly:

```text
experiment_id = CL8-SANDBOX-BURNIN-V1
```

## 66. Burn-in candidate freeze

The Preparation Stage binds:

```text
exact candidate commit
exact candidate tree
accepted CL8 contract SHA
source artifact SHA
standalone artifact SHA if used
qualification tooling SHA
account_scope_sha256
identity_key_id
Risk policy hash
strategy profile hashes
instrument configuration hashes
experiment start/end window
evidence output root
```

It MUST NOT record raw Account ID or token in shareable evidence.

## 67. Burn-in experiment gate

After Preparation Stage PASS, the user must separately send:

```text
START EXPERIMENT
```

for `CL8-SANDBOX-BURNIN-V1`.

No provider call occurs before that gate.

A prior `START EXPERIMENT` for controlled-clock qualification cannot be reused.

## 68. Environment restriction

Burn-in environment:

```text
T-Invest Sandbox only
```

No production API host. No real brokerage account. No real-account execution.

## 69. Burn-in duration

Frozen normal window:

```text
minimum continuous observation = 24 hours
maximum normal window = 48 hours
```

Stopping early for a safety event is allowed and produces `FAIL` or `INDETERMINATE`, not PASS.

## 70. Instruments

Burn-in uses:

```text
2 or 3 configured instruments
```

under the normal account-wide runtime configuration.

Single-instrument qualification is insufficient for Q7 PASS.

## 71. No manufactured strategy signals

The burn-in MUST NOT alter Strategy inputs, force signals, fabricate candles or manually create Strategy trades solely to satisfy coverage.

Provider orders may occur only through the accepted normal exact-mode path when natural Strategy decisions authorize them.

## 72. Required live lifecycle evidence

For Q7 full PASS, at least one naturally occurring exact-mode economic lifecycle must reach:

```text
fresh CL3/CL2 sync
-> CL4 reconciliation
-> CL5 READY
-> CL6 READY_FOR_LOCKED_REVALIDATION
-> CL7 locked dispatch proof
-> one provider POST attempt
-> provider outcome
-> canonical Portfolio reconciliation
-> required Risk accounting
-> terminal/local recovery closure
```

If no natural exact-mode economic lifecycle occurs by 48 hours:

```text
Q7 = INDETERMINATE
```

No synthetic or manual trade is used to turn it into PASS.

## 73. Simultaneous position observation

Where natural signals produce it, retain evidence of at least two simultaneously non-zero canonical positions for a real observation interval.

If this does not occur:

```text
LIVE_OVERLAP_NOT_OBSERVED
```

may be recorded as the narrow `EVIDENCE_GAP` permitted by this contract, provided the synthetic multi-position/concurrency suite is PASS.

No other core safety condition may use `EVIDENCE_GAP`.

## 74. Required burn-in events

The live observation window must cover or explicitly inject non-economic environmental conditions for:

```text
normal OPEN operation
OPEN -> MARKET_IDLE -> OPEN
controlled client disconnect/reconnect
read-only provider transient/rate-limit handling where safely observable
application restart
restart with canonical open position if naturally present
operator disarm/rearm boundary
account-level Start Sandbox / Stop Sandbox for the complete configured set
GUI read-back of per-instrument runtime, canonical Portfolio, Central reservations and Risk state
session A -> restart/session B read/sync recovery
```

No failure injection may create an extra economic provider mutation.

## 74.1. Live GUI/runtime observations

During Q7, evidence must show that the existing GUI/runtime workflow:

- starts and stops the account-wide configured set rather than an implicit single instrument;
- displays every configured instrument and its canonical runtime state;
- displays pending/uncertain Central state and account-wide reservation contention;
- displays Portfolio Risk status, halt/resync and canonical position state;
- remains coherent across disconnect, restart and `OPEN -> MARKET_IDLE -> OPEN`;
- emits no duplicate popup/refresh/provider path under repeated transient observations;
- does not bypass the CL7 locked dispatch proof.

Where natural positions do not overlap, only the exact section 88 evidence gap applies to the live overlap observation. The synthetic GUI multi-position/concurrency matrix remains mandatory and must be `PASS`.

## 75. New-order safety invariants

Across the entire burn-in:

```text
duplicate provider submit = 0
duplicate CashLedger cash effect = 0
double-counted reservation = 0
stale-proof provider dispatch = 0
unexplained cash reconciliation delta = 0
silent unknown operation = 0
external flow counted as strategy P&L = 0
fill without canonical Portfolio reconciliation = 0
fill without required Risk accounting = 0
multiple cash/reservation/execution owners = 0
GUI/direct bypass of accepted execution gate = 0
raw Account ID/token in shareable evidence = 0
```

Any non-zero count:

```text
Q7 FAIL
```

## 76. Ambiguous provider outcome

Any ambiguous POST outcome during burn-in:

- immediately blocks new exact dispatch;
- follows CL7 lookup/recovery only;
- performs no resubmit;
- is fully reconciled or remains an explicit blocker.

A still-unresolved ambiguous economic outcome at burn-in end:

```text
Q7 = INDETERMINATE
```

## 77. Burn-in stop condition

Burn-in stops immediately on:

```text
unexpected duplicate mutation
authority corruption
reconciliation mismatch not explained by pending evidence
privacy leak
wrong account/environment binding
unbounded retry
unexpected owner duplication
real-account endpoint detection
```

Evidence is frozen before investigation changes state.

## 77.1. Sandbox-account disposition

Before Q8, exactly one sanitized final disposition must exist:

```text
REDUNDANT_ACCOUNT_RETAINED_WITH_REASON
REDUNDANT_ACCOUNT_CLEANUP_COMPLETED
```

The disposition binds the exact candidate commit/tree, active `account_scope_sha256`, redundant `account_scope_sha256`, evidence run ID, canonical evidence SHA-256 and a finite sanitized reason/result. Raw Account IDs are never written to source, logs, reports, support bundles or shareable evidence.

`REDUNDANT_ACCOUNT_RETAINED_WITH_REASON` performs no provider mutation. It requires a fresh authorized account-list observation, proof that the active account is unchanged, proof that the redundant account is not referenced by runtime/configuration/backup/acceptance evidence, and a reviewed sanitized reason for retention.

Cleanup is a third, separately gated experiment:

```text
experiment_id = CL8-SANDBOX-ACCOUNT-CLEANUP-V1
```

Its Preparation Stage freezes:

```text
exact candidate commit/tree
accepted CL8 contract SHA
fresh account-list evidence SHA
active and redundant account-scope hashes
open position/order/pending/uncertain proof
supported API or manual provider procedure
masked/hash-only operator preview
rollback/stop conditions
```

Only after that Preparation Stage is `PASS` may the user separately send `START EXPERIMENT` for `CL8-SANDBOX-ACCOUNT-CLEANUP-V1`. Immediately before the provider-side action, the operator must enter exactly:

```text
CLOSE CL8 REDUNDANT SANDBOX ACCOUNT <redundant_account_scope_sha256>
```

The action is followed by a fresh account-list read-back proving that the active account and its scope are unchanged and that the redundant account is absent/closed. A missing, ambiguous or mismatched read-back is `INDETERMINATE`; there is no retry or alternate-account guessing.

No account cleanup is a side effect of GUI startup, bootstrap, Q7 burn-in or any other experiment gate.

---

# PART M — DETERMINISTIC RELEASE CANDIDATE

## 78. Candidate immutability

The exact candidate used for:

```text
Q4 artifacts
Q6 controlled clock
Q7 Sandbox burn-in
Q8 release review
```

must be the same commit/tree.

Any source change invalidates downstream qualification evidence.

A corrected source becomes a new release candidate and requires re-running all affected qualification phases.

## 79. Artifact immutability

Once source/standalone artifact hashes enter Q6 or Q7 Preparation Stage, those artifact bytes are immutable.

Rebuild with a different SHA means a new qualification candidate.

---

# PART N — QUALIFICATION IMPLEMENTATION REVIEW

## 80. Qualification implementation review protocol

Exactly:

```text
one independent/adversarial qualification-implementation review
```

Result:

```text
PASS
```

or finite fixed:

```text
CL8-I-R1-xx
```

At most one bounded correction batch, followed by one finding-scoped closure review.

Material blocker after closure:

```text
RESCOPE / ABORT / DEFER
```

No recursive implementation correction treadmill.

## 81. Release-cut review

The bounded release-metadata/package cut receives one dedicated read-only review restricted to:

- exact allowlist;
- no semantic runtime drift;
- version/package names;
- stale-label cleanup;
- artifact/build scripts;
- docs consistency;
- exact source candidate identity.

It is not a second general implementation review.

A material semantic code change found here:

```text
RESCOPE
```

---

# PART O — OFFLINE QUALIFICATION MATRIX

## 82. Minimum offline acceptance cases

```text
V310-CL8-001 exact predecessor/contract/allowlists
V310-CL8-002 v3.9 oracle identity
V310-CL8-003 six inherited CL7 custody failures exact disposition
V310-CL8-004 no-new-failure identity rule
V310-CL8-005 CL1–CL7 semantic source immutability outside allowlist
V310-CL8-006 runtime backup includes CL2 + CL7 custody
V310-CL8-007 runtime backup excludes secrets
V310-CL8-008 backup verify detects single-byte corruption
V310-CL8-009 isolated no-clobber restore
V310-CL8-010 CashLedger corrupt DB
V310-CL8-011 CashLedger WAL/lock matrix
V310-CL8-012 CL7 active/checksum/lastgood corruption matrix
V310-CL8-013 Central corruption matrix
V310-CL8-014 Portfolio corruption matrix
V310-CL8-015 Risk corruption matrix
V310-CL8-016 CL7 C0..C6 replay
V310-CL8-017 CL7 D0..D10 replay
V310-CL8-018 no resubmit on D4/D5/D6
V310-CL8-019 clean install offline safe defaults
V310-CL8-020 v3.9 -> v3.10 upgrade preserves runtime
V310-CL8-021 repeated bootstrap idempotent
V310-CL8-022 rollback from verified pre-upgrade backup
V310-CL8-023 in-place v3.9 downgrade after exact attempt rejected
V310-CL8-024 source release runtime-private denylist
V310-CL8-025 standalone runtime-private denylist
V310-CL8-026 source ZIP two-build deterministic
V310-CL8-027 standalone two-build deterministic
V310-CL8-028 clean standalone without system Python
V310-CL8-029 support bundle sanitation
V310-CL8-030 exception-path sanitation
V310-CL8-031 stale active release-label scan
V310-CL8-032 artifact manifest KAT
V310-CL8-033 evidence envelope KAT
V310-CL8-034 injected-clock future/stale/skew
V310-CL8-035 injected-clock attempt-marker restart
V310-CL8-036 no provider/private access in offline tests
V310-CL8-037 no production host/mutation route
V310-CL8-038 release-cut diff-shape only
V310-CL8-039 source tree clean before artifacts
V310-CL8-040 Git diff/check/ruff/compile/full regression
V310-CL8-041 Q0 exact accepted-#72 commit/tree/review/acceptance custody
V310-CL8-042 existing GUI account-wide configured-set/read-model matrix
V310-CL8-043 multi-session replay/restart/account-scope/watermark matrix
V310-CL8-044 Sandbox-account retained/cleanup disposition and privacy boundary
V310-CL8-045 phase summary substitution/run-ID/digest mismatch rejection
V310-CL8-046 exact pre-cut/post-cut failing-node set equality
```

---

# PART P — CONTRACT-OWNED KAT

## 83. QualificationEvidenceEnvelope KAT

Exact canonical object:

```json
{"candidate_commit":"ef2eba758bbffb587dbe237a96372b2273fdee03","candidate_tree":"f55788ff362a24d368b0d01dc0d68e85d8183199","contract_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","domain":"v3.10-cl8-qualification-evidence","generated_at":"2026-09-12T00:00:00.000000000Z","oracle_v39_commit":"412e126166831a8bac0435d61932ab328fc61f12","oracle_v39_tree":"18c2822d2aa7f174a99d7d7dc5f5a5851dc59eba","overall_status":"PASS","phase_results":{"ARTIFACTS":{"canonical_summary_sha256":"1111111111111111111111111111111111111111111111111111111111111111","run_id":"kat-q4-artifacts","status":"PASS"},"CONTROLLED_CLOCK":{"canonical_summary_sha256":"2222222222222222222222222222222222222222222222222222222222222222","run_id":"kat-q6-controlled-clock","status":"PASS"},"CORRUPTION_RECOVERY":{"canonical_summary_sha256":"3333333333333333333333333333333333333333333333333333333333333333","run_id":"kat-q2-corruption-recovery","status":"PASS"},"GUI_RUNTIME_PREREQUISITE":{"canonical_summary_sha256":"4444444444444444444444444444444444444444444444444444444444444444","run_id":"kat-q0-gui-runtime-prerequisite","status":"PASS"},"INSTALL_UPGRADE_ROLLBACK":{"canonical_summary_sha256":"5555555555555555555555555555555555555555555555555555555555555555","run_id":"kat-q3-install-upgrade-rollback","status":"PASS"},"PRIVACY":{"canonical_summary_sha256":"6666666666666666666666666666666666666666666666666666666666666666","run_id":"kat-q5-privacy","status":"PASS"},"REGRESSION":{"canonical_summary_sha256":"7777777777777777777777777777777777777777777777777777777777777777","run_id":"kat-q1-regression","status":"PASS"},"SANDBOX_BURNIN":{"canonical_summary_sha256":"8888888888888888888888888888888888888888888888888888888888888888","run_id":"kat-q7-sandbox-burnin","status":"PASS"},"STANDALONE":{"canonical_summary_sha256":"9999999999999999999999999999999999999999999999999999999999999999","run_id":"kat-q4-standalone","status":"PASS"}},"release_version":"v3.10.0","version":1}
```

Expected:

```text
qualification_evidence_sha256 =
a16672bd9f825e73b75a51bcd4a7af93f0bafb239b63bb86d5a4d1bf96ded57d
```

## 84. ReleaseArtifactManifest KAT

Exact canonical object:

```json
{"artifacts":[{"name":"moex_trading_robot_source_v3_10_0.zip","sha256":"1111111111111111111111111111111111111111111111111111111111111111","size_bytes":"123456"},{"name":"moex_trading_robot_standalone_v3_10_0.zip","sha256":"2222222222222222222222222222222222222222222222222222222222222222","size_bytes":"654321"}],"candidate_commit":"ef2eba758bbffb587dbe237a96372b2273fdee03","candidate_tree":"f55788ff362a24d368b0d01dc0d68e85d8183199","domain":"v3.10-cl8-release-artifact-manifest","qualification_evidence_sha256":"a16672bd9f825e73b75a51bcd4a7af93f0bafb239b63bb86d5a4d1bf96ded57d","release_version":"v3.10.0","runtime_private_files_present":false,"source_tree_clean":true,"version":1}
```

Expected:

```text
release_artifact_manifest_sha256 =
8d3dda9aa4c747d69a83632758500ac4483ff1787f5ad959b4be520cbcace0fe
```

Future implementation fixture MUST reproduce both exact SHA-256 values.

---

# PART Q — RELEASE ARTIFACT / MANIFEST RULES

## 85. Frozen artifact hash file

Human-readable SHA file:

```text
V3_10_0_RELEASE_SHA256.txt
```

contains exactly one line per distributed artifact:

```text
<lowercase sha256><two spaces><filename>
```

sorted lexicographically by filename and terminated by one LF.

## 86. Release manifest consistency

These must agree exactly:

```text
package __version__
human v3.10.0 Stable identity
source ZIP root/name
standalone ZIP root/name
RELEASE_MANIFEST_V3_10_0_STABLE.txt
build_manifest.json
qualification evidence candidate commit/tree
Git tag target commit
GitHub Release target commit
```

Any disagreement blocks publication.

---

# PART R — Q8 INDEPENDENT RELEASE REVIEW

## 87. Release review inputs

Q8 review receives an immutable package containing:

```text
exact candidate commit/tree
accepted CL8 contract identity
Q0 accepted-#72 commit/tree/review/acceptance identity
qualification implementation identity
release-cut diff
Q1..Q7 canonical summaries + SHA
artifact manifest
artifact SHA file
full regression disposition
privacy scans
controlled-clock evidence
Sandbox burn-in evidence
Sandbox-account final disposition
known EVIDENCE_GAP entries
```

No raw private runtime data.

## 88. Narrow EVIDENCE_GAP acceptance

The only built-in Q7 Stable-compatible evidence gap is:

```text
LIVE_OVERLAP_NOT_OBSERVED
```

and only when:

- Q7 otherwise PASS;
- at least one exact live lifecycle completed;
- 2–3 instruments were continuously configured;
- synthetic multi-position/concurrency coverage PASS;
- no artificial signal was created;
- release reviewer explicitly acknowledges the gap.

Any other gap is `INDETERMINATE` unless the contract is re-scoped.

## 89. Release review verdict

Exactly:

```text
PASS
```

or finite fixed:

```text
CL8-REL-R1-xx
```

CL8 does not authorize a release-review fix treadmill.

A material release blocker means:

```text
RESCOPE / DEFER
```

A documentation-only clerical error may be corrected only if it is already inside the frozen release-cut allowlist and does not invalidate candidate/artifact/live evidence identity; otherwise new candidate qualification is required.

## 89.1. Bounded release-review rescope `CL8-REL-R1-01..05`

The independent release-cut review of exact candidate
`58fc85f26d089da677d68bf3ded6a7cca05fb035` / tree
`9036c7f8d943720a47cbec3c1b68e0444e721458` fixed one finite finding set:

```text
CL8-REL-R1-01 first-run cleanup deletes active v3.10 release files
CL8-REL-R1-02 standalone ZIP loses mandatory empty mutable directories
CL8-REL-R1-03 regression identity/parser does not cover exact outcome
CL8-REL-R1-04 build manifest carries contradictory qualification claims
CL8-REL-R1-05 source template requests a self-referential identity update
```

Exactly one successor commit may close those findings. Its parent is the exact
candidate above and its changed paths must be a subset of:

```text
docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
docs/releases/V3_10_0_STABLE_QUALIFICATION_RU.md
current/tools/release_cleanup.py
current/tools/build_release.py
current/tools/v3_10_stable_qualification.py
current/tests/test_release_hygiene.py
current/tests/test_v3_10_stable_qualification.py
current/tests/test_v3_10_issue72_gui_runtime.py
current/install_and_run_gui.bat
current/BUILD_STANDALONE.bat
current/build_manifest.json
current/VERIFY_V3_10_0_STABLE.bat
```

No second correction round is implied. The successor receives only a
finding-scoped read-only closure review of `CL8-REL-R1-01..05`. Any surviving
material blocker is `DEFER / NEW RESCOPE`; test or review success grants no
qualification, experiment, Stable acceptance, publication or GitHub authority.

---

# PART S — Q9 EXPLICIT STABLE ACCEPTANCE

## 90. Stable acceptance is separate from qualification

Even if Q1–Q8 are PASS, v3.10 is not Stable until the user explicitly accepts the exact candidate.

Frozen acceptance phrase:

```text
ACCEPT V3.10.0 STABLE
```

The acceptance record must bind:

```text
candidate commit
candidate tree
CL8 contract commit/tree
Q0 accepted-#72 evidence SHA
qualification evidence SHA
artifact manifest SHA
source ZIP SHA
standalone ZIP SHA
Q8 release-review verdict
any acknowledged LIVE_OVERLAP_NOT_OBSERVED evidence gap
Sandbox-account final disposition and evidence SHA
```

## 91. Publication is another decision

Stable acceptance does not itself mutate GitHub.

Frozen publication phrase:

```text
PUBLISH V3.10.0 STABLE
```

Only after that separate decision may publication actions occur.

## 92. Publication target

Publication creates/updates only the accepted stable identity:

```text
tag = v3.10.0
target = exact accepted candidate commit
```

No tag may point to a synthetic merge, CI-only commit or different tree.

## 93. GitHub Release assets

GitHub Release assets, if used, must be the already-qualified exact artifact bytes.

Uploading/rebuilding different bytes after Stable acceptance is forbidden.

Asset SHA read-back must equal accepted manifest.

## 94. No post-acceptance source mutation

After:

```text
ACCEPT V3.10.0 STABLE
```

any source change invalidates the accepted candidate.

A change requires a new candidate, affected qualification reruns and new Stable acceptance.

## 95. Issue closure

Issue #56 or equivalent Stable issue closes only after:

```text
Stable acceptance
publication verification
tag target verification
artifact hash read-back
```

Issue closure is metadata and cannot substitute for acceptance evidence.

---

# PART T — POST-PUBLICATION VERIFICATION

## 96. Mandatory read-back

After publication verify:

```text
stable-line ref
v3.10.0 tag ref
GitHub Release target
source artifact SHA
standalone artifact SHA
release notes version
```

All must bind the exact accepted candidate.

## 97. Stable-line rule

If CL8 candidate was integrated by non-force fast-forward before final acceptance:

```text
program/v3-10-v4-stable-line
```

must equal the exact accepted candidate before tagging.

No merge/squash/rebase publication commit is inserted.

---

# PART U — PM0 SUCCESSOR GATE

## 98. PM0 cannot start early

PM0 may be created only after:

```text
CL8 = ACCEPTED / INTEGRATED / COMPLETED
v3.10.0 tag verified
```

PM0 predecessor is the exact v3.10.0 tag target commit/tree.

It is not merely the pre-release CL7 or CL8 contract commit.

---

# PART V — PRIVACY / EXPERIMENT GOVERNANCE

## 99. Experiment separation

CL8 defines three experiments:

```text
CL8-CONTROLLED-CLOCK-V1
CL8-SANDBOX-BURNIN-V1
CL8-SANDBOX-ACCOUNT-CLEANUP-V1
```

Each requires:

```text
its own Preparation Stage
its own exact START EXPERIMENT gate
```

One authorization cannot be reused for another experiment. Each `START EXPERIMENT` record must be explicitly associated with exactly one experiment ID and its frozen Preparation Stage.

## 100. No background or implicit experiment

No CI job, import, test collection, package launch or status command may automatically start:

- provider observation;
- Sandbox burn-in;
- Sandbox-account cleanup;
- OS clock manipulation;
- economic provider mutation.

Live actions are explicit foreground qualification actions only.

---

# PART W — REVIEW / GOVERNANCE

## 101. Contract review

Exactly:

```text
one independent/adversarial CL8 contract review
```

Result:

```text
PASS
```

or finite:

```text
CL8-R1-xx
```

At most one bounded contract correction batch and one finding-scoped closure review.

Material blocker after closure:

```text
RESCOPE / ABORT / DEFER
```

## 102. Qualification implementation review

One independent/adversarial implementation review.

At most one bounded implementation correction batch.

One finding-scoped closure.

No recursive correction cycle.

## 103. Release-cut review

One bounded read-only release-cut review.

A semantic defect discovered after qualification implementation acceptance does not authorize another implementation correction loop.

Disposition:

```text
RESCOPE / DEFER
```

## 104. Integration-readiness

Integration-readiness is limited to:

- exact accepted heads/trees;
- predecessor/base/merge-base;
- allowlist;
- contract blob;
- CI identity;
- release-tool dependency/call-graph drift;
- no semantic authority expansion.

It is not another general implementation review.

---

# PART X — FIRST-FAILURE / FAIL-CLOSED POLICY

## 105. General qualification precedence

For every phase:

1. source/candidate custody;
2. contract/version/schema;
3. evidence canonical integrity;
4. privacy boundary;
5. environment boundary;
6. structural preconditions;
7. semantic qualification;
8. artifact/report generation.

A privacy or custody failure prevents later PASS even if functional checks succeeded.

## 106. Experiment precedence

Before live provider/private action:

1. exact candidate;
2. Preparation Stage;
3. experiment ID;
4. privacy-safe account scope;
5. Sandbox environment proof;
6. safety/disarm preconditions;
7. explicit `START EXPERIMENT`;
8. live action.

Missing any prior step:

```text
NO PROVIDER ACTION
```

---

# PART Y — FINAL ACCEPTANCE INVARIANTS

## 107. Zero-tolerance Stable invariants

Stable cannot be accepted with evidence of:

```text
unbalanced CashLedger transaction
duplicate cash effect
duplicate provider submit
double-counted reservation
silent unknown operation
unexplained reconciliation delta
stale authorization dispatch
external contribution counted as strategy P&L
raw Account ID/token/identity key in shareable evidence
fill without canonical Portfolio reconciliation
fill without required Risk accounting
duplicate cash/reservation/execution owner
GUI/direct bypass of canonical Central/Risk/exact-cash execution gate
automatic resubmit after ambiguous provider outcome
silent authoritative state repair
silent exact->legacy cash fallback
real-account execution route
```

One confirmed occurrence is a release blocker.

## 108. Stable identity is exact, not "latest"

All release evidence refers to one exact commit/tree.

Words such as:

```text
latest
current
recent
main
```

do not carry release authority.

## 109. Exit state before CL8 contract acceptance

Current authority after this contract candidate is created:

```text
CL8 CONTRACT FREEZE CANDIDATE
QUALIFICATION IMPLEMENTATION BLOCKED
RELEASE CUT BLOCKED
PROVIDER ACCESS NOT AUTHORIZED
SANDBOX EXPERIMENT NOT AUTHORIZED
CONTROLLED-CLOCK EXPERIMENT NOT AUTHORIZED
STABLE ACCEPTANCE NOT GRANTED
TAG / GITHUB RELEASE NOT AUTHORIZED
REAL-ACCOUNT EXECUTION FORBIDDEN
```

The only currently authorized repository change remains:

```text
docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
```

---

# PART Z — BOUNDED CONTRACT CORRECTION R1

## 110. Correction custody

```text
correction parent = 4dbc42f5eaabade2125a1a5aff94f0dcb9e37edd
allowed changed path = docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
contract correction batches used = 1
contract correction budget after this batch = EXHAUSTED
```

The exact correction successor commit/tree is assigned by Git after this file is committed and is then subject only to the finding-scoped closure review below.

## 111. Fixed finding closure map

```text
CL8-R1-01 -> Q0 exact accepted-#72 prerequisite; unresolved/deferred means CL8 BLOCKED
CL8-R1-02 -> mandatory existing GUI/runtime synthetic and live qualification surfaces
CL8-R1-03 -> exact retained-or-cleaned Sandbox-account disposition and separate cleanup experiment
CL8-R1-04 -> offline plus live multi-session recovery/account-scope/watermark matrix
CL8-R1-05 -> desktop_gui.py static/display-only release-label allowance; no behaviour change
CL8-R1-06 -> restore_stable_default_risk_profile.bat text/identity-only release allowance
CL8-R1-07 -> every Q0-Q7 phase status/run_id/summary SHA bound by the envelope and KAT
CL8-R1-08 -> exact six-node pre-cut and exact fifteen-node post-cut failure equality
```

Closure review is limited to `CL8-R1-01..08`. A surviving material finding produces `RESCOPE / ABORT / DEFER`; it does not authorize a second correction batch or a new general contract review.

---

# PART AA — REGRESSION-ORACLE RESCOPE CL8-I-REG-01

## 112. Rescope custody and limits

```text
rescope base = 711508e369d23bd6670d5697403fb824b3ea293d
allowed changed path = docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
allowed semantic delta = PRE_RELEASE_CUT exact node-ID substitution in section 28
post-release superseded-node set = UNCHANGED
```

This is a regression-oracle rescope revision and does not consume or reopen the
exhausted contract-correction budget. It does not alter `Q0..Q9`, either
implementation allowlist, either KAT schema/digest, any experiment gate, the
release sequence or the Stable acceptance procedure.

The exact rescope successor commit/tree is assigned by Git after this file is
committed. Before amended-contract acceptance, a read-only
`REGRESSION-RESCOPE CLOSURE REVIEW` must prove the exact one-file range and the
single node-ID substitution above.

---

# PART AB — QUALIFICATION ADOPTION ORACLE RESCOPE

## 113. Exact adoption custody

Issue #72 was accepted and integrated after the original CL8 qualification
snapshot. The only authorized adoption line is:

```text
accepted Issue #72 predecessor = e27204ad110db36b8ace540bd0738874fab69565
accepted Issue #72 predecessor tree = a38d38617dcfa7e15dd1b8ce1f838aeec72c35f1
adoption branch = agent/v3-10-clean-cl8-qualification-adoption
accepted qualification source = a11cfc1f90055ef29d86606fe8377b4ddc2c10f0
accepted qualification source tree = bd92e233ab11f8576e7540d33dfbe9c589b1a13e
mechanical adoption commit = 70589366d3c34ede0cb0aba2a43f988c9f91fbd9
mechanical adoption tree = cfb1f9ea602316da30152a6f6c89b5f2018cf015
mechanical adoption parent = e27204ad110db36b8ace540bd0738874fab69565
source patch range = ef2eba758bbffb587dbe237a96372b2273fdee03..a11cfc1f90055ef29d86606fe8377b4ddc2c10f0
source patch bytes = 262597
source patch SHA-256 = d296fc39183dedc1bbcd3d10dd1b64dfc3b947e2f9e9f9341fb92d681331f560
mechanically adopted paths = 15
source blob mismatches = 0
Issue #72 blob mismatches before oracle correction = 0
```

The accepted Issue #72 commit is an ancestor of every CL8 adoption candidate.
Q0 binds review evidence SHA-256
`12ce5163e3cb041a2592439866882879bff5cfbe8e9c5f01b985778966e1e28b`
and explicit acceptance record SHA-256
`c1870a7ecb7c92a297314a5d3942b92a387e0daa68c6c1af8312de17eb9cd169`.

## 114. Bounded correction allowlist

The fixed finding set is:

```text
CL8-QA-ADOPT-R1-01
CL8-QA-ADOPT-R1-02
```

Exactly one adoption-oracle correction commit is authorized from the mechanical
adoption commit. Its complete changed-path set is:

```text
docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
current/tests/test_v3_10_stable_qualification.py
current/tests/test_v3_10_issue72_gui_runtime.py
```

All production modules, qualification implementation, fixtures, workflows,
release metadata and other tests are immutable in this correction.

## 115. Allowed oracle evolution

`CL8-QA-ADOPT-R1-01` replaces the old side-branch ancestry assertion with exact
proof of the topology above, exact 15-path source adoption, blob equality with
`a11cfc1f...`, and the exact three-path correction delta.

`CL8-QA-ADOPT-R1-02` permits the Issue #72 custody test to recognize only this
named CL8 adoption branch in a local checkout or the same exact head ref in a
GitHub `pull_request` event. The PR form must additionally bind the exact
stable-line base SHA and synthetic-merge parent pair. Both forms must prove the
accepted Issue #72 commit/tree, the exact mechanical adoption parent/tree, the
exact cumulative adoption path set, and byte identity of every Issue #72
implementation path other than the custody test itself. The original Issue #72
implementation-branch oracle remains unchanged.

The `PRE_RELEASE_CUT` expected failure set remains exactly the frozen six-node
CL2-CL7 custody baseline. Neither finding may be closed by adding an expected
failure, weakening path equality, accepting arbitrary descendant branches or
removing a semantic test.

## 116. Closure and authority

The adoption-oracle correction budget is one commit and is exhausted after this
batch. Closure review is limited to `CL8-QA-ADOPT-R1-01..02` and must bind the
exact successor commit/tree. Any surviving or new material failure produces
`REQUEST_CHANGES / RESCOPE / DEFER`.

This rescope grants no Ready, release-cut, provider, experiment, Stable
acceptance, publication or merge authority.

# PART AC — Q1 CANDIDATE CORRECTION RESCOPE

## 117. Fixed finding set and exact parent

This bounded rescope closes only:

```text
CL8-Q1-01
CL8-Q1-02
```

The only authorized correction parent is:

```text
candidate parent = ebd68c7d71929ca194dbcdb9685a140a9eb319d5
candidate parent tree = 170b959512e0a911ee7189d8b4ec1fa398892192
correction branch = agent/v3-10-clean-cl8-q1-correction-r1
correction commits = exactly one
```

The prior Q1 evidence for the parent is invalid for any successor and remains
historical failure evidence only. The successor must receive a fresh exact
commit/tree freeze and a complete Q1 rerun.

## 118. Frozen correction allowlist

The complete changed-path set of the single successor commit is exactly:

```text
docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
current/V3_10_0_STABLE_TEST_PLAN_RU.md
current/tests/test_v3_10_issue72_gui_runtime.py
current/tests/test_v3_10_stable_qualification.py
current/tools/build_release.py
current/tools/v3_10_stable_qualification.py
current/trading_robot/tbank_sandbox.py
```

Every other repository path is immutable.

A raw-blob recheck of the parent found five real formatter deltas. The earlier
eight-path working-tree report additionally counted CRLF materialization for
the three already-canonical parent blobs below. They remain byte-for-value
unchanged in the successor and are outside its changed-path set:

```text
current/tests/test_release_hygiene.py
current/tools/release_cleanup.py
current/trading_robot/__init__.py
```

## 119. Allowed semantic delta

`CL8-Q1-01` permits formatter output from `ruff 0.16.7` on the five Python
paths above. The following two test paths may additionally add only a local
successor-custody branch for this exact correction topology:

```text
current/tests/test_v3_10_issue72_gui_runtime.py
current/tests/test_v3_10_stable_qualification.py
```

That branch must require the exact branch name and parent commit/tree in
section 117, one direct successor commit, and the exact seven-path equality in
section 118. It may not accept arbitrary branches, descendants or paths. This
is a test-only custody adaptation caused by creating the required successor;
it changes no expected regression failure node ID.

The before/after Python ASTs of the remaining three Python paths, excluding
source locations, must be exactly equal. No production import, constant,
annotation, statement, expression, control-flow, authority, provider or
runtime semantic may change.

`CL8-Q1-02` permits exactly one test-plan substitution:

```text
exact nine superseded -> exact eight superseded
```

This aligns the active operator plan with the accepted contract and executable
POST_RELEASE_CUT oracle. The frozen oracle remains six inherited custody nodes
plus eight superseded release-metadata nodes; its node IDs do not change.

The contract change itself is limited to this Part AC governance record.

## 120. Successor closure and authority

The exact successor must prove:

```text
parent = ebd68c7d71929ca194dbcdb9685a140a9eb319d5
changed paths = exact section 118 set
Python AST mismatches outside the two exact custody tests = 0
custody-oracle topology/path equality = PASS
test-plan substitution count = 1
git diff --check = PASS
ruff format --check = PASS with ruff 0.16.7
full Q1 rerun = PASS
material findings = 0
```

This rescope grants no Q6 controlled-clock experiment, Q7 Sandbox burn-in,
provider access, real-account execution, Stable acceptance, tag, GitHub release
or publication authority. Any new Q1 failure blocks acceptance and requires a
separate governance decision.

# PART AD — Q2/Q3 QUALIFICATION CLOSURE RESCOPE

## 121. Fixed findings, parent and one-commit budget

This bounded qualification-only rescope closes exactly:

```text
CL8-Q23-R1-01
CL8-Q23-R1-02
CL8-Q23-R1-03
CL8-Q23-R1-04
CL8-Q23-R1-05
CL8-Q23-R1-06
CL8-Q23-R1-07
```

The only authorized correction topology is:

```text
correction parent = 0b26cb1cf1fbd059ef41e49274ee58d04685a55e
correction parent tree = ba309d04ceec97e1d3586e2c5031a7be3765850c
correction branch = agent/v3-10-clean-cl8-q23-correction-r1
correction commits = exactly one
```

The predecessor Q2/Q3 packet and its two phase summaries remain historical
failed evidence and may not be reused as successor PASS evidence.

## 122. Exact correction allowlist

The complete changed-path set of the one successor commit is exactly:

```text
docs/project/V3_10_CL8_STABLE_QUALIFICATION_RELEASE_CONTRACT_RU.md
current/tools/v3_10_stable_qualification.py
current/tests/test_v3_10_stable_qualification.py
```

All production modules, predecessor tests and fixtures, release artifacts,
workflows and every other repository path are immutable. The tools change may
only strengthen exact offline-case node bindings. The test change may only add
synthetic/offline qualification execution and the exact successor custody
oracle. It grants no product feature or runtime authority.

## 123. Required Q2 closure execution

`CL8-Q23-R1-01` requires the Q2 node map to bind the full accepted CL2 matrix:
schema/version, content/graph/head/revision tampering, WAL/SHM combinations,
writer lock and stale CAS, interrupted batches, online backup/restore/no-clobber,
and bounded large unresolved OperationInbox history. The large-history result
must be built from actual appends, close/reopen validation and byte-exact export;
fixture-declared counters are insufficient.

`CL8-Q23-R1-02` requires exact executed nodes for RuntimeCashAuthority
active/checksum/lastgood combinations, missing authority with remaining CL2
custody, pending-proof/attempt-count inconsistencies, malformed Central proof,
Portfolio corruption/staleness, Risk profile/state failure and cross-scope CL4,
CL5 and CL6 proof rejection. Every generated corrupt custody set is read back
after failure to prove no silent repair.

`CL8-Q23-R1-03` requires separately collected, point-labelled synthetic pytest
cases for every `C0..C6` and `D0..D10`. Each `C` point binds the durable authority
revision/state or pre-custody state across a fresh store instance. Each `D` point
binds an accepted CL7 proof/recovery node. `D3` proves no POST; `D4..D7` preserve
the attempt/uncertainty boundary; `D8`, `D9` and `D10` remain reconcile-only,
Risk-account-only and finalize-only. No node may authorize resubmission.

`CL8-Q23-R1-04` replaces the fixture-declared multi-session PASS with actual
session A/B close/reopen, exact replay, same-content/different-source, two open
writers with stale-then-fresh CAS, CL3 watermark/pagination, wrong-scope HMAC,
Central uncertain restart and CL7 pending restart executions. Only after those
executions may the canonical eight-scenario result be constructed from observed
revision/identity/outcome assertions.

## 124. Required Q3 closure execution

`CL8-Q23-R1-05` requires a fully bootstrapped synthetic runtime plus CL2 custody,
an adversarial `.env` canary and lock file, exact accounting for every present
member of `DEFAULT_RUNTIME_FILES`, manifest-last verification, source-byte
immutability, secret/lock exclusion, isolated no-clobber restore and the exact
CL2 writer/online-backup/interruption nodes.

`CL8-Q23-R1-06` requires a deterministic source archive built from the exact
successor checkout, extraction into an isolated directory and bootstrap of an
empty runtime through that extracted source with an injected fake secret
provider. It also requires `V39_ORACLE_COMMIT/V39_ORACLE_TREE` verification,
`git archive` materialization of that exact source, creation of a v3.9 runtime,
a verified pre-upgrade backup, bootstrap through the extracted exact v3.10
candidate source, byte preservation of all pre-existing v3.9 state, safe
`LEGACY_ACTIVE`/zero-attempt authority creation, no invented CashLedger and an
idempotent second bootstrap.

`CL8-Q23-R1-07` requires pre-attempt rollback only by isolated restore from the
verified v3.9 backup, with restored bytes bound to the source runtime. A separate
actual CL7 authority chain must persist an exact dispatch-attempt marker and
prove `ROLLBACK_FORBIDDEN_AFTER_ATTEMPT`. Release recovery text must state that
v3.9 downgrade after an exact attempt is `UNSUPPORTED`; the qualification does
not claim an in-place package/state rewind.

## 125. Successor packet and closure authority

The successor evidence packet must be newly generated after the one commit and
must bind the exact successor commit/tree, the exact three-path delta, complete
Q2 and Q3 JUnit node identities, canonical phase summaries, manifest hashes and
the closed finding set above. Closure review is limited to
`CL8-Q23-R1-01..07`. Any surviving material finding, new failure, missing node,
non-canonical evidence or path expansion yields `REQUEST_CHANGES / RESCOPE /
DEFER`; a second correction batch is not authorized.

This rescope grants no Q6 controlled-clock experiment, Q7 Sandbox burn-in,
provider or credential access, real-account execution, Stable acceptance,
Ready, merge, tag, GitHub Release or publication authority.
