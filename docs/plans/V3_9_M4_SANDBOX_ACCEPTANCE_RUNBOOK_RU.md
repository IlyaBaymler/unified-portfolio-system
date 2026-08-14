# v3.9 M4 — изолированный Sandbox acceptance

Дата: 2026-08-14
Статус: `FINAL REVIEW PASS / NO ARTIFICIAL ORDER / READY FOR COMMIT`

Результат gate 2026-08-14: основной runtime подготовлен из принятого M3
canonical revision 18. LKOH/SBER имеют `current_lots=0/0`, оба InstrumentRuntime
`STOPPED`, pending orders 0, Central intents/reservations 0, EventJournal 0.
Шесть checksum-пар валидны; `.env` и robot logs отсутствуют. Bootstrap расширен:
checksummed `portfolio_risk_metadata.json` является допустимым lot-size evidence
для чистого M3 с пустой Central history. Targeted prepare/configure matrix:
`20 passed`; full regression: `710 passed`.

Первая копия из более старого v3.8 revision 16 сохранена только как recoverable
`superseded-v38-revision16` и не допускается к дальнейшей конфигурации.

Gate `CONFIRM PORTFOLIO RISK POLICY` выполнен 2026-08-14. Sandbox policy имеет
статус `READY`, mode `OBSERVE_ONLY`, source `V3_9_M3_OPERATOR`; canonical,
Central, RiskState, InstrumentRuntime и EventJournal при подтверждении не
изменились. Последующий read-only configure preview — PASS: Windows Credential
Manager secure/available, Risk baseline `UNINITIALIZED`, LKOH/SBER lot size
`1/1`, current lots `0/0`, оба runtime `STOPPED`; proposal/intent/provider POST
не создавались, `writes_performed=false`. Следующий отдельный gate —
`CONFIGURE V3.9 SHADOW RUNTIMES`.

Gate `CONFIGURE V3.9 SHADOW RUNTIMES` выполнен 2026-08-14: checksummed config
manifest создан, все ранее существовавшие runtime-файлы byte-identical.
Идемпотентный повтор вернул `ALREADY_CONFIGURED`, `writes_performed=false`,
manifest hash не изменился. Следующий `ENFORCED` preview — PASS и не выполнял
записей. Он явно показывает текущую policy boundary: gross/net/instrument/
strategy/asset-class concentration, max-open-positions, fractional cash reserve
и fractional daily-turnover caps имеют значение `null`; `max_price_age_seconds`
равен `300`, warning utilization — `0.8`. Прежние single-order Risk limits
сохраняются.

Gate `CONFIGURE V3.9 ENFORCED RUNTIME` выполнен 2026-08-14. До изменения policy
создан и независимо проверен pre-activation backup
`v3_9_m4_pre_activation_20260814T091048162488Z.zip`: `VALID`, errors 0.
Portfolio policy имеет `READY/ENFORCED`, policy hash
`d88dd92c9d9c2f59ef170b7eed17f85be4dba665d02ceaab69108727e32f1333`;
checksummed `v3_9_enforced_runtime_manifest.json` имеет статус `ACTIVE`, его
checksum валиден. Оба InstrumentRuntime остаются `STOPPED`, baseline —
`UNINITIALIZED`; Strategy proposal, Central intent и provider POST не создавались.
Повтор вернул `ALREADY_CONFIGURED`, `writes_performed=false`; manifest и Risk
profile остались byte-identical. Authoritative acceptance path сконфигурирован,
но execution не armed и не выполнялся.

Цель gate — отдельно проверить authoritative Portfolio Risk admission и
dispatch-time proof, не изменяя принятый M3 runtime и не создавая искусственную
Strategy-заявку.

## 1. Жёсткие границы

- Используется новая директория runtime; перезапись запрещена.
- Уже запускавшийся M3 runtime не повышается на месте: наличие
  `v3_9_shadow_runtime_start_manifest.json` блокирует M4 configure.
- `preview` не пишет файлы, не создаёт Strategy proposal/Central intent и не
  разрешает provider POST.
- `apply` требует точную фразу `CONFIGURE V3.9 ENFORCED RUNTIME`.
- До изменения Risk profile автоматически создаётся и проверяется token-free
  pre-activation backup.
- Профиль `ENFORCED` без checksummed
  `v3_9_enforced_runtime_manifest.json` со статусом `ACTIVE` блокируется
  acceptance CLI.
- Конфигурация не заменяет отдельные operator arming, prepare, dispatch и
  reconciliation confirmations.

## 2. Подготовка новой изолированной копии

Запускать из директории `current`. Пути и account id подставляются оператором
локально и не включаются в evidence:

```powershell
$source = "<accepted-v3.9-m3-runtime>"
$runtime = "<new-isolated-v3.9-m4-runtime>"

python tools/v3_9_prepare_shadow_runtime.py preview `
  --source-runtime-dir $source `
  --runtime-dir $runtime
```

После проверки preview отдельный apply-gate:

```powershell
python tools/v3_9_prepare_shadow_runtime.py apply `
  --source-runtime-dir $source `
  --runtime-dir $runtime `
  --confirm "PREPARE ISOLATED V3.9 SHADOW RUNTIME"
```

Те же явно проверенные portfolio limits сначала сохраняются в безопасном
`OBSERVE_ONLY` состоянии, после чего создаётся M3 configuration manifest:

```powershell
python risk_profile_tool.py confirm-portfolio-shadow `
  --file "$runtime\risk_profiles.json" `
  --mode SANDBOX_EXECUTION `
  --account-id "<sandbox-account-id>" `
  --confirmation "CONFIRM PORTFOLIO RISK POLICY"

python tools/v3_9_configure_shadow_runtimes.py apply `
  --runtime-dir $runtime `
  --confirm "CONFIGURE V3.9 SHADOW RUNTIMES"
```

InstrumentRuntime должны оставаться `STOPPED`, Central history и EventJournal —
пустыми, START-manifest — отсутствовать.

## 3. Review M4 policy и активация

Сначала только preview:

```powershell
python tools/v3_9_configure_enforced_runtime.py preview `
  --runtime-dir $runtime
```

Оператор проверяет:

- `status=PREVIEW`;
- `portfolio_policy_mode=ENFORCED`;
- полный объект `portfolio_limits`, включая явные `null`;
- `runtime_status=STOPPED`;
- `strategy_proposal_created=false`;
- `central_intent_created=false`;
- `provider_post_authorized=false`;
- `writes_performed=false`.

После отдельного подтверждения:

```powershell
python tools/v3_9_configure_enforced_runtime.py apply `
  --runtime-dir $runtime `
  --confirm "CONFIGURE V3.9 ENFORCED RUNTIME"
```

Ожидается `status=CONFIGURED`, проверяемый `pre_activation_backup`, mode
`ENFORCED` и checksummed M4 manifest со статусом `ACTIVE`. Идемпотентный повтор
возвращает `ALREADY_CONFIGURED` без дополнительных записей.

Фактический результат gate соответствует этим условиям: backup `VALID`, manifest
checksum PASS, повтор не выполнил записей. Следующий шаг — только read-only
`status`; использовать `START V3.9 SHADOW RUNTIMES` для ENFORCED runtime нельзя.

## 4. Runtime observation без искусственной заявки

После конфигурации сначала выполняется read-only `status`. Отдельного ENFORCED
START gate нет: legacy `v3_9_start_shadow_runtimes.py` требует
`OBSERVE_ONLY` и к M4 не применяется. `prepare-one` активирует только выбранный
InstrumentRuntime и создаёт intent лишь при отдельном exact confirmation и
естественном свежем Strategy proposal на новой закрытой свече. Отсутствие
сигнала — валидный `NO_SIGNAL`, а не основание подделывать заявку.

Read-only status gate выполнен 2026-08-14. Первичная проверка выявила, что
общий acceptance CLI инициализировал recovery manager и обновлял только lock
metadata даже при пустой очереди. Status-path отделён от recovery и теперь
напрямую читает checksummed Central state. Повтор на M4 runtime: `READY`, Central
revision 0, queue/reservation/blocker — 0, activation `ACTIVE`, manifest checksum
PASS, `runtime_files_changed=0`. Regression: `710 passed`; scoped Ruff — PASS.
Proposal, intent, arming и provider API не выполнялись.

Для следующего шага добавлен отдельный `preview-natural` в acceptance CLI.
Он не создаёт `CentralOrderManager`, не запускает recovery/InstrumentRuntime и
не обновляет canonical или Risk baseline. Preview сверяет provider/canonical/
runtime lots, broker pending orders, UID/lot metadata, закрытые свечи и exchange
last price, после чего вычисляет Strategy proposal только в памяти.

Live preview 2026-08-14 дал `SIGNAL`: LKOH 30m candle `09:00 UTC` — `HOLD`,
target 0; SBER 1h candle `08:00 UTC` — естественный `BUY`, target 1 при current
lots 0. Quotes получены из `TBANK_LAST_PRICE_EXCHANGE`; оба runtime остались
`STOPPED`. `central_mutation_authorized=false`,
`intent_preparation_authorized=false`, `execution_authorized=false`, broker
order submit false, `writes_performed=false`, `runtime_files_changed=0`.
Regression: `712 passed`; scoped Ruff PASS. Следующий mutating intent gate требует
отдельного точного подтверждения `PREPARE V3.9 ENFORCED INTENT`; этот preview
его не предоставляет. Legacy-фраза v3.8 для активированного M4 runtime
отклоняется до чтения секрета или provider API.

Gate `PREPARE V3.9 ENFORCED INTENT` выполнен 2026-08-14 для естественного SBER
сигнала. Fresh canonical revision 18, single-order Risk и authoritative Portfolio
Risk дали `PASS`; target `0→1` принят без adjustment. Создан ровно один `QUEUED`
Central intent с finalized Portfolio Risk proof и reservation `27876` копеек;
Central revision стал 1. SBER InstrumentRuntime перешёл в `ACTIVE` и содержит
один pending intent, LKOH остался `STOPPED`. `broker_execution_authorized=false`:
dispatch/order submit не вызывались. Независимый checksummed reload подтвердил
один SBER `BUY 1`, activation manifest `ACTIVE`/checksum PASS и
`status_files_changed=0`. Повторный prepare не выполнялся. Следующий отдельный
gate — restart-проверка сохранности queued proof без dispatch/resubmit.

Queued restart gate выполнен отдельным новым процессом. Штатный recovery-path
вернул `IDLE`: `QUEUED` intent не переводился в `UNCERTAIN` и не отправлялся.
Central revision 1, SBER `BUY 1`, Portfolio Risk proof, reservation `27876`
копеек и SBER runtime `ACTIVE`/pending 1 сохранились. Central JSON byte-identical,
checksum валиден; единственное изменение — служебный
`central_order_state.json.lock`, material files changed 0. Secret provider,
broker API, order submit и resubmit не вызывались. Dispatch остаётся отдельным
armed exact-confirmation gate.

Отдельный offline `preflight-dispatch` реализован и выполнен до чтения секрета,
market/provider API и arming. Он повторно проверил exact queue head, canonical,
Risk policy/State и authoritative Portfolio Risk proof под lock order
`canonical → Risk → Central` и ожидаемо завершился fail-closed:
`CANDIDATE_PRICE_STALE; STALE_CANONICAL_SNAPSHOT`. Материальные runtime-файлы,
Central revision 1, единственный `QUEUED` SBER intent, proof и reservation не
изменились; обновилась только служебная metadata четырёх `.lock` файлов.
Переменные arming отсутствовали, broker API/order submit/resubmit — 0.

Для M4 dispatch выделен отдельный внешний контракт: только
`ARM_V3_9_ENFORCED_EXECUTION=YES` и exact confirmation
`ENABLE V3.9 ENFORCED EXECUTION`; legacy v3.8 arming/confirmation к
активированному M4 runtime не применяются. Этот контракт ещё не активирован.
Поскольку pre-dispatch proof устарел, следующий gate — отдельное явное
обновление canonical/quote и reauthorization/reprepare proof. Arming допустим
только после его PASS и отдельного подтверждения пользователя. Targeted matrix:
`48 passed`; full regression: `714 passed`; scoped Ruff: PASS.

Для следующего безопасного шага реализован отдельный `reauthorize-one`. Он
доступен только активированному M4 runtime, требует queue-head `intent_id` и
дословное подтверждение `REAUTHORIZE V3.9 ENFORCED INTENT`. Любое активное
`ARM_V3_8_SANDBOX_EXECUTION=YES` или `ARM_V3_9_ENFORCED_EXECUTION=YES`
отклоняется до чтения секрета/provider API. После подтверждения gate обновляет
canonical snapshot, заново вычисляет естественный Strategy proposal, получает
exchange quote и атомарно обновляет candidate price, cash reservation и
finalized proof. Если natural signal изменился, используется штатный
replace/cancel, искусственный target не создаётся. Broker mutation/order submit
не входят в этот action.

Исправлен reauthorization invariant: свежая цена и reservation теперь входят в
тот же Central lock transaction, что и новый proof; dispatch-time proof после
такого refresh воспроизводится. Targeted matrix: `53 passed`; full regression:
`718 passed`; scoped Ruff: PASS. Live runtime этим изменением ещё не обновлялся:
exact confirmation ожидается отдельно, dispatch остаётся не armed.

Live gate `REAUTHORIZE V3.9 ENFORCED INTENT` выполнен 2026-08-14. Provider GET
обновил canonical observation и exchange quote, после чего естественный SBER
proposal изменился с прежнего `BUY 1` на `HOLD → 0`. Preflight и single-order
Risk дали `PASS`; позиционное изменение отсутствует, поэтому новый Portfolio
Risk proof не создавался. Старый stale SBER `BUY 1` штатно перешёл в
`CANCELLED/OPERATOR_CANCELLED`, reservation освобождена.

Post-gate состояние: Central `READY`, revision 2, queue/reservation/blocker 0;
canonical revision 18 `FRESH/READY`; LKOH `STOPPED`, SBER `ACTIVE` с
`current_lots=0` и pending 0; activation `ACTIVE`. Checksum PASS для Central,
InstrumentRuntime, canonical, legacy shadow и activation manifest. Обе arming
переменные отсутствовали; broker mutation/order submit/resubmit — 0. Это
безопасный `CANCELLED_NO_POSITION_CHANGE`, а не успешное обновление proof и не
разрешение на dispatch.

При естественном proposal отдельными gates проверяются:

1. admission сохраняет finalized Portfolio Risk proof и одну Central reservation;
2. restart сохраняет queued proof без повторной отправки;
3. изменение canonical/queue/policy/RiskState до dispatch даёт `0 provider POST`;
4. dispatch требует отдельное arming и exact confirmation;
5. confirmed fill проходит canonical reconciliation, Risk accounting и
   post-fill Portfolio Risk recalculation;
6. ambiguous submission остаётся `UNCERTAIN` и не resubmit автоматически.

## 5. Acceptance boundary

M4 runtime acceptance считается выполненным только после evidence реального
цикла либо явно принятого no-signal observation, проверки backup и явного
решения пользователя. Успешная конфигурация не является разрешением release,
real-account execution, Commit/Push или merge.

Финальный review M4 завершён и принят 2026-08-14 без искусственной заявки.
Изменённый Python scope: Ruff PASS; полная регрессия `718 passed`;
`git diff --check` PASS. Runtime evidence: Central revision 2, queue/blocker/
reservation 0, canonical revision 18 `FRESH/READY`, pending 0, activation
`ACTIVE`, 13/13 checksum-пар PASS. Pre-activation backup повторно проверен:
`VALID`, errors/warnings 0, 11 entries. Live dispatch/fill намеренно не
выполнялся; stale-proof, dispatch, fill, reconciliation и uncertain/no-resubmit
paths закрыты автоматической матрицей. M4 принят в пределах Sandbox
no-artificial-order boundary и готов к отдельному Commit/Push gate.
