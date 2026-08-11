# План тестирования v3.7-beta1

## Автоматические gate

Запустить `install_and_verify_v3_7_beta1.bat` или `VERIFY_V3_7_BETA1.bat`.

Обязательные результаты:

- version/manifest `0.3.7b1` — PASS;
- full pytest regression — `453 passed`;
- release hygiene и deterministic ZIP audit — PASS;
- compileall — PASS;
- Risk Lab — 8/8 PASS.

Перед публичным выпуском отдельно запустить portable GUI на чистой Windows.
Локальная Codex-среда может проверить PyInstaller layout, но её Tcl/Tk runtime
не подходит для GUI smoke.

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

## Safety regression

Проверить canonical-only preflight, revision conflict, checksum/lastgood,
pending/uncertain recovery, duplicate submit=0, Risk accounting, MARKET_IDLE,
restart/disconnect recovery и отсутствие private runtime файлов в ZIP.

## Ручной Sandbox smoke

Первый запуск — execution off. Затем один контролируемый BUY→HOLD→SELL при 1 lot
с полным canonical reconciliation. Real-account smoke не допускается.
