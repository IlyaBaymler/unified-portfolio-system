# CL8 Q7 — bounded amendment: RiskState custody in final Preparation

Status: contract amendment candidate. This document has no provider, experiment, runtime, release, or publication authority.

## Exact binding and cause

- Predecessor implementation candidate: `a66ffdaef3ae433ac974f561da783690075724ce` / tree `f80e5afa968ee7aa5467cff778ee73d7167e06c0`.
- Accepted Q7 preparation-rescope contract: `1b87316a310e094c8c1c0d2bd3790221b49f60b4`, SHA-256 `119fba8413ec367390eb86072b264d5f23594e8ae2c023b28115d6554636fbaf`.
- Consumed Stage B Preparation: `342f4f8e4abb4e5d28914042def2eda04c8d87c5cc6f5ba5eda27c6d031fccf0`.
- Terminal disposition: `BLOCKED_AT_FINALIZE / INTERNAL_BOUNDARY_FAILED`, SHA-256 `f3e52ff49769683d793f5152d1cb5eb20e0347c343df2b5b56ac99b4cb83c1a5`.
- The post-B1 final-record builder reads `risk_state.revision`. `RiskStateStore.load_account()` returns `RiskState`, which has no revision property. Synthetic access reproduces `AttributeError`. The privacy-safe terminal evidence does not retain the exact historical traceback; this is a code-grounded cause, not a claim to have recovered the traceback.

## Sole semantic amendment

For the `v3.10-cl8-q7-final-preparation` record only, replace the required field `risk_state_revision` in section 50 of `V3_10_CL8_Q7_PREPARATION_RESCOPE_CONTRACT_RU.md` with required `risk_state_guard_hash`. The old field is forbidden in this record; the two fields are not aliases.

`risk_state_guard_hash` is the lowercase SHA-256 custody value produced by the accepted `trading_robot.risk_runtime.risk_state_guard_hash()` from the exact account-level `RiskState` returned by `RiskStateStore.load_account()`. Before B1 creation, the finalizer must prove equality with `fresh_evidence.context.risk_state_guard_hash` from the same fresh CL6 rebuild. The final record must bind that exact value. No schema version, timestamp, file mtime, synthetic counter, GUI `UNKNOWN`, or default value may substitute for a RiskState revision.

Existing fresh Portfolio, Central, RiskPolicy, CashLedger, CL4, CL5, CL6, authority, account-scope, and Q4/Q5 bindings remain required. If the RiskState hash is missing, malformed, non-exact, or mismatched, finalization fails closed before B1. The B1 backup and final record remain create-once. No extra provider call or retry is authorized by this amendment.

## Frozen implementation scope

After separate read-only review and explicit acceptance of this amendment, one bounded successor implementation may change only:

```text
current/tools/v3_10_q7_prepare_runtime.py
current/tests/test_v3_10_q7_preparation_runtime.py
```

The test must use a real `RiskState` without a `revision` attribute, prove successful exact hash binding, reject wrong/missing hashes before B1, and preserve final-record canonical schema, source custody, no-POST and fail-closed behavior. Test mocks must not invent a `revision` property.

The consumed Preparation and the current B1 cannot be promoted. After successor acceptance, repeat exact-candidate Q1, Q4/Q5, offline Stage A/B0, and a new Stage B Preparation. Any new provider READ still requires its exact SHA and a new `START EXPERIMENT`; burn-in remains closed.
