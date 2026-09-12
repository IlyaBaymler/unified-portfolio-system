# V3.10 Issue #72 — GUI/runtime implementation review map

Contract authority: `5bb7569992a93817fad939a7fc8919444001b8c0`,
tree `6e843c504ea91033c1226a0223ea0578256b80b4`.

Этот документ описывает локальную implementation-кандидатуру. Он не является acceptance,
publication, provider-access или experiment authority.

| Предмет | Решение | Accepted owner | Mutation boundary | Path | Oracle |
|---|---|---|---|---|---|
| Account-level Start/Stop | REFACTOR | ConfiguredExecutionSet + GlobalScheduler | one full-registry CAS | `gui_runtime_controller.py`, `global_scheduler.py`, `instrument_runtime.py` | I72-03..05 |
| Single-instrument GUI bot | REMOVE | none | none | `desktop_gui.py` | I72-13 |
| Strategy proposal | REFACTOR | accepted strategy hook | CentralOrderCoordinator | `gui_runtime_controller.py` | I72-15 |
| Provider mutation | KEEP | SandboxExecutionAdapter | adapter only after CL7 locked proof | `gui_runtime_controller.py` | I72-14, I72-16..18 |
| CL7 authority | KEEP | RuntimeCashAuthority | read-only Start gate | `gui_runtime_controller.py` | I72-17 |
| Portfolio Risk | REFACTOR | one non-null PortfolioRiskRuntime | Central + adapter share one object | `gui_runtime_controller.py` | I72-18 |
| Runtime JSON access | REFACTOR | InstrumentRuntimeStore | checksum-managed load/CAS | `instrument_runtime.py` | I72-03..05 |
| Dashboard calculations | REMOVE | Portfolio/Central/Risk/CL7 snapshots | copy/join/format only | `dashboard_view.py` | I72-06..12 |
| Multi-position rendering | KEEP | PortfolioState | read-only | `dashboard_view.py`, `desktop_gui.py` | I72-06, I72-12 |
| Diagnostic snapshot/recovery | KEEP | diagnostics recovery surface | no submit/close callback | `desktop_gui.py` | I72-14 |
| Diagnostic BUY/SELL/close | REMOVE | none | no Tk command | `desktop_gui.py` | I72-14 |
| Risk policy editor | REMOVE | Risk operator tools | no GUI writer | `desktop_gui.py` | I72-25..26 |
| Risk/Central/CL7 visibility | REFACTOR | accepted owner snapshots | read-only | `dashboard_view.py` | I72-08..11 |
| Disconnect/market state | REFACTOR | GuiRuntimeController | process-local pause only | `gui_runtime_controller.py` | I72-19..23 |
| Refresh errors/modal dialogs | REFACTOR | one status surface | bounded queue update | `desktop_gui.py` | I72-23 |
| Account identity in GUI | REFACTOR | CL7 scope hash | raw ID remains internal only | `desktop_gui.py` | I72-24, I72-29 |
| Stale milestone labels | REMOVE | package/manifest version | display only | `desktop_gui.py` | I72-30 |
| Account cleanup | DEFER | separate experiment | no implementation call | cleanup runbook | I72-27..29 |
| Q0 evidence | KEEP | committed Git tree + executed producers | canonical evidence only | `v3_10_issue72_q0_evidence.py` | I72-01..32 |

## Review boundaries

- Start validates all owner snapshots before one group CAS.
- A CAS mismatch writes zero bytes and leaves scheduler memory unchanged.
- Stop prevents new work and preserves Central/provider/CL7 custody.
- Restart restores persisted runtimes; pending, submitted or uncertain custody blocks proposal.
- `OPEN → MARKET_IDLE → OPEN` retains one scheduler instance and its watermarks.
- The committed AST must expose no active GUI call to `post_order`,
  `post_order_once`, diagnostic `execute`, diagnostic close, or RiskPolicy apply.
- Offline implementation verification performs zero provider calls and zero provider
  mutations.

## Current disposition

`IMPLEMENTED LOCALLY / NOT REVIEWED / NOT ACCEPTED / NOT PUBLISHED`.
`START EXPERIMENT` has not been given.
