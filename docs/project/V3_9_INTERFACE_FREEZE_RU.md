# v3.9 Portfolio Risk — матрица interface freeze

Дата: 2026-08-14
Статус: `UPSTREAM MERGED / M1+M2 FROZEN / M3 FINAL REVIEW PASS / M4 FINAL REVIEW PASS / READY FOR COMMIT`

## Назначение

Документ заменяет предпосылку пакета обновления о ещё не завершённом v3.8.
PR #42 уже объединён в `main` commit `7525d7e`, поэтому v3.9 начинается от
фактических v3.8 contracts. Authoritative enforcement запрещён, пока строки со
статусом `OPEN` не будут закрыты отдельным review.

## Upstream contracts

| Contract | Текущий источник | Статус для v3.9 | Остаточное решение |
|---|---|---|---|
| Canonical multi-position snapshot | `PortfolioState` schema 2, revision и decision checksum | READY | Не менять schema в pure-domain PR |
| Actual/target/ownership/pending/uncertain | `PositionState` и reconciliation | READY | Adapter обязан сохранять unknown явно |
| NAV и broker cash | `AccountState.total_value`, `CashBalance.available/blocked` | READY/NULLABLE | Unknown NAV/cash блокирует risk increase |
| Price и lot metadata | canonical `PositionState.current_price` + явная checksummed metadata schema 1 | M2 FROZEN | Adapter умножает unit price на explicit lot size; average-price fallback и silent default запрещены, unknown/stale — fail closed |
| Asset-class taxonomy | `PositionState.asset_type`, profile `class_code` | M1 FROZEN | `asset_class` нормализуется в uppercase; unknown допускается в metrics как `UNKNOWN`, но блокирует policy check, которому нужна taxonomy |
| Active reservations | `CentralOrderIntent.reserved_cash_kopecks`, lifecycle statuses | M2 FROZEN | Read-only projection hash включает account/revision/intent/status/amount; mutation остаётся за Central |
| Central proposal/queue | `CentralOrderCoordinator` / `CentralOrderManager` | M4 IMPLEMENTED LOCALLY | Admission builder выполняется внутри account-wide Central mutation; replacement/reauthorization и reservation не разделяются |
| Order/execution identifiers | intent id, broker id, EventJournal, Risk execution id | READY | Сохранить сквозную correlation/idempotence |
| Restart/recovery | checksummed stores, pending/uncertain blocker, reconciliation | M4 TARGETED PASS | Clean restart сохраняет queued proof; IN_FLIGHT recovery остаётся UNCERTAIN без auto-resubmit; mutation инвалидирует proof |
| Sequential simultaneous proposals | account-wide queue | M4 TARGETED PASS | Distinct BUY contention сериализуется; совместно недопустимые заявки не получают двойной резерв |
| Global lock order | canonical, Risk profile/state, Central stores | M4 FROZEN | `canonical → Risk profile → Risk state → Central`; admission, dispatch и reconciliation не должны инвертировать порядок |
| External cash semantics | persistent Risk resync, Cash-flow Manager ещё отсутствует | M2 FROZEN | До v3.10 любое необъяснимое изменение → `RISK_RESYNC_REQUIRED`; adapter переносит gate в immutable input, read-only report блокируется |

## Зафиксированный M1 valuation contract

- v3.9 остаётся long-only и поддерживает только `RUB`; иная currency блокирует
  изменение.
- `price_per_lot_rub` и `PositionRiskInput.lot_price_rub` уже включают
  `lot_size`: `market_value_rub = actual_lots × lot_price_rub`.
- `lot_size` сохраняется в input/decision proof. Несовпадение candidate и
  canonical metadata блокирует изменение; silent default в будущем adapter
  запрещён.
- Candidate использует явно переданные `price_source` и aware `price_at`.
  Для уже открытых позиций adapter передаёт canonical valuation source/time.
  M2 source priority: только canonical `PositionState.current_price`; historical
  `average_price` не является valuation fallback. Timestamp берётся из
  canonical snapshot, но любой unknown, future или stale результат блокирует
  изменение.
- `strategy_id` сохраняется как canonical ownership id; `asset_class`, ticker,
  currency и price source нормализуются в uppercase.
- NAV/cash/price могут быть `null` только как явное unknown. Для нового
  exposure unknown данные, unreconciled position, blocking intent, resync или
  data-quality flag дают structured fail-closed reason.
- Drawdown положителен при падении: `(high_watermark - NAV) / high_watermark`,
  с нижней границей `0`.
- Pure decision содержит hashes canonical snapshot, Central reservation
  projection, policy и RiskState guard, но всегда имеет
  `execution_authorized=false`.

## Единственная adapter boundary

`PortfolioRiskInputAdapter` — единственный слой, которому разрешено знать
конкретные persistence/runtime структуры v3.8. Он получает одну canonical
lease и одну Central reservation projection и строит immutable DTO.

M2 read service загружает canonical/Central stores с существующей integrity
проверкой, проверяет account scope Risk profile и принимает внешнюю instrument
metadata только как JSON schema 1 с обязательным SHA-256 sidecar. Отсутствие
metadata не подменяется значениями по умолчанию: lot size/currency/valuation
остаются explicit unknown. Report не сохраняет policy/state и не выдаёт
execution authorization.

Pure Portfolio Risk модули:

- не читают файлы или environment;
- не вызывают broker API;
- не импортируют GUI/execution adapters;
- не создают второй position/cash/reservation ledger;
- возвращают deterministic current/projected metrics, rule caps и decision.

## Enforcement gate

M3 freeze фиксирует только observability contract: eligible означает proposal,
прошедший canonical preflight и получивший существующее v3.8 Risk decision;
shadow выполняется до Central mutation, использует тот же canonical snapshot и
pre-mutation Central state, исключая заменяемую reservation текущего intent.
Результат никогда не авторизует execution. Unconfigured policy даёт
`UNAVAILABLE`; configured policy вычисляется только как `OBSERVE_ONLY`.
Идемпотентность — одно событие на deterministic shadow key при restart и
конкурентном повторе. Локальные gates: core targeted `171 passed`,
isolated-runtime `6 passed`, full `663 passed`, changed-file Ruff PASS.
Изолированный runtime LKOH/SBER подготовлен из принятого v3.8 revision 16 без
изменения 37 source-файлов, без секретов и без искусственной заявки; начальная
coverage равна 0/`INCOMPLETE`, а token-free backup проверен. Реальная
post-configuration выборка затем получена и описана ниже, но ещё не принята как
финальное evidence из-за `HALTED` решений.

После START добавлен operational one-cycle gate: preview реальных закрытых свечей
LKOH/SBER прошёл без записей и дал две естественные `HOLD=0` proposal. Apply
отделён фразой `RUN V3.9 SHADOW OBSERVATION` и имеет hard boundary
`observe_only` после canonical preflight, v3.8 Risk и M3 observer, до Central
mutation; broker order interface отсутствует. Реальный runtime использует LKOH
30m и SBER 1h.

Первый apply записал две естественные `HOLD=0` observations: coverage 100%,
`UNAVAILABLE=0`, target drift `MATCH=2`, unexplained drift 0; Central и broker
не изменены. Выборка пока не является финальным M3 acceptance evidence: обе
decision были `HALTED` из-за поздней цены и ошибочного future-snapshot. Runner
исправлен так, чтобы evaluation time фиксировался после canonical reconciliation,
а operator output не содержал raw account/runtime/instrument IDs. Append-only
evidence сохранено без переписывания; authoritative M4 не разрешён до чистого
повтора на новой естественной свежей свече.

Повторный gate записал ещё две естественные `HOLD=0` observations и подтвердил
устранение `SNAPSHOT_FROM_FUTURE`; aggregate coverage `4/4`, unavailable 0,
`MATCH=4`, unexplained drift 0, без Central/Risk-counter/broker mutation. Но
обе decision остались `HALTED/CANDIDATE_PRICE_STALE`. Freeze уточнён: candle
time не может считаться свежим временем candidate price. M3 теперь требует
immutable `PortfolioRiskCandidateQuote` из официального `GetLastPrices` с ценой,
UTC time и source; missing/invalid quote даёт `UNAVAILABLE`, stale/future — hard
block. Operational `observe_only` не имеет candle fallback. Реальный read-only
preview подтвердил свежие LKOH/SBER exchange quotes без записей; targeted 86,
full 689, Ruff PASS. До чистого повторного evidence M4 не разрешён.

Следующий exact-confirmation gate записал одно новое естественное LKOH
observation для свечи `20:00 UTC`. Immutable candidate quote из
`GetLastPrices` (`TBANK_LAST_PRICE_EXCHANGE`, `20:42:41 UTC`) дал
`EVALUATED/PASS`, hard blocks `[]`, drift `MATCH`, unexplained drift false.
SBER не имел новой закрытой свечи после checkpoint `19:00 UTC`, поэтому
идемпотентно не создал дубликат. Aggregate report: coverage `5/5`, unavailable
0, `MATCH=5`, unexplained drift 0, execution authorization false. Central
byte-identical; canonical economic state, Risk counters/reservations и pending
orders не изменились. Clean-quote contract подтверждён для LKOH, но новый SBER
eligible proposal в выборку не вошёл. M4 остаётся закрыт до отдельного review и
не выводится из формального PASS shadow report автоматически.

Exact-confirmation gate 2026-08-14 завершил двухинструментную clean-выборку:
LKOH `06:30 UTC` дал `HOLD=0`, SBER `06:00 UTC` — естественный `BUY 1`. Для
обоих proposal actual v3.8 и shadow approved targets совпали (`0/1`), decisions
`PASS`, hard blocks/policy halts/adjustments отсутствуют, drift `MATCH`,
unexplained drift false. Aggregate coverage `7/7`, unavailable 0, `MATCH=7`,
unexplained drift 0; execution authorization false. Central byte-identical,
canonical revision/позиции и Risk economic state неизменны, broker POST 0.
Token-free post-gate backup VALID. M3 observability contract подтверждён и готов
к final review; authoritative admission M4 остаётся за отдельным gate.

Финальный review M3 — PASS: `689 passed`, scoped/critical Ruff, compileall и
diff-check PASS; publication scope не содержит secret/runtime artifacts.
Boundary review подтверждает, что Portfolio Risk остаётся только observability:
его decision не участвует в authorization, Central admission или dispatch.
На момент M3 freeze M4 был только готов к отдельной реализации и не активирован.

Отдельная M4-ветка реализует authoritative boundary без переписывания M1–M3.
`ExecutionAuthorization` получает nested Portfolio Risk proof; Central завершает
post-admission revision/projection hash только под своим lock. Dispatch
реконструирует pre-admission projection, проверяет deterministic decision/input,
повторяет оценку с текущим временем и блокирует POST при stale proof. Legacy
M3 path остаётся наблюдением; M4 подключается явно только для подтверждённого
`ENFORCED` profile и независимой exchange quote.

Targeted M4 matrix: `13 passed`; строгий Ruff изменённого M4 scope, compileall и
diff-check — PASS; full regression: `710 passed`. Проверены cash contention,
projected strategy concentration с активной очередью, fail-closed lock timeout,
queue/policy/state/canonical invalidation, clean restart, duplicate
reauthorization, no duplicate submit и post-fill current metrics. Automated
final review завершён, но изолированный Sandbox runtime gate ещё не выполнен,
поэтому M4 не имеет release/operational acceptance.

M4 activation boundary дополнительно заморожена checksummed manifest
`v3_9_enforced_runtime_manifest.json`. Автоматический runtime wiring разрешён
только при `activation_status=ACTIVE`, совпадающих runtime path/account
fingerprint/policy hash и mode `ENFORCED`. Свежий runtime должен оставаться
STOPPED, с пустыми Central/EventJournal и без M3 START-manifest. Configuration
preview не пишет состояние; apply сначала создаёт проверяемый backup и требует
точную фразу `CONFIGURE V3.9 ENFORCED RUNTIME`. Targeted configuration matrix:
`7 passed`; M3/M4 affected matrix: `37 passed`.

Изолированный M4 seed создан из принятого M3 revision 18: LKOH/SBER `0/0`,
InstrumentRuntime `STOPPED`, pending/Central/EventJournal 0, checksum PASS.
После prepare Portfolio policy имела статус
`CONFIGURATION_REQUIRED/OBSERVE_ONLY`; gate `CONFIRM PORTFOLIO RISK POLICY`
перевёл её в `READY/OBSERVE_ONLY`, не изменяя остальные runtime stores.
Configure preview подтвердил secure credential provider, baseline
`UNINITIALIZED`, STOPPED runtimes и нулевые proposal/intent/POST.
Bootstrap принимает checksummed Portfolio Risk metadata как lot-size evidence
при пустой M3 Central history.

SHADOW configuration manifest создан и проверен; идемпотентный повтор не пишет
файлы. Последующий M4 preview подтвердил: portfolio-wide hard caps явно `null`,
freshness `300s`, warning utilization `0.8`, прежние single-order limits
сохранены.

ENFORCED configuration gate выполнен 2026-08-14. Pre-activation backup имеет
`VALID`, errors 0; policy — `READY/ENFORCED`, checksummed activation manifest —
`ACTIVE` с валидным checksum. Идемпотентный повтор не пишет состояние. Оба
InstrumentRuntime остаются `STOPPED`, baseline `UNINITIALIZED`, Central и
EventJournal пусты, proposal/intent/provider POST — 0. Это конфигурирует
authoritative acceptance path, но не armed execution и не runtime acceptance.

Read-only status boundary проверена на активированном M4 runtime. Status-path
отделён от `CentralOrderManager.initialize/recover_after_restart`, чтобы не
обновлять lock metadata. Результат: Central `READY`, revision 0,
queue/reservation/blocker 0, activation manifest `ACTIVE`/checksum PASS,
`runtime_files_changed=0`. Regression `710 passed`, scoped Ruff PASS.

Между status и intent admission добавлен read-only `preview-natural`. Он не
создаёт manager/recovery и не пишет canonical/runtime/Risk/Central stores; только
сверяет broker state и вычисляет Strategy proposal в памяти. Live результат:
LKOH `HOLD → 0`, SBER natural `BUY → 1`, оба runtime `STOPPED`, exchange quotes
available, mutation/intent/arming/order submit false, изменённых файлов 0.
Regression `712 passed`, scoped Ruff PASS. Admission contract ещё не вызван.
Для активированного M4 runtime prepare допускает только отдельную фразу
`PREPARE V3.9 ENFORCED INTENT`; v3.8 confirmation сохраняется лишь для legacy.

Live M4 admission выполнен для SBER `0→1`. Preflight, single-order Risk и
Portfolio Risk — PASS; один `QUEUED` intent содержит finalized proof и reservation
`27876` копеек. Central revision 1, SBER runtime `ACTIVE`/pending 1, LKOH
`STOPPED`. Dispatch/order submit не выполнялись. Checksummed reload подтвердил
activation `ACTIVE` и не выполнил записей. Следующий boundary — queued restart.

Live queued restart boundary PASS. Новый процесс вызвал recovery; SBER intent
остался `QUEUED`, proof/reservation/pending и Central revision 1 сохранены.
Central bytes/checksum неизменны, material files changed 0; обновилась только
lock metadata. Broker API/submit/resubmit не вызывались, dispatch не armed.

Dispatch boundary дополнен отдельным offline `preflight-dispatch`: exact queue
head, canonical, Risk policy/State и finalized Portfolio Risk proof проверяются
до секрета/provider API. Live proof был отклонён fail-closed как
`CANDIDATE_PRICE_STALE; STALE_CANONICAL_SNAPSHOT`; material state не изменён,
provider POST 0. Для M4 заморожен отдельный внешний контракт
`ARM_V3_9_ENFORCED_EXECUTION=YES` + `ENABLE V3.9 ENFORCED EXECUTION`; legacy
v3.8 arming/confirmation не принимаются. Контракт не активирован до отдельной
reauthorization/reprepare свежего proof. Targeted tests `48 passed`, full
regression `714 passed`, scoped Ruff PASS.

Reauthorization contract заморожен отдельно от dispatch: action
`reauthorize-one`, queue-head identity и exact confirmation
`REAUTHORIZE V3.9 ENFORCED INTENT`. Одновременное наличие legacy/M4 arming
запрещено до секрета и provider reads; provider POST отсутствует. Свежая
canonical revision, natural proposal и exchange quote обновляют candidate price,
reservation и finalized proof атомарно; drift сигнала маршрутизируется только в
replace/cancel. Targeted `53 passed`, full regression `718 passed`, Ruff PASS.
Live confirmation ещё не получена, поэтому runtime state не менялся.

Live confirmation затем выполнено. Fresh natural proposal для SBER стал
`HOLD → 0`; preflight и Risk прошли, но авторизуемого изменения позиции нет.
Контракт корректно отменил stale `BUY 1` как `CANCELLED/OPERATOR_CANCELLED`, не
создавая новый proof. Итог: Central revision 2 `READY`, queue/reservation 0,
canonical `FRESH/READY`, SBER `ACTIVE`/pending 0, LKOH `STOPPED`, activation
`ACTIVE`, checksum PASS. Arming и broker POST отсутствовали.

Финальный M4 interface review принят без искусственного order flow. Все
изменённые Python interfaces проходят Ruff, полная регрессия — `718 passed`,
diff-check PASS. Runtime имеет нулевые queue/blocker/reservation/pending,
13/13 checksum-пар PASS и валидный pre-activation backup. Lock order, separate
arming, atomic admission/reauthorization и stale-proof invalidation считаются
замороженными для M4 Commit/Push gate; live dispatch не является частью этого
принятия.

Post-policy configure-gate для LKOH/SBER закрыт отдельной точной фразой. Он
добавил только checksummed `v3_9_shadow_runtime_config_manifest.json`, не изменил
существующие runtime-файлы и сохранил оба InstrumentRuntime в `STOPPED`.
Canonical lots/lot metadata, secure credential provider, пустая Central history,
`READY/OBSERVE_ONLY` и согласованно `UNINITIALIZED` Risk baselines проверены;
proposal/intent/POST не создавались. Идемпотентный повтор —
`ALREADY_CONFIGURED`, post-config backup valid, full regression `669 passed`.

Authoritative PR допускается только после выполнения всех условий:

- все milestone-gate строки выше закрыты реализацией и tests;
- Risk profile/state migration и rollback прошли;
- global/instrument kill switches переживают restart;
- shadow покрывает 100% eligible proposals;
- необъяснённый shadow decision drift равен 0;
- queue/reservation mutation инвалидирует старый proof;
- concurrency matrix подтверждает 0 double reservation;
- dispatch-time stale proof подтверждён как 0 provider POST.
