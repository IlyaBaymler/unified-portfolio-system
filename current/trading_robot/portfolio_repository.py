from __future__ import annotations

"""Atomic single-writer persistence for canonical v3.7-alpha3 state."""

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .locking import InterProcessFileLock, LockUnavailableError
from .portfolio_model import (
    LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION,
    PORTFOLIO_STATE_SCHEMA_VERSION,
    PortfolioModelError,
    PortfolioState,
    validate_portfolio_document,
)
from .runtime_integrity import FileIntegrityReport, inspect_json_file
from .state_persistence import StatePersistenceError, StateSaveResult, atomic_write_json


class PortfolioRepositoryError(RuntimeError):
    pass


class PortfolioStateMissingError(PortfolioRepositoryError):
    pass


class PortfolioAccountScopeError(PortfolioRepositoryError):
    pass


class PortfolioChecksumError(PortfolioRepositoryError):
    pass


class PortfolioRevisionConflictError(PortfolioRepositoryError):
    pass


class PortfolioRevisionRollbackError(PortfolioRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class PortfolioRepositoryStatus:
    path: str
    exists: bool
    valid: bool
    account_id: str | None
    version: int | None
    sha256: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "valid": self.valid,
            "account_id": self.account_id,
            "version": self.version,
            "sha256": self.sha256,
            "detail": self.detail,
        }


class PortfolioRepository:
    SCHEMA_VERSION = PORTFOLIO_STATE_SCHEMA_VERSION

    def __init__(
        self,
        path: str | Path,
        *,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.checksum_path = self.path.with_name(self.path.name + ".sha256")
        self.last_good_path = self.path.with_name(self.path.name + ".lastgood")
        self.lock_timeout_seconds = max(0.1, float(lock_timeout_seconds))

    def exists(self) -> bool:
        return self.path.exists()

    def raw_document(self) -> Mapping[str, Any]:
        if not self.path.exists():
            raise PortfolioStateMissingError(f"Portfolio state is missing: {self.path}")
        data = self.path.read_bytes()
        self._verify_checksum(data)
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise PortfolioRepositoryError(
                f"Portfolio state is not valid UTF-8 JSON: {exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise PortfolioRepositoryError("Portfolio state root must be an object.")
        return raw

    def raw_schema_version(self) -> int | None:
        if not self.path.exists():
            return None
        raw = self.raw_document()
        try:
            return int(raw.get("version"))
        except (TypeError, ValueError) as exc:
            raise PortfolioRepositoryError("Portfolio state version is invalid.") from exc

    def load(self, *, expected_account_id: str | None = None) -> PortfolioState:
        if not self.path.exists():
            raise PortfolioStateMissingError(f"Portfolio state is missing: {self.path}")
        raw = self.raw_document()
        try:
            state = PortfolioState.from_dict(raw)
        except PortfolioModelError as exc:
            raise PortfolioRepositoryError(str(exc)) from exc
        self._check_account_scope(state, expected_account_id)
        return state

    @contextmanager
    def locked_snapshot(
        self,
        *,
        expected_account_id: str | None = None,
    ) -> Iterator[PortfolioState]:
        """Hold the canonical snapshot stable across M4 authorization work.

        The global M4 lock order is canonical portfolio, Risk profile, Risk
        state, then Central orders.  Callers must not attempt to save through
        this repository while the read lease is held.
        """

        lock = InterProcessFileLock(
            self.lock_path,
            timeout_seconds=self.lock_timeout_seconds,
        )
        try:
            lock.acquire()
        except LockUnavailableError as exc:
            raise PortfolioRepositoryError(
                f"Portfolio state is locked by another process: {self.path}"
            ) from exc
        try:
            yield self.load(expected_account_id=expected_account_id)
        finally:
            lock.release()

    def load_optional(
        self,
        *,
        expected_account_id: str | None = None,
    ) -> PortfolioState | None:
        if not self.path.exists():
            return None
        return self.load(expected_account_id=expected_account_id)

    def load_last_good(
        self,
        *,
        expected_account_id: str | None = None,
    ) -> PortfolioState:
        if not self.last_good_path.exists():
            raise PortfolioStateMissingError(
                f"Last-good portfolio state is missing: {self.last_good_path}"
            )
        repository = PortfolioRepository(
            self.last_good_path,
            lock_timeout_seconds=self.lock_timeout_seconds,
        )
        return repository.load(expected_account_id=expected_account_id)

    def save(
        self,
        state: PortfolioState,
        *,
        allow_account_initialization: bool = True,
        expected_revision: int | None = None,
        allow_equal_revision: bool = True,
    ) -> StateSaveResult:
        if not isinstance(state, PortfolioState):
            raise TypeError("state must be PortfolioState")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock = InterProcessFileLock(
                self.lock_path,
                timeout_seconds=self.lock_timeout_seconds,
            )
            lock.acquire()
        except LockUnavailableError as exc:
            raise PortfolioRepositoryError(
                f"Portfolio state is locked by another process: {self.path}"
            ) from exc
        try:
            if self.path.exists():
                current = self.load()
                if (
                    current.account_id
                    and state.account_id
                    and current.account_id != state.account_id
                ):
                    raise PortfolioAccountScopeError(
                        "Portfolio state belongs to a different account: "
                        f"{current.account_id} != {state.account_id}."
                    )
                if (
                    not current.account_id
                    and state.account_id
                    and not allow_account_initialization
                ):
                    raise PortfolioAccountScopeError(
                        "Initializing an empty portfolio account scope is disabled."
                    )
                if expected_revision is not None and int(current.revision) != int(
                    expected_revision
                ):
                    raise PortfolioRevisionConflictError(
                        "Portfolio revision conflict: expected "
                        f"{expected_revision}, current {current.revision}."
                    )
                if int(state.revision) < int(current.revision):
                    raise PortfolioRevisionRollbackError(
                        "Portfolio revision rollback is forbidden: "
                        f"{state.revision} < {current.revision}."
                    )
                if (
                    int(state.revision) == int(current.revision)
                    and not allow_equal_revision
                ):
                    raise PortfolioRevisionConflictError(
                        f"Portfolio revision {state.revision} was already committed."
                    )
            try:
                return atomic_write_json(
                    self.path,
                    state.to_dict(),
                    write_checksum=True,
                    keep_last_good=True,
                    validate_roundtrip=True,
                )
            except StatePersistenceError as exc:
                raise PortfolioRepositoryError(str(exc)) from exc
        finally:
            lock.release()

    def initialize_empty(self) -> StateSaveResult | None:
        if self.path.exists():
            self.load()
            return None
        return self.save(PortfolioState.empty())

    def inspect(self) -> FileIntegrityReport:
        def validator(raw: Mapping[str, Any]) -> None:
            validate_portfolio_document(raw)
            data = self.path.read_bytes()
            self._verify_checksum(data)

        return inspect_json_file(
            self.path,
            expected_versions={LEGACY_PORTFOLIO_STATE_SCHEMA_VERSION, self.SCHEMA_VERSION},
            validator=validator,
        )

    def status(self) -> PortfolioRepositoryStatus:
        report = self.inspect()
        account_id: str | None = None
        version: int | None = None
        if report.valid:
            try:
                state = self.load()
                account_id = state.account_id or None
                version = state.version
            except PortfolioRepositoryError:
                pass
        return PortfolioRepositoryStatus(
            path=str(self.path),
            exists=self.path.exists(),
            valid=report.valid,
            account_id=account_id,
            version=version,
            sha256=report.sha256,
            detail=report.detail,
        )

    def _verify_checksum(self, data: bytes) -> None:
        if not self.checksum_path.exists():
            # Bootstrap/migration may encounter a pre-checksum document.  A
            # subsequent save adds the sidecar; absence alone is not corruption.
            return
        try:
            expected = self.checksum_path.read_text(encoding="ascii").strip().split()[0]
        except (OSError, UnicodeError, IndexError) as exc:
            raise PortfolioChecksumError(
                f"Cannot read portfolio checksum sidecar: {exc}"
            ) from exc
        actual = hashlib.sha256(data).hexdigest()
        if expected.lower() != actual.lower():
            raise PortfolioChecksumError(
                f"Portfolio checksum mismatch: expected {expected}, actual {actual}."
            )

    @staticmethod
    def _check_account_scope(
        state: PortfolioState,
        expected_account_id: str | None,
    ) -> None:
        expected = str(expected_account_id or "").strip()
        if expected and state.account_id and state.account_id != expected:
            raise PortfolioAccountScopeError(
                f"Portfolio state account mismatch: {state.account_id} != {expected}."
            )
