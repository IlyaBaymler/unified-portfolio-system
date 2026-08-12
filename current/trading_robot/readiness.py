from __future__ import annotations

"""Production-readiness gate for the v3.6 RC Sandbox-only release."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping
from uuid import uuid4

from .runtime_backup import RuntimeBackupManager
from .portfolio_model import PORTFOLIO_STATE_SCHEMA_VERSION, validate_portfolio_document
from .runtime_integrity import inspect_json_file, inspect_sqlite_file
from .secret_provider import probe_secret_provider


class ReadinessStatus(StrEnum):
    READY_FOR_SANDBOX = "READY_FOR_SANDBOX"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    code: str
    title: str
    status: str
    detail: str
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProductionReadinessReport:
    generated_at: str
    status: ReadinessStatus
    account_id: str | None
    checks: tuple[ReadinessCheck, ...]
    warnings: tuple[str, ...]
    compatibility_shadow_status: str = "DISABLED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "status": str(self.status),
            "account_id": self.account_id,
            "checks": [item.to_dict() for item in self.checks],
            "warnings": list(self.warnings),
            "compatibility_shadow_status": self.compatibility_shadow_status,
        }


class ProductionReadinessEvaluator:
    def __init__(
        self,
        runtime_dir: str | Path,
        *,
        app_version: str,
        backups_dir: str | Path | None = None,
        build_manifest_path: str | Path | None = None,
    ) -> None:
        self.runtime_dir = Path(runtime_dir).resolve()
        self.app_version = str(app_version)
        self.backups_dir = (
            Path(backups_dir).resolve()
            if backups_dir is not None
            else self.runtime_dir / "backups"
        )
        self.build_manifest_path = (
            Path(build_manifest_path).resolve()
            if build_manifest_path is not None
            else self.runtime_dir / "build_manifest.json"
        )

    def evaluate(
        self,
        *,
        account_id: str | None,
        api_status: Mapping[str, Any] | None = None,
    ) -> ProductionReadinessReport:
        checks: list[ReadinessCheck] = []
        warnings: list[str] = []
        normalized_account = str(account_id or "").strip() or None

        json_names = (
            "strategy_profiles.json",
            "risk_profiles.json",
            "risk_state.json",
            "robot_state.json",
            "portfolio_state.json",
            "sandbox_diagnostic_state.json",
        )
        json_reports = {}
        for name in json_names:
            if name == "portfolio_state.json":
                report = inspect_json_file(
                    self.runtime_dir / name,
                    expected_versions={PORTFOLIO_STATE_SCHEMA_VERSION},
                    validator=validate_portfolio_document,
                )
            else:
                report = inspect_json_file(self.runtime_dir / name)
            json_reports[name] = report
        invalid = [
            f"{name}={report.status}"
            for name, report in json_reports.items()
            if not report.valid
        ]
        checks.append(
            ReadinessCheck(
                "RUNTIME_INTEGRITY",
                "Целостность runtime JSON",
                "PASS" if not invalid else "FAIL",
                "Все runtime JSON валидны."
                if not invalid
                else "Проблемные файлы: " + ", ".join(invalid),
                blocking=bool(invalid),
            )
        )

        portfolio_state = self._load_json("portfolio_state.json")
        portfolio_account = ""
        portfolio_blocking = False
        if isinstance(portfolio_state.get("account"), Mapping):
            portfolio_account = str(
                portfolio_state["account"].get("account_id") or ""
            ).strip()
        portfolio_blocking = bool(portfolio_state.get("blocking", False))
        migration = portfolio_state.get("migration")
        migration = migration if isinstance(migration, Mapping) else {}
        portfolio_schema_ok = int(portfolio_state.get("version") or 0) == 2
        portfolio_source_ok = str(
            portfolio_state.get("portfolio_source") or ""
        ).upper() == "CANONICAL"
        migration_ok = (
            str(migration.get("status") or "").upper() == "COMPLETED"
            and not bool(migration.get("legacy_read_path_enabled", True))
        )
        shadow_status = str(
            migration.get("compatibility_shadow_status") or "DISABLED"
        ).upper()
        if shadow_status == "NOT_CONFIGURED":
            shadow_status = "DISABLED"
        if shadow_status not in {"OK", "DEGRADED", "DISABLED"}:
            shadow_status = "DEGRADED"
        portfolio_scope_mismatch = bool(
            normalized_account
            and portfolio_account
            and portfolio_account != normalized_account
        )
        portfolio_gate_failed = (
            portfolio_blocking
            or portfolio_scope_mismatch
            or not portfolio_schema_ok
            or not portfolio_source_ok
            or not migration_ok
        )
        portfolio_detail_parts = [
            f"schema={portfolio_state.get('version', '—')}",
            f"revision={portfolio_state.get('revision', '—')}",
            f"freshness={portfolio_state.get('freshness', '—')}",
            f"state={portfolio_state.get('state_status', '—')}",
            f"source={portfolio_state.get('portfolio_source', '—')}",
            f"migration={migration.get('status', '—')}",
            f"legacy_read={migration.get('legacy_read_path_enabled', '—')}",
            f"shadow={shadow_status}",
            f"account={portfolio_account or 'not-yet-bound'}",
        ]
        if portfolio_scope_mismatch:
            portfolio_detail_parts.append(
                f"selected={normalized_account}; account mismatch"
            )
        checks.append(
            ReadinessCheck(
                "CANONICAL_PORTFOLIO_STATE",
                "Каноническое состояние Portfolio Manager",
                "FAIL" if portfolio_gate_failed else "PASS",
                "; ".join(portfolio_detail_parts),
                blocking=portfolio_gate_failed,
            )
        )

        preflight = self._latest_portfolio_preflight(
            self._load_json("robot_state.json"),
            normalized_account,
        )
        if preflight is None:
            preflight_required = self.app_version.startswith("0.3.7a")
            preflight_status = "WARN" if preflight_required else "PASS"
            preflight_detail = (
                "Portfolio preflight ещё не выполнялся в текущем runtime."
                if preflight_required
                else "Canonical preflight не является обязательным для этой версии."
            )
            preflight_blocking = False
        else:
            context = preflight.get("context")
            context = context if isinstance(context, Mapping) else {}
            preflight_revision = context.get("snapshot_revision")
            canonical_revision = portfolio_state.get("revision")
            revision_matches = (
                preflight_revision is not None
                and canonical_revision is not None
                and int(preflight_revision) == int(canonical_revision)
            )
            passed = str(preflight.get("status") or "").upper() == "PASS"
            preflight_blocking = not passed
            preflight_status = "PASS" if passed and revision_matches else (
                "FAIL" if preflight_blocking else "WARN"
            )
            reasons = preflight.get("reasons") or []
            preflight_detail = (
                f"status={preflight.get('status', '—')}; "
                f"revision={preflight_revision}; canonical={canonical_revision}; "
                f"source={context.get('portfolio_source', '—')}; "
                f"migration={context.get('migration_status', '—')}; "
                f"legacy_read={context.get('legacy_read_path_enabled', '—')}; "
                f"dual_read={((context.get('dual_read') or {}).get('status') if isinstance(context.get('dual_read'), Mapping) else '—')}; "
                f"reasons={reasons or 'none'}"
            )
        checks.append(
            ReadinessCheck(
                "PORTFOLIO_PREFLIGHT",
                "Canonical Portfolio Preflight",
                preflight_status,
                preflight_detail,
                blocking=preflight_blocking,
            )
        )

        writable, writable_detail = self._runtime_writable()
        checks.append(
            ReadinessCheck(
                "RUNTIME_WRITABLE",
                "Доступность runtime-каталога для записи",
                "PASS" if writable else "FAIL",
                writable_detail,
                blocking=not writable,
            )
        )
        try:
            free_bytes = shutil.disk_usage(self.runtime_dir).free
        except OSError:
            free_bytes = 0
        disk_ok = free_bytes >= 100 * 1024 * 1024
        checks.append(
            ReadinessCheck(
                "DISK_SPACE",
                "Свободное место",
                "PASS" if disk_ok else "WARN",
                f"Свободно {free_bytes / (1024 ** 2):.1f} MiB.",
                blocking=False,
            )
        )
        if not disk_ok:
            warnings.append("Свободного места меньше 100 MiB; журнал/backup могут не записаться.")

        journal_report = inspect_sqlite_file(self.runtime_dir / "trading_events.db")
        checks.append(
            ReadinessCheck(
                "JOURNAL_INTEGRITY",
                "Целостность EventJournal",
                "PASS" if journal_report.valid else "FAIL",
                journal_report.detail,
                blocking=not journal_report.valid,
            )
        )

        checks.append(
            ReadinessCheck(
                "ACCOUNT_IDENTITY",
                "Sandbox Account ID",
                "PASS" if normalized_account else "FAIL",
                normalized_account or "Счёт не выбран.",
                blocking=normalized_account is None,
            )
        )

        robot_state = self._load_json("robot_state.json")
        risk_state = self._load_json("risk_state.json")
        if normalized_account:
            known_robot_accounts = self._robot_accounts(robot_state)
            known_risk_accounts = set(
                str(key) for key in (risk_state.get("accounts") or {})
            ) if isinstance(risk_state.get("accounts"), Mapping) else set()
            identity_detail = (
                f"selected={normalized_account}; robot_state="
                f"{'present' if normalized_account in known_robot_accounts else 'not-yet-present'}; "
                f"risk_state={'present' if normalized_account in known_risk_accounts else 'not-yet-present'}"
            )
            checks.append(
                ReadinessCheck(
                    "ACCOUNT_STATE_SCOPE",
                    "Привязка runtime к выбранному счёту",
                    "PASS"
                    if normalized_account in known_robot_accounts
                    or normalized_account in known_risk_accounts
                    else "WARN",
                    identity_detail,
                    blocking=False,
                )
            )

        pending = self._pending_orders(robot_state, normalized_account)
        uncertain = [
            item
            for item in pending
            if str(item.get("lifecycle_state") or "")
            not in {"RISK_ACCOUNTED", "SUBMISSION_FAILED", "REJECTED", "CANCELLED"}
        ]
        pending_details = (
            "Нет незавершённых заявок."
            if not uncertain
            else "; ".join(
                f"{item.get('order_id') or 'no-id'}:{item.get('lifecycle_state') or 'unknown'}"
                for item in uncertain[:5]
            )
        )
        checks.append(
            ReadinessCheck(
                "PENDING_ORDER",
                "Незавершённые/неопределённые заявки",
                "PASS" if not uncertain else "FAIL",
                pending_details,
                blocking=bool(uncertain),
            )
        )

        account_state: dict[str, Any] = {}
        if normalized_account and isinstance(risk_state.get("accounts"), Mapping):
            raw = risk_state["accounts"].get(normalized_account)
            if isinstance(raw, Mapping):
                account_state = dict(raw)
        kill_switch = bool(account_state.get("kill_switch_active"))
        resync = bool(account_state.get("risk_resync_required"))
        checks.append(
            ReadinessCheck(
                "RISK_PERSISTENT_GATE",
                "Kill switch / Risk resync",
                "PASS" if not (kill_switch or resync) else "FAIL",
                "Нет persistent risk-блокировок."
                if not (kill_switch or resync)
                else ", ".join(
                    item
                    for item, active in (
                        ("KILL SWITCH", kill_switch),
                        ("RISK RESYNC", resync),
                    )
                    if active
                ),
                blocking=kill_switch or resync,
            )
        )

        snapshot_at = account_state.get("last_snapshot_at")
        snapshot_age = self._age_seconds(snapshot_at)
        snapshot_ok = snapshot_age is not None and snapshot_age <= 900
        checks.append(
            ReadinessCheck(
                "SNAPSHOT_FRESHNESS",
                "Свежесть подтверждённого портфеля",
                "PASS" if snapshot_ok else "WARN",
                f"Возраст: {snapshot_age:.1f} с."
                if snapshot_age is not None
                else "Подтверждённый snapshot отсутствует.",
                blocking=False,
            )
        )
        if not snapshot_ok:
            warnings.append("Перед торговым циклом требуется свежая reconciliation.")

        api = dict(api_status or {})
        authenticated = api.get("authenticated")
        available = api.get("available")
        if authenticated is None and available is None:
            api_check_status = "WARN"
            api_detail = "API ещё не проверен в текущем GUI-сеансе."
        elif authenticated and available:
            api_check_status = "PASS"
            api_detail = "Аутентификация и API доступны."
        else:
            api_check_status = "FAIL"
            api_detail = str(api.get("detail") or "API недоступен или токен неверен.")
        checks.append(
            ReadinessCheck(
                "API_AVAILABILITY",
                "T-Invest Sandbox API",
                api_check_status,
                api_detail,
                blocking=api_check_status == "FAIL",
            )
        )

        # The readiness gate probes the selected provider itself instead of
        # relying on a GUI-only hint.  Only metadata is retained; the token
        # value never enters the report.
        complete_provider_metadata = all(
            key in api
            for key in (
                "secret_provider",
                "secret_provider_secure",
                "secret_provider_available",
                "secret_present",
            )
        )
        explicit_provider = bool(api.get("secret_provider"))
        probe = probe_secret_provider(self.runtime_dir)
        provider_name = str(api.get("secret_provider") or probe.provider or "unknown")
        provider_secure = api.get("secret_provider_secure")
        if provider_secure is None:
            provider_secure = probe.secure
        provider_available = api.get("secret_provider_available")
        if provider_available is None:
            provider_available = (
                True
                if explicit_provider and authenticated is True
                else probe.available
            )
        secret_present = api.get("secret_present")
        if secret_present is None:
            secret_present = (
                True
                if explicit_provider and authenticated is True
                else probe.credential_present
            )

        provider_mismatch = bool(
            not complete_provider_metadata
            and not (explicit_provider and authenticated is True)
            and api.get("secret_provider")
            and str(api.get("secret_provider")) != str(probe.provider)
        )
        secret_blocking = False
        if provider_available is False:
            secret_status = "FAIL" if authenticated is False else "WARN"
            secret_detail = (
                f"{provider_name}: хранилище недоступно"
                + (f" ({probe.error})" if probe.error else ".")
            )
            secret_blocking = authenticated is False
        elif secret_present is False:
            if authenticated is True:
                secret_status = "WARN"
                secret_detail = (
                    f"{provider_name}: запись не найдена, но API уже "
                    "аутентифицирован; возможен provider mismatch."
                )
            elif authenticated is False:
                secret_status = "FAIL"
                secret_detail = f"{provider_name}: запись токена отсутствует."
                secret_blocking = True
            else:
                secret_status = "WARN"
                secret_detail = (
                    f"{provider_name}: запись токена отсутствует; API ещё "
                    "не проверен."
                )
        elif provider_secure is True:
            if authenticated is True:
                secret_status = "PASS"
                secret_detail = (
                    f"{provider_name}, запись найдена и токен успешно использован."
                )
            elif authenticated is False:
                secret_status = "FAIL"
                secret_detail = (
                    f"{provider_name}, запись найдена, но API-аутентификация "
                    "неуспешна."
                )
                secret_blocking = True
            else:
                secret_status = "WARN"
                secret_detail = (
                    f"{provider_name}, запись найдена; API-аутентификация "
                    "ещё не проверена."
                )
        else:
            secret_status = "WARN"
            secret_detail = (
                f"{provider_name}, запись найдена; .env fallback менее защищён."
            )
            warnings.append(
                "Токен хранится в .env fallback; Credential Manager предпочтителен."
            )

        if provider_mismatch:
            secret_status = "WARN" if secret_status == "PASS" else secret_status
            secret_detail += (
                f" Активный provider ({api.get('secret_provider')}) не совпадает "
                f"с probe ({probe.provider})."
            )
            warnings.append(
                "Обнаружено расхождение между активным и обнаруженным "
                "хранилищем токена."
            )
        checks.append(
            ReadinessCheck(
                "SECRET_PROVIDER",
                "Хранилище токена",
                secret_status,
                secret_detail,
                blocking=secret_blocking,
            )
        )

        latest_backup = self._latest_valid_backup()
        checks.append(
            ReadinessCheck(
                "VALID_BACKUP",
                "Последняя проверенная резервная копия",
                "PASS" if latest_backup else "WARN",
                str(latest_backup) if latest_backup else "Валидная backup-копия не найдена.",
                blocking=False,
            )
        )
        if latest_backup is None:
            warnings.append("Перед длительным Sandbox-сеансом создайте runtime backup.")

        manifest_status, manifest_detail = self._build_manifest_status()
        checks.append(
            ReadinessCheck(
                "BUILD_MANIFEST",
                "Версия и build manifest",
                manifest_status,
                manifest_detail,
                blocking=False,
            )
        )

        if any(item.blocking and item.status == "FAIL" for item in checks):
            status = ReadinessStatus.BLOCKED
        elif any(item.status in {"WARN", "FAIL"} for item in checks):
            status = ReadinessStatus.DEGRADED
        else:
            status = ReadinessStatus.READY_FOR_SANDBOX
        return ProductionReadinessReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            status=status,
            account_id=normalized_account,
            checks=tuple(checks),
            warnings=tuple(warnings),
            compatibility_shadow_status=shadow_status,
        )

    def _runtime_writable(self) -> tuple[bool, str]:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            probe = self.runtime_dir / f".readiness.{os.getpid()}.{uuid4().hex}.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True, f"Запись доступна: {self.runtime_dir}"
        except OSError as exc:
            return False, str(exc)

    def _load_json(self, name: str) -> dict[str, Any]:
        path = self.runtime_dir / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _latest_portfolio_preflight(
        root: Mapping[str, Any],
        account_id: str | None,
    ) -> dict[str, Any] | None:
        bots = root.get("bots")
        if not isinstance(bots, Mapping):
            return None
        candidates: list[dict[str, Any]] = []
        for key, state in bots.items():
            if not isinstance(state, Mapping):
                continue
            if account_id and not str(key).startswith(account_id + "|"):
                continue
            preflight = state.get("last_portfolio_preflight")
            if isinstance(preflight, Mapping):
                candidates.append(dict(preflight))
        if not candidates:
            return None
        def sort_key(item: Mapping[str, Any]) -> str:
            context = item.get("context")
            return str(
                (context.get("evaluated_at") if isinstance(context, Mapping) else "")
                or ""
            )
        return max(candidates, key=sort_key)

    @staticmethod
    def _pending_orders(
        root: Mapping[str, Any],
        account_id: str | None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        bots = root.get("bots")
        if not isinstance(bots, Mapping):
            return result
        for key, state in bots.items():
            if not isinstance(state, Mapping):
                continue
            pending = state.get("pending_order")
            if not isinstance(pending, dict):
                continue
            if account_id:
                key_matches = str(key).startswith(account_id + "|")
                pending_account = str(pending.get("account_id") or "").strip()
                # An unscoped pending order is ambiguous and therefore blocks all
                # account-scoped readiness gates until explicitly resolved.
                if not key_matches and pending_account and pending_account != account_id:
                    continue
            result.append(dict(pending))
        return result

    @staticmethod
    def _robot_accounts(root: Mapping[str, Any]) -> set[str]:
        bots = root.get("bots")
        if not isinstance(bots, Mapping):
            return set()
        return {str(key).split("|", 1)[0] for key in bots if "|" in str(key)}

    @staticmethod
    def _age_seconds(value: Any) -> float | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds(),
        )

    def _build_manifest_status(self) -> tuple[str, str]:
        if not self.build_manifest_path.exists():
            return (
                "WARN" if self.app_version.startswith(("0.3.6rc", "0.3.7a")) else "FAIL",
                f"software_version={self.app_version}; build_manifest.json отсутствует.",
            )
        try:
            document = json.loads(self.build_manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return "FAIL", f"Build manifest не читается: {exc}"
        manifest_version = str(document.get("software_version") or "")
        if manifest_version != self.app_version:
            return "FAIL", f"Версия manifest={manifest_version!r}, package={self.app_version!r}."
        return "PASS", f"software_version={self.app_version}; manifest подтверждён."

    def _latest_valid_backup(self) -> Path | None:
        if not self.backups_dir.exists():
            return None
        manager = RuntimeBackupManager(
            self.runtime_dir,
            app_version=self.app_version,
        )
        candidates = sorted(
            self.backups_dir.glob("*.zip"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in candidates:
            if manager.verify_backup(path).valid:
                return path
        return None
