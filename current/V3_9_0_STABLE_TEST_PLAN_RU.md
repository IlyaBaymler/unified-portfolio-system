# План квалификации v3.9.0 Stable Candidate

## A. Автоматический source gate

- exact version/manifest/commit contract;
- full pytest regression без снижения покрытия;
- critical и strict v3.9 Ruff;
- compileall и `git diff --check`;
- release hygiene, runtime/private file scan;
- два deterministic source ZIP с одинаковым SHA-256;
- standalone layout verifier требует `0.3.9/stable`, Risk schema >= 4 и
  Sandbox-only flags.

## B. Standalone/upgrade/rollback

- clean install в новой папке;
- PyInstaller executable запускается без system Python;
- restart из portable launcher;
- upgrade через verified backup/preview/restore;
- isolated rollback к принятому v3.8 runtime;
- checksums, last-good и SQLite integrity PASS;
- provider POST/resubmit при maintenance равны нулю.

## C. Runtime matrix

- restart до POST, после POST и до reconciliation;
- disconnect/MARKET_IDLE recovery;
- partial fill пересчитывает exposure по факту;
- external position/cash fail-closed;
- stale canonical/queue/policy/state proof даёт 0 POST;
- global/instrument kill switches сохраняются после restart;
- final state: pending/uncertain/blockers/reservations согласованы.

## D. Burn-in и release evidence

- естественный Sandbox burn-in 24–48 h на 2–3 инструментах;
- zero duplicate submit, double reservation, stale-proof dispatch;
- zero fill без canonical reconciliation и Risk accounting;
- final backup/support exact-value and exception-path secret scan;
- deterministic manifest/source/package hashes;
- explicit user acceptance до tag/release.

Автоматический PASS не закрывает B–D без фактических артефактов и ручного
подтверждения.
