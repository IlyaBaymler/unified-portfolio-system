# План тестирования v3.7-alpha3 — Canonical State Cutover

## 1. Граница

```text
T-Invest Sandbox only
SBER
1 лот
long-only
одна PRIMARY
реальный счёт и multi-asset отключены
```

Перед началом: execution остановлен, pending/uncertain отсутствуют, последний fill reconciled и Risk-accounted, создан проверенный pre-cutover backup.

## 2. Обязательные acceptance-тесты

### Тест 1. Verifier и manifest

Запустить `install_and_verify_v3_7_alpha3.bat`.

Ожидается:

```text
version = 0.3.7a3
PortfolioState schema = 2
source = CANONICAL
mode = canonical-only
legacy reads = false
cutover complete = true
full pytest PASS
alpha3 targeted PASS
Risk Lab 8/8 PASS
```

### Тест 2. Миграция пустого портфеля

Schema 1: actual=0, target=0, MATCHED, no pending. Выполнить preview и cutover.

Ожидается: backup schema1 + SHA, report COMPLETED, schema2/CANONICAL, counters/history не изменены. Повторный запуск — `ALREADY_MIGRATED`.

### Тест 3. Миграция открытой Strategy-позиции

До cutover: actual=1, target=1, ownership=SMA, MATCHED. После cutover и restart: те же actual/target/owner, revision не уменьшилась, повторный BUY отсутствует, естественная SELL проходит.

### Тест 4. Блокировка небезопасной миграции

На копиях runtime проверить: pending, uncertain, TARGET_MISMATCH, UNATTRIBUTED, account mismatch, incomplete fill, stale/corrupt/checksum mismatch.

Ожидается `CANONICAL_MIGRATION_BLOCKED`, schema1 не перезаписана, broker POST=0.

### Тест 5. Canonical-only proof

Подменить legacy reader исключением и выполнить GUI refresh, Readiness, preflight, HOLD, recovery inspect.

```text
legacy portfolio read count = 0
canonical read count > 0
```

Повреждение write-only shadow даёт `COMPATIBILITY_DEGRADED`, но не меняет canonical state.

### Тест 6. Single-writer/revision

Проверить:

- одна business transaction: N→N+1;
- повтор того же transaction_id идемпотентен;
- два writer на revision N: один commit, второй REVISION_CONFLICT;
- revision rollback отклоняется;
- OneDrive lock: retry/recovered либо fail-closed;
- checksum соответствует JSON.

### Тест 7. Strategy BUY→HOLD→SELL

Для BUY/SELL ожидается:

```text
CANONICAL_TRANSACTION_COMMITTED
CANONICAL_ONLY_PREFLIGHT_PASSED
RISK_EVALUATED PASS
INTENT_SAVED
ORDER_SUBMITTED
ORDER_ACCEPTED
FILLED
BROKER_POSITION_RECONCILED
POST_FILL_CANONICAL_RECONCILED
EXECUTION_RECORDED
RISK_ACCOUNTED
```

Финал: actual=0, target=0, ownership inactive, pending=null, MATCHED, duplicate=0.

### Тест 8. Restart с открытой позицией

BUY→stop→close GUI→start→HOLD→SELL.

Недопустимо: повторный BUY, новый request ID для старой свечи, revision rollback, reconstruction из legacy.

### Тест 9. Минимальная crash matrix

Пользовательские representative-точки:

- после TARGET_PROPOSED — старый target переоценивается, POST=0;
- после ORDER_SUBMITTED — lookup прежнего request ID, replay запрещён;
- после FILLED — только canonical reconcile/record/accounting;
- после CANONICAL_RECONCILED — только record/accounting.

Критерии: duplicate submit=0, revision rollback=0, только недостающая фаза.

### Тест 10. External/diagnostic/Risk gates

- manual BUY → EXTERNAL/UNATTRIBUTED, preflight BLOCKED;
- Strategy BUY→diagnostic SELL → явное FILLED, actual=0/target=1, TARGET_MISMATCH;
- `ACK EXTERNAL CLOSE SBER 0` → actual=0/target=0/MATCHED без сброса Risk history;
- kill switch или max orders → no INTENT/POST, policy block WARNING.

### Тест 11. Disconnect, transport и MARKET_IDLE

Disconnect 5–10 минут:

```text
STALE
preflight BLOCKED
api_degraded/circuit_open
orders=0
```

После восстановления: FRESH, новая revision, MATCHED.

`IncompleteRead` → retry/backoff/recovered, не NON_TRANSIENT_API_ERROR.

`OPEN→MARKET_IDLE→OPEN`: ночью orders=0, после открытия fresh preflight, старый candle не повторяется.

### Тест 12. Backup, standalone, single-instance и rollback

Backup содержит schema2, migration report, shadow, Risk/robot states и DB; токен отсутствует. Restore на копии проходит integrity.

Standalone работает без Python. Второй GUI не получает execution ownership.

Rollback alpha3→alpha2 только через pre-cutover backup и при отсутствии сделок после точки backup.

## 3. Итоговый gate

```text
legacy reads during execution = 0
direct canonical writes outside coordinator = 0
duplicate submits = 0
fills without canonical reconciliation = 0
executions without Risk accounting = 0
revision rollback = 0
silent migration fallback = 0
unresolved pending/uncertain = 0
secret leaks = 0
FAIL checks = 0
```

После P0-матрицы рекомендуется одна непрерывная Sandbox-сессия 12–24 часа.

## 4. Сохраняемые материалы

- Trading Events CSV;
- `canonical_migration_report.json`;
- `portfolio_state.json` после migration/BUY/SELL/restart;
- GUI/debug logs;
- Risk Lab JSON/CSV;
- backup manifest;
- build manifest.

`.env` и токен не отправлять.
