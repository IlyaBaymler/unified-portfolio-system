# План разработки v3.9.0 — Portfolio Risk Engine

Дата: 2026-08-14
Статус: `IN DEVELOPMENT / M3 FINAL REVIEW PASS / M4 FINAL REVIEW PASS / READY FOR COMMIT`
Базовый commit: `7525d7e` — merge PR #42 (`v3.8 Multi-Instrument Sandbox`)

План сверен с пакетом обновления
`v3_9_portfolio_risk_codex_plan_2026-08-13.zip`, SHA-256
`635657cf3b65968c2968e56958a3ef094ec1ee1cb4241572584ce86089258bdd`.
Из пакета приняты staged rollout `read-only → shadow → authoritative`,
diagnostic HHI, `max_open_positions`, adapter boundary, shadow decision-drift
audit, interface-freeze checklist и расширенные release invariants. Схема
ожидания будущего v3.8/cherry-pick не применяется: v3.8 уже объединён в `main`.

## 1. Цель релиза

Перевести Risk Engine от проверки одного instrument target с общими
account-level счётчиками к детерминированной оценке текущего и проектного
состояния всего портфеля.

v3.9 должна до постановки заявки отвечать на четыре вопроса:

1. Какова текущая суммарная экспозиция и концентрация портфеля?
2. Какими станут экспозиция, концентрация и свободный денежный резерв после
   учёта уже активных Central intents и нового candidate?
3. Разрешает ли account-scoped policy этот переход и какой максимальный target
   остаётся допустимым?
4. Не изменились ли canonical portfolio, Central reservations, Risk policy или
   Risk state до финального Sandbox handoff?

## 2. Входной gate и база v3.8

PR #42 объединён в `main`. База уже предоставляет:

- checksum-managed canonical `PortfolioState` schema 2;
- до трёх long-only Sandbox-инструментов;
- account-wide `CentralOrderState`, последовательную очередь и RUB cash
  reservations;
- per-order `ExecutionAuthorization` с canonical и dispatch-time Risk proof;
- account-level daily/weekly loss, drawdown, turnover и global kill switch;
- обязательную post-fill canonical reconciliation и idempotent Risk accounting.

При этом реальный operator-only v3.8 multi-lot сценарий `0→3→5→2→0` остаётся
release gate. Разработку чистого v3.9 domain/policy слоя можно вести в
изолированной ветке от merged `main`, но v3.9 release qualification не должна
подменять или закрывать незавершённую приёмку v3.8.

Фактическое состояние upstream interfaces и остающиеся решения зафиксированы
в `docs/project/V3_9_INTERFACE_FREEZE_RU.md`.

## 3. Архитектурные границы

### 3.1. Единственные источники истины

- `PortfolioState` — фактические позиции, broker cash, ownership,
  reconciliation и freshness.
- `CentralOrderState` — активные intents и единственный внутренний реестр
  зарезервированных денег.
- `RiskProfileStore` / `RiskStateStore` — лимиты, loss/turnover baselines и
  global/instrument kill switches.

Portfolio Risk не создаёт отдельный position, cash или reservation ledger.
Его snapshot является неизменяемой проекцией одной canonical lease и одной
Central-order revision.

### 3.2. Safety boundary

- Sandbox only; real-account execution остаётся запрещённым.
- Long-only, без плеча, short, margin и derivatives.
- Risk Engine не содержит broker `POST` и не авторизует исполнение сам по себе.
- GUI, Scheduler, Strategy и будущий Supervisor не могут обходить
  Portfolio Risk, Central Order Manager или execution adapter.
- Неоднозначные, stale, corrupted или несовместимые данные блокируют рост
  риска до recovery/reconciliation.
- Risk-reducing переход может быть разрешён во время policy halt только при
  свежем и однозначном execution/canonical state.
- Ни один v3.9 профиль Sandbox Execution не активируется с неявными новыми
  лимитами после upgrade.

### 3.3. Что не входит в v3.9

- covariance/correlation model, VaR/CVaR и stress Monte Carlo;
- автоматический rebalance или portfolio optimizer;
- Cash-flow Manager, дивиденды, купоны, комиссии и налоги — это v3.10;
- multi-strategy attribution и per-strategy timeframe — v4.x;
- динамический universe, AI/ML allocation и crypto;
- увеличение лимита v3.8 более чем до трёх инструментов;
- Stable/real execution.

## 4. Функциональный scope

### 4.1. Immutable Portfolio Risk snapshot

Добавить чистые, сериализуемые модели, не зависящие от Tkinter и raw broker
payload:

- `PositionRiskInput` / `PortfolioRiskPosition`;
- `PortfolioRiskReservation`;
- `ProposedPortfolioChange`;
- `PortfolioRiskInput`;
- `PortfolioRiskSnapshot`;
- `PortfolioRiskMetrics`;
- portfolio-limit fields в существующем versioned `RiskPolicy`, без второго
  независимого policy store;
- `PortfolioRiskDecision`;
- `PortfolioRiskAssessment`.

`PortfolioRiskInputAdapter` является единственной интеграционной границей,
которая знает конкретные v3.8 `PortfolioState`, `CentralOrderState` и runtime
contracts. Pure builder/evaluator не читает файлы, broker API, environment или
global state. Одинаковый полностью заданный input, включая evaluation time,
должен давать одинаковый canonical decision payload и decision id.

Snapshot должен содержать доказательства входа:

- `account_id`;
- canonical revision, decision checksum и snapshot timestamp;
- Central-order revision и checksum/projection hash активных reservations;
- Risk policy hash и RiskState guard hash;
- текущие и проектные позиции по `instrument_id`;
- нормализованные `strategy_id`, `asset_class`, currency, lot size и valuation;
- available/blocked broker cash и внутренний reserved cash;
- timestamp оценки и причины неизвестных значений.

Snapshot строится только из одной согласованной canonical lease. Смешивание
позиций, cash или цен из разных revisions запрещено.

### 4.2. Метрики текущего и проектного портфеля

Минимальный набор:

- gross exposure RUB: сумма абсолютных market values;
- gross и net exposure как доля equity;
- invested capital и cash share;
- instrument concentration;
- strategy concentration по canonical ownership;
- asset-class concentration;
- количество открытых позиций;
- HHI concentration как read-only diagnostic, не hard gate v3.9;
- available RUB cash;
- Central reserved RUB cash;
- свободный cash после reservations;
- free-cash ratio;
- policy reserve после исполнения candidate;
- daily/weekly P&L, high-watermark drawdown;
- account-wide daily turnover, utilization и order count.

Каждая метрика рассчитывается в вариантах `current` и `projected`. Decision
должен объяснять использованную цену и каждый ограничивший cap.

Для первой версии доли экспозиции считаются относительно положительного
canonical equity. Если equity, цена, lot size, ownership или asset-class
metadata необходимы для проверки роста риска, но неизвестны или противоречат
друг другу, новый exposure блокируется.

### 4.3. Policy

Минимальные portfolio-wide лимиты:

- `max_gross_exposure_rub` и/или `max_gross_exposure_fraction`;
- `max_net_exposure_fraction` для forward-compatible metric contract;
- `max_instrument_concentration_fraction`;
- `max_strategy_concentration_fraction`;
- default и per-asset-class concentration limits;
- `max_open_positions`;
- `min_cash_reserve_rub` и/или `min_cash_reserve_fraction`;
- `max_daily_turnover_fraction` относительно подтверждённого day-start equity;
- существующие daily/weekly loss, drawdown, turnover и order-count limits;
- `warning_utilization_fraction` только для observability;
- `allow_risk_reducing_orders_during_halt`;
- явный режим `ENFORCED` для Sandbox и `OBSERVE_ONLY` только для Dry-run/
  qualification.

Decision statuses сохраняют понятную семантику:

- `PASS` — requested target разрешён полностью;
- `ADJUSTED` — target детерминированно уменьшен до безопасного lot cap;
- `BLOCKED` — изменение позиции запрещено;
- `REDUCTION_ALLOWED` — разрешено только снижение риска;
- `HALTED` — заявка не требуется, но рост риска остановлен.

Portfolio Risk никогда не увеличивает target выше запроса Strategy.

### 4.4. Общий денежный резерв

`CentralOrderState.reserved_cash_kopecks` остаётся единственным владельцем
внутреннего резерва заявок. Portfolio Risk использует:

```text
projected_free_cash =
    canonical_available_rub
    - active_central_reservations_rub
    - new_candidate_reservation_rub
```

и затем проверяет policy cash reserve. Нельзя:

- создавать второй persisted reserve balance внутри RiskState;
- повторно вычитать broker blocked cash, если canonical `available` уже его
  исключает;
- авторизовать два BUY по одному и тому же остатку через независимые snapshots.

Admission candidate и reservation должны происходить под единым account-wide
Central mutation/lock contract. При изменении очереди proof становится stale и
требует повторной оценки queue head.

### 4.5. Kill switches

Поддержать два persisted scope:

- global account kill switch;
- instrument-level kill switch по `instrument_id`.

Оба блокируют увеличение соответствующего exposure и по умолчанию допускают
только безопасное сокращение. Требуются:

- reason, source, set-at, operator reference;
- exact confirmation для clear;
- idempotent engage/clear;
- EventJournal events;
- отражение в dashboard и support bundle;
- dispatch-time recheck до любого market/provider API.

Kill switch не отменяет уже отправленную заявку автоматически. Такой случай
переходит в обычный inspect/reconcile workflow.

### 4.6. External cash и P&L baseline

До v3.10 Cash-flow Manager daily/weekly loss и drawdown остаются operational
equity metrics. Необъяснимое пополнение, вывод или другое внешнее изменение
cash нельзя автоматически считать P&L: оно выставляет
`RISK_RESYNC_REQUIRED`, сохраняет причину и требует явного baseline review.

## 5. Persistence и migration

### 5.1. Risk profile

Текущий checksummed `risk_profiles.json` эволюционирует versioned migration,
без параллельного файла с конкурирующей policy.

Правила upgrade:

- существующие per-order и account-level значения сохраняются точно;
- новые portfolio limits не подставляются молча для Sandbox Execution;
- migrated Sandbox profile получает состояние
  `PORTFOLIO_POLICY_CONFIGURATION_REQUIRED`, пока оператор явно не сохранит и
  не проверит новые лимиты;
- Dry-run может получить документированный conservative/observe-only profile;
- повреждённая или неизвестная schema блокирует exposure.

### 5.2. Risk state

Расширить `RiskState` additive schema migration для instrument kill switches и
последнего подтверждённого portfolio-risk proof. Не дублировать в state текущие
позиции, market values или cash: они всегда пересчитываются из canonical
snapshot.

Backup/verify/restore, checksums, last-good recovery и standalone bootstrap
обязательны для новой schema до подключения Sandbox dispatch.

## 6. Интеграция с v3.8 execution path

Интеграция выполняется по схеме:

```text
canonical lease + Central reservation projection + Risk policy/state
        ↓
PortfolioRiskInputAdapter
        ↓
pure current/projected builder + evaluator + sizing
        ↓
single-order Risk + Portfolio Risk + reservation cap aggregator
        ↓
Central Order Manager admission
        ↓
dispatch-time proof recheck
```

Pure evaluator не импортирует persistence или execution adapters.

### 6.1. Prepare/admission

`CentralOrderCoordinator` строит candidate из execution-unauthorized Strategy
proposal и передаёт его Portfolio Risk вместе с:

- canonical lease;
- текущим Central queue/reservation projection;
- account-scoped policy/state;
- valuation/metadata всех позиций.

`ExecutionAuthorization` становится доказательством сразу двух уровней:

- текущий single-order Risk decision;
- portfolio-wide projected Risk decision.

Proof включает canonical revision/checksum, Central reservation revision/hash,
policy hash, state guard hash, decision id и approved target.

### 6.2. Queue changes

Каждый enqueue, cancel, failure или reconciliation меняет reservation
projection. После этого сохранённая portfolio authorization считается stale.
Перед dispatch queue head должен быть повторно оценён на актуальной queue и
canonical revision. Нельзя молча использовать proof, сформированный до
последующего admission другого инструмента.

### 6.3. Dispatch-time guard

Финальный execution adapter до market/provider API проверяет:

- exact queue head и operator arming;
- canonical revision/decision checksum;
- Central reservation projection;
- Risk policy hash;
- RiskState guard, global и instrument kill switches;
- portfolio-risk decision proof.

Требуется единый документированный lock order для canonical, Central и Risk
stores. Любая невозможность получить согласованный proof возвращает явный
fail-closed status и сохраняет `0 POST`.

### 6.4. Post-fill

После broker terminal state:

1. canonical reconciliation подтверждает фактическую позицию;
2. Risk execution accounting идемпотентно записывает turnover/order count;
3. Portfolio Risk пересчитывает фактические current metrics;
4. только после этого снимается account-wide execution blocker;
5. оставшиеся queued intents требуют новой authorization.

## 7. Release train

- `v3.9-alpha1` — read-only current Portfolio Risk snapshot/report; execution
  не меняется.
- `v3.9-alpha2` — prospective SHADOW evaluation для каждого eligible
  proposal, lot caps и decision-drift audit; enforcement отсутствует.
- `v3.9-alpha3` — authoritative Portfolio Risk preflight только после
  завершённых policy/state migration, recovery tests и объяснённого shadow
  drift.
- `v3.9-beta1` — operator UX, persistence/recovery hardening, standalone и
  multi-session qualification.
- `v3.9.0` — Stable только после 24–48 h Sandbox burn-in и explicit user
  acceptance.

Порядок важен: пакет предлагал persistence после authoritative alpha3, но для
этого проекта durable kill-switch/policy recovery является prerequisite
enforcement, а не последующей доработкой.

## 8. Milestones разработки

### M0 — Contract freeze и fixtures

- утвердить формулы, valuation/metadata contract и fail-closed matrix;
- зафиксировать schema migration и будущий lock order gate;
- добавить umbrella GitHub Issue v3.9 и child checklists;
- закрыть pure-domain valuation/metadata решения в
  `V3_9_INTERFACE_FREEZE_RU.md`; lock order остаётся явным M4 gate, а external
  cash adapter/state contract — M2 gate;
- создать synthetic 0/1/3-instrument portfolio fixtures;
- записать baseline v3.8 regression и artifact hashes.

Gate: документация не оставляет неоднозначности между canonical cash,
broker blocked cash и Central reservations.

Статус на 2026-08-13: pure-domain часть M0 закрыта синтетическими fixtures и
contracts. GitHub umbrella/child Issues, v3.8 artifact baseline и
integration-specific lock/external-cash gates не считаются выполненными этим
локальным M1 изменением.

### M1 — Pure Portfolio Risk domain

- реализовать immutable models и deterministic calculator;
- current/projected exposure, concentration, open-position count и
  diagnostic HHI;
- lot caps для candidate без persistence и broker calls;
- structured reasons, breaches и metrics.

Gate: pure unit tests, property/boundary tests, `0 POST`, отсутствие изменений
в v3.8 execution path.

Реализация M1 размещается в отдельных `portfolio_risk_model.py`,
`portfolio_risk_evaluator.py` и `portfolio_risk_sizing.py`. Это временный
immutable computational policy DTO: в M2 он должен быть адаптирован к
единственному versioned `RiskPolicy` store, а не сохранён во втором policy
файле. Локальный gate 2026-08-13: targeted `37 passed`, full regression
`613 passed`, Ruff PASS; pure-модули не импортируют persistence, execution,
broker или GUI слои и не содержат provider `POST`.

### M2 — Read-only adapter и safe policy/state migration (`alpha1`)

- реализовать единственный `PortfolioRiskInputAdapter` к v3.8 contracts;
- опубликовать read-only current metrics/report без влияния на execution;
- versioned Risk profile/state schemas;
- explicit Sandbox configuration gate;
- global/instrument kill-switch API;
- checksum, corruption, account-scope, backup/restore tests.

Gate: read-only результаты совпадают с synthetic/manual fixtures; upgrade
v3.8→v3.9 fail-closed до явной настройки policy; execution behaviour и broker
calls идентичны v3.8; rollback восстанавливает исходный runtime.

Локальный статус 2026-08-13: `M2 IMPLEMENTED / REVIEWED`.

- `PortfolioRiskInputAdapter` использует canonical position/NAV/RUB cash,
  Central active reservations и RiskState guard; average-price fallback и
  silent lot-size default отсутствуют;
- read-only service/CLI читает checksummed canonical/Central state и
  checksummed metadata schema 1, не изменяет runtime и всегда выдаёт
  `execution_authorized=false`;
- единственный `RiskPolicy` расширен additive schema 2, RiskState — schema 3;
  schema 1/2 мигрируются в памяти и переписываются только штатным save;
- legacy Sandbox policy получает `CONFIGURATION_REQUIRED`; точная фраза
  активации — `CONFIRM PORTFOLIO RISK POLICY`;
- instrument kill switch persistent, idempotent и проверяется в final Sandbox
  dispatch guard до market/provider API;
- targeted M2 regression и full regression — PASS, changed-file Ruff — PASS.
  На Windows единый длинный процесс дважды встретил внешнюю race очистки
  SQLite temporary directory (`WinError 145`) уже после успешной операции;
  эквивалентная последовательная матрица прошла полностью: targeted
  `137 passed`, full `636 passed` (`562` в основном наборе + `74`
  temp-чувствительных теста отдельными процессами).

M2 намеренно не вызывает pure evaluator из v3.8 admission/execution path.
Первый этап, где prospective решения получают runtime coverage, — M3 SHADOW.

### M3 — Prospective SHADOW и observability (`alpha2`)

- строить projected post-trade snapshot для каждого eligible proposal;
- рассчитывать per-rule lot caps и итоговый approved target;
- записывать decision id, policy/proof hashes, current/projected metrics и
  structured reasons в EventJournal;
- сравнивать shadow decision с фактическим v3.8 admission/execution;
- классифицировать missing inputs и decision drift без изменения заявки.

Gate: 100% eligible proposals получают ровно одно shadow decision; Central
transitions и broker calls не меняются; необъяснённый decision drift равен 0.

Локальный статус 2026-08-14: `IMPLEMENTED / LOCAL GATES PASS / TWO-INSTRUMENT RUNTIME EVIDENCE PASS / FINAL REVIEW PASS`.

- observer запускается только после существующего v3.8 Risk decision и всегда
  принудительно использует computational mode `OBSERVE_ONLY`;
- фактический approved target и reason codes v3.8 сохраняются отдельно от
  prospective decision; drift разделён на объяснённый и необъяснённый;
- EventJournal гарантирует одно событие `portfolio_risk_shadow` на shadow key
  при restart/конкурентном повторе; недоступная policy или входы дают
  структурированный `UNAVAILABLE`;
- shadow failure не меняет candidate, `ExecutionAuthorization`, Central queue,
  dispatch guard или provider calls; report открывает runtime SQLite read-only;
- core targeted matrix: `171 passed`; isolated-runtime matrix: `6 passed`;
  full regression: `663 passed`; changed-file Ruff: PASS.

Синтетический gate закрыт. До M4 необходимо получить реальный runtime report с
coverage 100%, `UNAVAILABLE=0` и unexplained drift 0; сам факт локального PASS
не является разрешением authoritative enforcement.

Операторская подготовка не создаёт искусственную заявку. Сначала существующий
Sandbox profile явно подтверждается командой `risk_profile_tool.py
confirm-portfolio-shadow` с account ID и точной фразой
`CONFIRM PORTFOLIO RISK POLICY`; команда сохраняет текущие финансовые лимиты и
фиксирует `OBSERVE_ONLY`. Затем ожидается естественный Strategy proposal, после
чего `tools/v3_9_portfolio_risk_shadow_report.py` читает журнал строго read-only.
Первый read-only аудит двухинструментного runtime дал 0 eligible observations и
статус `INCOMPLETE`; искусственная заявка для закрытия gate не создавалась.

Изолированный M3 runtime подготовлен транзакционным
`tools/v3_9_prepare_shadow_runtime.py` из принятого двухинструментного v3.8
snapshot revision 16. Seed переносит canonical portfolio и конфигурации,
строит checksummed lot metadata из принятой terminal Central history и не
копирует секреты/логи/broker-order history; новые Central state, Risk counters
и EventJournal пусты, instrument runtimes остановлены, Portfolio Policy имеет
`CONFIGURATION_REQUIRED`/`OBSERVE_ONLY`. Хэши всех 37 файлов источника до/после
совпали. Начальный отчёт имеет 0 observations/`INCOMPLETE`; token-free backup
создан и проверен. Следующий gate — отдельное операторское подтверждение policy,
а не M4 и не искусственная заявка.

Policy подтверждена, затем выполнен отдельный
`CONFIGURE V3.9 SHADOW RUNTIMES`. Инструмент
`tools/v3_9_configure_shadow_runtimes.py` проверил revision 16, два
checksummed profile/runtime scope, canonical lots LKOH=1/SBER=0, lot size 1,
пустую Central history, `READY/OBSERVE_ONLY`, отсутствие Risk kill/resync и
secure Windows Credential Manager. Единственные новые файлы — config-manifest
и его SHA-256; оба InstrumentRuntime оставлены `STOPPED`, Risk baselines имеют
согласованный статус `UNINITIALIZED` и должны инициализироваться только первым
fresh Risk evaluation. Идемпотентный повтор вернул `ALREADY_CONFIGURED`;
proposal/intent/provider POST не создавались, token-free backup проверен.
Локальные gates после этого шага: configure+seed targeted `12 passed`, full
regression `669 passed`.

Отдельный runtime start-gate реализован в
`tools/v3_9_start_shadow_runtimes.py` и требует точную фразу
`START V3.9 SHADOW RUNTIMES`. До локальных записей он read-only проверяет
Sandbox account, UID/lot identity LKOH/SBER, свежий portfolio и отсутствие
active/uncertain orders. Затем он может опубликовать только неблокирующую fresh
canonical reconciliation, инициализировать только полностью pristine RiskState
и одной checksum-managed записью перевести все InstrumentRuntime в `ACTIVE`.
Proposal, Central intent, broker mutation и order submission не создаются.
Targeted gate: `61 passed`; full regression: `676 passed`; Ruff PASS.

Первый реальный preview 2026-08-13 правильно остановился до записей: provider
показал `LKOH=0`, но sealed revision 16 и runtime сохраняют `LKOH=1`, target 1,
ownership `ATTRIBUTED`; SBER flat. Post-condition: runtimes `STOPPED`, Risk
baselines `UNINITIALIZED`, EventJournal empty, start-manifest отсутствует.
Следующий отдельный gate — явное acknowledgement внешнего flat-close LKOH;
повтор START допускается только после согласованного canonical/runtime состояния.

Recovery-gate `tools/v3_9_ack_external_close.py` выполнен точной фразой
`ACK EXTERNAL CLOSE LKOH 0`. Provider повторно подтвердил обе позиции flat и
0 active/uncertain orders. Изменена только локальная canonical/runtime проекция
LKOH: 1→0, target 0, ownership `FLAT`, origin `EXTERNAL`, `MATCHED`; canonical
revision 17 неблокирующая. Оба runtime остаются `STOPPED`, Risk baselines —
`UNINITIALIZED`, Central history/reservations — пустыми. Recovery-manifest
checksummed и входит в VALID token-free backup; broker mutation/order submission
не выполнялись. START preview после ACK PASS. Targeted ACK+START `10 passed`,
full regression `681 passed`, Ruff PASS.

Повторный `START V3.9 SHADOW RUNTIMES` после ACK успешно выполнен. Fresh provider
snapshot подтвердил LKOH/SBER 0, UID/lot identity и отсутствие orders. Canonical
revision 18 — `FRESH`, `MATCHED`, non-blocking; Risk baselines инициализированы
из этого snapshot, counters 0, kill/resync false. Оба InstrumentRuntime имеют
`ACTIVE`, current lots 0, pending 0; Central по-прежнему empty. Start-manifest
checksummed; proposal, intent и provider order submit отсутствуют. Идемпотентный
повтор вернул `ALREADY_STARTED` без записей; post-START backup VALID.

Следующий operational M3 gate не создаёт искусственную заявку: дождаться
естественного eligible Strategy proposal и затем выполнить строго read-only
`tools/v3_9_portfolio_risk_shadow_report.py` для проверки coverage 100%,
`UNAVAILABLE=0` и unexplained drift 0.

Проверка показала, что сохранённый статус `ACTIVE` сам по себе не запускал
фоновый `GlobalScheduler`. Поэтому добавлен изолированный одноцикловый
`tools/v3_9_run_shadow_observation.py`. Его `preview` читает provider account,
identity/lot, portfolio/orders, trading status и реальные закрытые свечи,
вычисляет предложения только в памяти и гарантирует `writes_performed=false`.
`apply` отделён точной фразой `RUN V3.9 SHADOW OBSERVATION`: он выполняет свежую
canonical reconciliation, существующий v3.8 Risk и M3 observer, но перед любым
Central enqueue/cancel/authorization возвращается через `observe_only`; broker
POST интерфейс в tool отсутствует.

Реальный preview 2026-08-13 успешно получил LKOH 30m candle `18:30 UTC` и SBER 1h
candle `18:00 UTC`; обе естественные стратегии дали `HOLD`, target 0. Ничего не
сохранено, Central/provider mutation и execution authorization равны false.

Первый exact-confirmation apply выполнен на последующих естественных свечах LKOH
30m `19:00 UTC` и SBER 1h `18:00 UTC`. Записаны две append-only observations;
read-only report дал coverage `2/2`, `UNAVAILABLE=0`, `MATCH=2`, unexplained
drift `0`, без Central mutation, reservation или broker POST. Формальный status
report — `PASS`, но выборка не принята как финальная: обе prospective decisions
имели status `HALTED` с `CANDIDATE_PRICE_STALE` и `SNAPSHOT_FROM_FUTURE`.
Первый код ожидаем для позднего запуска за пределами 300-секундного price-age
окна; второй оказался дефектом runner — evaluation timestamp фиксировался до
создания свежего canonical snapshot. Runner переведён на post-reconciliation
UTC timestamp и redacted operator summary; исходные journal events не изменены.
Post-fix targeted `28 passed`, full `685 passed`, Ruff PASS; token-free backup
проверен как VALID. M4 не открыт до повторной чистой observation на новой
естественной свежей свече.

Повторный exact-confirmation gate обработал новые свечи LKOH 30m `19:30 UTC` и
SBER 1h `19:00 UTC`: 8/8 scheduler actions `COMPLETED`, две новые `HOLD=0`
observations, без `SNAPSHOT_FROM_FUTURE`. Сводно coverage `4/4`, unavailable 0,
`MATCH=4`, unexplained drift 0; Central revision/history/reservations, Risk order
count/turnover и broker POST остались нулевыми. Обе новые prospective decision
получили только `CANDIDATE_PRICE_STALE`.

Контрактный review установил первопричину: исторический `HistoricCandle` не даёт
отдельного timestamp сделки, согласованного с close; candle time нельзя выдавать
за свежесть цены при `max_price_age_seconds=300`. Попытка опереться на потоковый
`last_trade_ts` отклонена после проверки официальной схемы: у `HistoricCandle`
этого поля нет. Реализован официальный unary `GetLastPrices`, возвращающий цену
последней сделки и её UTC time. Они входят в immutable
`PortfolioRiskCandidateQuote` вместе с source `TBANK_LAST_PRICE_EXCHANGE`, в
shadow identity и `ProposedPortfolioChange`; missing/invalid quote даёт
structured `UNAVAILABLE`, stale/future — штатные hard blocks. Operational
`observe_only` не имеет candle fallback; compatibility fallback остаётся только
для существующего v3.8 non-observe queue-path и должен быть удалён до M4.

Реальный read-only preview подтвердил Sandbox response для LKOH/SBER: exchange
timestamps `20:32:04/20:32:20 UTC`, новая LKOH 30m candle `20:00 UTC`, обе
natural цели `HOLD=0`, writes/Central/broker mutation false. Targeted `86 passed`,
full `689 passed`, Ruff и compileall PASS. Следующая живая запись требует нового
exact-confirmation gate; M4 остаётся закрыт. Повторный evidence backup — VALID.

Новый exact-confirmation gate выполнен на естественной LKOH 30m свече
`20:00 UTC`. Единственный новый eligible proposal дал `HOLD=0`; shadow result —
`EVALUATED`, decision `PASS`, hard blocks отсутствуют, drift `MATCH`, unexplained
drift false. Candidate quote получен через `GetLastPrices`, source
`TBANK_LAST_PRICE_EXCHANGE`, timestamp `20:42:41 UTC`. SBER не имел новой
закрытой 1h свечи после уже обработанной `19:00 UTC`, поэтому scheduler не
создал дубликат observation. Aggregate report после append-only записи:
coverage `5/5`, `UNAVAILABLE=0`, `MATCH=5`, unexplained drift 0,
`execution_authorized=false`.

Pre/post audit подтвердил hard boundary: Central state byte-identical; canonical
revision 18, lots LKOH/SBER 0, cash/positions/blocking и Risk counters/
reservations экономически неизменны. Обновились только ожидаемые timestamps
reconciliation/evaluation и transaction metadata; оба InstrumentRuntime
остались `ACTIVE`, pending orders 0. Post-gate backup
`v3_9_shadow_observation_clean_quote_2026-08-13.zip` — VALID, token-free,
14 entries, warnings/errors 0. Clean candidate-quote path доказан для LKOH, но
в этом цикле не возник новый eligible SBER proposal. Поэтому M3 implementation
и полученное eligible coverage приняты как PASS, а полнота двухинструментной
clean runtime-выборки остаётся отдельным evidence gap. Переход к M4 возможен
только через отдельный review; gate не открывает authoritative enforcement.

Следующий exact-confirmation gate 2026-08-14 сформировал недостающую полную
clean-выборку. LKOH 30m candle `06:30 UTC` дала `HOLD=0`; SBER 1h candle
`06:00 UTC` дала естественный `BUY`, requested target 1. Свежие exchange quotes
получены через `GetLastPrices` непосредственно перед evaluation. Для обоих
proposal Portfolio Risk вернул `EVALUATED/PASS`, approved target `0/1`, hard
blocks, policy halts и adjustments отсутствуют; v3.8 actual target совпал с
shadow target, drift `MATCH`, unexplained drift false. `OBSERVE_ONLY` остался
единственным warning. Все 8 scheduler actions завершены, failures 0.

Append-only aggregate report после gate: coverage `7/7`, `UNAVAILABLE=0`,
`MATCH=7`, unexplained drift 0, `execution_authorized=false`. Pre/post audit:
Central state byte-identical, revision 0, intents/reservations 0; canonical
revision 18 и реальные позиции LKOH/SBER `0/0`; broker submit не вызывался.
Runtime сохранил только новые checkpoints, reconciliation/evaluation timestamps,
transaction metadata и Risk daily-date. Post-gate backup
`v3_9_shadow_observation_two_instrument_clean_2026-08-14.zip` — VALID,
token-free, 14 entries, warnings/errors 0.

Критерии M3 runtime gate выполнены: 100% eligible proposals получили ровно одно
shadow decision, необъяснённый drift отсутствует, Central/broker behavior не
изменён. M3 готов к финальному review. Переход к authoritative M4 остаётся
отдельным решением и этим observation gate не разрешён.

Финальный review M3 завершён 2026-08-14 без блокирующих замечаний. Полная
регрессия: `689 passed`; строгий Ruff по v3.9 runtime/tool core и safety
boundaries — PASS; critical Ruff по всем изменённым Python-файлам — PASS;
compileall и `git diff --check` — PASS. Сканирование publication scope не нашло
GitHub/TBank token, private key или untracked runtime artifact patterns.

M4 readiness review подтвердил, что текущий diff не содержит authoritative
Portfolio Risk admission: shadow decision не входит в `ExecutionAuthorization`,
Central reservation/admission не объединены с portfolio proof, а dispatch не
применяет Portfolio Risk enforcement. Это ожидаемая безопасная граница M3.
M4 имеет статус `READY TO START`, но должен разрабатываться и приниматься
отдельно; текущий publication scope ограничен M1–M3.

### M4 — Authoritative Central admission и dispatch (`alpha3`)

- portfolio evaluation из одной canonical lease и Central projection;
- atomic candidate admission/reservation contract;
- portfolio proof в `ExecutionAuthorization`;
- reauthorization при каждом queue/reservation change;
- multi-candidate cash/concentration contention;
- dispatch-time recheck всех proof components;
- documented lock order и concurrency tests;
- restart/disconnect/timeout/uncertain handling;
- post-fill portfolio recalculation;
- external activity → resync required.

Gate: два по отдельности допустимых BUY, совместно нарушающих cash или
concentration, не проходят одновременно; policy/state/portfolio/queue change
между prepare и dispatch всегда даёт `0 provider POST`; ambiguous submission
не resubmit автоматически.

Локальная реализация M4 начата в отдельной ветке от принятого M1–M3 commit
`0d9ec4f`. Добавлены `PortfolioRiskAuthorizationProof` и
`PortfolioRiskRuntime`; Central выполняет evaluation, replacement/
reauthorization и cash reservation в одной lock-сериализованной mutation.
Admission proof содержит canonical revision/checksum, pre-admission Central
revision/projection, Risk policy/state hashes, deterministic decision/input id,
approved target, quote provenance и завершённые admission revision/projection.

Зафиксированный lock order: `canonical portfolio → Risk profile → Risk state →
Central`. Dispatch удерживает canonical и Risk guards, воспроизводит сохранённый
Portfolio Risk decision на исходной projection, повторяет live-time evaluation
и только затем переводит queue head в `IN_FLIGHT`. Любое изменение
canonical/queue/reservation/policy/state возвращает fail-closed status до
provider POST. Reconciliation перестроен без инверсии Central→Risk; confirmed
fill требует canonical proof и Risk accounting, после чего выполняется current
Portfolio Risk recalculation.

Локальный targeted gate 2026-08-14: `13 passed`. Он включает parallel cash
contention, совместную projected strategy concentration, finalized proof
round-trip, exact dispatch reproduction, clean restart, self-excluding
reauthorization, post-fill recalculation, fail-closed Risk-lock timeout и
`0 POST` для queue/policy/RiskState/canonical mutation. Строгий Ruff по
изменённому M4 scope, compileall и `git diff --check` — PASS; full regression:
`710 passed`. Automated final review M4 завершён. Отдельный изолированный
runtime acceptance остаётся обязательным; Commit/Push и live gate этим статусом
не разрешены.

M4 configuration gate реализован отдельно. `v3_9_configure_enforced_runtime.py`
принимает только свежую
изолированную M3-configured копию со STOPPED runtimes, пустыми
Central/EventJournal и без START-manifest. Preview показывает точный объект
portfolio limits; apply требует `CONFIGURE V3.9 ENFORCED RUNTIME`, создаёт и
проверяет pre-activation backup, затем сохраняет checksummed `ACTIVE` manifest.
Acceptance CLI не подключает `ENFORCED` policy без совпадающего manifest.
Configuration gate: `7 passed`; затронутая M3/M4 acceptance matrix: `37 passed`.
Порядок ручной проверки зафиксирован в
`V3_9_M4_SANDBOX_ACCEPTANCE_RUNBOOK_RU.md`; искусственная заявка не требуется.

Gate `PREPARE ISOLATED V3.9 SHADOW RUNTIME` выполнен для отдельного M4 runtime
из принятого M3 revision 18. Canonical/runtime lots LKOH/SBER `0/0`, оба runtime
`STOPPED`, pending/Central/EventJournal 0, checksum PASS, secrets/logs не
копировались. Policy намеренно оставлена
`CONFIGURATION_REQUIRED/OBSERVE_ONLY`; следующий отдельный gate —
`CONFIRM PORTFOLIO RISK POLICY`. Revision-16 seed из старого v3.8 baseline
помечен superseded и не допускается к конфигурации.

Gate `CONFIRM PORTFOLIO RISK POLICY` выполнен на основном M4 seed: policy
`READY/OBSERVE_ONLY`, непрофильные runtime stores byte-identical. Последующий
configure preview подтвердил secure Windows Credential Manager, baseline
`UNINITIALIZED`, LKOH/SBER lot size `1/1`, lots `0/0`, STOPPED runtimes и
нулевые proposal/intent/POST. Следующий отдельный gate —
`CONFIGURE V3.9 SHADOW RUNTIMES`.

Gate `CONFIGURE V3.9 SHADOW RUNTIMES` выполнен: checksummed manifest создан,
pre-existing runtime files не изменились, повтор `ALREADY_CONFIGURED` без
записей. `ENFORCED` preview PASS и показал точную policy boundary: новые
portfolio-wide hard caps остаются явными `null`, price freshness `300s`, warning
utilization `0.8`; single-order Risk limits сохранены. До отдельного
`CONFIGURE V3.9 ENFORCED RUNTIME` pre-activation backup и authoritative wiring
отсутствовали.

Gate `CONFIGURE V3.9 ENFORCED RUNTIME` выполнен 2026-08-14: pre-activation
backup проверен как `VALID`, errors 0; policy переведена в `READY/ENFORCED`,
checksummed activation manifest имеет `ACTIVE` и валидный checksum. Повтор
`ALREADY_CONFIGURED` не выполнил записей; manifest и Risk profile byte-identical.
InstrumentRuntime остаются `STOPPED`, baseline `UNINITIALIZED`, proposal/intent/
provider POST — 0. Следующий этап начинается с read-only status; legacy SHADOW
START требует `OBSERVE_ONLY` и к M4 runtime не применяется. Execution не armed.

Read-only status gate выполнен. Общий acceptance CLI исправлен: `status` больше
не создаёт recovery manager и не обновляет lock metadata. Повтор на изолированном
M4 runtime дал Central `READY`, revision 0, queue/reservation/blocker 0,
activation `ACTIVE`, manifest checksum PASS и `runtime_files_changed=0`.
Regression `710 passed`, scoped Ruff PASS; execution и provider API не armed.

Следующий read-only `preview-natural` реализован и выполнен без recovery,
canonical refresh, runtime start или Risk baseline mutation. Provider/canonical/
runtime lots согласованы, pending orders отсутствуют. LKOH дал `HOLD → 0`, SBER
дал естественный `BUY → 1`; authoritative exchange quotes получены для обоих.
Central mutation/intent/arming/order submit — false, `runtime_files_changed=0`.
Regression `712 passed`, scoped Ruff PASS. Mutating intent gate остаётся отдельным
и требует точного операторского подтверждения
`PREPARE V3.9 ENFORCED INTENT`; legacy v3.8 confirmation отклоняется.

Intent gate выполнен на естественном SBER `BUY 1`: canonical revision 18,
preflight/Risk/Portfolio Risk `PASS`, finalized proof сохранён в единственном
`QUEUED` Central intent, reservation `27876` копеек, Central revision 1. SBER
runtime `ACTIVE`/pending 1, LKOH `STOPPED`; broker execution false, dispatch и
order submit 0. Независимый checksummed status reload не изменил файлов.
Следующий acceptance gate проверяет queued restart без resubmit.

Queued restart gate PASS: новый процесс выполнил recovery, оставил единственный
SBER intent в `QUEUED`, сохранил Central revision 1, finalized proof, reservation
`27876` копеек и runtime pending 1. Central JSON/checksum byte-identical;
изменилась только `.lock` metadata, material state 0. Secret/provider/submit/
resubmit не вызывались. Dispatch остаётся не armed.

Следующий pre-dispatch gate отделён от исполнения: offline-команда повторяет
queue/canonical/Risk/Portfolio Risk validation под требуемым lock order до
секрета и provider API. Live revalidation отклонила сохранённый proof с
`CANDIDATE_PRICE_STALE; STALE_CANONICAL_SNAPSHOT`; provider POST 0, material
state 0, только `.lock` metadata обновлена. M4 dispatch получает собственные
arming `ARM_V3_9_ENFORCED_EXECUTION=YES` и exact confirmation
`ENABLE V3.9 ENFORCED EXECUTION`, но они не активированы. Сначала требуется
отдельный reauthorization/reprepare gate со свежими canonical/quote. Targeted
matrix `48 passed`, full regression `714 passed`, scoped Ruff PASS.

Dedicated `reauthorize-one` реализован для этого boundary. Он требует exact
`REAUTHORIZE V3.9 ENFORCED INTENT`, отвергает обе dispatch-arming переменные до
секрета/provider API и не имеет broker POST path. Canonical refresh, естественный
Strategy proposal и exchange quote формируют новый candidate; при сохранённой
identity цена, reservation и finalized proof обновляются одной Central
транзакцией, при изменившемся signal выполняется штатный replace/cancel.
Targeted `53 passed`, full regression `718 passed`, scoped Ruff PASS. Live gate
ещё не подтверждён; stale proof остаётся fail-closed.

Live reauthorization подтверждён отдельно и завершился безопасным
`CANCELLED_NO_POSITION_CHANGE`: свежий natural SBER proposal стал `HOLD → 0`,
preflight/Risk PASS, поэтому stale `BUY 1` отменён без нового Portfolio Risk
proof. Central revision 2, queue/reservation/blocker 0, canonical `FRESH/READY`,
activation `ACTIVE`, проверенные checksums PASS. Dispatch arms отсутствовали,
provider POST/order submit/resubmit 0.

M4 final review — PASS в принятой no-artificial-order границе. Полная регрессия
`718 passed`, весь изменённый M4 Python scope Ruff PASS, diff-check PASS.
Финальный runtime audit: 13/13 checksum-пар PASS, Central queue/blocker/
reservation 0, pending 0, canonical `FRESH/READY`, activation `ACTIVE`;
pre-activation backup `VALID`, errors/warnings 0, 11 entries. Live dispatch/fill
не требовался для этого gate; M4 готов к отдельной фиксации ветки.

### M5 — Persistence/recovery/UX hardening (`beta1`)

- read-only Portfolio Risk dashboard projection;
- inspect/explain CLI;
- configure/review policy CLI;
- engage/clear global и instrument kill switches с exact confirmation;
- EventJournal, backup и sanitized support-bundle coverage.
- external cash baseline resync workflow;
- restart с active reservation, partial fill и pending/uncertain recovery;
- standalone bootstrap/layout и rollback к принятому v3.8 runtime.

Gate: GUI не рассчитывает risk самостоятельно и не изменяет execution state
без явной operator command.

### M6 — Stable qualification

- full automated regression;
- standalone bootstrap/layout/upgrade/rollback;
- restart, disconnect, partial fill и reconciliation matrix;
- 2–3 instrument Sandbox burn-in 24–48 h;
- manual global/instrument kill-switch acceptance;
- docs, manifest, deterministic source ZIP и secret scan.

Gate: explicit user acceptance. Merge implementation не означает release
publication или разрешение real account.

## 9. Обязательная test matrix

### Domain/policy

1. Empty portfolio и positive cash.
2. Одна позиция на точной границе каждого лимита.
3. Три позиции с разными instrument/strategy/asset-class scopes.
4. Instrument, strategy и asset-class concentration breach.
5. Gross/net exposure и cash reserve ограничивают target до одного lot точно.
6. `max_open_positions` допускает reduction, но блокирует новый instrument.
7. HHI совпадает с заранее рассчитанным значением и не является hard gate.
8. Unknown equity/price/lot size/ownership/asset class.
9. Stale/future/corrupt canonical snapshot.
10. Daily/weekly rollover в `Europe/Moscow`.
11. Drawdown, turnover и order-count limits после нескольких executions.
12. Global и instrument kill switch: BUY blocked, safe reduction allowed.

### Queue/concurrency

13. Два BUY проходят по отдельности, но совместно превышают cash reserve.
14. Два инструмента совместно превышают strategy/asset-class concentration.
15. Queue mutation делает старый proof недействительным.
16. Policy/state/canonical change после authorization даёт `0 POST`.
17. Два процесса одновременно пытаются зарезервировать один cash остаток.
18. Lock timeout, process crash и last-good recovery.

### Shadow/determinism

19. Одинаковый fully specified input даёт одинаковый canonical decision и id.
20. Каждый eligible proposal получает ровно одно shadow decision.
21. Shadow режим не меняет candidate, Central transitions или broker calls;
    новые journal/report events являются единственным допустимым отличием.

### Execution/recovery

22. Partial fill пересчитывает exposure по факту, а не по request.
23. Restart до POST, после POST и до reconciliation.
24. Ambiguous response остаётся `UNCERTAIN`, без duplicate submit.
25. External/manual position вызывает fail-closed resync.
26. Необъяснимое external cash change не принимается как P&L и требует resync.
27. Fill не закрывается без canonical reconciliation и Risk accounting.
28. Оставшаяся queue переоценивается после каждого fill/cancel/reject.

### Packaging/security

29. Profile/state migration и rollback.
30. Backup/verify/restore с новой schema.
31. Support bundle не содержит token или raw Account ID.
32. Standalone работает без системного Python.

## 10. Release gates v3.9.0

- v3.8 acceptance не имеет открытых release blockers;
- targeted v3.9 matrix — PASS;
- full regression — PASS без снижения существующего покрытия;
- scoped Ruff, compileall и `git diff --check` — PASS;
- schema migration/rollback — PASS;
- read-only expected-value audit — PASS;
- shadow coverage 100% eligible proposals, unexplained decision drift 0;
- zero duplicate submit;
- zero double cash reservation;
- zero dispatch при stale portfolio/queue/policy/state proof;
- zero fill без canonical reconciliation;
- zero execution без Risk accounting;
- zero unresolved pending/uncertain state к release decision;
- backup/support bundle/secret scan — PASS;
- operator global и instrument kill-switch tests — PASS;
- unexplained external cash change всегда требует baseline resync;
- rollback к принятому v3.8 runtime — PASS на изолированной копии;
- 2–3 instrument Sandbox burn-in 24–48 h — PASS;
- explicit user acceptance до tag/release/закрытия umbrella Issue.

## 11. Предлагаемая структура модулей

```text
current/trading_robot/
├── portfolio_risk_model.py
├── portfolio_risk_evaluator.py
├── portfolio_risk_sizing.py
├── portfolio_risk_adapter.py       # только после pure-domain review
├── portfolio_risk_runtime.py       # только shadow/integration stages
└── portfolio_risk_reporting.py
```

Не помещать весь Portfolio Risk в существующий `risk.py` или GUI. При этом
policy/state persistence эволюционирует в существующих `RiskPolicy`,
`RiskProfileStore` и `RiskStateStore`; отдельный конкурирующий
`portfolio_risk_persistence` store не создаётся.

## 12. Предлагаемое разбиение PR

1. `v3.9-contract-domain` — plan, immutable models, calculator и fixtures.
2. `v3.9-read-only-migration` — adapter, current snapshot/report, profile/state
   schemas и kill switches без execution impact.
3. `v3.9-shadow-observability` — prospective decisions, lot caps и drift audit.
4. `v3.9-central-enforcement` — queue projection, common reserve,
   authorization proof и final dispatch guard.
5. `v3.9-beta1-recovery-ux` — concurrency/recovery, CLI/dashboard,
   backup/support/standalone.
6. `v3.9-qualification` — standalone, burn-in evidence и release docs.

Каждый PR должен сохранять Sandbox-only boundary, иметь собственный targeted
gate и не зависеть от ручного broker сценария для unit/integration acceptance.

## 13. Первый следующий шаг

Начать с M0/M1 в новой ветке от `main`:

1. создать umbrella Issue `v3.9.0 — Portfolio Risk Engine`;
2. утвердить policy fields, valuation formula, metadata taxonomy и lock order;
3. реализовать только pure model/evaluator/sizing modules и synthetic tests;
4. провести review модели до изменения persistence или execution path.

Такой порядок позволяет проверить математику и fail-closed semantics отдельно
от наиболее рискованной части — конкурентного admission и Sandbox dispatch.
