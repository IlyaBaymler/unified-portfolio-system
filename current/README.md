# MOEX Research Robot v3.9.0 Stable Candidate

## V3.10 Issue #72 local implementation candidate

Основной GUI Sandbox-path теперь проектируется как один account-level
`ConfiguredExecutionSet`: Start/Stop проходят через `GuiRuntimeController` и одну
full-registry CAS-транзакцию `GlobalScheduler`. Proposal допускается только после
canonical Portfolio, Central, Risk, same-instance Portfolio Risk и pre-existing
`EXACT_CASH_ARMED` CL7 validation; provider mutation остаётся только в
`SandboxExecutionAdapter`.

GUI Risk Policy является read-only. Диагностические submit/close callbacks удалены,
dashboard показывает отдельные actual/target, reconciliation, Central statuses, Risk и
CL7 fields. Account identifiers отображаются только как scope hashes.

Статус: `LOCAL IMPLEMENTATION CANDIDATE / NOT REVIEWED / NOT ACCEPTED / NOT PUBLISHED`.
Provider access и `START EXPERIMENT` не авторизованы.

Исследовательский менеджер инвестиционного портфеля для **T-Invest Sandbox**.
Версия `0.3.9` фиксирует реализацию Portfolio Risk Engine M1–M5.3 из exact
baseline commit `cddd80f3caf7191ecf2f85df9e1ccfea97af6cc4` как кандидата M6.

```text
GUI:                         v3.9.0
Python package:              0.3.9
Release channel:             stable
Qualification status:        candidate
PortfolioState:              schema 2, canonical-only
Portfolio Risk state:        schema 4, ENFORCED
Execution:                   configured instruments, T-Invest Sandbox only
Real account execution:      disabled
Automatic position adoption: disabled
```

## Зафиксированная архитектура

- PortfolioState остаётся canonical actual-position/cash truth;
- CentralOrderManager единолично владеет queue, reservations и dispatch proof;
- Portfolio Risk оценивает current/projected account-wide exposure и выполняет
  pre-dispatch revalidation;
- stale/mixed canonical, queue, policy или Risk proof блокирует увеличение риска;
- external cash/position change требует явного reconciliation/resync;
- global и instrument kill switches изменяются только exact-confirmation
  operator-командами;
- provider POST доступен только через Sandbox ExecutionAdapter;
- real-account execution отсутствует.

## Qualification boundary

M5.3 persistence/restore/support/structural-layout review и post-merge CI уже
пройдены. Это не заменяет M6: actual standalone build/launch, clean install,
upgrade/rollback, restart/disconnect/partial-fill matrix, manual kill switches и
24–48-часовой burn-in на 2–3 инструментах пока не приняты.

`build_manifest.json` намеренно сохраняет `status=candidate`,
`user_acceptance=false` и все ручные M6 flags в `false`. Tag/release и
real-account permission этой веткой не создаются.

Автоматический M6 source gate выполнен: targeted группы `27 + 112 + 46`, full
regression `780 passed`, pip check, release hygiene, critical/strict Ruff,
compileall и candidate safety boundary — PASS.

Два deterministic source ZIP прошли byte/hash, root/path, sorted member,
timestamp/mode, `ZIP_CONTENTS.txt`, manifest и private/runtime scan. Exact
artifact hashes хранятся во внешнем evidence index.

## Быстрый запуск Windows

```bat
install_and_verify_v3_9_0.bat
run_gui.bat
```

Portable-кандидат создаётся `BUILD_STANDALONE.bat`; запускать его нужно из новой
папки через `MOEX Research Robot.bat`. До Sandbox Execution проверьте точный
Sandbox account, свежий canonical snapshot, пустые pending/uncertain и Risk
`READY/ENFORCED` без `risk_resync_required`.

## Документация

- `V3_9_0_STABLE_ARCHITECTURE_RU.md` — frozen ownership/safety boundary;
- `V3_9_0_STABLE_TEST_PLAN_RU.md` — M6 qualification matrix;
- `V3_9_0_STABLE_RECOVERY_RUNBOOK_RU.md` — restart/restore/rollback;
- `UPDATE_TO_V3_9_0_STABLE.md` — clean install и upgrade;
- `RELEASE_MANIFEST_V3_9_0_STABLE.txt` — честный статус candidate gates.
