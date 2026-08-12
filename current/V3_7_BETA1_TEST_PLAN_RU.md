# План тестирования v3.7-beta1

## Автоматические gate

Запустить `install_and_verify_v3_7_beta1.bat` или `VERIFY_V3_7_BETA1.bat`.

Обязательные результаты:

- version/manifest `0.3.7b1` — PASS;
- full pytest regression — `454 passed`;
- release hygiene и deterministic ZIP audit — PASS;
- compileall — PASS;
- Risk Lab — 8/8 PASS.

Установка, GUI-launch и restart portable-пакета подтверждены пользователем на
Windows. После split-runtime fix сохранённый SANDBOX_EXECUTION profile успешно
загружен; Risk runtime error не повторился.

## Beta1 targeted matrix

1. Warning присутствует в первом snapshot и исчезает после устранения причины.
2. Empty bootstrap warning не переносится в следующий broker observation.
3. SecretProvider present/absent/unavailable различаются без plaintext.
4. `.env_fallback` имеет отдельный status.
5. Transient outage + более поздний safe snapshot даёт WARN, не FAIL.
6. Transient outage без восстановления даёт FAIL.
7. Legacy `NOT_CONFIGURED` читается как `DISABLED`.
8. Shadow `DEGRADED` отображается, но не подменяет canonical readiness.
9. Support manifest содержит только безопасные portfolio observability metadata.
10. Portable GUI использует sibling `runtime` для risk, robot и portfolio state;
    mutable state не создаётся внутри `app`.

## Safety regression

Проверить canonical-only preflight, revision conflict, checksum/lastgood,
pending/uncertain recovery, duplicate submit=0, Risk accounting, MARKET_IDLE,
restart/disconnect recovery и отсутствие private runtime файлов в ZIP.

## Ручной Sandbox smoke

Первый запуск — execution off. Затем один контролируемый BUY→HOLD→SELL при 1 lot
с полным canonical reconciliation. Real-account smoke не допускается.

Результат 2026-08-11 — PASS:

- один полный BUY→HOLD→SELL;
- submitted/accepted/filled — 2/2/2;
- canonical reconciliation и Risk accounting — 2/2;
- duplicate submit, Risk runtime, API и canonical transaction errors — 0;
- финальный state — `READY/FRESH/MATCHED`, `blocking=false`, shadow `OK`,
  warnings `0`.

## Расширенный release smoke — PASS 2026-08-12

- 16 ч 09 мин Sandbox burn-in — PASS;
- 6 Strategy BUY→HOLD→SELL, 12/12 broker orders — PASS;
- intentional disconnect и восстановление fresh `MATCHED` — PASS;
- restart с открытой позицией без повторного POST — PASS;
- `OPEN → MARKET_IDLE → OPEN` — PASS;
- Risk Burn-in report и support bundle — reviewed;
- duplicate submit, fill без canonical reconciliation, execution без Risk
  accounting и unresolved pending/uncertain execution — 0.

Временный burn-in лимит `max_orders_per_day=32` после проверки возвращён к
стандартному значению `4`.

Rollback artifact принятого alpha3 проверен 2026-08-12 в изолированной копии:
SHA-256, manifest/safety contract и release hygiene — PASS; regression —
`443 passed`.
