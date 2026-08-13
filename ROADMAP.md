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

## v3.10.0 — Cash-flow Manager

- пополнения, выводы, дивиденды, купоны, комиссии и налоги;
- CashLedger;
- отделение доходности от внешних потоков;
- бюджет ребалансировки.

## v4.0.0 — Portfolio Supervisor Foundation

- формальный `StrategyRuntime` с identity
  `instrument + strategy + config + timeframe`;
- `StrategyProposal` / `StrategyIntent`;
- deterministic `ConfiguredCandidateSet`;
- TargetPortfolio и RebalancePlan;
- несколько стратегий/timeframe на instrument;
- Capital Allocation;
- performance attribution;
- единый audit trail — Issue #40.

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
