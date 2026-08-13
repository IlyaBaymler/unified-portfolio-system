# v3.8 Multi-Instrument Sandbox — план реализации

Дата начала: 2026-08-13.

Рабочая ветка: `feature/v3-8-instrument-runtime`.

Связанные задачи: Issue #39; после завершения Stable gate — основной этап
`v3.8.0 Multi-Instrument Sandbox` из `ROADMAP.md`.

## Архитектурное уточнение после реализации

Пакет ревизии от 2026-08-13 принят в совместимой форме:

- checksummed `MultiInstrumentProfile` является фактическим
  `ConfiguredExecutionSet`;
- текущий persisted `InstrumentRuntime` сохраняется как уже проверенный
  transitional ExecutionSlot;
- он не становится canonical position owner или обязательным public domain
  aggregate следующих версий;
- формальный `StrategyRuntime` переносится в v4.0;
- `InstrumentUniverse`/Universe Manager переносится в v5.x;
- multi-lot flow прошёл автоматическую qualification; реальная operator-only
  Sandbox acceptance остаётся отдельным revised-scope gate.

Решение и revised scope:
`docs/project/ARCHITECTURE_REVIEW_2026-08-13_RU.md` и
`docs/plans/V3_8_REVISED_SCOPE_RU.md`.

## Архитектурная граница

Опережающая разработка ведётся отдельно от финального burn-in `v3.7.0`.
Новый scheduler не подключается к broker POST до отдельного review и acceptance.

Неизменные safety gates:

```text
Strategy proposal
→ Portfolio Policy
→ Portfolio Risk
→ canonical preflight
→ Central Order Manager / Execution Engine
→ post-fill reconciliation
```

`GlobalScheduler` обслуживает temporal lifecycle и не получает прямой маршрут
отправки заявки.

## M1 — InstrumentRuntime как transitional ExecutionSlot

Статус 2026-08-13: реализовано и покрыто автоматическими сценариями.

- immutable `InstrumentRuntimeConfig`;
- собственный fixed `candle_interval` каждого инструмента;
- `runtime_config_hash` и runtime key с обязательным timeframe;
- account/instrument execution scope без ложного разделения ownership по timeframe;
- независимый `last_processed_candle` каждого runtime;
- отдельные decision/scheduler/Risk/reconciliation/market-status cadences;
- детерминированное последовательное обслуживание runtime;
- отсутствие cross-talk при новой свече одного инструмента;
- запрет второго runtime того же инструмента в v3.8;
- explicit timeframe transition только для остановленного, flat runtime без pending;
- versioned checksum-managed persistence и fail-closed restore;
- restart без повторной обработки уже принятой свечи;
- изоляция сбоя одного runtime от обслуживания остальных.

Сценарии M1:

```text
V38-M1-01  SBER 1h + LKOH 30m + YDEX 15m создаются в одном scheduler
V38-M1-02  новая свеча SBER не запускает decision LKOH/YDEX
V38-M1-03  одна и та же свеча не обрабатывается повторно
V38-M1-04  Risk/reconciliation работают без новой свечи
V38-M1-05  restart восстанавливает timeframe и last_processed_candle
V38-M1-06  active/open/pending runtime блокирует смену timeframe
V38-M1-07  stopped+flat transition создаёт новую versioned identity
V38-M1-08  corrupt/checksum mismatch блокирует восстановление
V38-M1-09  ошибка одного runtime не останавливает остальные
V38-M1-10  несколько timeframe не создают несколько execution owner одного instrument
```

## M2 — интеграция существующего Strategy/Bot runtime

Статус 2026-08-13: read-only foundation и lifecycle observability реализованы,
broker POST не подключён.

Реализовано:

- adapter между `InstrumentRuntime` и существующим Strategy Engine;
- versioned checksum-managed multi-instrument configuration/profile store;
- максимум три инструмента в mode-separated профилях `DRY_RUN`/`SANDBOX`;
- поддержка 15m/30m/1h в candle loading и warm-up policy;
- безопасный bootstrap runtime registry без неявной смены timeframe/cadence;
- runtime backup/restore, startup validation, support-bundle integrity и release hygiene;
- synthetic multi-instrument run без broker POST;
- read-only proposal с явным следующим gate
  `PORTFOLIO_POLICY_RISK_PREFLIGHT_EXECUTION`;
- read-only GUI projection профилей и runtime registry с checksum/identity status;
- периодическое обновление GUI только по времени изменения runtime-файлов;
- journal adapter для scheduler/runtime lifecycle и завершённых/ошибочных действий;
- изоляция отказа journal sink от temporal persistence и обслуживания runtime.

### Архитектурный review M2 — 2026-08-13

Результат: `PASS` для read-only proposal stage; разрешение на broker execution
не выдаётся.

- proposal всегда содержит `execution_authorized=false`;
- следующий gate явно зафиксирован как
  `PORTFOLIO_POLICY_RISK_PREFLIGHT_EXECUTION`;
- runtime identity и config hash включают instrument и fixed timeframe;
- успешный decision сначала сохраняет `last_processed_candle`, поэтому restart
  не повторяет уже принятую свечу;
- checksum/identity mismatch отображается как `ATTENTION`/`ERROR` и не
  исправляется GUI автоматически;
- lifecycle journal записывается после сохранённого перехода;
- недоступность journal не отменяет уже сохранённое temporal состояние;
- broker POST trap подтверждает отсутствие вызова из Strategy adapter;
- GlobalScheduler пока не подключён к Central Order Manager и не является
  production execution entrypoint.

## M3 — Central Order Manager и account-wide reconciliation

Статус 2026-08-13: безопасный orchestration foundation, изолированный
Sandbox execution adapter и operator-only acceptance workflow реализованы
локально; автоматический маршрут из `GlobalScheduler`, production GUI и
существующего bot runtime не подключён.

Реализовано:

- единая последовательная очередь заявок;
- account-wide pending/uncertain gate;
- детерминированные intent/idempotency identifiers и duplicate-submit gate;
- точное резервирование RUB в копейках с опорой на канонический snapshot lease;
- повторная проверка revision/decision checksum перед передачей intent внешнему
  execution adapter;
- restart recovery `IN_FLIGHT → UNCERTAIN` без автоматической повторной отправки;
- сохранение `SUBMITTED`/`UNCERTAIN` как account-wide blocker до канонической
  reconciliation;
- доказательство reconciliation через свежую canonical portfolio revision,
  decision checksum и фактическое количество лотов;
- явная reauthorization оставшейся очереди после изменения портфеля;
- checksum-managed persistence, runtime backup/restore, startup validation,
  support-bundle integrity, release hygiene и lifecycle journal;
- `prepare_next()` возвращает `execution_authorized=false`; broker POST methods
  в Central Order Manager отсутствуют.
- отдельный `SandboxExecutionAdapter` является единственной новой границей с
  `post_order` и требует точного arming confirmation;
- market availability проверяется до атомарной подготовки именно ожидаемого
  queue head;
- disconnect до подготовки оставляет intent в `QUEUED`, а timeout/lost response
  после начала submit переводит его в `UNCERTAIN` без повторной отправки;
- restart inspection выполняется только read-only по детерминированному request
  ID;
- одиночный broker `404` не считается доказательством `NOT_SUBMITTED`;
- canonical reconciliation требует snapshot, снятый после последнего
  `SUBMITTED`/`UNCERTAIN` перехода, и сохраняет его timestamp в proof.
- `CentralOrderCoordinator` связывает read-only proposal, один canonical
  snapshot lease, Portfolio preflight и `SANDBOX_EXECUTION` Risk без доступа к
  broker POST;
- оставшийся `QUEUED` intent явно переавторизуется после изменения canonical
  portfolio revision, а второй активный intent того же instrument scope
  блокируется;
- real `TBankSandboxClient` проверен на точное соответствие request ID,
  retry-safe transport и ambiguous HTTP/invalid-body outcomes;
- terminal fill освобождает account blocker только после идемпотентной записи
  execution ID, turnover и order count в `RiskState`;
- first-time configuration CLI создаёт checksummed profiles/runtimes для двух
  или трёх Sandbox instruments без права перезаписи существующего registry;
- отдельный isolated-runtime bootstrap проверяет принятую v3.7 canonical
  baseline, account-scopes Risk без изменения policy, создаёт чистые RiskState
  и Central Order history и не копирует secrets/logs/order history;
- operator CLI выполняет `prepare-one`, exact-head `dispatch-one`, read-only
  `inspect` и canonical `reconcile`; GUI, scheduler и bot loop его не вызывают;
- synthetic end-to-end `prepare → POST → reconcile → Risk accounting` проходит
  с ровно одним submit.

### Архитектурный review M3 — 2026-08-13

Результат: `TWO-INSTRUMENT OPERATOR-ONLY PASS / THREE-INSTRUMENT PREPARE PASS / EXECUTION NOT TRIGGERED`.
Реальный Sandbox acceptance для SBER/LKOH пройден по
`V3_8_SANDBOX_ACCEPTANCE_RUNBOOK_RU.md`; automatic production activation
по-прежнему не разрешён.

- Central Order Manager не содержит provider API и не отправляет заявки;
- adapter принимает только account-scoped `SANDBOX_EXECUTION` policy с точной
  фразой `ENABLE V3.8 SANDBOX EXECUTION`;
- deterministic intent UUID используется как provider request ID;
- внутренние transport retries допустимы только с тем же request ID;
- `408`/`409`/`425`/`429`, transport failure, `5xx`, потерянный или
  некоррелируемый ответ трактуются как ambiguous submission;
- explicit rejection и local validation failure имеют разные terminal outcomes;
- terminal provider response не снимает account blocker без более нового
  canonical portfolio snapshot;
- adapter не импортирован production entrypoints и не вызывается scheduler.

Граница завершения M3 по final local review:

- operator-only implementation и двухинструментный execution/reconciliation
  acceptance завершены без блокирующих findings;
- V38-A10 в новом чистом runtime прошёл configure, fresh prepare и restart для
  SBER/LKOH/YDEX; новый execution signal отсутствовал, искусственная заявка не
  создавалась;
- activation route для GUI/bot/GlobalScheduler остаётся отдельным решением и
  этим review не разрешён;
- до публикации требуется синхронизация с актуальным GitHub `main` и повтор
  автоматических gates.

## Не входит в v3.8

- несколько StrategyRuntime одного инструмента с разными timeframe — Issue #40;
- multi-timeframe strategy modules и Supervisor-selected horizon — Issue #41;
- InstrumentUniverse, automatic discovery и market-wide scanning;
- real-account execution;
- dynamic timeframe change активного lifecycle;
- обход Portfolio Risk, preflight или post-fill reconciliation.

## Gate перед публикацией ветки

- M1–M3 targeted runtime/coordinator/adapter/operator matrix — PASS: 110;
- explicit static multi-lot bootstrap и end-to-end
  `0->3->5->2->0` — AUTOMATED PASS;
- provider partial fill continuation, restart/inspection без resubmit и
  competing BUY cash reservation — AUTOMATED PASS;
- synthetic operator `prepare → single POST → reconcile → Risk accounting` — PASS;
- isolated-runtime bootstrap preview для принятой final-burnin baseline — PASS;
- V38-A00 isolated runtime + verified token-free backup — PASS;
- V38-A01 real Sandbox read-only SBER/LKOH configuration preview — PASS;
- V38-A02 checksummed SBER/LKOH STOPPED runtime configuration — PASS;
- V38-A03 offline status/restart recovery — PASS;
- V38-A04 SBER preparation — PREFLIGHT_BLOCKED на новой внешней позиции 1 lot
  без canonical PRIMARY ownership; provider POST отсутствует;
- V38-A04 recovery — burn-in происхождение подтверждено оператором, `ADOPT SBER
  1` завершён с `ATTRIBUTED/MATCHED`, canonical revision 12 и без provider POST;
- V38-A04 SBER retry — QUEUED SELL 1, preflight/Risk PASS, exact pending/runtime
  sync PASS, broker execution не авторизован;
- V38-A04 LKOH — QUEUED BUY 1; итоговая account-wide очередь SBER→LKOH,
  exact runtime pending sync и BUY cash reservation PASS, provider POST 0;
- V38-A06 — ровно один SBER SELL 1 Sandbox POST, terminal FILL;
- V38-A07 — offline restart сохранил account blocker без resubmit, deterministic
  read-only inspection подтвердил FILL 1 по 280.67 RUB;
- V38-A08 — SBER canonical/Risk reconciliation PASS: flat/MATCHED revision 14,
  Risk order count 1/turnover 280.67 RUB, blocker снят, LKOH authorization stale;
- V38-A09 reauthorization — тот же LKOH BUY 1 intent обновлён на canonical
  revision 14; exact queue/runtime sync PASS, второй dispatch ещё не выполнен;
- V38-A09 dispatch/restart — ровно один LKOH BUY 1 POST, terminal FILL; offline
  restart сохранил blocker без resubmit, inspection подтвердил 4555.00 RUB;
- V38-A09 reconciliation — LKOH 1 lot ATTRIBUTED/MATCHED, оба central intent
  RECONCILED/FILLED, Risk order count 2/turnover 4835.67 RUB, queue/blocker/
  pending IDs очищены — PASS;
- final verified backup и redacted support bundle secret scan — PASS;
- full regression — PASS: 570;
- scoped Ruff для новых M1–M3 модулей, CLI и тестов — PASS;
- compileall — PASS;
- `git diff --check` для tracked diff — PASS;
- ручная проверка read-only GUI projection — PASS;
- broker POST не подключён к GUI/bot/scheduler; единственный execution route —
  отдельный operator CLI с двумя точными confirmation gates;
- v3.7 Stable manifest и execution path не изменены;
- CLI help для configuration и acceptance entrypoints — PASS;
- реальный двухинструментный Sandbox acceptance — PASS;
- V38-A10 трёхинструментный Sandbox acceptance — CONFIGURATION PASS,
  execution flow NOT EXECUTED;
- V38-A10 isolated-runtime seed preview from accepted canonical revision 16 —
  PASS;
- V38-A10 isolated-runtime seed apply + verified token-free backup — PASS;
  three-instrument execution flow pending;
- V38-A10 real Sandbox read-only SBER/LKOH/YDEX configuration preview — PASS;
- V38-A10 checksummed SBER/LKOH/YDEX STOPPED runtime configuration, loader
  validation, repeat-apply guard, offline READY status and verified token-free
  backup — PASS;
- V38-A10 SBER fresh prepare — PASS / NO_POSITION_CHANGE: `0 → 0`, intent и
  provider POST отсутствуют, Central/Risk пусты, runtime sync PASS;
- V38-A10 LKOH fresh prepare — PASS / NO_POSITION_CHANGE: bootstrap runtime
  синхронизирован `0 → 1`, Strategy target 1, intent и provider POST отсутствуют,
  Central/Risk пусты;
- V38-A10 YDEX fresh prepare + three-runtime offline restart — PASS /
  NO_POSITION_CHANGE: SBER 0, LKOH 1, YDEX 0, все runtime `ACTIVE` без pending,
  Central/Risk пусты; новый execution/reconciliation цикл не запускался из-за
  отсутствия position change;
- V38-ML-REAL-NO-SIGNAL — PASS: отдельный runtime создан из canonical revision
  40 (`READY`, две позиции `MATCHED`), SBER/LKOH/YDEX сконфигурированы с
  `max_order_lots=5`, account/Risk scope и checksums валидны. Все три fresh
  market-driven prepare вернули `NO_POSITION_CHANGE` (`0→0`), preflight/Risk
  `PASS`; intent, queue, blocker, reservation, pending IDs и provider POST не
  появились. Offline status подтвердил три `ACTIVE` runtime, Central revision 0
  и нулевые Risk turnover/order/execution counters; EventJournal
  `integrity=ok`, order/execution events 0. Pre-intent backup
  `runtime_backup_20260813T130139Z.zip` повторно проверен и не содержит токен;
- final local review — PASS без P0/P1 findings; после multi-lot qualification:
  full regression 570, targeted matrix 110 и scoped Ruff PASS;
- final three-instrument support bundle secret scan — PASS; v3.8 state stores,
  canonical portfolio и EventJournal валидны, токен не включён;
- ветка синхронизирована с GitHub `main` на `026e70b`; локальный v3.8 diff
  восстановлен без конфликтов, повторные automated gates пройдены;
- package version и build manifest намеренно остаются `0.3.7 Stable`: текущий
  результат является merge candidate operator-only implementation, но не
  собранным v3.8 release artifact;
- revised multi-lot acceptance `0->3->5->2->0` — AUTOMATED PASS / REAL SANDBOX
  PENDING; readiness runtime с лимитом 5 подтверждена без искусственной заявки,
  но выполненные реальные acceptance-заявки остаются single-lot;
- commit/push/PR выполняются только отдельным подтверждением.
