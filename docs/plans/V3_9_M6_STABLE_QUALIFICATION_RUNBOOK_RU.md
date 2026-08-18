# v3.9 M6 — Stable Qualification Runbook

Дата подготовки: 2026-08-15  
Статус: `AUTOMATED SOURCE + DETERMINISTIC ARTIFACTS PASS / MANUAL GATES PENDING`

## 1. Exact baseline

M6 release candidate создаётся в отдельной ветке
`agent/v3-9-m6-stable-qualification` от `main`
`dd3b9a35f5d2b2dfb9e8776facb81712848e2c7d`.

Функциональный implementation baseline — M5.3 squash merge
`cddd80f3caf7191ecf2f85df9e1ccfea97af6cc4`: два последующих `main` commit
изменяли только V4 documentation. M6 diff ограничивается release identity,
version labels, packaging, qualification automation и release docs; он не может
расширять Portfolio Risk, Central или execution semantics. Exact Stable commit
будет зафиксирован только после review и публикации этого release-only diff.

## 2. Подготовленный candidate contract

- Python/display identity: `0.3.9 / v3.9.0`;
- channel/status: `stable / candidate`;
- `sandbox_only=true`, `real_account_execution=false`;
- PortfolioState schema 2, RiskState schema 4, Central schema 1;
- exact implementation/base commits записаны в `build_manifest.json`;
- все ручные M6 flags остаются `false`;
- v3.7 root release docs заменены v3.9 candidate documents;
- build/source/portable scripts используют только v3.9 filenames;
- `tools/v3_9_stable_preflight.py` проверяет contract fail-closed и не
  обращается к broker/runtime.

## 3. Gate A — automated source preflight

Из чистого checkout:

```powershell
cd current
.venv\Scripts\python.exe tools\v3_9_stable_preflight.py `
  --source-root . `
  --output verification_output\m6_source_preflight.json

.\VERIFY_V3_9_0_STABLE.bat --no-pause
```

Ожидается:

- `status=PASS`, `failures=[]`;
- version/manifest/schemas/exact commits согласованы;
- legacy и private runtime files отсутствуют;
- manual gates остаются `PENDING`;
- full regression, critical/strict Ruff, compileall и pip check PASS.

Автоматический source PASS не является Stable acceptance.

Фактический результат 2026-08-15: release/standalone/preflight/artifacts `27 passed`,
Portfolio Risk/Central `112 passed`, persistence/recovery `46 passed`, full
regression `780 passed`; pip check, release hygiene, critical/strict Ruff,
compileall и candidate safety boundary — PASS. Broker/runtime/provider POST не
использовались.

## 4. Gate B — deterministic source artifacts

Создать два независимых ZIP во внешнем evidence root:

```powershell
$python = ".venv\Scripts\python.exe"
$evidence = "<outside-source>\v3_9_m6\source"

& $python tools\build_release.py --root . `
  --output "$evidence\candidate-a.zip" `
  --archive-root "moex_trading_robot_research_v3_9_0"

& $python tools\build_release.py --root . `
  --output "$evidence\candidate-b.zip" `
  --archive-root "moex_trading_robot_research_v3_9_0"

Get-FileHash "$evidence\candidate-a.zip" -Algorithm SHA256
Get-FileHash "$evidence\candidate-b.zip" -Algorithm SHA256
```

SHA-256 должны совпасть. Независимый scan проверяет отсутствие token, raw
Account ID, `.env`, runtime JSON/SQLite, logs, locks, WAL/SHM, backups и
абсолютных private paths.

Фактический Gate B: два ZIP byte-identical; exact SHA-256, sizes и per-member
hashes записаны во внешнем `v3_9_m6_source_artifact_evidence.json`. Verifier
подтвердил один exact root, sorted members, fixed timestamps/modes, точный
`ZIP_CONTENTS.txt`, отсутствие traversal/duplicates/runtime/private members и
Sandbox-only candidate manifest.

## 5. Gate C — actual standalone

`BUILD_STANDALONE.bat` должен создать
`dist\MOEX_Research_Robot_v3_9_0` с actual executable. Structural fixture из
M5.3 в M6 не допускается.

Перед передачей пользователю проверить:

1. layout verifier: `0.3.9/stable`, Risk schema >= 4;
2. mutable package directories пусты;
3. `.env`, runtime state, token и Account ID отсутствуют;
4. clean Windows host запускает executable без system Python;
5. install/start/close/restart из `MOEX Research Robot.bat` проходят;
6. после закрытия GUI lock повторно захватывается;
7. startup не создаёт proposal/intent/dispatch/resubmit/provider POST.

Actual build и actual user-host launch — разные confirmations.

## 6. Gate D — isolated upgrade и rollback

1. Остановить accepted runtime; queue/pending/uncertain должны быть проверены.
2. Создать verified backup без token.
3. Скопировать candidate в новую isolated папку.
4. Выполнить preview restore.
5. Restore только после `RESTORE RUNTIME`.
6. Проверить schema 2/4, checksums/last-good, SQLite integrity, account scope,
   Central queue/reservations и Portfolio Risk state.
7. Выполнить startup/read-only reconciliation без dispatch/resubmit.
8. На новой disposable copy повторить rollback к принятому v3.8 backup.

Source runtime, accepted runtime и rollback evidence не должны изменяться
перекрёстно.

## 7. Gate E — runtime/recovery matrix

На natural Sandbox activity проверить и сохранить redacted evidence:

- restart до POST, после POST и до reconciliation;
- disconnect и MARKET_IDLE/OPEN;
- partial fill с пересчётом actual exposure;
- stale canonical/queue/policy/state proof даёт `0 POST`;
- ambiguous response остаётся `UNCERTAIN` без duplicate submit;
- external position/cash требует explicit ack/resync;
- fill закрывается только после canonical reconciliation и Risk accounting;
- queue переоценивается после fill/cancel/reject.

Искусственная заявка не требуется, если natural session не создаёт подходящий
сценарий: пункт остаётся pending, а не симулируется в real Sandbox runtime.

## 8. Gate F — manual kill switches

Global и instrument проверки выполняются отдельными operator confirmations:

1. read-only status/explain;
2. exact-confirmation enable;
3. доказательство блокировки risk increase и `0 POST`;
4. restart и read-only проверка сохранности;
5. exact-confirmation disable;
6. свежая revalidation до следующего execution.

Kill switch не сбрасывается автоматически и не считается принятым по unit test.

## 9. Gate G — 24–48 h burn-in

Burn-in использует 2–3 configured instruments и естественные BUY/HOLD/SELL,
cancel/reject/idle события. Финальный отчёт обязан подтвердить:

- duplicate submit = 0;
- double cash reservation = 0;
- stale-proof dispatch = 0;
- fills without canonical reconciliation = 0;
- executions without Risk accounting = 0;
- unresolved pending/uncertain = 0 к release decision;
- unexplained external cash/position adoption = 0;
- Risk runtime errors и secret findings = 0;
- final canonical/Central/Risk state согласован.

Startup success или короткий цикл не заменяет длительный gate.

## 10. Gate H — final evidence review

Собрать во внешнем evidence root:

- source preflight report и full test logs;
- оба deterministic source ZIP и SHA-256;
- standalone package hash/layout/launch evidence;
- clean install, upgrade и rollback reports;
- restart/disconnect/partial-fill/reconciliation evidence;
- global/instrument kill-switch confirmations;
- final backup verification и sanitized support bundle;
- burn-in CSV/report;
- exact-head GitHub CI и annotations.

Final review проверяет evidence read-only. `Stable PASS`, tag/release и закрытие
tracking Issue возможны только после отдельной explicit user acceptance.

## 11. Следующий отдельный gate

После Gate B: `BUILD V3.9 M6 ACTUAL STANDALONE`.
