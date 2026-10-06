"""One explicit selected-v4 controller action on an EXISTING sandbox runtime.

No runtime bootstrap/migration, no implicit resync approval, no automatic arm.
A tick on an explicitly ARMED runtime may perform its single authorized POST.
Native Tk Start/controls are not changed by this command.
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

from trading_robot.runtime_cash_authority import RuntimeCashAuthorityStore
from trading_robot.versioned_runtime_route import VersionedRouteError
from trading_robot import cash_observation_versions as versions


def run_existing_runtime(root: Path, *, selected_root: Path, selection_sha256: str,
                         expected_authority_sha256: str, instrument_id: str,
                         execution_order_type: str, action: str = "tick",
                         confirmation: str | None = None, intent_id: str | None = None,
                         plan_sha256: str | None = None) -> dict[str, Any]:
    """Compose once, perform one action, close only the composition we own."""
    if (execution_order_type != "MARKET" or not root.is_dir() or not selected_root.is_dir()
            or action not in {"tick", "arm", "disarm", "prepare-resync", "confirm-resync"}):
        raise VersionedRouteError("V4_ROUTE_CLI_ARGUMENT_INVALID")
    versions._hash(selection_sha256)
    versions._hash(expected_authority_sha256)
    import desktop_gui
    # Refuse before accessing/replacing any borrowed GUI composition.
    if desktop_gui._PRODUCTION_COMPOSITION is not None:
        raise VersionedRouteError("V4_ROUTE_CLI_GUI_ALREADY_COMPOSED")
    record = RuntimeCashAuthorityStore(root).load(allow_missing_legacy=False)
    if record.sha256 != expected_authority_sha256 or not record.state.name.startswith("EXACT_CASH_VERSIONED_"):
        raise VersionedRouteError("V4_ROUTE_CLI_CHECKPOINT_MISMATCH")
    controller = None
    try:
        controller = desktop_gui._compose_production_gui_runtime(
            root, execution_order_type=execution_order_type, require_new=True)
        route = controller.bind_versioned_runtime(target_root=selected_root, selection_sha256=selection_sha256,
            expected_authority_sha256=expected_authority_sha256, instrument_id=instrument_id)
        if action == "tick":
            tick = controller.run_cycle()
            return {"tick": tick.to_dict(), "result": route.last_result.public_summary()}
        if action == "arm":
            if not confirmation or not intent_id:
                raise VersionedRouteError("V4_ROUTE_CLI_CONFIRMATION_REQUIRED")
            return route.arm(intent_id=intent_id, confirmation=confirmation).public_summary()
        if action == "disarm":
            return route.disarm(arm_sha256=versions._hash(plan_sha256)).public_summary()
        if action == "prepare-resync":
            plan = route.prepare_resync()
            return {"status": "EXPLICIT_CASH_FLOW_CONFIRMATION_REQUIRED", "plan_sha256": plan.plan_sha256,
                    "confirmation": plan.confirmation, "authority_sha256": plan.before_authority_sha256}
        if not confirmation:
            raise VersionedRouteError("V4_ROUTE_CLI_CONFIRMATION_REQUIRED")
        return route.confirm_resync(plan_sha256=versions._hash(plan_sha256), confirmation=confirmation).public_summary()
    finally:
        if controller is not None:
            try:
                controller.execution_adapter.cl7_ledger_store.close()
            finally:
                close = getattr(controller.cycle_source.provider, "close", None)
                try:
                    if callable(close):
                        close()
                finally:
                    with desktop_gui._PRODUCTION_COMPOSITION_LOCK:
                        active = desktop_gui._PRODUCTION_COMPOSITION
                        if active is not None and active[1] is controller:
                            desktop_gui._PRODUCTION_COMPOSITION = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    parser.add_argument("--selected-root", required=True, type=Path)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--authority-sha256", required=True)
    parser.add_argument("--instrument-id", required=True)
    parser.add_argument("--execution-order-type", required=True, choices=("MARKET",))
    parser.add_argument("action", choices=("tick", "arm", "disarm", "prepare-resync", "confirm-resync"))
    parser.add_argument("--confirmation")
    parser.add_argument("--intent-id")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args(argv)
    try:
        result = run_existing_runtime(args.runtime_dir, selected_root=args.selected_root,
            selection_sha256=args.selection_sha256, expected_authority_sha256=args.authority_sha256,
            instrument_id=args.instrument_id, execution_order_type=args.execution_order_type,
            action=args.action, confirmation=args.confirmation, intent_id=args.intent_id, plan_sha256=args.plan_sha256)
    except Exception:
        print(json.dumps({"status": "VERSIONED_CLI_BLOCKED", "automatic_retry": False}))
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
