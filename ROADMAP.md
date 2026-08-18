# Roadmap

Дата обновления: 2026-08-13.

## v3.6.0 — Stable Sandbox Core — завершено

- Strategy Engine: SMA, Donchian, Ensemble, PRIMARY/SHADOW;
- Risk Engine и Risk Dashboard;
- Startup Recovery Coordinator;
- атомарный runtime и SQLite EventJournal;
- backup/verify/restore и support bundle;
- Windows Credential Manager;
- MARKET_IDLE и transient API resilience;
- Windows standalone без установленного Python;
- длительный Sandbox acceptance без duplicate submit.

## v3.7-alpha1.x — Read-only Portfolio Manager — завершено

- каноническая broker-agnostic модель портфеля;
- GUI виртуального портфеля;
- ownership, origin, target и reconciliation;
- safe external-close acknowledgement;
- Risk Profile GUI editor;
- version/timestamp/collision-safe exports;
- diagnostic fill feedback;
- transport recovery для IncompleteRead.

## v3.7-alpha2 — Canonical Preflight — завершено

- единый `PortfolioPreflightContext` для Risk и Execution;
- snapshot revision/checksum lease;
- revision recheck перед broker POST;
- mandatory post-fill canonical reconciliation;
- canonical/legacy dual-read transition;
- race, restart, disconnect и external-activity acceptance.

## v3.7-alpha3 — Canonical State Cutover — принято

- `PortfolioState` schema 2;
- canonical-only read path;
- `PortfolioTransactionCoordinator` — единственный writer;
- explicit schema 1 -> 2 migration;
- write-only compatibility shadow;
- recovery через broker + EventJournal + canonical state;
- migration tests — PASS;
- 15+ часов burn-in, 10 исполнений, 4 Strategy BUY->SELL;
- 0 duplicate submit, 0 missing reconciliation/accounting;
- revision monotonicity и disconnect/restart/MARKET_IDLE — PASS.

## v3.7-beta1 — принято 2026-08-12

Цель: стабилизация принятой canonical-only архитектуры без новых торговых функций.

Реализованный scope:

- warnings пересчитываются из текущего snapshot;
- `READY + MATCHED + blocking=false` не содержит stale blocking warnings;
- bootstrap различает Credential Manager, `.env` fallback, absent и unavailable;
- recovered transient outages отражаются как infrastructure WARN/PASS;
- compatibility shadow имеет независимый статус `OK/DEGRADED/DISABLED`;
- schema 2, single-writer, preflight и post-fill protocol не меняются;
- единый sibling `runtime` для Risk, robot и portfolio state в portable-сборке;
- полный alpha3 regression и standalone functional smoke.

Проверено 2026-08-11 и 2026-08-12:

- `454 passed`, Risk Lab `8/8 PASS`;
- установка, standalone-запуск и restart из `run_gui.bat` — PASS;
- один полный BUY→HOLD→SELL с 2/2 исполнениями;
- 0 duplicate submit, missing reconciliation, missing Risk accounting и
  runtime/API/canonical transaction errors;
- финальный canonical state `READY/FRESH/MATCHED`, `blocking=false`, shadow
  `OK`, warnings `0`.

Финальный gate пройден:

- 16 ч 09 мин Sandbox burn-in;
- 6 Strategy BUY→HOLD→SELL, 12/12 orders;
- intentional disconnect и restart с открытой позицией — PASS;
- `OPEN → MARKET_IDLE → OPEN` — PASS;
- reports/support bundle reviewed;
- 0 duplicate submit, missing reconciliation, missing Risk accounting и
  unresolved pending/uncertain execution.

### Beta1 handoff gate

Source, tests, build manifest и sanitized evidence опубликованы в
`v3-7-beta1`, прошли GitHub/ChatGPT review и объединены в `main` через PR #35.

Issues этапа:

- #31 — beta1 scope, completed;
- #32 — implementation/acceptance checklist, completed;
- #33 — Codex → GitHub implementation/evidence handoff, completed.

Результат: принятая `v3.7-beta1 / 0.3.7b1`.

## v3.7.0 — Portfolio Manager Stable — кандидат квалифицируется

Issue #34 — release qualification and final acceptance.

Цель: зафиксировать canonical-only Portfolio Manager как стабильное одноинструментное Sandbox-ядро без изменения архитектуры.

Локально выполнено 2026-08-12:

- beta1 acceptance complete;
- full regression `455 passed`;
- crash/recovery, migration и backup/restore subset `61 passed`;
- migration schema1->schema2 regression PASS;
- Risk Lab `8/8 PASS`;
- standalone build и layout PASS;
- accepted beta1 rollback artifact: hash PASS, `454 passed`;
- release hygiene/secret scan PASS;
- deterministic clean source ZIP PASS;
- 0 duplicate submit;
- 0 fill without canonical reconciliation;
- 0 execution without Risk accounting.

Остаётся подтвердить на пользовательском Windows/Sandbox-контуре:

- clean install/upgrade;
- фактический standalone без установленного Python;
- rollback exercise в тестовой копии;
- backup/verify/restore и sanitized support bundle рабочего runtime;
- финальный Sandbox burn-in 24–48 h;
- review evidence и отдельное пользовательское acceptance.

Temporal scope `v3.7.0` заморожен: один configured `candle_interval`; per-instrument/per-strategy timeframe относится к следующим версиям.

## v3.8.0 — Static Configured Multi-Position Sandbox

Первый функциональный этап после `v3.7.0 Stable`:

- 2–3 заранее настроенных инструмента через checksummed
  `ConfiguredExecutionSet`;
- несколько canonical positions;
- multi-lot target transitions и частичное уменьшение позиции как revised
  acceptance scope;
- последовательная account-wide очередь заявок и минимальный cash reservation;
- account-wide reconciliation;
- фиксированный `candle_interval` и отдельный `last_processed_candle` каждого
  configured execution slot — Issue #39;
- Global Scheduler не связывает candle interval с Risk/reconciliation cadence;
- текущий persisted `InstrumentRuntime` используется только как внутренний
  transitional ExecutionSlot, не как canonical position owner или обязательный
  публичный контракт следующих версий;
- без `InstrumentUniverse`, автоматического выбора инструментов и Portfolio
  Supervisor.

Пример допустимой конфигурации:

```text
SBER → 1h
LKOH → 30m
YDEX → 15m
```

Dynamic переключение timeframe и multi-timeframe strategies не входят в v3.8.
Автоматическая qualification `0 → 3 → 5 → 2 → 0`, включая Risk-adjusted
entry, volatility-target partial reduction, restart/inspection, canonical/Risk
reconciliation и cash contention, пройдена. Реальная Sandbox acceptance
multi-lot остаётся отдельным release gate; выполненные реальные заявки были
single-lot. Дополнительный isolated readiness-прогон на canonical revision 40
подтвердил SBER/LKOH/YDEX с `max_order_lots=5`, три Strategy/Risk
`NO_POSITION_CHANGE`, пустую Central очередь и 0 Sandbox POST; этот результат
не заменяет реальный execution-сценарий `0 → 3 → 5 → 2 → 0`.

Перед operator dispatch дополнительно действует persisted Risk authorization
guard: совпадение `SANDBOX_EXECUTION` policy hash и dispatch-relevant
`RiskState` обязательно удерживается под profile/state locks до завершения
единственного POST handoff. Kill switch, Risk resync, изменение counters/policy
и legacy authorization без state proof блокируют отправку до явной
reauthorization. Regression после закрытия PR #42 review finding:
targeted `116 passed`, full `576 passed`.

Подробно: `docs/plans/V3_8_REVISED_SCOPE_RU.md`.

## v3.9.0 — Portfolio Risk Engine

- суммарная экспозиция;
- концентрация инструмента, стратегии и класса активов;
- portfolio-wide turnover/loss/drawdown;
- общий денежный резерв;
- global и instrument-level kill switch.

Разработка разделена на pure domain/policy слой, versioned migration,
account-wide Central admission, dispatch-time proof, recovery/observability и
отдельную Sandbox qualification. Canonical `PortfolioState` остаётся
источником позиций и broker cash, а `CentralOrderState` — единственным
владельцем внутренних reservations; Portfolio Risk не создаёт параллельный
ledger.

M1 pure-domain, M2 read-only migration, M3 prospective SHADOW и M4
authoritative enforcement squash-merged через PR #43 в `main` (`d589f345`).
Immutable model,
evaluator и sizing не читают runtime-файлы и не вызывают broker API.
`PortfolioRiskInputAdapter` строит вход только из canonical `PortfolioState`,
Central reservation projection, существующего `RiskState` и явной instrument
metadata. Computational policy DTO адаптируется к единственному persisted
`RiskPolicy`; второго policy/position/cash/reservation ledger нет.

Risk profile schema 2 и RiskState schema 3 мигрируются additively. Legacy
Sandbox profile получает `CONFIGURATION_REQUIRED`, пока оператор не выполнит
точное подтверждение новой Portfolio Policy. Global/instrument kill switches
переживают restart; instrument halt включён в существующий final dispatch guard
и блокирует provider POST. Read-only report всегда возвращает
`execution_authorized=false` и не меняет runtime stores.

M3 записывает ровно одно идемпотентное решение на deterministic shadow key,
сравнивает его с фактическим v3.8 approved target и не меняет candidate,
Central transitions, authorization или provider calls. Локальные gates:
core targeted `171 passed`, isolated-runtime `6 passed`, full `663 passed`,
changed-file Ruff PASS. Для реального M3 gate добавлен транзакционный
`tools/v3_9_prepare_shadow_runtime.py`: он сохраняет canonical portfolio и
конфигурации из принятого v3.8, строит checksummed lot metadata, но создаёт
пустые Central/Risk/journal, останавливает instrument runtimes и не копирует
секреты, логи или broker-order history. На 2026-08-13 runtime LKOH/SBER
подготовлен из revision 16; 37 source-файлов не изменились, начальный report
ожидаемо имеет 0 observations/`INCOMPLETE`, token-free backup проверен.
Искусственная заявка не создавалась. Перед M4
нужна реальная runtime-выборка: shadow coverage 100%, `UNAVAILABLE=0` и
unexplained drift 0. Подключение Portfolio Risk к authoritative Central
admission остаётся отдельным M4 gate после review этой выборки и
concurrency/proof design.

Post-policy configure-gate `tools/v3_9_configure_shadow_runtimes.py` также
закрыт: он проверяет secure credential provider, canonical/runtime lots,
checksummed UID/lot metadata, `READY/OBSERVE_ONLY`, пустую Central queue и
согласованно пустые Risk baselines. Единственная запись — checksummed
config-manifest; оба runtime остаются `STOPPED`, proposal/intent/POST равны 0.
Идемпотентный повтор дал `ALREADY_CONFIGURED`; backup проверен. Full regression:
`669 passed`.

Отдельный start-gate `tools/v3_9_start_shadow_runtimes.py` реализован и прошёл
targeted `61 passed`, full `676 passed`, Ruff PASS. Он не создаёт proposal,
Central intent или broker order и допускает локальные записи только после
свежего read-only provider preflight. Первый реальный preview остановлен
fail-closed: broker `LKOH=0`, sealed canonical/runtime `LKOH=1`; runtime остался
`STOPPED`, Risk baselines — `UNINITIALIZED`, журнал — пустой. Следующий gate —
явное acknowledgement внешнего flat-close LKOH, затем повтор
`START V3.9 SHADOW RUNTIMES`; автоматическое adoption запрещено.

External close LKOH затем явно подтверждён точной фразой
`ACK EXTERNAL CLOSE LKOH 0`. Recovery-tool повторно доказал provider flat/no
orders, перевёл только canonical/runtime LKOH 1→0, сохранил runtimes `STOPPED`,
Risk pristine и Central empty. Canonical revision 17 неблокирующая; повторный
START preview PASS, token-free post-ACK backup VALID. Полная регрессия после
recovery integration: `681 passed`, Ruff PASS. Следующий отдельный gate — только
повторная команда `START V3.9 SHADOW RUNTIMES`.

Повторный START-gate после ACK выполнен: provider flat/no-orders, canonical
revision 18 `FRESH`/non-blocking, Risk baselines `READY`, оба runtime `ACTIVE`,
Central empty. Proposal/intent/order submission отсутствуют. Идемпотентный
повтор PASS, post-START token-free backup VALID. Следующий M3 acceptance gate —
не создавать искусственную заявку, а дождаться естественного eligible Strategy
proposal и проверить shadow coverage/drift read-only отчётом.

Operational gap закрыт отдельным `tools/v3_9_run_shadow_observation.py`: `preview`
делает только provider GET и вычисляет Strategy proposal в памяти, а `apply`
требует точную фразу `RUN V3.9 SHADOW OBSERVATION` и использует общий
`GlobalScheduler`/v3.8 Risk/M3 observer с обязательным `observe_only`-выходом до
любого Central mutation. Реальный preview LKOH/SBER прошёл без записей:
закрытые свечи LKOH 30m/SBER 1h `18:30/18:00 UTC`, обе естественные цели
`HOLD=0`, broker POST и execution authorization отсутствуют.

Первый подтверждённый apply затем записал две естественные `HOLD=0` observations
для свечей `19:00/18:00 UTC`. Coverage `2/2`, unavailable `0`, target drift
`MATCH=2`, unexplained drift `0`; Central и broker не изменились. Однако обе
prospective decision были `HALTED`: поздний запуск дал штатный
`CANDIDATE_PRICE_STALE`, а runner ошибочно добавил `SNAPSHOT_FROM_FUTURE`, потому
что evaluation timestamp фиксировался до provider snapshot. Порядок времени и
редакция внутренних IDs в операторском JSON исправлены, append-only evidence не
переписано. Следующий gate — новая естественная свежая свеча и повторный
observation; до чистой выборки M4 по-прежнему запрещён.

Повторный observation gate получил новые LKOH 30m/SBER 1h свечи и подтвердил
исправление snapshot-time: `SNAPSHOT_FROM_FUTURE` отсутствует. Итоговая выборка
имеет coverage `4/4`, unavailable 0, `MATCH=4`, unexplained drift 0; Central,
Risk counters и broker path не изменены. Однако обе новые decision снова
`HALTED/CANDIDATE_PRICE_STALE`. Причина теперь локализована не во времени запуска:
исторический candle timestamp не является свежим timestamp биржевой цены.
Implementation gate закрыт через официальный `GetLastPrices`: exchange price,
его UTC time и source передаются одной immutable candidate-quote структурой;
missing/invalid/stale/future остаются fail-closed, operational candle fallback
удалён. Реальный read-only preview получил свежие LKOH/SBER exchange quotes и
новую LKOH свечу без записей; targeted `86 passed`, full `689 passed`, Ruff PASS.
Следующий gate — отдельно подтверждённый natural observation; M4 закрыт.

Natural observation gate выполнен на новой LKOH 30m свече `20:00 UTC` без
искусственной заявки. Новое событие `EVALUATED/PASS` использовало свежий
`TBANK_LAST_PRICE_EXCHANGE` quote `20:42:41 UTC`, не получило hard blocks и дало
`MATCH`/unexplained drift false. SBER не имел новой закрытой свечи после уже
обработанной `19:00 UTC`, поэтому идемпотентно не создал дубликат. Aggregate
read-only report: coverage `5/5`, unavailable 0, `MATCH=5`, unexplained drift 0,
execution authorization false. Central остался byte-identical, canonical
economic state и Risk counters/reservations не изменились; оба runtime `ACTIVE`,
pending 0. Post-gate backup VALID, token-free, 14 entries. Clean quote path для
LKOH подтверждён; clean two-instrument sample ещё не получен, поэтому M4 не
открывается автоматически и требует отдельного review.

Следующий exact-confirmation gate 2026-08-14 закрыл clean sample по обоим
инструментам: LKOH `06:30 UTC` — `HOLD=0`, SBER `06:00 UTC` — естественный
`BUY 1`. Оба shadow decision получили `PASS`, approved target `0/1`, hard
blocks/policy halts/adjustments отсутствуют, drift `MATCH`; scheduler 8/8,
failures 0. Aggregate coverage `7/7`, unavailable 0, `MATCH=7`, unexplained
drift 0, execution authorization false. Central остался byte-identical и пуст,
canonical revision/позиции и Risk economic counters не изменились; broker POST
0. VALID token-free backup содержит 14 entries. M3 runtime evidence — PASS,
следующий этап — отдельный финальный review M3; authoritative M4 не включён.

Исторический финальный review M3 — PASS: `689 passed`, scoped core Ruff PASS, critical Ruff
по всему изменённому Python scope PASS, compileall/diff-check PASS, secret и
runtime-artifact scan чистый. M4 readiness review подтвердил проектную границу:
на тот момент authoritative portfolio proof, atomic account-wide admission и
dispatch enforcement ещё не были реализованы и не входили в M1–M3 publication
scope.

После этого отдельный M4 (`alpha3`) добавил immutable Portfolio Risk proof,
атомарные account-wide admission/reservation, фиксированный lock order,
dispatch-time reproduction и revalidation, restart/recovery и post-fill
recalculation. Изолированный Sandbox acceptance и финальный review завершены без
искусственного создания заявки; live dispatch/fill намеренно оставлен за
границей этого gate. PR #43 снят с draft и squash-merged; post-merge
Windows/Python 3.12 CI дал `721 passed`, pip check, critical/strict Ruff,
compileall PASS и annotations 0.

Повторный review PR `#43` устранил fail-open подстановку `RUB`: checksummed
sandbox metadata теперь переносит только проверенную валюту источника либо
сохраняет явное `unknown`, а authoritative admission без валюты блокируется до
Central/Risk mutation. Configure/start также не активируют runtime с неизвестной
валютой. ROADMAP синхронизирован с фактическим M4 scope. Локальный correction
gate: targeted `77 passed`, оба уровня Ruff, `pip check`, compileall и
diff-check — PASS; полный Windows regression повторяется обязательным PR CI.
M5 `beta1` начат отдельно в `agent/v3-9-beta1-recovery-ux`. Первый срез M5.1
добавляет единый operator CLI `tools/v3_9_risk_control.py`: read-only
inspect/explain/policy review и exact-confirmation global/instrument kill-switch
transitions. Account ID в выводе маскируется; mutation ограничена существующим
атомарным `risk_state.json` с last-good `.bak` и не создаёт proposal, Central
intent, arming или provider POST. Corrupt policy проверяется до mutation, поэтому ошибка не может
маскировать уже выполненную запись. Финальный review дополнительно закрыл
canonical uppercase instrument ID, блокирующий статус `OBSERVE_ONLY` и
обязательный Sandbox account scope. Targeted `7 passed`, full regression
`728 passed`, pip check, critical/strict Ruff и compileall PASS. PR #44 снят с
draft и squash-merged в `main` (`ac308660`); post-merge CI: `728 passed`, все
gates PASS, annotations 0.

M5.2 реализован в отдельной ветке `agent/v3-9-beta1-m5-2-recovery`. Новый
`RiskState` schema 4 сохраняет proof необъяснимого `EXTERNAL_CASH_CHANGE`, а
подтверждённый fill обновляет cash anchor после canonical reconciliation.
`tools/v3_9_external_cash_resync.py` реализует read-only `prepare` и отдельный
exact-confirmation `apply`, связанный с policy/canonical/Risk/Central hashes.
Reservation, pending/uncertain, stale proof или position-drift source блокируют
resync; policy обязан задавать конечный snapshot-age limit, а фактический
wall-clock возраст повторно проверяется в `prepare` и `apply`. Execution counters,
kill switches и IDs сохраняются.

Isolated runtime и restart/read-only acceptance 2026-08-14 прошли на естественном
external cash change без искусственной заявки. `prepare` не выполнил запись,
exact-confirmation `apply` изменил атомарный RiskState и обновил его `.bak`; два
новых процесса подтвердили сохранность baselines и запрет повторного применения
consumed proof. Proposal/intent/dispatch/resubmit/provider POST отсутствовали.
Boundary-inclusive targeted matrix после final-review fixes: `157 passed`, full
CI regression: `747 passed`. Non-cash resync gate имеет приоритет над cash evidence,
trusted cash anchor не продвигается до его отдельного устранения, material delta
сравнивается в целых копейках, а post-apply output error не скрывает уже
выполненную запись. CLI также до любой operation отклоняет lexical/resolved
`--output` внутри runtime и не может отчётом перезаписать state. M5.2
squash-merged через PR #45 в `main` (`cc02294`); post-merge CI run
`31825722096` прошёл с `747 passed`, pip check, critical/strict Ruff и
compileall PASS.

M5.3 локально реализован в
`agent/v3-9-beta1-m5-3-qualification`: checksum-aware backup source,
restore primary/last-good consistency, transactional rollback companions,
read-only EventJournal, v3.9-aware sanitized support bundle, Sandbox-only
standalone layout и единый read-only final-review tool. Final-review correction
закрыл unknown-account redaction, backup content/version validation, orphan
recovery rollback, support checksum coverage и structural launcher contract.
Disposable restore выявил и закрыл recovery-only gap для `UNCHANGED` primary;
успешная и аварийная ветки закреплены regression tests. Targeted matrix:
`57 passed`; full local regression: `771 passed`; pip check, critical/strict
Ruff и compileall PASS. Isolated M5.3 runtime подготовлен из принятого M5.2:
31/31 material hash совпали, lock/WAL/SHM/secrets не копировались, read-only
inspect — PASS. Verified backup, sanitized support bundle и explicit
structural-only standalone layout пересозданы; повторный final-review — PASS,
failures 0, material hash changes 0, raw Account ID evidence leaks 0.
Isolated `RESTORE RUNTIME` принят на новой disposable-копии принятого v3.8:
backup verification PASS, 10/10 restored entries, checksum 6/6, last-good 5/5,
offline inspect `IDLE`, material/residual changes 0, WAL/SHM 0. Release identity
и фактический standalone build/launch не заявлены выполненными. Post-final-review
correction запретил произвольный Risk dashboard exception text в support bundle
и сохранил `RuntimeBackupError` failures в rollback diagnostics. M5.3
squash-merged через PR #46 в `main` (`cddd80f3`); exact-head и post-merge GitHub
Actions прошли с `774 passed`, pip check, critical/strict Ruff и compileall PASS,
annotations 0. Следующий v3.9 gate — M6 Stable qualification.

M6 preparation начата от `main` `dd3b9a35`; functional implementation baseline
— `cddd80f3`, а M6 diff ограничен release identity/packaging/qualification.
Release candidate identity —
`0.3.9 / v3.9.0 / stable`; manifest, source/standalone builders, root release
docs и fail-closed source preflight синхронизированы. Все manual qualification
flags остаются false. Следующий отдельный gate —
Automated Gate A/B прошёл: targeted `27 + 112 + 46`, full regression
`780 passed`, pip check, release hygiene, critical/strict Ruff и compileall
PASS. Два source ZIP byte-identical; deterministic structure/manifest/private
scan PASS. Следующий gate — `BUILD V3.9 M6 ACTUAL STANDALONE`;
фактический standalone, recovery matrix, kill switches и 24–48 h burn-in ещё
не приняты.

Подробности:

- `docs/plans/V3_9_PORTFOLIO_RISK_ENGINE_PLAN_RU.md`;
- `docs/plans/V3_9_M5_1_OPERATOR_CONTROL_RUNBOOK_RU.md`;
- `docs/plans/V3_9_M5_2_EXTERNAL_CASH_RECOVERY_RUNBOOK_RU.md`;
- `docs/plans/V3_9_M5_3_PERSISTENCE_STANDALONE_RUNBOOK_RU.md`;
- `docs/project/V3_9_INTERFACE_FREEZE_RU.md`;
- `docs/project/V3_9_ISSUE_PROPOSAL_RU.md`.

## v3.10.0 — Cash-flow Manager

- пополнения, выводы, дивиденды, купоны, комиссии и налоги;
- CashLedger;
- отделение доходности от внешних потоков;
- бюджет ребалансировки.

## v4.0.0 — Portfolio Supervisor Foundation

Статус: `M0 INTERFACE ACCEPTED 2026-08-15 / PUBLICATION TRACKED BY PR #70 / AUTHORITATIVE IMPLEMENTATION BLOCKED`.

- formal `StrategyRuntimeId = instrument + strategy + config + timeframe`;
- versioned `StrategyContributionProposal`, без конфликта с существующим
  direct-target `StrategyProposal`;
- deterministic ProposalSnapshot/DecisionEpoch с quote/cash/Risk/Central proofs;
- fixed-point contribution budgets, без float в persisted/hash contracts;
- один Supervisor-owned aggregate target policy на instrument;
- ex-ante TargetAttribution отдельно от ex-post realized attribution;
- deterministic CapitalAllocator и одна net RebalanceAction на instrument;
- Portfolio Risk остаётся mandatory hard gate;
- Central остаётся владельцем queue/reservations/admission/dispatch proof;
- authoritative v4 provider path проходит только через ExecutionAdapter; current
  legacy bot/diagnostic POST routes изолируются от v4 modules;
- pre-schema3 stale cap воспроизводится из shadow-only accepted checkpoint, а
  после cutover — только из committed canonical target attribution;
- attribution-only transition не создаёт intent/order/cash effect;
- schema-3 target-owner migration только после shadow и rollback gates.

M0 source audit выполнен documents-only на baseline `65fe0fb`; принятый freeze
добавляет verified current inventory, unique DTO/serialization contracts,
legacy-route retirement, dual canonical-before/after target transaction,
durable exact-after recovery record, single target-subtree writer,
schema-2 -> 3 migration, lock order и testability register. Третий post-fix final
review завершён с PASS, explicit user acceptance записан 2026-08-15. M1/M2 ждут
принятые M0 и v3.9 baseline; M4/M5
дополнительно зависят от принятого v3.10 Decimal/Money contract (#49). #53
открывает read-only CashAvailability shadow interface; authoritative M5 ждёт
принятый Portfolio Risk cash context #55 и отдельную activation acceptance.
Core M3 может использовать explicit sentinel до #53, но #60 не закрывается без
принятого CashAvailability shadow subgate. Принятие M0 не отменяет отдельный
v3.9 M6 baseline gate для начала M1/M2.
Existing Issue #40 используется для alpha2 runtime/scheduling и не дублируется.

Подробности:

- `docs/plans/V4_0_PORTFOLIO_SUPERVISOR_PLAN_RU.md`;
- `docs/plans/V4_0_M0_TESTABILITY_REGISTER_RU.md`;
- `docs/project/V4_0_ARCHITECTURE_REVIEW_2026-08-14_RU.md`;
- `docs/project/V4_0_CURRENT_INTERFACE_INVENTORY_RU.md`;
- `docs/project/V4_0_INTERFACE_FREEZE_RU.md`;
- `docs/project/V4_0_ISSUE_MAP_RU.md`.

GitHub tracking: umbrella #57, milestones #58-#68 и existing alpha2 Issue #40.
Публикация отслеживается PR #70 (`Closes #58`). Его merge является
documents-only publication и не означает начало implementation автоматически.

## v4.1.0 — Ограниченный реальный контур

Только после отдельного решения и полного Sandbox gate:

1. Read-only real portfolio.
2. Confirmation Mode.
3. Limited Autonomous с allowlist и минимальными лимитами.
4. Расширение автономности после отдельного acceptance.

## v5.x — InstrumentUniverse / adaptive selection

Дальняя исследовательская ветвь после стабильного Portfolio Supervisor и
Portfolio Risk:

- `InstrumentCatalog` и point-in-time Universe Manager;
- eligibility по liquidity, spread, history, listing, asset class и venue health;
- rule-based universe сначала в SHADOW/Sandbox;
- multi-timeframe Strategy Modules — Issue #41;
- StrategyIntent/StrategyProposal с явным timeframe/decision horizon;
- versioned profiles для наборов timeframe;
- rule-based Strategy Selector сначала в SHADOW/Sandbox;
- затем statistical/ML-assisted scoring при наличии достаточных данных;
- Supervisor выбирает проверенный StrategyProfile/module, но не переписывает произвольно timeframe работающей стратегии;
- Universe не отправляет заявки и не обходит Supervisor/Risk/Execution;
- Policy Guard, Portfolio Risk и Execution safety остаются обязательными.

Подробные планы: `docs/plans/CANDLE_INTERVAL_EVOLUTION_RU.md` и
`docs/plans/INSTRUMENT_SELECTION_EVOLUTION_RU.md`.

## Боковая ветка — Crypto / Digital Assets Integration

Необязательная ветка дальнейшего развития. Она не блокирует и не изменяет последовательность основной линии `v3.8 → v3.9 → v3.10 → v4.x`.

Начинать активную реализацию предполагается только после появления устойчивой multi-instrument/portfolio architecture и asset-agnostic границ.

Предпочтительная последовательность:

```text
основная portfolio architecture
        ↓
multi-account / multi-venue abstraction
        ↓
Crypto Market Data + Read-only Portfolio
        ↓
Crypto Spot Paper / Shadow
        ↓
Crypto Spot Sandbox / Test Environment
        ↓
Cross-Asset Portfolio: securities + crypto
        ↓
отдельное решение о Limited Live Crypto
```

Архитектурные правила ветки:

- crypto не добавляется как «ещё один MOEX ticker»;
- общий Portfolio Manager/Supervisor остаётся asset-agnostic;
- venue-specific execution изолируется за `ExecutionVenueAdapter` или эквивалентной абстракцией;
- lot-based securities и fractional crypto quantity поддерживаются через общий quantity contract;
- 24/7 market/venue health не связывается с MOEX `MARKET_IDLE` semantics;
- stablecoin считается отдельным crypto asset, а не обычным fiat cash;
- Portfolio Risk получает asset-class, venue и stablecoin concentration limits;
- первая реализация — Spot only: без leverage, margin, derivatives, staking, lending, DeFi и autonomous withdrawals;
- Strategy/Supervisor/AI не получают прямого venue POST в обход Policy/Risk/Execution/reconciliation.

Перед выбором реального crypto venue требуется отдельная актуальная проверка законодательства, доступности API, KYC/AML и условий площадки для юрисдикции пользователя.

Подробный план: `docs/plans/CRYPTO_INTEGRATION_BRANCH_RU.md`.

## Temporal architecture rule

Не смешивать:

```text
candle_interval       — размер свечи стратегии
decision cadence      — проверка новой закрытой свечи
scheduler cadence     — обслуживание runtime
risk refresh cadence  — обновление Risk
reconciliation cadence — сверка с брокером
```

Эти интервалы независимы и не должны автоматически меняться друг за другом.

## Не делать до соответствующего этапа

- не разрешать real execution в v3.7/v3.8;
- не добавлять multi-instrument до v3.8;
- не менять PortfolioState schema в beta1/Stable без отдельной архитектурной причины;
- не совмещать observability/release fixes с новыми стратегиями;
- не удалять recovery/audit данные ради упрощения;
- не считать Sandbox acceptance доказательством прибыльности;
- не вводить dynamic timeframe switching в v3.8;
- не вводить InstrumentUniverse или market-wide scanning в v3.8;
- не считать timeframe свойством всей позиции при нескольких StrategyRuntime;
- не позволять Supervisor/AI обходить Portfolio Risk/Policy/Execution gates;
- не внедрять crypto execution в основной core до появления отдельной multi-venue/asset-agnostic границы.
