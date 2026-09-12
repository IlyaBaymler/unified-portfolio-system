# План квалификации v3.10.0 Stable Release Candidate

## Q0 — GUI/runtime prerequisite

Accepted Issue #72 commit/tree, independent review SHA и explicit acceptance SHA
должны быть неизменны и входить в ancestry exact release candidate.

## Q1 — regression и GUI/runtime

- full pytest: exact six inherited custody failures плюс exact nine superseded
  v3.9 release-metadata nodes, без новых failures;
- account-level Start/Stop, configured multi-instrument set и canonical positions;
- Central reservations, Risk, pending/uncertain и CL7 status visibility;
- restart/disconnect/`OPEN -> MARKET_IDLE -> OPEN`;
- zero duplicate refresh, popup и provider mutation paths.

## Q2–Q3 — corruption, recovery, install и rollback

- CL2 SQLite/WAL/SHM, CL7 active/checksum/last-good, Central, Portfolio и Risk;
- CL7 C0–C6 и D0–D10 crash/replay cases, zero resubmit;
- clean install, v3.9→v3.10 upgrade, repeated bootstrap, isolated restore;
- fail-closed rejection of v3.9 downgrade after exact-mode attempt.

## Q4–Q5 — artifacts и privacy

- два byte-identical source ZIP и standalone ZIP builds;
- fixed member ordering/timestamps/compression and exact `ZIP_CONTENTS.txt`;
- clean standalone launch without system Python;
- no runtime/private files, secrets, raw account IDs or private evidence;
- manifest and `V3_10_0_RELEASE_SHA256.txt` exact consistency.

## Q6–Q7 — separately gated qualification

Controlled-clock and Sandbox burn-in require their own Preparation Stage and
fresh `START EXPERIMENT`. Q7 uses 24–48 hours and 2–3 continuously configured
instruments. Sandbox-account final disposition is mandatory before Q8.

## Q8–Q9

Q8 performs one independent exact-candidate release review. Q9 requires the user
phrase `ACCEPT V3.10.0 STABLE`. Publication remains a later decision with phrase
`PUBLISH V3.10.0 STABLE` and exact tag/artifact read-back.
