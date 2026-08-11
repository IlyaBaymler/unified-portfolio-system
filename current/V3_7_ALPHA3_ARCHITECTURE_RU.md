# Архитектура v3.7-alpha3 — Canonical State Cutover

## 1. Цель

Удалить дублирующий legacy read-path из торгового решения и сделать `portfolio_state.json` schema 2 единственным источником портфельных данных для GUI, Preflight, Risk, Execution и Recovery.

## 2. Владельцы данных

### PortfolioState schema 2

- broker actual lots;
- committed Strategy target;
- ownership и origin;
- pending/uncertain order;
- freshness и reconciliation;
- revision/checksum;
- migration и compatibility shadow status.

### robot_state.json

- Strategy/session runtime;
- last processed candle;
- MARKET_IDLE markers;
- непортфельные recovery-данные.

### risk_state.json

- daily order count/turnover;
- loss/drawdown baselines;
- kill switch/resync;
- recorded execution IDs.

### EventJournal

Неизменяемый аудит intent, order lifecycle, fill, reconciliation, canonical transaction, migration и Risk accounting.

## 3. Single-writer

Только `PortfolioTransactionCoordinator` может сохранять PortfolioState:

```text
load revision N
→ validate scope
→ apply transform
→ validate invariants
→ atomic write + checksum
→ revision N/N+1
→ audit event
```

GUI, Risk, Strategy и Execution не пишут JSON напрямую.

## 4. Revision semantics

Revision увеличивается только при изменении decision-relevant состояния:

- actual/target;
- ownership/origin;
- pending/uncertain;
- freshness/reconciliation;
- account/source/migration state.

Изменение отображаемой цены или warning без влияния на решение не обязано увеличивать revision.

## 5. Миграция schema 1 → 2

```text
execution stopped
→ preview
→ account/freshness/reconciliation/pending validation
→ schema1 backup + SHA-256
→ exact confirmation
→ canonical transaction
→ schema2 atomic commit
→ migration report
```

Блокирующие условия:

- execution active;
- account mismatch;
- stale/blocking state;
- TARGET_MISMATCH/UNATTRIBUTED;
- pending/uncertain order;
- unsupported/corrupt schema.

Silent fallback запрещён.

## 6. Canonical-only preflight

Strategy order разрешается только при:

```text
source = CANONICAL
migration = COMPLETED
legacy reads = false
freshness = FRESH
reconciliation = MATCHED
pending/uncertain = none
account/mode/instrument scope = matched
revision unchanged before POST
```

## 7. Post-fill

```text
FILLED
→ broker position reconciliation
→ stage confirmed target
→ canonical broker refresh
→ POST_FILL_CANONICAL_RECONCILED
→ EXECUTION_RECORDED
→ RISK_ACCOUNTED
```

Ошибка canonical refresh не теряет fill, но блокирует новые входы.

## 8. Compatibility shadow

`portfolio_legacy_shadow.json` записывается только из canonical state. Он:

- не читается Preflight/Risk/Execution;
- не исправляет canonical state;
- не разрешает заявку;
- используется только для диагностики и контролируемого rollback.

Ошибка shadow переводит совместимость в `DEGRADED`, но не подменяет источник истины.

## 9. Safety boundary

Sandbox only, один инструмент, long-only. Multi-asset, cash reservation, shorts и live execution отсутствуют.
