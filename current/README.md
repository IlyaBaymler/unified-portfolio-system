# MOEX Research Robot v3.10.0 Stable Release Candidate

Исследовательский менеджер инвестиционного портфеля для **T-Invest Sandbox**.

```text
Python package:              0.3.10
Display identity:            v3.10.0
Release channel:             stable
Release-cut status:          candidate
Stable acceptance:           not granted
Publication:                 not authorized
Real-account execution:      disabled
```

Release cut построен от exact accepted CL8 qualification-adoption predecessor
`7a569eadfb2a99c5314ae43d24da0dee47819d6c` с tree
`d4f6bf5d1b00f4b944ac0aece669a00cd72b5847`. Сам release cut ещё должен пройти
отдельный exact-head review, Q1–Q8 qualification и explicit Stable acceptance.

## Денежный контур v3.10

- CL1 задаёт exact `Money`, identity и append-only CashLedger semantics;
- CL2 владеет durable append-only persistence и OperationInbox;
- CL3 преобразует read-only provider observations в deterministic inputs;
- CL4 создаёт opening proof и shadow reconciliation без execution authority;
- CL5 вычисляет immutable CashAvailability proof без владения деньгами;
- CL6 передаёт cash context только в Reporting и Portfolio Risk;
- CL7 владеет durable runtime cutover/recovery state machine и запрещает
  automatic resubmit после ambiguous provider outcome;
- accepted Issue #72 связывает GUI/runtime с account-level configured set,
  Central, Risk и exact-cash authority без второго mutation owner.

`MATCHED`, `READY` или `EXACT_CASH_ARMED` сами по себе не являются приказом на
сделку. Provider POST остаётся за существующим Sandbox execution boundary после
всех Central/Risk/CL7 проверок. Real-account endpoint отсутствует.

## Текущий qualification gate

Q0 принят на exact Issue #72 `e27204ad110db36b8ace540bd0738874fab69565`.
Qualification infrastructure принята и интегрирована. Q1–Q7, artifact hashes,
Q8 independent release review и Q9 explicit Stable acceptance для нового exact
release candidate пока не зафиксированы. Controlled-clock и Sandbox действия
запускаются только после отдельных Preparation Stage и `START EXPERIMENT`.

## Быстрый локальный запуск Windows

```bat
install_and_verify_v3_10_0.bat
run_gui.bat
```

Portable candidate создаётся `BUILD_STANDALONE.bat`. До любого Sandbox execution
проверьте exact account scope, отсутствие unresolved pending/uncertain outcome,
fresh broker proof, `MATCHED` reconciliation, CashAvailability, Portfolio Risk,
Central reservation/dispatch proof и CL7 final freshness gate.

## Документация

- `V3_10_0_STABLE_ARCHITECTURE_RU.md` — ownership и authority boundaries;
- `V3_10_0_STABLE_TEST_PLAN_RU.md` — Q0–Q9 qualification matrix;
- `V3_10_0_STABLE_RECOVERY_RUNBOOK_RU.md` — restart/recovery/rollback;
- `UPDATE_TO_V3_10_0_STABLE.md` — clean install и upgrade;
- `RELEASE_MANIFEST_V3_10_0_STABLE.txt` — candidate custody и открытые gates.
