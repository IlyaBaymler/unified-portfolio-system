# Issue #72 — multi-instrument Sandbox qualification runbook

## Offline Preparation Stage

1. Проверить exact contract ancestry и 14-path custody.
2. Выполнить dedicated synthetic suite для 1–3 инструментов.
3. Доказать group CAS, zero partial start, full-set stop, restart/recovery-first,
   disconnect и `OPEN → MARKET_IDLE → OPEN`.
4. Проверить dashboard с тремя строками и двумя одновременными ненулевыми позициями.
5. Выполнить committed AST reachability check.
6. Выполнить полный predecessor regression и зафиксировать exact failure identity.

Offline preparation использует только synthetic identifiers и fake owner snapshots:
`provider calls = 0`, `provider mutations = 0`. Она не является live qualification.

## Separate live gate

Live Sandbox действия запрещены до отдельного решения, exact accepted implementation,
accepted account disposition и явной команды пользователя `START EXPERIMENT`.

После этой команды отдельный bounded runner должен:

1. привязать masked/hash account scope без raw Account ID в evidence;
2. прочитать fresh Portfolio/Central/Risk/CL7 evidence;
3. подтвердить `EXACT_CASH_ARMED` и отсутствие pending dispatch proof;
4. запустить полный configured set одной account-level командой;
5. проверить несколько canonical positions и все configured instruments;
6. выполнить restart/session-B и recovery-first cases;
7. остановить полный set, сохранив unresolved custody;
8. сформировать sanitized evidence и fresh post-run read-back.

Любое расхождение scope, custody, Risk, CL7 или unexpected provider result завершает run
fail-closed без automatic resubmit.
