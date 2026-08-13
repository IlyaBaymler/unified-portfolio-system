# v3.8 Multi-Instrument Sandbox — operator acceptance

Дата подготовки: 2026-08-13.

Статус: `AUTOMATED MULTI-LOT QUALIFICATION PASS / REAL SANDBOX MULTI-LOT PENDING`.

Архитектурное уточнение: persisted `InstrumentRuntime` в этом runbook —
переходный внутренний ExecutionSlot, а не canonical position owner или
долгосрочный public domain aggregate. Revised scope описан в
`docs/plans/V3_8_REVISED_SCOPE_RU.md`. Все фактически выполненные реальные
Sandbox-сценарии были single-lot. Multi-lot flow автоматизирован и проверен,
но не считается принятым до отдельного реального operator-only прогона.

Фактический прогресс 2026-08-13:

- `V38-A00 PASS` — создан отдельный checksummed runtime, Risk привязан к
  canonical Sandbox account, Central Order history и RiskState пусты, verified
  backup создан без токена;
- `V38-A01 PASS` — read-only broker lookup подтвердил разные instrument UID:
  LKOH/TQBR/30m `02cfdf61-6298-4c0f-a9ca-9cabc82afaf3`,
  SBER/TQBR/1h `e6123145-9665-43e0-8413-cd61b8aa9b13`;
- `writes_performed=false`; multi-instrument profiles/runtime registry не
  созданы;
- `V38-A02 PASS` — созданы два checksummed runtime со статусом `STOPPED`;
  повторный apply отказал до credential/network и ничего не перезаписал;
- `V38-A03 PASS` — offline restart/status: `READY`, revision 0, reserved cash 0,
  blocker отсутствует, очередь пуста;
- `V38-A04 PREFLIGHT_BLOCKED` — fresh broker portfolio обнаружил SBER 1 lot,
  тогда как принятая baseline была flat и canonical target равен 0. Позиция
  имеет `OWNERSHIP_MISSING/UNATTRIBUTED`; intent не создан, Risk не запускался,
  `broker_execution_authorized=false`. До явного operator решения ownership
  подготовка SBER/LKOH остановлена. После fail-closed результата SBER runtime
  безопасно возвращён в `STOPPED` с `current_lots=1` и без pending IDs;
- явным подтверждением `ADOPT SBER 1` установлено, что позиция относится к
  предыдущей burn-in сессии. Fresh recovery precheck прошёл, canonical ownership
  восстановлен: revision 12, `actual_lots=target_lots=1`, `ATTRIBUTED/MATCHED`,
  portfolio `READY`; audit events `OWNERSHIP_RECOVERY_PRECHECK` и
  `OWNERSHIP_RECOVERED` записаны. Broker POST отсутствует. Повтор V38-A04 требует
  нового точного подтверждения подготовки intent;
- повтор V38-A04 для SBER завершён `QUEUED`: fresh canonical revision 12,
  Strategy/Risk target `1 → 0`, preflight/Risk `PASS`, SELL 1 intent
  `e10e93d2-58c3-511d-91d8-f57153deb195`, sequence 1. Central blocker отсутствует,
  reserved cash 0, runtime ACTIVE и содержит ровно этот pending ID;
- `broker_execution_authorized=false`, provider POST отсутствует. Подготовка
  LKOH требует отдельного точного подтверждения;
- V38-A04 для LKOH завершён `QUEUED`: Strategy/Risk target `0 → 1`, BUY 1 intent
  `f6953b8e-cf43-59dd-ae61-ba639ce09c5e`, sequence 2, preflight/Risk `PASS`;
- итоговая очередь детерминирована: sequence 1 SBER SELL 1, sequence 2 LKOH BUY 1.
  Central revision 2, blocker отсутствует, reserved cash 461318 kopecks. Оба
  runtime ACTIVE, каждый содержит ровно свой central pending ID;
- verified pre-dispatch backup создан без токена. Ни один provider POST не
  выполнялся;
- `V38-A06 PASS` — точный queue head SBER SELL 1 отправлен ровно один раз:
  intent/request ID `e10e93d2-58c3-511d-91d8-f57153deb195`, broker order ID
  `cdff9023-528d-466f-befd-8ab8de05dffa`, provider `FILL`, executed lots 1;
- `V38-A07 PASS` — после завершения operator process новый offline status
  сохранил SBER как единственный `SUBMITTED` account blocker, LKOH остался
  sequence 2. Read-only inspection по deterministic request ID вернул terminal
  `ORDER_OBSERVED/FILL`, execution price 280.67 RUB из `executedOrderPrice`;
- повторный dispatch не выполнялся. Verified post-submit/pre-reconcile backup
  создан без токена. До V38-A08 canonical position/runtime/Risk намеренно ещё не
  изменены;
- `V38-A08 PASS` — повторный provider inspection подтвердил FILL 1 по 280.67
  RUB; canonical revision 14, SBER `actual=target=0`, `FLAT/MATCHED`, portfolio
  `READY`. Central SBER intent `RECONCILED/FILLED`, Risk execution status
  `RECORDED` с execution ID, равным intent ID;
- RiskState: daily order count 1, turnover 280.67 RUB, resync не требуется.
  Account blocker снят, SBER runtime `current_lots=0` без pending IDs;
- LKOH остаётся sequence 2, но его authorization revision 12 намеренно stale
  относительно canonical revision 14. До нового `prepare-one` dispatch LKOH
  запрещён. Verified post-reconcile backup создан без токена;
- `V38-A09 reauthorization PASS` — повторный fresh Strategy/Risk сохранил тот же
  LKOH BUY 1 intent `f6953b8e-cf43-59dd-ae61-ba639ce09c5e`, status
  `REAUTHORIZED`, authorization revision обновлена до canonical revision 14.
  Очередь содержит только LKOH, blocker отсутствует, runtime pending ID совпадает;
- verified pre-LKOH-dispatch backup создан без токена. Второй provider POST ещё
  не выполнялся;
- `V38-A09 dispatch/restart PASS` — точный queue head LKOH BUY 1 отправлен ровно
  один раз: intent/request ID `f6953b8e-cf43-59dd-ae61-ba639ce09c5e`, broker
  order ID `03bad187-8a84-42e9-828f-979cc90895aa`, provider `FILL`, executed lots
  1. Новый offline process сохранил LKOH как единственный `SUBMITTED` blocker,
  очередь пуста;
- deterministic read-only inspection подтвердил terminal `ORDER_OBSERVED/FILL`
  и execution price 4555.00 RUB из `executedOrderPrice`. Повторный dispatch не
  выполнялся; verified post-submit/pre-reconcile backup создан без токена;
- `V38-A09 reconciliation PASS` — canonical revision 16, LKOH
  `actual_lots=target_lots=1`, `ATTRIBUTED/MATCHED`, SBER остаётся
  `actual_lots=target_lots=0`, `FLAT/MATCHED`, portfolio `READY`;
- оба central intent имеют `RECONCILED/FILLED`, два уникальных Risk execution ID
  совпадают с intent ID. RiskState: daily order count 2, turnover 4835.67 RUB,
  resync не требуется;
- итоговые queue/blocker/reserved cash равны нулю; оба runtime не содержат
  pending IDs, LKOH current lots 1, SBER current lots 0;
- `V38-A05 SKIPPED` — market был executable; согласно сценарию MARKET_IDLE probe
  не запускался отдельно, потому что мог отправить реальную Sandbox заявку;
- финальный verified backup и redacted support bundle созданы без токена;
  support secret scan `clean=true`, findings отсутствуют;
- исходная automated matrix 104 PASS, full regression 564 PASS;
- `V38-A10 seed preview PASS` — новый target отсутствует, источником выбран
  принятый canonical revision 16 с подтверждённым ownership LKOH; Risk scope
  `MATCHING`, secrets не копируются, `writes_performed=false`. Создание нового
  runtime и трёхинструментный broker flow ещё не выполнялись.
- `V38-A10 seed apply PASS` — отдельный three-instrument runtime создан из
  canonical revision 16: SBER flat, LKOH 1 lot `ATTRIBUTED/MATCHED`, portfolio
  `READY`; Central history и RiskState пусты, account-scoped Risk валиден.
  Multi-instrument profiles/runtime registry ещё не созданы, forbidden files и
  secrets отсутствуют. Verified seed backup создан без токена.
- `V38-A10 configuration preview PASS` — read-only broker lookup подтвердил три
  разных UID: SBER `e6123145-9665-43e0-8413-cd61b8aa9b13`, LKOH
  `02cfdf61-6298-4c0f-a9ca-9cabc82afaf3`, YDEX
  `7de75794-a27f-4d81-a39b-492345813822`; таймфреймы 1h/30m/15m.
  `writes_performed=false`, profiles/runtime registry по-прежнему отсутствуют.
- `V38-A10 configuration apply PASS` — созданы checksummed profiles SBER 1h,
  LKOH 30m и YDEX 15m и три runtime со статусом `STOPPED`, без pending IDs.
  Штатные loaders подтвердили checksum, identity и account scope; повторный
  apply отказал до credential/network, ничего не перезаписав. Offline acceptance
  status: `READY`, Central revision 0, reserved cash 0, blocker отсутствует,
  очередь и RiskState пусты. LKOH runtime пока имеет bootstrap `current_lots=0`,
  тогда как canonical revision 16 содержит принятый LKOH 1 lot; обязательная
  fresh synchronization выполняется следующим `prepare-one` до создания intent.
  Verified backup `three_instrument_configured_20260813T113846Z.zip` создан без
  токена. Стратегии и broker POST не запускались.
- `V38-A10 SBER prepare PASS / NO_POSITION_CHANGE` — fresh broker/canonical
  synchronization подтвердила `current_lots=0` и Strategy target `0`; intent не
  создан, Portfolio preflight и Risk для заявки не потребовались,
  `broker_execution_authorized=false`. Central остаётся `READY` revision 0,
  reserved cash 0, очередь/blocker/Risk executions отсутствуют. SBER runtime
  `ACTIVE`, `current_lots=0`, без pending IDs; LKOH и YDEX остаются `STOPPED`.
  Verified checkpoint `three_instrument_sber_noop_20260813T114148Z.zip` создан
  без токена. Подготовка LKOH требует нового точного подтверждения.
- `V38-A10 LKOH prepare PASS / NO_POSITION_CHANGE` — fresh synchronization
  устранила bootstrap-разрыв: фактические и runtime lots теперь равны 1,
  Strategy target также 1. Intent не создан, Portfolio preflight и Risk для
  заявки не потребовались, `broker_execution_authorized=false`. Central остаётся
  `READY` revision 0 без queue/blocker/reserved cash; RiskState пуст. LKOH
  runtime `ACTIVE`, `current_lots=1`, без pending IDs; SBER остаётся `ACTIVE/0`,
  YDEX — `STOPPED/0`. Verified checkpoint
  `three_instrument_lkoh_noop_20260813T114318Z.zip` создан без токена.
  Подготовка YDEX требует нового точного подтверждения.
- `V38-A10 YDEX prepare PASS / NO_POSITION_CHANGE` — fresh broker/canonical
  synchronization и Strategy proposal подтвердили `0 → 0`; intent, Portfolio
  order preflight, Risk reservation и broker POST отсутствуют,
  `broker_execution_authorized=false`. Отдельный offline restart/status после
  трёх последовательных prepare подтвердил все runtime как `ACTIVE`: SBER 0,
  LKOH 1, YDEX 0, без pending IDs. Canonical остаётся `READY` revision 16;
  Central `READY` revision 0 без intents/queue/blocker/reserved cash, RiskState
  пуст и не требует resync. Verified checkpoint
  `three_instrument_prepare_no_signal_20260813T114545Z.zip` создан без токена.
  Трёхинструментный prepare acceptance пройден, но новый execution/reconciliation
  цикл в этом snapshot не запускался из-за отсутствия position change.
- `FINAL LOCAL REVIEW PASS` — блокирующие code/safety findings отсутствуют;
  исходный full regression 564 PASS, targeted v3.8 matrix 104 PASS, scoped Ruff и
  compileall PASS, branch Ruff delta относительно исходного HEAD равен `-4`.
  Release hygiene, source archive check на 191 файл и CLI help PASS. Финальный
  redacted support bundle
  `three_instrument_final_review_20260813T115158Z.zip` имеет
  `secret_scan_clean=true`; checksummed v3.8 state и EventJournal валидны.
  Искусственная заявка для A10 не создавалась.

- `V38-ML-AUTO PASS` — ветка синхронизирована с `main` на `026e70b`; first-time
  configuration поддерживает явный static `--max-order-lots 5`. End-to-end
  fake-transport qualification прошла `0→3→5→2→0`: Risk-adjusted entry,
  Strategy volatility-target reduction, Strategy exit, четыре уникальных intent
  и ровно четыре POST. Между dispatch и reconcile каждый шаг восстановлен из
  checksummed persisted state; inspection не создаёт повторный POST. После
  каждого fill canonical lots, runtime projection и Risk execution accounting
  совпадают; queue/blocker/reserved cash очищены.
- `V38-ML-PARTIAL PASS` — terminal provider partial fill сохраняется как
  `PARTIALLY_FILLED`, учитывает только executed lots и разрешает новый intent на
  оставшийся target после canonical reconciliation.
- `V38-ML-CASH PASS` — competing multi-lot BUY с недостаточным unreserved cash
  отклоняется без изменения очереди; достаточный snapshot создаёт точную сумму
  reservations и сохраняет deterministic order.
- `V38-ML-REAL-NO-SIGNAL PASS` — новый изолированный runtime подготовлен из
  принятого v3.7 canonical revision 40 (`READY`, две позиции `MATCHED`) и
  настроен для SBER 1h, LKOH 30m и YDEX 15m с явным
  `max_order_lots=5`. Read-only broker lookup, checksum/account/Risk scope и
  verified token-free backup
  `runtime_backup_20260813T130139Z.zip` — PASS. Три последовательных
  market-driven prepare дали `NO_POSITION_CHANGE`: SBER `0→0`, LKOH `0→0`,
  YDEX `0→0`; preflight/Risk — `PASS`, intent, queue, blocker, reserved cash,
  pending IDs и provider POST отсутствуют. После offline restart/status все три
  runtime `ACTIVE`, Central `READY` revision 0, Risk turnover/order/execution
  counters равны нулю. EventJournal SQLite `integrity=ok`: 9 служебных событий
  и 0 order/execution events; все sidecar checksums совпадают.
- актуальные gates: targeted v3.8 matrix 110 PASS, full regression 570 PASS,
  scoped Ruff, compileall и `git diff --check` PASS. Реальный multi-lot broker
  flow `0→3→5→2→0` ещё не выполнялся: readiness с лимитом 5 подтверждена, но
  естественный Strategy signal в этом snapshot отсутствовал.

Этот сценарий предназначен только для отдельного runtime каталога ветки v3.8
и T-Invest Sandbox. Он не разрешает реальный счёт и не подключает execution к
production GUI, существующему bot loop или `GlobalScheduler`.

## Зафиксированная граница

```text
saved SANDBOX strategy profile
→ first-time multi-instrument profile bootstrap
→ fresh canonical broker portfolio
→ complete candles / read-only StrategyProposal
→ Portfolio preflight + SANDBOX_EXECUTION Risk
→ Central Order Manager queue
→ explicit operator dispatch of exactly one queue head
→ terminal provider inspection
→ canonical target + fresh broker portfolio reconciliation
→ idempotent RiskState execution accounting
→ release of the account-wide blocker
```

Только `SandboxExecutionAdapter` вызывает `post_order`. Подготовка intent,
просмотр статуса, inspection и canonical reconciliation не отправляют новую
заявку.

## Обязательные условия

1. Использовать отдельную копию runtime v3.8. Каталог принятой v3.7 Stable не
   изменять.
2. Остановить GUI, bot loop и scheduler на всё время operator acceptance.
3. В `strategy_profiles.json` должен существовать принятый профиль
   `SANDBOX_EXECUTION`.
4. В `risk_profiles.json` должен существовать включённый профиль
   `SANDBOX_EXECUTION` с `account_scope`, точно равным Sandbox account ID.
5. Токен должен находиться в штатном secret provider. Не передавать токен в
   аргументах команд и не сохранять его в отчётах.
6. Начинать с пустой истории `central_order_state.json`. First-time bootstrap
   намеренно не перезаписывает существующие v3.8 profiles/runtimes.
7. Сохранить runtime backup до первого `apply`.

Команды ниже выполняются из каталога `current` рабочей ветки v3.8:

```powershell
$python = "<path-to-qualified-python.exe>"
$runtime = "<isolated-v3.8-runtime-directory>"
$account = "<sandbox-account-id>"
```

## V38-A00 — подготовка изолированного runtime

Принятая v3.7 baseline может содержать корректный, но ещё не привязанный к
конкретному account профиль Risk. Нельзя копировать весь рабочий runtime и
нельзя ослаблять v3.8 account-scope gate. Сначала выполнить безопасный preview:

```powershell
$sourceRuntime = "<accepted-v3.7-runtime-directory>"

& $python -m tools.v3_8_prepare_runtime preview `
  --source-runtime-dir $sourceRuntime `
  --runtime-dir $runtime
```

Ожидается `status=PREVIEW`, `writes_performed=false`, проверенный canonical
portfolio, принятые Sandbox Strategy/Risk profiles и
`source_risk_scope=UNSCOPED` либо `MATCHING`. Account ID в отчёт не попадает:
используется только его короткий SHA-256 fingerprint.

После проверки preview создать новый runtime точным подтверждением:

```powershell
& $python -m tools.v3_8_prepare_runtime apply `
  --source-runtime-dir $sourceRuntime `
  --runtime-dir $runtime `
  --confirm "PREPARE ISOLATED V3.8 SANDBOX RUNTIME"
```

Ожидается `status=PREPARED`. Инструмент:

- не изменяет source runtime и отказывается перезаписывать target;
- переносит только проверенные canonical portfolio и Strategy/Risk profiles;
- привязывает Risk profiles к account из canonical portfolio без изменения
  RiskPolicy;
- создаёт чистые `risk_state.json` и `central_order_state.json`;
- не копирует `.env`, token, логи, журнал и broker-order history;
- записывает checksummed `v3_8_runtime_seed_manifest.json`.

До V38-A02 сохранить проверенный backup уже подготовленного target runtime.
Если source Risk profile привязан к другому account, bootstrap обязан завершиться
отказом; account scope нельзя заменять неявно.

## V38-A01 — first-time configuration preview

Пример для двух инструментов:

```powershell
& $python -m tools.v3_8_configure preview `
  --runtime-dir $runtime `
  --account-id $account `
  --instrument "SBER,TQBR,CANDLE_INTERVAL_HOUR" `
  --instrument "LKOH,TQBR,CANDLE_INTERVAL_30_MIN"
```

Ожидается:

- `status=PREVIEW`;
- два разных `instrument_id`;
- `writes_performed=false`;
- отсутствуют токен и account secret;
- `multi_instrument_profiles.json` и `instrument_runtimes.json` не созданы.

## V38-A02 — first-time configuration apply

После проверки preview:

```powershell
& $python -m tools.v3_8_configure apply `
  --runtime-dir $runtime `
  --account-id $account `
  --instrument "SBER,TQBR,CANDLE_INTERVAL_HOUR" `
  --instrument "LKOH,TQBR,CANDLE_INTERVAL_30_MIN" `
  --confirm "CONFIGURE V3.8 SANDBOX RUNTIMES"
```

Ожидается `CONFIGURED`, два checksummed файла и два runtime со статусом
`STOPPED`. Повторный `apply` обязан завершиться отказом и ничего не
перезаписывать. Единственное восстановление частичного write — совпадающий
profile при отсутствующем runtime registry.

## V38-A03 — offline status

```powershell
& $python -m tools.v3_8_sandbox_acceptance status `
  --runtime-dir $runtime `
  --account-id $account
```

Команда не читает токен и не обращается к сети. Если после сбоя сохранился
`IN_FLIGHT`, startup recovery переводит его в `UNCERTAIN`; автоматический
resubmit запрещён.

## V38-A04 — подготовка двух Risk-authorized intents

Использовать точные `instrument_id` из preview/apply, сначала для SBER, затем
для LKOH:

```powershell
& $python -m tools.v3_8_sandbox_acceptance prepare-one `
  --runtime-dir $runtime `
  --account-id $account `
  --instrument-id "<SBER-instrument-id>" `
  --confirm "PREPARE V3.8 SANDBOX INTENT"

& $python -m tools.v3_8_sandbox_acceptance prepare-one `
  --runtime-dir $runtime `
  --account-id $account `
  --instrument-id "<LKOH-instrument-id>" `
  --confirm "PREPARE V3.8 SANDBOX INTENT"
```

`prepare-one` выполняет fresh canonical refresh, загружает complete candles,
строит read-only proposal, выполняет Risk и сохраняет intent. Broker POST не
вызывается.

Допустимые результаты:

- `QUEUED` — новый intent готов;
- `REAUTHORIZED` — существующий intent подтверждён на новой canonical revision;
- `NO_POSITION_CHANGE` — стратегия выдала HOLD, заявка не нужна;
- `PREFLIGHT_BLOCKED`, `RISK_BLOCKED`, `CANONICAL_CHANGED` — отправка запрещена;
- любой `runtime_sync_warning` — STOP, `dispatch-one` обязан оставаться
  заблокированным.

Нельзя вручную подменять target для получения BUY. Если стратегия дала HOLD,
дождаться следующей закрытой свечи или выбрать другой заранее согласованный
Sandbox instrument.

Повторить `status` и зафиксировать:

- очередь содержит два разных instrument scope;
- queue sequence детерминирован;
- account blocker отсутствует;
- reserved RUB cash равен сумме активных BUY reservations.

## V38-A05 — MARKET_IDLE без POST

Вне торговой сессии выполнить dispatch только для текущего queue head. Значение
`intent_id` копируется из `status` без ручного сокращения.

```powershell
$env:ARM_V3_8_SANDBOX_EXECUTION = "YES"
try {
  & $python -m tools.v3_8_sandbox_acceptance dispatch-one `
    --runtime-dir $runtime `
    --account-id $account `
    --intent-id "<exact-queue-head-intent-id>" `
    --confirm "ENABLE V3.8 SANDBOX EXECUTION"
}
finally {
  Remove-Item Env:\ARM_V3_8_SANDBOX_EXECUTION -ErrorAction SilentlyContinue
}
```

Ожидается `MARKET_IDLE`, `order_was_sent=false`, intent остаётся `QUEUED`.
Если рынок доступен, этот сценарий пропустить: команда может отправить реальную
Sandbox заявку.

## V38-A06 — первый dispatch

Во время торговой сессии повторить команду V38-A05 с явным намерением отправить
одну Sandbox заявку.

Ожидается один из результатов:

- `SUBMITTED` — перейти к inspection, не повторять dispatch;
- `SUBMISSION_REJECTED` — новый submit не выполнять;
- `SUBMISSION_UNCERTAIN` или `STATE_COMMIT_UNCERTAIN` — account-wide STOP,
  только inspection/recovery;
- `MARKET_STATUS_UNAVAILABLE`, `MARKET_STATUS_UNCERTAIN`,
  `CANONICAL_PREFLIGHT_BLOCKED` — POST не доказан, сначала устранить причину и
  снова проверить `status`.

## V38-A07 — disconnect/restart recovery

После `SUBMITTED` закрыть operator process до reconcile и снова выполнить
`status`, затем:

```powershell
& $python -m tools.v3_8_sandbox_acceptance inspect `
  --runtime-dir $runtime `
  --account-id $account
```

Требования:

- новый POST отсутствует;
- inspection использует deterministic request ID;
- единичный provider `404` даёт `NOT_FOUND_UNCERTAIN`, а не
  `NOT_SUBMITTED`;
- transport failure даёт `INSPECTION_UNAVAILABLE` и сохраняет blocker;
- только terminal `ORDER_OBSERVED` разрешает следующий шаг.

## V38-A08 — canonical reconciliation и Risk accounting

После terminal inspection:

```powershell
& $python -m tools.v3_8_sandbox_acceptance reconcile `
  --runtime-dir $runtime `
  --account-id $account `
  --intent-id "<exact-blocking-intent-id>" `
  --confirm "CONFIRM V3.8 CANONICAL RECONCILIATION"
```

Команда не отправляет новую заявку. Она:

1. повторно читает terminal provider order;
2. фиксирует canonical target с deterministic transaction ID;
3. получает fresh broker portfolio;
4. проверяет фактические lots;
5. идемпотентно записывает fill в `risk_state.json`;
6. только затем снимает Central Order blocker;
7. синхронизирует `InstrumentRuntime.current_lots/pending_order_ids`.

Для fill ожидается:

- `status=RECONCILED`;
- `risk_execution_status=RECORDED` или безопасный `DUPLICATE` при recovery;
- `risk_execution_id=intent_id`;
- `runtime_sync_warning=null`;
- новая canonical revision и snapshot позже submit/uncertain transition.

## V38-A09 — обязательная reauthorization второго инструмента

После первого fill снова выполнить `prepare-one` для второго инструмента.
Ожидается тот же intent ID со статусом `REAUTHORIZED` либо безопасная замена
старого `QUEUED` intent, если Risk изменил approved target. До этого шага
`dispatch-one` второго инструмента должен быть заблокирован изменившейся
canonical revision.

Затем повторить V38-A06–V38-A08 для второго queue head.

Финальные инварианты:

- ровно два provider POST и два terminal central intent;
- account blocker отсутствует;
- очередь пуста;
- обе фактические позиции совпадают с canonical targets;
- два уникальных execution ID присутствуют в RiskState;
- `daily_order_count` и turnover учитывают оба fill;
- оба InstrumentRuntime не содержат pending IDs;
- support bundle не содержит токен.

## V38-A10 — третий инструмент

Только после PASS сценариев A01–A09 повторить first-time acceptance в новом
чистом runtime для трёх инструментов, например SBER 1h, LKOH 30m, YDEX 15m.
Не добавлять третий instrument в уже принятую двухинструментную историю через
неявную перезапись profiles.

## Stop conditions

Немедленно остановить acceptance и сохранить support bundle при любом из
условий:

- duplicate provider POST/request ID;
- два account blocker одновременно;
- provider fill без execution price;
- canonical actual lots не доказывают reported fill;
- Risk execution accounting отсутствует или имеет другой ID/policy proof;
- runtime pending IDs расходятся с Central Order Manager;
- checksum mismatch, account scope mismatch или secret в отчёте;
- попытка обращения к real-account endpoint.
