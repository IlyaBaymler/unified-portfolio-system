# Unified Portfolio System

Приватный исследовательский проект единой системы управления инвестиционным портфелем.

Целевая архитектура объединяет:

- торговый робот и исполнение заявок;
- Portfolio Manager как единый источник портфельного состояния;
- Risk Engine;
- Cash-flow Manager;
- автоматическое реинвестирование;
- мониторинг, журналирование и постепенную автономизацию.

## Текущий статус

### Последняя опубликованная стабильная версия

**MOEX Research Robot v3.6.0 Stable** — принятое одноинструментное ядро для T-Invest Sandbox.

### Принятый alpha baseline

**v3.7-alpha3 Canonical State Cutover** — пользовательский acceptance пройден.

Подтверждено:

- `PortfolioState` schema 2;
- canonical-only read path;
- `PortfolioTransactionCoordinator` как единственный writer;
- write-only compatibility shadow;
- обязательный canonical preflight и post-fill reconciliation;
- migration tests schema 1 -> schema 2 — PASS;
- около 15 ч 19 мин Sandbox burn-in;
- 10 исполнений, включая 4 полных Strategy BUY->SELL;
- 0 duplicate submit;
- 0 fill без canonical reconciliation;
- 0 execution без Risk accounting;
- revision 0->19 без rollback;
- disconnect, circuit breaker persistence, restart и MARKET_IDLE — PASS.

### Текущая принятая версия разработки

**v3.7-beta1 / `0.3.7b1` — финальный acceptance пройден 2026-08-12.**

Beta1 не меняет торговую архитектуру. Реализованы:

- пересчёт Portfolio warnings из текущего snapshot без stale carry-over;
- metadata-only проверка Windows Credential Manager в bootstrap report;
- классификация восстановленных transient outages как infrastructure WARN/PASS;
- отдельная observability write-only compatibility shadow;
- split-runtime fix: Risk, robot и portfolio state используют единый sibling
  `runtime`-каталог portable-сборки.

Подтверждено:

- полный regression — `454 passed`, Risk Lab — `8/8 PASS`;
- установка, standalone-запуск и restart из `run_gui.bat` — PASS;
- один полный Sandbox BUY→HOLD→SELL, 2/2 заявок исполнены и учтены Risk;
- 0 duplicate submit, runtime/API/canonical transaction errors;
- финальный canonical state: `READY`, `FRESH`, `MATCHED`, `blocking=false`,
  compatibility shadow `OK`, warnings `0`.

Дополнительно подтверждены 16 ч 09 мин burn-in, 6 полных BUY→HOLD→SELL,
12/12 заявок с reconciliation/Risk accounting, intentional disconnect,
restart с открытой позицией и `OPEN → MARKET_IDLE → OPEN`. На всей принятой
сессии: 0 duplicate submit, missing reconciliation, missing Risk accounting и
unresolved execution. Итоговый Risk report и support bundle просмотрены.
Финальный результат зафиксирован в
`docs/releases/V3_7_BETA1_FUNCTIONAL_ACCEPTANCE_RU.md`.

Проверяемый GitHub handoff для Issues #31–#33 находится в
`docs/releases/V3_7_BETA1_GITHUB_HANDOFF_RU.md`.

GitHub-задачи этапа:

- Issue #31 — beta1 scope, completed;
- Issue #32 — implementation/acceptance matrix, completed;
- Issue #33 — Codex → GitHub handoff, completed;
- PR #35 — reviewed и merged в `main`;
- Issue #34 — активная `v3.7.0 Stable` qualification.

### Stable-кандидат

**v3.7.0 / `0.3.7` подготовлен как release candidate и объединён в `main`
через PR #36.** Это не функциональное расширение: schema 2, canonical-only
reads, single-writer, Risk/Execution protocol и broker lifecycle остаются
замороженными.

Локально подтверждено:

- полный regression — `456 passed`;
- crash/recovery, migration и backup/restore subset — `62 passed`;
- Risk Lab — `8/8 PASS`;
- standalone build/layout, release hygiene и clean source ZIP — PASS;
- accepted beta1 rollback artifact — `454 passed`;
- deterministic source ZIP — два совпадающих SHA-256.

Clean install/upgrade, фактический standalone-запуск без Python и
backup/verify/restore подтверждены. Qualification выявила и устранила утечку
Account ID внутри составного `transaction_id`; пересобранный support bundle
прошёл встроенный и независимый exact-value scan; исправление опубликовано
через PR #38. Остаются rollback exercise, финальный 24–48-часовой burn-in и
отдельное пользовательское acceptance.
До этого кандидат не является опубликованным Stable.

Подробности: `docs/releases/V3_7_0_STABLE_QUALIFICATION_RU.md`.

### Multi-instrument этап

Реализация `v3.8.0 Static Configured Multi-Position Sandbox` reviewed и merged
в `main` через PR #42, merge commit `7525d7e`. Revised scope и граница runtime:
`docs/plans/V3_8_REVISED_SCOPE_RU.md`.

Автоматическая multi-lot qualification `0→3→5→2→0` пройдена; реальный
operator-only Sandbox multi-lot acceptance остаётся открытым release gate.
Readiness дополнительно подтверждена на изолированном canonical revision 40
runtime: SBER/LKOH/YDEX настроены с `max_order_lots=5`, три market-driven
prepare завершились `NO_POSITION_CHANGE` без intent и Sandbox POST.
Review draft PR #42 выявил и закрыл dispatch-time Risk freshness gap: каждый
новый intent сохраняет guard hash значимых полей `RiskState`, а
`dispatch-one` под lock повторно сверяет account-scoped Risk policy/state.
Kill switch, Risk resync, смена policy, изменение counters или legacy intent
без guard proof дают fail-closed результат до market/provider API. После
исправления targeted v3.8 matrix — `116 passed`, full regression —
`576 passed`.

Merge implementation не закрывает release acceptance: реальный operator-only
Sandbox multi-lot сценарий `0→3→5→2→0` остаётся открытым gate v3.8.

### Текущий этап v3.9

`v3.9.0 Portfolio Risk Engine` развивает account-wide current/projected risk,
общий денежный резерв, концентрацию инструмента/стратегии/класса активов и
global/instrument kill switches. План сверен с отдельным пакетом обновления и
зафиксирован как staged rollout:

```text
pure domain → read-only → prospective SHADOW → authoritative preflight
            → beta1 recovery/UX → Stable qualification
```

Локально реализованы M1 pure-domain, M2 read-only migration (`alpha1`), M3
prospective SHADOW/observability (`alpha2`) и отдельная M4 authoritative
ветка (`alpha3`, Sandbox acceptance и automated final review PASS; cumulative
draft PR `#43`):

- immutable input/policy/decision DTO, current/projected metrics и
  deterministic whole-lot caps;
- единственный `PortfolioRiskInputAdapter` поверх canonical `PortfolioState`,
  Central reservations и существующего `RiskState`;
- read-only CLI/report с `execution_authorized=false`;
- additive Risk profile schema 2 и RiskState schema 3 с безопасной миграцией;
- явный Sandbox gate `CONFIRM PORTFOLIO RISK POLICY`;
- persistent global/instrument kill switches; instrument halt проверяется в
  последнем dispatch guard и даёт `0 provider POST`.
- `PortfolioRiskShadowObserver` рассчитывает prospective decision после
  принятого v3.8 Risk result, но не меняет target, authorization, Central queue
  или provider path;
- одно событие `portfolio_risk_shadow` на deterministic shadow key переживает
  restart и конкурентный повтор; ошибки shadow/journal изолированы;
- read-only shadow report показывает coverage, unavailable observations и
  классифицированный decision drift.
- M4 `PortfolioRiskRuntime` объединяет enforced policy/state, одну locked
  canonical snapshot и текущую Central reservation projection;
- Portfolio Risk proof сохраняется в `ExecutionAuthorization`, а Central
  завершает admission revision/hash только внутри атомарной queue mutation;
- единый lock order: canonical portfolio → Risk profile → Risk state → Central;
- dispatch повторно воспроизводит сохранённый decision, проверяет live freshness
  и даёт `0 provider POST` при изменении canonical/queue/policy/RiskState;
- confirmed fill сохраняет canonical reconciliation и Risk accounting до
  post-fill пересчёта current Portfolio Risk metrics.

M3 подключён только как наблюдение и принудительно вычисляется в
`OBSERVE_ONLY`. Legacy Sandbox profile сохраняет поведение v3.8; без явного
`CONFIRM PORTFOLIO RISK POLICY` shadow записывает структурированный
`UNAVAILABLE`, а не применяет неявные лимиты. Authoritative enforcement в
принятом M1–M3 scope отсутствует. M4 подключается только явно при подтверждённом
`ENFORCED` policy и независимой биржевой candidate quote; это не разрешение live
Sandbox gate. Локальный M3 gate: core targeted
`171 passed`, isolated-runtime matrix `6 passed`, full regression `663 passed`,
changed-file Ruff PASS. Первая реальная runtime-выборка предъявлена: coverage
100% и unexplained target drift 0, но из-за двух `HALTED` решений ещё не принята
как финальный acceptance evidence и остаётся операционным gate перед M4.

Read-only отчёт запускается через
`python tools/v3_9_portfolio_risk_report.py <runtime> --account-id <id>`.
Опциональный metadata JSON имеет schema `version: 1` и обязательный соседний
SHA-256 sidecar `<name>.sha256`; отсутствующие lot/currency/price остаются
явными unknown и дают блокирующий status, silent defaults запрещены.
Shadow coverage/drift читается без изменения runtime SQLite через
`python tools/v3_9_portfolio_risk_shadow_report.py <runtime> --account-id <id>`.
Перед естественным M3 Sandbox-наблюдением существующий профиль переводится в
shadow-ready состояние только явной операторской командой; она сохраняет все
существующие финансовые лимиты и включает только `OBSERVE_ONLY`:

`python risk_profile_tool.py confirm-portfolio-shadow --file <runtime>/risk_profiles.json --mode SANDBOX_EXECUTION --account-id <id> --confirmation "CONFIRM PORTFOLIO RISK POLICY"`.

Команда не создаёт заявку. После неё следует дождаться естественного Strategy
proposal и повторить shadow report; к M4 допускается только отчёт с coverage
100%, `UNAVAILABLE=0` и unexplained drift 0.

Отдельный checksummed runtime для этого gate создаётся транзакционно и без
копирования `.env`, логов или истории broker orders:

`python tools/v3_9_prepare_shadow_runtime.py apply --source-runtime-dir <accepted-v3.8-runtime> --runtime-dir <new-v3.9-runtime> --confirm "PREPARE ISOLATED V3.9 SHADOW RUNTIME"`.

Seed сохраняет canonical portfolio и двухинструментные конфигурации, извлекает
проверенный lot size из принятой Central history в отдельный checksummed
`portfolio_risk_metadata.json`, но создаёт пустые Central/Risk counters/journal
и остановленные instrument runtimes. Portfolio Policy намеренно остаётся
`CONFIGURATION_REQUIRED`/`OBSERVE_ONLY`; seed не создаёт Strategy proposal,
Central intent или Sandbox POST. Seed-manifest и metadata входят в проверяемый
token-free runtime backup и корректно восстанавливаются вместе с sidecar-хэшами.

Изолированный двухинструментный runtime подготовлен 2026-08-13 из принятого
v3.8 snapshot revision 16; контрольные хэши всех 37 файлов источника до/после
совпали. Начальный read-only отчёт подтвердил `execution_authorized=false`,
`CONFIGURATION_REQUIRED`, `total_eligible_observations=0` и корректный
`INCOMPLETE`. Искусственная заявка не создавалась; следующий отдельный gate —
явная настройка shadow policy, затем естественный Strategy proposal.

После подтверждения policy отдельный configure-gate выполняется точной командой:

`python tools/v3_9_configure_shadow_runtimes.py apply --runtime-dir <v3.9-runtime> --confirm "CONFIGURE V3.9 SHADOW RUNTIMES"`.

Gate проверяет checksummed seed/configuration, canonical lots, UID/lot metadata,
пустую Central history, `READY/OBSERVE_ONLY`, Risk kill/resync и secure Windows
Credential Manager. Он идемпотентно добавляет только checksummed config-manifest;
оба InstrumentRuntime остаются `STOPPED`, Risk baselines — `UNINITIALIZED` до
первого свежего Risk evaluation. Strategy proposal, Central intent и provider
POST не создаются. Реальный gate LKOH/SBER пройден; существующие runtime-файлы
не изменились, token-free post-config backup проверен. Полная регрессия:
`669 passed`.

Следующий отдельный gate реализован командой
`python tools/v3_9_start_shadow_runtimes.py apply --runtime-dir <v3.9-runtime> --confirm "START V3.9 SHADOW RUNTIMES"`.
Он выполняет read-only provider preflight (account/instrument identity, lot size,
portfolio и orders), отказывается от любого position drift или active/uncertain
order, публикует свежую canonical reconciliation, инициализирует только pristine
Risk baselines и атомарно переводит весь checksummed runtime set в `ACTIVE`.
Strategy proposal, Central intent, broker mutation и order submission не входят
в этот gate. Локальные проверки: targeted `61 passed`, full `676 passed`, Ruff
PASS.

Реальный preview 2026-08-13 остановлен fail-closed до любых записей: broker
сообщил `LKOH=0`, тогда как sealed canonical/runtime сохраняет `LKOH=1` с target
1 и ownership `ATTRIBUTED`; SBER остаётся flat. Runtime оставлен `STOPPED`, Risk
baselines `UNINITIALIZED`, EventJournal пуст, start-manifest не создан. Перед
повтором START требуется отдельное явное acknowledgement внешнего закрытия LKOH;
START не усыновляет внешнее изменение позиции автоматически.

Отдельный recovery-gate `ACK EXTERNAL CLOSE LKOH 0` выполнен 2026-08-13 через
`tools/v3_9_ack_external_close.py`. Повторный provider preflight подтвердил
LKOH/SBER flat и отсутствие broker orders. Canonical revision 17 содержит
LKOH target 0, ownership `FLAT`, origin `EXTERNAL`, reconciliation `MATCHED`;
оба InstrumentRuntime синхронизированы на 0, но оставлены `STOPPED`. RiskState
остался pristine, Central — пустым, broker mutation/order submission не было.
Повторный START preview PASS. Post-ACK backup проверен; полный gate:
`681 passed`, Ruff PASS.

Повторный gate `START V3.9 SHADOW RUNTIMES` выполнен 2026-08-13 после ACK.
Fresh provider preflight подтвердил LKOH/SBER flat, UID/lot identity и 0 broker
orders. Canonical revision 18 — `FRESH`, неблокирующая; Risk baselines
инициализированы из того же snapshot и имеют `READY`, counters 0, no kill/resync.
Оба InstrumentRuntime — `ACTIVE`, current lots 0, pending 0; Central остаётся
пустым. Strategy proposal, Central intent и broker order не создавались.
Идемпотентный повтор вернул `ALREADY_STARTED` без записей. Token-free post-START
backup проверен как VALID.

Для обслуживания `ACTIVE` runtimes без искусственной заявки добавлен
`tools/v3_9_run_shadow_observation.py`. `preview` выполняет только provider GET и
не сохраняет вычисленные Strategy proposals. Реальный preview получил последние
закрытые свечи LKOH 30m `18:30 UTC` и SBER 1h `18:00 UTC`; обе естественные цели —
`HOLD=0`, `writes_performed=false`, Central/provider mutation и execution
authorization отсутствуют. Запись M3 evidence отделена точной фразой
`RUN V3.9 SHADOW OBSERVATION`; путь принудительно выходит до Central mutation и
не содержит broker POST.

Первый apply выполнен 2026-08-13 на естественных закрытых свечах LKOH 30m
`19:00 UTC` и SBER 1h `18:00 UTC`: записаны две append-only shadow observations,
coverage `2/2`, `UNAVAILABLE=0`, target drift `MATCH=2`, unexplained drift `0`.
Обе цели остались `HOLD=0`; Central revision/history/reservations и broker orders
не изменились. Формальный shadow report имеет `PASS`, но обе prospective decision
получили `HALTED`: штатный `CANDIDATE_PRICE_STALE` из-за позднего запуска и
ложный `SNAPSHOT_FROM_FUTURE` из-за времени, взятого до provider reconciliation.
Runner исправлен: evaluation time теперь фиксируется после свежего canonical
snapshot; операторский JSON больше не раскрывает account/runtime/instrument IDs.
Исходные события не переписывались. Для финального M3 acceptance нужна новая
естественная свежая свеча и повторный observation gate; M4 остаётся закрыт.
Post-fix verification: targeted `28 passed`, full `685 passed`, Ruff PASS;
token-free evidence backup проверен как VALID.

Повторный gate `RUN V3.9 SHADOW OBSERVATION` получил новые закрытые свечи LKOH
30m `19:30 UTC` и SBER 1h `19:00 UTC` и записал ещё две `HOLD=0` observations.
Scheduler завершил 8/8 действий, `SNAPSHOT_FROM_FUTURE` больше не возник;
сводный отчёт: coverage `4/4`, `UNAVAILABLE=0`, `MATCH=4`, unexplained drift 0.
Central revision 0, intents/reservations 0, Risk order count/turnover 0, broker
POST 0. Обе новые decision всё ещё `HALTED/CANDIDATE_PRICE_STALE`.

Review выявил контрактный blocker: исторический `HistoricCandle` не предоставляет
timestamp сделки для своей close price, а его `time` является временем свечи и
не подходит для 300-секундного Portfolio Risk freshness proof на 30m/1h.
Исправление использует официальный `GetLastPrices`: отдельный immutable candidate
quote содержит согласованные exchange price, UTC timestamp и source; unknown,
invalid, stale и future quote остаются fail-closed. Operational `observe_only`
больше не имеет fallback на candle time; legacy fallback сохранён только в
неизменённом v3.8 queue-path и не допускается в M3/M4 acceptance.

Реальный read-only preview подтвердил Sandbox contract: LKOH/SBER получили
`TBANK_LAST_PRICE_EXCHANGE` timestamps `20:32:04/20:32:20 UTC`, LKOH новую свечу
`20:00 UTC`, обе стратегии дали `HOLD=0`; `writes_performed=false`, Central и
broker не изменились. Targeted `86 passed`, full `689 passed`, Ruff/compileall
PASS. Новый apply требует отдельного `RUN V3.9 SHADOW OBSERVATION`; M4 закрыт.

Exact-confirmation gate затем выполнен на новой естественной LKOH 30m свече
`20:00 UTC`. Записано одно новое `HOLD=0` observation: `EVALUATED`, prospective
decision `PASS`, hard blocks отсутствуют, drift `MATCH`, unexplained drift false;
candidate quote имеет source `TBANK_LAST_PRICE_EXCHANGE` и timestamp
`20:42:41 UTC`. SBER корректно не создал повторное observation, потому что его
последняя закрытая 1h свеча осталась уже обработанной `19:00 UTC`. Aggregate
read-only report: coverage `5/5`, `UNAVAILABLE=0`, `MATCH=5`, unexplained drift
0, execution authorization false. Central state byte-identical pre-gate backup;
canonical revision/позиции/cash и Risk counters/reservations экономически не
изменились — обновились только reconciliation/evaluation timestamps и
transaction metadata. Оба runtime `ACTIVE`, pending orders 0. Post-gate
token-free backup `v3_9_shadow_observation_clean_quote_2026-08-13.zip` проверен:
VALID, 14 entries, warnings/errors 0. Clean candidate-quote path подтверждён на
LKOH; отдельного нового eligible SBER proposal в этом цикле не было, поэтому
двухинструментный clean sample остаётся неполным. M4 остаётся закрыт до review.

На следующем exact-confirmation gate 2026-08-14 получена полная clean-выборка
по обоим runtime. LKOH 30m `06:30 UTC` дал естественный `HOLD=0`, SBER 1h
`06:00 UTC` — естественный `BUY`, target 1. Оба результата `EVALUATED/PASS`,
shadow approved target совпал с v3.8 (`0/1`), hard blocks/policy halts/
adjustments отсутствуют, drift `MATCH`, unexplained drift false; единственное
предупреждение — принудительный `OBSERVE_ONLY`. Scheduler завершил 8/8 действий,
failure count 0. Aggregate report: coverage `7/7`, `UNAVAILABLE=0`, `MATCH=7`,
unexplained drift 0, execution authorization false.

Pre/post audit подтвердил отсутствие authoritative side effects: Central state
byte-identical, revision/intents/reservations `0`; canonical revision 18 и
позиции LKOH/SBER `0/0`; broker submit не вызывался. Изменились только ожидаемые
reconciliation/evaluation timestamps, transaction metadata и Risk daily-date.
Token-free backup `v3_9_shadow_observation_two_instrument_clean_2026-08-14.zip`
проверен как VALID: 14 entries, warnings/errors 0. M3 runtime evidence — PASS и
готово к финальному review; M4 этим gate автоматически не открывается.

Финальный review M3 завершён без блокирующих замечаний: full regression
`689 passed`, строгий Ruff по v3.9 runtime/tool core и critical Ruff по всем
изменённым Python-файлам — PASS, compileall и `git diff --check` — PASS;
GitHub/TBank/private-key patterns и untracked runtime artifacts отсутствуют.
Проверка M4 boundary для опубликованного M1–M3 scope подтвердила отсутствие
authoritative side effects. В отдельной локальной ветке M4 добавлены immutable
proof, atomic admission/reservation, dispatch-time reproduction, строгая
инвалидация при mutation, lock order, recovery и post-fill recalculation.
Целевой M4 gate: `13 passed`; он дополнительно подтверждает, что активные
резервации входят в projected concentration, а тайм-аут Risk-lock не оставляет
частичной Central mutation. Полная регрессия: `710 passed`; строгий Ruff,
compileall и `git diff --check` — PASS. Automated final review M4 завершён;
Commit/Push и изолированный runtime acceptance ещё не выполнены.

Для следующего gate добавлен
`docs/plans/V3_9_M4_SANDBOX_ACCEPTANCE_RUNBOOK_RU.md`. Отдельный CLI
`tools/v3_9_configure_enforced_runtime.py` разрешает только свежий STOPPED
runtime с пустыми Central/EventJournal и без START-manifest. Preview раскрывает
точные portfolio limits без записей; apply требует
`CONFIGURE V3.9 ENFORCED RUNTIME`, сначала создаёт проверяемый pre-activation
backup и завершает checksummed `ACTIVE` manifest. `ENFORCED` без этого manifest
acceptance CLI трактует fail-closed. Конфигурация не создаёт proposal/intent и
не разрешает provider POST.

Изолированный M4 seed подготовлен из принятого M3 canonical revision 18:
LKOH/SBER `0/0`, runtimes `STOPPED`, pending/Central/EventJournal 0, checksum
PASS, secrets/logs не копировались. Policy остаётся
`CONFIGURATION_REQUIRED/OBSERVE_ONLY`; execution не авторизован. Более ранняя
revision-16 копия сохранена как `superseded-v38-revision16` и не используется.

В новом M4 runtime выполнен `CONFIRM PORTFOLIO RISK POLICY`: policy
`READY/OBSERVE_ONLY`, остальные runtime stores не изменились. Read-only
`CONFIGURE V3.9 SHADOW RUNTIMES` preview — PASS: secure credential provider,
baseline `UNINITIALIZED`, runtimes `STOPPED`, proposal/intent/POST отсутствуют.
На этом preview-этапе configuration manifest ещё не был создан.

Gate `CONFIGURE V3.9 SHADOW RUNTIMES` затем выполнен: manifest checksum PASS,
повтор `ALREADY_CONFIGURED` без записей. M4 `ENFORCED` preview также PASS и
read-only. Portfolio-wide hard caps в текущем подтверждённом профиле явно
остаются `null`; freshness `300s`, warning utilization `0.8`, прежние
single-order limits сохранены.

Gate `CONFIGURE V3.9 ENFORCED RUNTIME` выполнен 2026-08-14. Проверяемый
pre-activation backup — `VALID`, errors 0; policy — `READY/ENFORCED`, checksummed
activation manifest — `ACTIVE` и валиден. Повтор вернул `ALREADY_CONFIGURED`
без записей, manifest и Risk profile byte-identical. Оба InstrumentRuntime
остаются `STOPPED`, baseline `UNINITIALIZED`; proposal/intent/provider POST — 0.
Authoritative acceptance path сконфигурирован, но execution не armed и не
выполнялся. Следующий gate начинается с read-only `status`; SHADOW START к этому
runtime неприменим.

Read-only M4 status выполнен. Выявлен и устранён побочный lock-write общего
acceptance CLI: status теперь не инициализирует recovery manager и напрямую
читает checksummed Central state. Live-повтор подтвердил `READY`, revision 0,
queue/reservation/blocker 0, activation `ACTIVE`, checksum PASS и строго
`runtime_files_changed=0`. Полная регрессия — `710 passed`, scoped Ruff — PASS.
Ни Strategy proposal, ни Central intent, ни arming/provider API не запускались.

Затем добавлен строго read-only `preview-natural`, закрывающий gap между status
и mutating `prepare-one`. Live M4 preview сверил provider/canonical/runtime lots,
pending orders, UID/lot metadata, закрытые свечи и exchange quotes. LKOH дал
`HOLD → 0`, SBER — естественный `BUY → 1` при current lots 0. Оба runtime
остались `STOPPED`; Central mutation, intent preparation, execution authorization
и broker order submit — false, изменённых runtime-файлов 0. Полная регрессия —
`712 passed`, scoped Ruff — PASS. Intent ещё не подготовлен и требует отдельного
точного подтверждения `PREPARE V3.9 ENFORCED INTENT`; legacy v3.8 confirmation
к активированному M4 runtime не применяется.

`PREPARE V3.9 ENFORCED INTENT` выполнен для естественного SBER `BUY 1`.
Canonical revision 18, single-order Risk и Portfolio Risk — PASS; Central
revision 1 содержит один `QUEUED` intent с finalized proof и reservation
`27876` копеек. SBER runtime `ACTIVE` с одним pending intent, LKOH `STOPPED`.
Broker execution не авторизован, dispatch/order submit не выполнялись.
Независимый reload: activation `ACTIVE`, checksum PASS, изменённых status-gate
файлов 0. Следующий gate — queued restart verification без resubmit.

Queued restart verification выполнен новым процессом через штатный recovery.
Intent остался `QUEUED`, Central revision 1, proof/reservation `27876` копеек и
SBER pending state сохранены; Central bytes и checksum неизменны. Обновилась
только служебная `.lock` metadata, material files changed 0. Secret provider,
broker API, order submit и resubmit не вызывались. Dispatch не armed.

Добавлен отдельный offline `preflight-dispatch`, выполняющий exact queue-head,
canonical, Risk policy/State и authoritative Portfolio Risk revalidation до
секрета, provider API и arming. Live gate завершился fail-closed:
`CANDIDATE_PRICE_STALE; STALE_CANONICAL_SNAPSHOT`; материальное состояние,
Central revision 1, `QUEUED` intent, proof и reservation не изменились, затронута
только `.lock` metadata. Для активированного M4 runtime dispatch теперь требует
отдельные `ARM_V3_9_ENFORCED_EXECUTION=YES` и exact confirmation
`ENABLE V3.9 ENFORCED EXECUTION`; они не применялись. Следующий шаг — отдельная
reauthorization/reprepare свежего proof, не arming и не отправка заявки.
Targeted tests: `48 passed`; full regression: `714 passed`; scoped Ruff: PASS.

Отдельный M4 `reauthorize-one` готов и ожидает exact confirmation
`REAUTHORIZE V3.9 ENFORCED INTENT`. Он заранее запрещает совмещать refresh с
любой v3.8/v3.9 dispatch-arming переменной, обновляет canonical/естественный
proposal/exchange quote и атомарно сохраняет свежие candidate price, reservation
и finalized proof. Изменившийся natural signal приводит только к штатному
replace/cancel; broker mutation и order submit отсутствуют. Targeted tests:
`53 passed`; full regression: `718 passed`; Ruff PASS. Live reauthorization ещё
не запускалась, текущий stale proof и невооружённый dispatch boundary сохранены.

Live `REAUTHORIZE V3.9 ENFORCED INTENT` получил новый natural SBER `HOLD → 0`.
Preflight/Risk PASS, но position change отсутствует, поэтому stale `BUY 1`
отменён как `CANCELLED/OPERATOR_CANCELLED`, новый proof не создавался. Central
revision 2 теперь `READY`, queue/reservation/blocker 0; canonical `FRESH/READY`,
LKOH `STOPPED`, SBER `ACTIVE`/pending 0, activation `ACTIVE`, проверенные
checksum-пары PASS. Arming, broker POST, order submit и resubmit — 0.

Финальный M4 review принят без искусственной заявки: full regression
`718 passed`, изменённый Python scope Ruff PASS, `git diff --check` PASS.
Read-only финальная сверка подтвердила 13/13 checksum-пар, Central queue/
blocker/reservation 0, pending 0 и `VALID` pre-activation backup без ошибок и
предупреждений. Live dispatch/fill сознательно не выполнялся и остаётся за
пределами этого Sandbox acceptance. M1–M4 опубликованы в cumulative draft PR
`#43`; публикация не означает разрешение live execution.

Повторный review PR `#43` устранил fail-open валютный default: sandbox seed
теперь сохраняет только явную checksummed валюту источника либо `unknown`, а M4
authoritative admission без валюты возвращает
`PORTFOLIO_RISK_CURRENCY_UNKNOWN` до Central/Risk mutation. Регрессионные тесты
проверяют оба исхода, а configure/start не активируют runtime с неизвестной
валютой. ROADMAP синхронизирован с фактически реализованным M4. Локальный
correction gate: targeted `77 passed`, Ruff/compileall/diff-check PASS; полный
Windows regression остаётся обязательной повторной проверкой PR CI.

### GitHub Actions CI

Workflow `.github/workflows/ci.yml` запускается для pull request, push в `main`
и вручную через `workflow_dispatch`. Gate использует `windows-latest` и Python
3.12, устанавливает `requirements-dev.txt`, выполняет `pip check`, critical
Ruff по всему `current/`, strict Ruff по v3.9 Portfolio Risk/runtime/tool/test
scope, compileall и полную pytest-регрессию с workspace-local `--basetemp`.
`GITHUB_TOKEN` ограничен `contents: read`; credentials, Sandbox token и broker
API для CI не требуются. Устаревший full-tree Ruff baseline не объявляется
исправленным: вне v3.9 scope CI блокирует только ошибки классов
`E9,F63,F7,F82`.

Локальный CI-equivalent gate от 2026-08-14 прошёл полностью: YAML и permissions
проверены, `pip check` и оба уровня Ruff прошли, Python sources скомпилированы,
полная регрессия завершилась результатом `718 passed`. Для воспроизводимости на
Windows временные пути CI привязаны к `RUNNER_TEMP`; EventJournal гарантированно
закрывает соединения, а checkpointed read-only journal не создаёт SQLite
`-wal`/`-shm` sidecar-файлы.

M1–M4 squash-merged через PR #43 в `main` (`d589f345`); post-merge CI завершён
результатом `721 passed`, pip check, critical/strict Ruff и compileall PASS,
annotations 0. Первый beta1-срез M5.1 реализован в
`agent/v3-9-beta1-recovery-ux` и добавляет единый
`tools/v3_9_risk_control.py`: `inspect`, `explain` и `review-policy` строго
read-only, а global/instrument kill-switch transitions требуют отдельных точных
confirmation-фраз и меняют только checksummed Risk state без proposal, intent
или dispatch. Финальный review исправил canonical uppercase instrument ID,
запрет admission-status для `OBSERVE_ONLY` и обязательность Sandbox account
scope. Targeted gate: `7 passed`; full regression: `728 passed`; pip check,
critical/strict Ruff и compileall PASS. PR #44 снят с draft и squash-merged в
`main` (`ac308660`); post-merge CI: `728 passed`, все gates PASS, annotations 0.

Срез M5.2 реализован в `agent/v3-9-beta1-m5-2-recovery`. Он добавляет persistent detection
`EXTERNAL_CASH_CHANGE`, обновление cash anchor после подтверждённого fill и
двухфазный canonical-proof workflow `prepare → apply`. Apply запрещён при stale
canonical/Risk/Central proof, reservation, pending/uncertain или non-cash
resync source; `prepare` и `apply` повторно проверяют фактический wall-clock
возраст canonical snapshot по конечному policy limit. Turnover/order counters,
kill switches и execution IDs сохраняются.

Isolated token-free runtime gate 2026-08-14 принят на естественном external cash
change без искусственной заявки: read-only `prepare` не изменил stores, отдельный
exact-confirmation `apply` атомарно обновил `risk_state.json` и его `.bak`; другие
material stores не изменились. Два запуска в новых процессах подтвердили
сохранность baselines, очищенный resync gate и отсутствие dispatch/resubmit/
provider POST; повторное использование consumed proof fail-closed. Final-review
correction удерживает trusted cash anchor до отдельного устранения non-cash gate,
сравнивает material delta в целых копейках, закрепляет фактический mutation
inventory и правдиво сообщает уже выполненную RiskState mutation при последующей
ошибке записи CLI output. Post-review correction до любой runtime operation
отклоняет lexical или resolved `--output` внутри `--runtime-dir`, поэтому отчёт
не может перезаписать runtime state. Boundary-inclusive targeted matrix:
`157 passed`; full CI regression: `747 passed`; live Account ID удалён из test
fixtures. M5.2 squash-merged через PR #45 в `main` (`cc02294`); post-merge
GitHub Actions run `31825722096` завершился успешно: `747 passed`, pip check,
critical/strict Ruff и compileall PASS.

M5.3 локально реализован отдельно в
`agent/v3-9-beta1-m5-3-qualification`. Срез добавляет checksum-aware backup
source validation, согласованный restore checksum/last-good, transactional
rollback companions, side-effect-free read-only EventJournal для пустого WAL,
расширенный sanitized support bundle и параметризованный Sandbox-only
standalone layout verifier. Новый read-only
`tools/v3_9_persistence_qualification.py` объединяет runtime, backup, support
bundle и standalone evidence, маскирует Account ID и сохраняет все dispatch/
resubmit/provider POST flags выключенными. Реальный accepted M5.2 runtime
добавил coverage checksummed provenance manifest. После final-review correction
закрыты unknown-account error redaction, backup content/version validation,
orphan recovery rollback, support checksum coverage и structural launcher
contract. Во время фактического disposable restore дополнительно закрыт gap
recovery-only ветки: `UNCHANGED` primary теперь транзакционно восстанавливает
отсутствующий checksum/last-good без перезаписи primary и ложной audit-копии.
Targeted matrix: `57 passed`; full local regression: `771 passed`; pip check,
critical/strict Ruff и compileall PASS. Isolated M5.3 runtime подготовлен byte-identical allowlist-
копированием 31 material-файла; lock/WAL/SHM/secrets не переносились, повторный
read-only inspect — PASS. Verified backup и sanitized support bundle созданы;
standalone artifact честно ограничен `STRUCTURAL_LAYOUT_FIXTURE` без executable.
Пересозданные artifacts и повторный final-review: PASS, failures 0, material
hash changes 0, raw Account ID evidence leaks 0. `RESTORE RUNTIME` принят на
новой disposable-копии v3.8 backup `90609f...cede`: 10/10 entries, checksum
6/6, last-good 5/5, offline inspect `IDLE`, residual/material hash changes 0,
WAL/SHM и raw Account ID leaks 0. Исходный M5.3 runtime не изменён.
Фактический standalone build/launch остаётся gate M6. Следующий отдельный gate —
`COMMIT/PUSH M5.3`.

Основные документы:

- `docs/plans/V3_9_PORTFOLIO_RISK_ENGINE_PLAN_RU.md`;
- `docs/plans/V3_9_M4_SANDBOX_ACCEPTANCE_RUNBOOK_RU.md`;
- `docs/plans/V3_9_M5_1_OPERATOR_CONTROL_RUNBOOK_RU.md`;
- `docs/plans/V3_9_M5_2_EXTERNAL_CASH_RECOVERY_RUNBOOK_RU.md`;
- `docs/plans/V3_9_M5_3_PERSISTENCE_STANDALONE_RUNBOOK_RU.md`;
- `docs/project/V3_9_INTERFACE_FREEZE_RU.md`;
- `docs/project/V3_9_ISSUE_PROPOSAL_RU.md`.

## Рабочая связка ChatGPT + Codex

```text
Codex local
  -> implementation / tests / build
  -> push version branch
GitHub
  -> auditable code diff / Issues / evidence
ChatGPT
  -> анализ результатов / архитектурный контроль / acceptance
  -> новые Issues / docs / roadmap / release decision
  -> следующий task для Codex
```

Код новой версии должен быть отправлен в соответствующую version branch до ChatGPT-review. Локальные неподтверждённые результаты сами по себе не считаются acceptance evidence.

## Архитектурные границы v3.7

```text
T-Invest Sandbox only
one executable instrument
long-only
PortfolioState schema 2
canonical-only reads
single writer
real account execution disabled
multi-instrument execution disabled
```

Реальный торговый счёт не разрешён. Переход к multi-instrument execution относится к v3.8 и возможен только после принятия v3.7.0 Stable.

## Структура репозитория

- `releases/` — release records, manifests и SHA-256;
- `docs/releases/` — release/acceptance records;
- `docs/plans/` — планы следующих этапов;
- `docs/project/` — сводный статус, Codex sync gate и review notes;
- `docs/DEVELOPMENT_PROCESS.md` — процесс ChatGPT/Codex/GitHub;
- `docs/ARCHITECTURE.md` — текущая v3.7 и целевая архитектура;
- `ROADMAP.md` — последовательность версий;
- `SECURITY.md` — правила работы с секретами и execution boundary;
- `v3-7-alpha3` — frozen accepted alpha baseline;
- `v3-7-beta1` — принятая beta implementation/evidence branch;
- `v3-7-0-stable` — ветка Stable candidate, merged через PR #36.

`develop` сохраняется как историческая ветка и не является обязательной частью текущего local-Codex workflow.

## Безопасность репозитория

Репозиторий не должен содержать:

```text
.env
API tokens / credentials
Account ID
risk_state.json
robot_state.json
portfolio_state.json
portfolio_legacy_shadow.json
sandbox_diagnostic_state.json
canonical_migration_report*.json
runtime_bootstrap_report.json
trading_events.db*
working logs
runtime/
backups/
support/
unsanitized reports/
```

Использовать только `.env.example`, исходники/тесты, release manifests, SHA-256 и очищенные диагностические материалы.
