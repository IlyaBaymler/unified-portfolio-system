"""Explicit, single exact financial recovery tick on an existing desktop runtime.

No dispatch, cancellation, Strategy, arm, runtime migration or environment-secret
fallback. The old cutover CLI remains a separate historical authority surface.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CURRENT = Path(__file__).resolve().parents[1]
if str(CURRENT) not in sys.path:
    sys.path.insert(0, str(CURRENT))

from trading_robot.exact_recovery_tick import run_exact_recovery_tick
from trading_robot.runtime_cash_authority import RuntimeCashAuthorityState, RuntimeCashAuthorityStore


def recover_existing_runtime(root: Path, *, execution_order_type: str) -> dict[str, Any]:
    """Compose the shipped owners, then run only the guarded recovery helper."""
    if execution_order_type != "MARKET" or not root.is_dir():
        raise ValueError("EXACT_RECOVERY_CLI_ARGUMENT_INVALID")
    # Preflight before composing transport/stores; no implicit bootstrap.
    record = RuntimeCashAuthorityStore(root).load(allow_missing_legacy=False)
    if record.state is not RuntimeCashAuthorityState.EXACT_CASH_DISPATCH_PENDING:
        raise ValueError("EXACT_RECOVERY_CLI_NOT_PENDING")
    import desktop_gui

    previous = desktop_gui._PRODUCTION_COMPOSITION
    controller = None
    try:
        controller = desktop_gui._compose_production_gui_runtime(
            root, execution_order_type=execution_order_type,
        )
        controller.require_execution_observable()
        controller.validate_metadata_binding()
        result = run_exact_recovery_tick(
            controller.execution_adapter,
            recovery=controller.cycle_source.portfolio_recovery,
        )
        return result.to_canonical_dict()
    finally:
        # A CLI-owned composition is disposable; never close a borrowed GUI's
        # connections when this callable is used in a process with an open GUI.
        if previous is None and controller is not None:
            try:
                controller.execution_adapter.cl7_ledger_store.close()
            finally:
                close = getattr(controller.cycle_source.provider, "close", None)
                if callable(close):
                    close()
                desktop_gui._PRODUCTION_COMPOSITION = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    # This is an explicit composition choice, never an automatic conversion of
    # BESTPRICE. The stored request-bound proof is checked by the exact owners.
    parser.add_argument("--execution-order-type", required=True, choices=("MARKET",))
    args = parser.parse_args(argv)
    try:
        payload = recover_existing_runtime(args.runtime_dir, execution_order_type=args.execution_order_type)
    except Exception:
        print(json.dumps({"status": "EXACT_RECOVERY_CLI_BLOCKED", "automatic_retry": False}, sort_keys=True))
        return 2
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
