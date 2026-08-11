# v3.7-alpha3 — Acceptance Record

Дата фиксации: 2026-08-11.  
Версия: `0.3.7a3`.  
Этап: Canonical State Cutover.

## Принятые архитектурные решения

- `PortfolioState` schema 2 — единственный авторитетный источник портфельного состояния;
- legacy portfolio reads отключены;
- `PortfolioTransactionCoordinator` — единственный writer;
- compatibility shadow — write-only и не влияет на canonical readiness;
- новая Strategy-заявка требует canonical preflight;
- revision повторно проверяется перед broker POST;
- после fill обязательна canonical reconciliation до завершения lifecycle;
- миграция schema 1 → schema 2 выполняется явно, с preview, backup и подтверждением;
- real account и multi-instrument execution отключены.

## Migration acceptance

Пользователь подтвердил успешное прохождение migration tests без ошибок.

Проверялись:

- preview и cutover;
- schema 1 backup и checksum;
- schema 2 after restart;
- сохранение actual/target/ownership;
- блокировка небезопасных состояний;
- rollback на тестовой копии.

## Burn-in acceptance

Сводка переданных журналов:

| Показатель | Результат |
|---|---:|
| Суммарная длительность сессий | ~15 ч 19 мин |
| Исполнений | 10 |
| Diagnostic executions | 2 |
| Strategy executions | 8 |
| Полных Strategy BUY→SELL | 4 |
| Уникальных orderRequestId | 10 |
| Duplicate execution | 0 |
| Fill без reconciliation | 0 |
| Execution без Risk accounting | 0 |
| Post-fill canonical reconciliation | 8/8 Strategy fills |
| Canonical transactions | 68/68 committed |
| Transaction failures | 0 |
| Revision | 0 → 19 |
| Revision rollback | 0 |
| Revision conflict | 0 |
| Preflight PASS | 37 |
| Preflight BLOCK due runtime defect | 0 |
| Финальная позиция | 0 |
| Финальный target | 0 |

Дополнительно подтверждены:

- DNS/network outage и retries;
- circuit breaker persistence через restart;
- API recovery;
- MARKET_IDLE и последующий fresh preflight;
- stale-decision rejection после открытия рынка;
- отсутствие повторной заявки на старой свече.

## Неблокирующие замечания для beta1

1. Старые Portfolio warnings могли сохраняться после перехода в `READY/MATCHED`.
2. Bootstrap report мог ошибочно предупреждать об отсутствии токена при использовании Credential Manager.
3. Recovered transient outage должен отображаться как infrastructure WARN/PASS, а не application FAIL.
4. Write-only compatibility shadow требует отдельного статуса observability.

Замечания не затрагивают order lifecycle, Risk accounting, canonical transaction protocol или migration safety.

## Решение

`v3.7-alpha3` принята. Отдельный `alpha3.1` не выпускается. Исправления observability включаются непосредственно в `v3.7-beta1`.
