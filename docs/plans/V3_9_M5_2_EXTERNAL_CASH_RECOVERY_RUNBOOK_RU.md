# v3.9 M5.2 — External Cash Resync и Recovery Runbook

Дата: 2026-08-14
Статус: `COMPLETED / SQUASH-MERGED PR #45 / POST-MERGE CI PASS`

## 1. Граница этапа

M5.2 добавляет fail-closed обработку необъяснимого изменения RUB cash и
закрывает автоматическую restart/no-resubmit matrix. Этап не создаёт Strategy
proposal, Central intent, dispatch, resubmit или provider POST.

M5.2 не включает EventJournal/support bundle, backup/restore, standalone и
rollback qualification: они остаются отдельным M5.3.

## 2. External cash contract

Подтверждённое исполнение после canonical reconciliation обновляет
`RiskState.last_cash_rub` и `last_snapshot_at`. Поэтому следующий Risk cycle не
принимает экономический эффект уже учтённой заявки за внешнюю активность.

Если новый fresh Risk snapshot наблюдает иное значение RUB cash без
предшествующего подтверждённого execution accounting, Risk Engine атомарно
сохраняет:

- `risk_resync_required=true`;
- `risk_resync_source=EXTERNAL_CASH_CHANGE`;
- предыдущий cash anchor и наблюдаемое значение cash/equity;
- timestamp detection и canonical snapshot.

Новые увеличивающие exposure решения после этого блокируются кодом
`RISK_RESYNC_REQUIRED`. Position/ownership drift сохраняет собственный source и
не может быть очищен командой external-cash resync. Уже активный non-cash gate
не заменяется последующим наблюдением cash drift. Если position/ownership drift
обнаружен после cash gate, он получает приоритет, cash-specific evidence
очищается, а cash apply отклоняется. При этом trusted `last_cash_rub` не
продвигается Risk evaluation. После отдельного устранения non-cash gate оставшееся
изменение cash обнаруживается новым fresh Risk cycle.

## 3. Двухфазный operator workflow

### 3.1. Read-only prepare

Сначала остановить instrument runtimes и получить новый canonical broker
snapshot. Затем выполнить:

```powershell
python tools/v3_9_external_cash_resync.py prepare `
  --runtime-dir <isolated-v3.9-runtime> `
  --account-id <sandbox-account-id> `
  --output <outside-runtime>\external_cash_resync_proof.json
```

`prepare` участвует в lock order
`Portfolio → Risk profile → Risk state → Central`, но не изменяет runtime
stores. Команда возвращает SHA-256 proof и точную confirmation-фразу.
CLI до запуска `prepare` или `apply` проверяет lexical и resolved путь
`--output`: он обязан находиться вне `--runtime-dir`. Это исключает перезапись
Risk, Central, canonical или другого runtime state операторским отчётом.

Proof связывает:

- enforced policy hash и точный account scope;
- canonical revision, decision/document checksums и `snapshot_at`;
- canonical equity/RUB cash и исходный cash anchor;
- dispatch-relevant RiskState guard hash;
- Central revision и reservation projection hash.

### 3.2. Apply

Использовать только неизменённый proof:

```powershell
python tools/v3_9_external_cash_resync.py apply `
  --runtime-dir <isolated-v3.9-runtime> `
  --account-id <sandbox-account-id> `
  --proof-sha256 <full-proof-sha256> `
  --confirm "APPLY V3.9 EXTERNAL CASH RESYNC <full-proof-sha256>"
```

Любое изменение policy, canonical snapshot, RiskState или Central projection
делает proof stale и требует нового `prepare`.

CLI отделяет результат operation от доставки отчёта. Если `apply` уже записал
RiskState, а последующая запись `--output` завершилась ошибкой, команда выходит
с ненулевым кодом, но возвращает `status=APPLIED_OUTPUT_ERROR`,
`operation_status=APPLIED` и `writes_performed=true`. Повторять apply по этому
proof нельзя; оператор должен проверить атомарный RiskState и его last-good
`.bak`. Ошибка вывода после read-only `prepare` сохраняет
`writes_performed=false`.

## 4. Обязательные preconditions

Apply допускается только если одновременно выполнены условия:

1. Sandbox policy `READY`, `ENFORCED`, enabled и имеет точный непустой account
   scope;
2. canonical portfolio имеет source `CANONICAL`, completed migration,
   `FRESH`, status `READY/ACTIVE`, `blocking=false`;
3. canonical snapshot новее момента cash-change detection;
4. policy включает stale-snapshot blocking и задаёт конечный
   `max_snapshot_age_seconds`; фактический wall-clock возраст snapshot не
   превышает этот limit ни при `prepare`, ни при `apply`;
5. в canonical portfolio нет active/uncertain pending orders;
6. Central reservation равен нулю, нет active/pending/uncertain intent;
7. resync source точно `EXTERNAL_CASH_CHANGE` и существует исходный cash anchor;
8. текущий RUB cash действительно отличается от anchor минимум на одну копейку;
   сравнение выполняется в целых копейках без binary-float drift;
9. proof SHA-256 и confirmation-фраза совпадают полностью.

## 5. Разрешённая mutation

Apply атомарно обновляет account entry в `risk_state.json` и refresh-ит
last-good `risk_state.json.bak`. Checksum sidecar для RiskState не входит в M5.2;
proof связывает состояние через `risk_state_guard_hash`:

- принимает canonical equity/cash как новые daily/weekly/high-watermark
  baselines;
- обновляет `last_equity_rub`, `last_cash_rub`, `last_snapshot_at`;
- очищает только external-cash resync evidence.

Daily turnover, order count, recorded execution IDs, global/instrument kill
switches и Central history сохраняются. Legacy `reset-baselines` не является
M5.2 acceptance-командой и программно отклоняет source
`EXTERNAL_CASH_CHANGE`: очистить этот gate без canonical proof нельзя. Более
поздний position/ownership mismatch получает приоритет и очищает cash-specific
evidence, но не продвигает trusted cash anchor. После отдельного non-cash reset
следующий fresh Risk cycle повторно обнаруживает оставшийся cash delta.

## 6. Restart/no-resubmit matrix

Автоматический gate подтверждает:

- queued Portfolio Risk proof переживает restart без duplicate submit;
- `IN_FLIGHT` после restart становится `UNCERTAIN`, automatic resubmit запрещён;
- `SUBMITTED/UNCERTAIN` остаётся account-wide blocker до canonical
  reconciliation;
- partial fill учитывается по фактическим lots, Risk accounting выполняется
  ровно один раз, reservation освобождается;
- restart после partial-fill reconciliation не создаёт новую отправку, а
  повторный enqueue того же idempotency key возвращает terminal intent.

## 7. Локальные доказательства

- boundary-inclusive targeted Risk/resync/recovery matrix: `157 passed`;
- full CI regression: `747 passed in 34.93s`;
- `pip check`: PASS;
- critical Ruff: PASS;
- strict v3.9 Ruff: PASS;
- compileall: PASS.

Test fixtures больше не содержат live Sandbox Account ID. M5.2 acceptance не
является общей приёмкой `v3.9-beta1`: M5.3 qualification остаётся отдельной.

## 8. Isolated runtime и restart evidence

Gate выполнен 2026-08-14 в token-free runtime, подготовленном из принятого M4
source. Использовано естественно возникшее `EXTERNAL_CASH_CHANGE`; искусственная
заявка не создавалась. Перед prepare оба instrument runtime имели статус
`STOPPED`, positions/orders и Central reservation/pending/uncertain были нулевыми.

Read-only `prepare` не изменил material runtime hashes. После отдельного точного
operator confirmation live read-only preflight повторно проверил canonical,
Risk и Central; `apply` атомарно обновил `risk_state.json` и last-good `.bak`, а
остальные material stores сохранил byte-identical. Новые daily/weekly/
high-watermark, equity и cash baselines приняты из canonical snapshot; turnover,
order count, execution IDs и kill switches сохранены.

Два запуска в новых Python-процессах подтвердили сохранность baselines,
`risk_resync_required=false`, отсутствие evidence и остановленные runtimes.
Повторное использование consumed proof отклонено fail-closed. Provider POST,
proposal, Central intent, dispatch и resubmit не выполнялись. Проверенный
token-free pre-apply backup сохранён как rollback evidence.

## 9. Итог и следующий отдельный gate

PR #45 squash-merged в `main` (`cc02294`). Post-merge GitHub Actions run
`31825722096`: `747 passed`, pip check, critical/strict Ruff и compileall PASS.
Следующий отдельный этап — M5.3; его runbook:
`docs/plans/V3_9_M5_3_PERSISTENCE_STANDALONE_RUNBOOK_RU.md`.
