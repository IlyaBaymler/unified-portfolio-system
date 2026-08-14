# Предложение GitHub Issues для v3.9 Portfolio Risk

Дата: 2026-08-14
Статус: `DRAFT — NOT PUBLISHED`

Отдельного v3.9 Issue на момент сверки нет. Пакет обновления предлагает восемь
задач; ниже они адаптированы к факту, что PR #42 уже merged. Создание Issues,
milestone, labels и assignees выполняется отдельно после review документа.

## 1. Umbrella — `v3.9.0 — Portfolio Risk Engine`

Цель: account-wide current/projected risk, concentration, cash reserve,
loss/drawdown/turnover, global/instrument kill switches и staged enforcement.

Gate: `0 duplicate submit`, `0 double reservation`, `0 stale-proof POST`,
`0 fill without reconciliation`, `0 execution without Risk accounting`.

## 2. Pure domain и adapter boundary

Цель: immutable DTO, formulas, deterministic evaluator/sizing и synthetic
fixtures без GUI, persistence и broker dependencies.

Зависимость: interface-freeze review; не зависит от ручного Sandbox сигнала.

Локально: `DONE (M1+M2)`, публикация Issue/PR отдельно не выполнялась.

## 3. `v3.9-alpha1` — read-only snapshot

Цель: adapter из одной canonical lease/queue projection, current metrics,
HHI diagnostic и report, без влияния на execution.

Gate: exact expected-value audit и идентичное v3.8 execution behaviour.

Локально: `DONE / ALPHA1 CANDIDATE` — read-only report, versioned migration,
configuration gate, restart/integrity/account-scope и `0 POST` regression
покрыты; ручной release acceptance не заявлен.

## 4. `v3.9-alpha2` — prospective policy и lot caps

Цель: projected portfolio, per-rule caps и статусы
`PASS/ADJUSTED/REDUCTION_ALLOWED/BLOCKED`.

Gate: каждый cap объясним и никогда не увеличивает Strategy target.

Локально: `DONE (M1 evaluator/sizing + M3 runtime shadow use)`; публикация и
release acceptance отдельно не выполнялись.

## 5. `v3.9-alpha2` — shadow observability

Цель: одно shadow decision на каждый eligible proposal, EventJournal/report,
current/projected metrics и drift classification без enforcement.

Gate: coverage 100%, unexplained drift 0, broker calls/Central transitions не
изменены.

Локально: `IMPLEMENTED / TESTED`; deterministic restart/concurrent duplicate,
EventJournal, read-only report, drift и failure isolation покрыты. Targeted
`171 passed`, full `657 passed`, Ruff PASS. Первая реальная Sandbox выборка дала
coverage `2/2`, `UNAVAILABLE=0`, `MATCH=2`, unexplained drift 0 и не изменила
Central/broker, но обе shadow decision были `HALTED`: поздняя цена и исправленный
после gate дефект future-snapshot timestamp. Финальный acceptance требует чистого
повтора на новой естественной свежей свече; M4 пока закрыт.

Повторный runtime gate подтвердил fix future-snapshot и довёл aggregate coverage
до `4/4`, unavailable 0, `MATCH=4`, unexplained drift 0 без Central/broker
изменений, но снова получил два `CANDIDATE_PRICE_STALE`. Root cause: provider
historic candle time нельзя использовать как свежесть candidate price, а
`HistoricCandle` не содержит отдельного trade timestamp. Fix реализован через
официальный `GetLastPrices` и immutable exchange quote (price/time/source), со
structured `UNAVAILABLE` для missing/invalid и hard blocks для stale/future.
Read-only Sandbox preview PASS; targeted 86, full 689, Ruff PASS. Следующий
natural apply остаётся отдельным operator gate и только он может закрыть M3.

Natural apply выполнен на новой LKOH 30m свече `20:00 UTC`: одно новое
`HOLD=0` observation имеет `EVALUATED/PASS`, hard blocks отсутствуют,
`MATCH`, unexplained drift false; candidate quote source
`TBANK_LAST_PRICE_EXCHANGE`, time `20:42:41 UTC`. SBER не имел новой закрытой
1h свечи после checkpoint `19:00 UTC` и не создал дубликат. Aggregate report —
coverage `5/5`, unavailable 0, `MATCH=5`, unexplained drift 0,
`execution_authorized=false`. Central byte-identical, canonical economic state,
Risk counters/reservations и pending orders неизменны. Post-gate backup VALID,
token-free, 14 entries. M3 eligible coverage и clean LKOH quote path — PASS;
полный clean sample по обоим инструментам остаётся evidence gap. M4 требует
отдельного review и не разрешён этим gate автоматически.

Повтор 2026-08-14 дал полный clean sample: LKOH 30m `06:30 UTC` — `HOLD=0`,
SBER 1h `06:00 UTC` — естественный `BUY 1`. Оба результата `EVALUATED/PASS`,
approved targets совпали с v3.8 (`0/1`), reason codes пусты, drift `MATCH`,
unexplained drift false. Scheduler 8/8, failures 0; aggregate coverage `7/7`,
unavailable 0, `MATCH=7`, unexplained drift 0, execution authorization false.
Central byte-identical и пуст, canonical revision/позиции и Risk economic
counters не изменены, broker POST 0. Evidence backup VALID/token-free, 14
entries. M3 runtime gate выполнен и готов к финальному review; M4 требует
отдельного решения.

Финальный review M3 завершён: `689 passed`, scoped core Ruff и critical Ruff по
изменённому Python scope PASS, compileall/diff-check PASS, secret/runtime
artifact scan чистый. M3 принят как FINAL REVIEW PASS. M4 readiness boundary
PASS, но authoritative proof/admission/dispatch ещё не реализованы; M4 остаётся
отдельной задачей после публикации M1–M3.

## 6. `v3.9-alpha3` — authoritative Central preflight

Цель: объединить single-order Risk, Portfolio Risk и reservation caps;
добавить atomic admission и dispatch-time proof recheck.

Gate: только после persistence/recovery и shadow gates; stale proof или
contention всегда дают 0 provider POST/0 double reservation.

Локально: `AUTOMATED FINAL REVIEW PASS`. В отдельной M4-ветке реализованы nested Portfolio Risk
proof, atomic Central admission/reservation, зафиксированный lock order
`canonical → Risk profile → Risk state → Central`, dispatch reproduction,
mutation invalidation, restart и post-fill recalculation. Targeted matrix
`13 passed`, включая projected concentration активной очереди и fail-closed
Risk-lock timeout; full regression `710 passed`, строгий Ruff, compileall и
diff-check — PASS. Runtime acceptance, Commit/Push и GitHub delivery остаются
отдельными gates.

Операторская активация M4 подготовлена отдельным fail-closed gate: только свежий
STOPPED runtime с пустыми Central/EventJournal и без START-manifest; preview не
пишет файлы, apply требует `CONFIGURE V3.9 ENFORCED RUNTIME`, создаёт проверяемый
pre-activation backup и checksummed `ACTIVE` manifest. `ENFORCED` profile без
совпадающего manifest не подключается к acceptance execution path. Gate не
создаёт искусственную заявку и не разрешает provider POST. Configuration tests:
`7 passed`, affected acceptance matrix: `37 passed`.

Prepare gate выполнен: отдельный M4 runtime создан из принятого M3 canonical
revision 18, LKOH/SBER `0/0`, оба runtime `STOPPED`, pending/Central/EventJournal
0, checksum PASS, секреты/логи не копировались. Policy остаётся
`CONFIGURATION_REQUIRED/OBSERVE_ONLY`; никакой execution не авторизован.

После prepare выполнен `CONFIRM PORTFOLIO RISK POLICY`: policy теперь
`READY/OBSERVE_ONLY`, остальные runtime stores неизменны. Shadow configuration
preview PASS: secure credential provider, Risk baseline `UNINITIALIZED`, оба
runtime `STOPPED`, proposal/intent/provider POST 0. На этом preview-этапе apply
configuration manifest ещё не выполнялся.

Gate `CONFIGURE V3.9 SHADOW RUNTIMES` выполнен, checksum manifest валиден,
идемпотентный повтор не пишет состояние. `ENFORCED` preview PASS/read-only:
portfolio-wide hard caps явно `null`, freshness `300s`, warning utilization
`0.8`; single-order limits сохранены.

Gate `CONFIGURE V3.9 ENFORCED RUNTIME` выполнен 2026-08-14: pre-activation
backup `VALID`, errors 0; policy `READY/ENFORCED`, checksummed activation
manifest `ACTIVE` и валиден. Повтор `ALREADY_CONFIGURED` не выполнил записей;
manifest и Risk profile byte-identical. InstrumentRuntime остаются `STOPPED`,
baseline `UNINITIALIZED`, proposal/intent/provider POST — 0. Authoritative
acceptance path сконфигурирован, но execution не armed; следующий шаг —
read-only status без SHADOW START и без искусственной заявки.

Read-only status выполнен. Исправлен побочный lock-write: status больше не
инициализирует recovery manager. Live-повтор: Central `READY`, revision 0,
queue/reservation/blocker 0, activation `ACTIVE`, checksum PASS,
`runtime_files_changed=0`; proposal/intent/arming/provider API — 0. Полная
регрессия `710 passed`, scoped Ruff PASS. Следующий evidence gate ждёт
естественный Strategy proposal либо фиксирует принятый `NO_SIGNAL`.

Read-only `preview-natural` реализован и выполнен. Provider/canonical/runtime
lots совпали, pending orders 0, UID/lot metadata и exchange quotes валидны. LKOH
дал `HOLD → 0`, SBER — естественный `BUY → 1`. Оба InstrumentRuntime остались
`STOPPED`; Central mutation, intent preparation, arming и order submit — false,
`runtime_files_changed=0`. Regression `712 passed`, scoped Ruff PASS. Следующий
intent gate не выполнен и требует отдельного точного подтверждения
`PREPARE V3.9 ENFORCED INTENT`; legacy v3.8 confirmation для M4 запрещена.

M4 intent gate выполнен на естественном SBER `BUY 1`: canonical revision 18,
preflight/Risk/Portfolio Risk PASS, один `QUEUED` Central intent с finalized
proof, reservation `27876` копеек, Central revision 1. SBER runtime ACTIVE с
pending 1, LKOH STOPPED. Broker execution/dispatch/order submit — 0; независимый
status reload checksum PASS и без записей. Следующий gate — restart queued proof
без resubmit, а не dispatch.

Queued restart gate выполнен новым процессом: recovery оставил SBER intent
`QUEUED`, Central revision 1, finalized proof, reservation `27876` копеек и
pending 1 без изменений. Central JSON/checksum byte-identical; material state
changes 0, обновилась только `.lock` metadata. Secret provider/broker API/order
submit/resubmit — 0. Dispatch остаётся отдельным закрытым gate.

Pre-dispatch revalidation реализована отдельной offline-командой до arming,
секрета и provider API. Live запуск fail-closed отклонил сохранённый proof:
`CANDIDATE_PRICE_STALE; STALE_CANONICAL_SNAPSHOT`; queue/proof/reservation и
материальные runtime-файлы не изменились, provider POST 0. Отдельный M4 dispatch
контракт — `ARM_V3_9_ENFORCED_EXECUTION=YES` и exact confirmation
`ENABLE V3.9 ENFORCED EXECUTION`; legacy v3.8 значения не применяются. Arming не
выполнялся: следующий gate должен явно обновить canonical/quote и reauthorize/
reprepare proof. Targeted matrix `48 passed`, full regression `714 passed`,
scoped Ruff PASS.

Следующий gate реализован как M4-only `reauthorize-one` с exact confirmation
`REAUTHORIZE V3.9 ENFORCED INTENT`. Он не допускает установленных v3.8/v3.9
dispatch arms, не вызывает provider POST и обновляет только canonical/natural
proposal/quote, Risk authorization, Central candidate/reservation/finalized
proof и runtime projection. Refresh одного intent теперь атомарно сохраняет
новую цену и reservation вместе с proof; новый natural signal использует
replace/cancel без искусственного target. Targeted `53 passed`, full regression
`718 passed`, Ruff PASS. Live gate ожидает отдельного подтверждения.

Live gate подтверждён и дал `CANCELLED_NO_POSITION_CHANGE`: новый natural SBER
signal — `HOLD → 0`, preflight/Risk PASS, stale `BUY 1` отменён, reservation
освобождена, новый proof не создавался. Central revision 2, queue/reservation/
blocker 0, canonical `FRESH/READY`, activation `ACTIVE`, checksums PASS. SBER
runtime `ACTIVE` с pending 0, LKOH `STOPPED`; dispatch arming/provider POST/order
submit/resubmit — 0.

M4 закрыт `FINAL REVIEW PASS` без искусственной заявки и squash-merged через
PR #43 в `main` (`d589f34527499b31afc5d19736ec1ccb9cc1e465`). Post-merge CI:
`721 passed`, pip check, critical/strict Ruff и compileall PASS, annotations 0.
Финальный runtime audit до публикации: 13/13 checksum-пар PASS, Central
queue/blocker/reservation 0, pending 0 и `VALID` pre-activation backup
(11 entries, errors/warnings 0). Live dispatch/fill не выполнялся по принятой
acceptance boundary; safety-path покрыт автоматическими
dispatch/fill/uncertain/no-resubmit tests.

## 7. Persistence/recovery foundation и `v3.9-beta1` hardening

Цель: versioned migration существующих Risk stores, kill-switch recovery,
external cash resync, backup/restore, standalone, dashboard/support bundle.
Задача имеет завершённый foundation и три отдельных beta1-чекпоинта:

- foundation M1–M4: schema migration, explicit Sandbox policy configuration,
  durable kill switches, Central enforcement и restart/rollback tests — merged;
- M5.1 operator control plane: read-only inspect/explain и policy review,
  отдельные exact-confirmation команды для global/instrument kill switches;
- M5.2 recovery: external cash baseline resync и restart matrix для active
  reservation, partial fill и pending/uncertain;
- M5.3 packaging: EventJournal, backup/restore, sanitized support bundle,
  standalone bootstrap/layout и rollback к принятому v3.8 runtime.

Gate: migration idempotent, corrupt state fail-closed, isolated rollback к
принятому v3.8 проходит.

M5.1 operator control plane реализован и прошёл локальный финальный review.
Исправлены canonical uppercase instrument ID, блокирующий Sandbox status для
`OBSERVE_ONLY` и обязательный account scope. Targeted `7 passed`, full
regression `728 passed`, pip check, critical/strict Ruff, compileall и
diff-check PASS. Proposal/intent/dispatch/provider POST отсутствуют;
PR #44 снят с draft и squash-merged в `main` (`ac308660`). Post-merge CI:
`728 passed`, pip check, critical/strict Ruff, compileall PASS, annotations 0.

M5.2 локально реализован в `agent/v3-9-beta1-m5-2-recovery`: RiskState schema 4
отличает необъяснимый `EXTERNAL_CASH_CHANGE` от position/ownership drift,
confirmed fill обновляет cash anchor, а двухфазный CLI связывает apply с
enforced policy, fresh canonical revision/checksums, Risk guard и пустой Central
reservation projection. Stale proof, pending/uncertain или non-cash source
fail-closed; automatic dispatch/resubmit отсутствуют, economic counters и
execution IDs сохраняются. `prepare` и `apply` требуют конечный policy limit и
повторно проверяют фактический wall-clock возраст canonical snapshot. Isolated
runtime и два restart/read-only запуска приняты 2026-08-14 на естественном
external cash change без искусственной заявки: apply атомарно обновил RiskState и
его `.bak`, provider POST/dispatch/resubmit отсутствовали, consumed proof
отклонён. Final-review correction закрепляет приоритет non-cash resync gate,
сохранность trusted cash anchor до последовательного recovery, one-kopeck-safe
comparison, фактический mutation inventory и правдивый статус уже выполненной
mutation при ошибке записи CLI output. Boundary-inclusive targeted matrix
`157 passed`, full CI regression `747 passed`; live Sandbox Account ID удалён из test fixtures.
Post-review correction до запуска operation отклоняет lexical/resolved
`--output` внутри runtime. M5.2 опубликован в draft PR #45; следующий отдельный
gate — post-output-boundary final review. M5.3 не выполнен.

## 8. `v3.9.0` — acceptance и release qualification

Цель: full/targeted regression, simultaneous proposals, partial fill,
restart/disconnect/MARKET_IDLE, backup/rollback, secret scan и 24–48 h
2–3-instrument Sandbox burn-in.

Gate: zero-count invariants выполнены, pending/uncertain отсутствуют, explicit
user acceptance получен до tag/release/закрытия umbrella Issue.

## Рекомендуемые зависимости

```text
Umbrella
  ├── Pure domain / adapter contract
  │      ↓
  ├── alpha1 read-only
  │      ↓
  ├── alpha2 prospective policy
  │      ↓
  ├── alpha2 shadow observability
  │      ↓
  ├── persistence/recovery foundation из Issue 7
  │      ↓
  ├── alpha3 authoritative preflight
  │      ↓
  ├── оставшийся beta1 UX/recovery hardening из Issue 7
  │      ↓
  └── Stable acceptance/release
```
