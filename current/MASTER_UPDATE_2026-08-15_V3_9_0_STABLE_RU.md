# Master Update 2026-08-15 — v3.9.0 M6 Stable Candidate

## Exact baseline

- implementation: `cddd80f3caf7191ecf2f85df9e1ccfea97af6cc4` (PR #46);
- qualification branch base: `dd3b9a35f5d2b2dfb9e8776facb81712848e2c7d`;
- commits после implementation baseline меняли только V4 documentation;
- version/channel: `0.3.9 / stable`, status `candidate`.

## Уже принято до M6

- M1–M4 Portfolio Risk shadow/enforcement;
- M5.1 operator controls;
- M5.2 external cash recovery;
- M5.3 persistence, support redaction, structural standalone layout и
  disposable v3.8 restore;
- post-merge CI M5.3: 774 tests, Ruff/compileall PASS, annotations 0.

## Открытые M6 gates

Actual executable build/launch, clean install, upgrade/rollback, restart/
disconnect/partial-fill matrix, manual global/instrument kill switches,
24–48 h natural Sandbox burn-in, final artifacts/secret scan и explicit user
acceptance. До их закрытия tag/release не создаются.

## Automated source preflight

Gate A выполнен 2026-08-15 на isolated M6 worktree: targeted groups
`27 + 112 + 46 passed`, full regression `780 passed`, pip check, release
hygiene, critical/strict Ruff, compileall и candidate safety boundary — PASS.
Broker/runtime/provider POST не использовались. Этот PASS не закрывает ручные
M6 gates.

Gate B построил два byte-identical source ZIP с exact archive root. Verifier
проверил sorted members, fixed timestamps/modes, точный `ZIP_CONTENTS.txt`,
Sandbox-only manifest, отсутствие traversal/duplicates/runtime/private members
и known private-path canary. SHA-256 хранится только во внешнем evidence index,
чтобы source archive не содержал самоссылку.
