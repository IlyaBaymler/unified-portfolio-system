from __future__ import annotations

"""Secret-provider abstraction for T-Invest Sandbox credentials.

Windows Credential Manager is preferred when available.  The existing .env
file remains a compatibility fallback and is explicitly reported as less safe.
"""

import base64
import json
import os
import re
import secrets
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values, set_key


class SecretProvider(Protocol):
    name: str
    secure: bool

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


Q7_PROTECTED_KEYS = (
    "TBANK_SANDBOX_TOKEN",
    "TBANK_SANDBOX_ACCOUNT_ID",
    "V310_CL_IDENTITY_KEY_HEX",
    "V310_CL_IDENTITY_KEY_ID",
)
Q7_IDENTITY_MUTEX = r"Local\MOEXResearchRobot.V310CLIdentityProvisioning.v1"
Q7_IDENTITY_CONFIRMATION = "PROVISION V3.10 CL7 IDENTITY"
_IDENTITY_ID_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_IDENTITY_HEX_RE = re.compile(r"[0-9a-f]{64,128}")


class Q7SecretError(RuntimeError):
    """Finite fail-closed error at the protected Q7 secret boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason).strip().upper()
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class Q7ProtectedSecrets:
    token: str
    account_id: str
    identity_key: bytes
    identity_key_id: str
    provider: str
    secure: bool

    def metadata(self) -> dict[str, object]:
        return {
            "secret_provider": self.provider,
            "secret_provider_secure": self.secure,
            "token_present": True,
            "account_present": True,
            "identity_key_present": True,
            "identity_key_id": self.identity_key_id,
        }


@dataclass(frozen=True, slots=True)
class IdentityProvisioningResult:
    status: str
    provider: str
    secure: bool
    identity_key_present: bool
    identity_key_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "secret_provider": self.provider,
            "secret_provider_secure": self.secure,
            "identity_key_present": self.identity_key_present,
            "identity_key_id": self.identity_key_id,
        }


class WindowsIdentityProvisioningMutex(AbstractContextManager[None]):
    """Per-user product-writer lock for create-once CL7 identity custody."""

    def __init__(self, *, timeout_ms: int = 5_000) -> None:
        self.timeout_ms = int(timeout_ms)
        self._handle = None

    def __enter__(self) -> None:
        if os.name != "nt":
            raise Q7SecretError("IDENTITY_PROVISIONING_LOCK_FAILED")
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        handle = kernel32.CreateMutexW(None, False, Q7_IDENTITY_MUTEX)
        if not handle:
            raise Q7SecretError("IDENTITY_PROVISIONING_LOCK_FAILED")
        result = kernel32.WaitForSingleObject(handle, self.timeout_ms)
        if result != 0:  # WAIT_OBJECT_0 only; abandonment is a failed gate.
            if result == 0x00000080:  # WAIT_ABANDONED: this caller owns it.
                kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
            raise Q7SecretError("IDENTITY_PROVISIONING_LOCK_FAILED")
        self._handle = (kernel32, handle)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self._handle is not None:
            kernel32, handle = self._handle
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
            self._handle = None
        return False


@dataclass(slots=True)
class MappingSecretProvider:
    """Explicit test/development provider; never selected in production."""

    values: Mapping[str, str]
    name: str = "explicit environment"
    secure: bool = False

    def get(self, key: str) -> str | None:
        value = str(self.values.get(key, "") or "").strip()
        return value or None

    def set(self, key: str, value: str) -> None:
        raise Q7SecretError("SECRET_PROVIDER_READ_ONLY")

    def delete(self, key: str) -> None:
        raise Q7SecretError("SECRET_PROVIDER_READ_ONLY")


@dataclass(slots=True)
class EnvFileSecretProvider:
    path: Path
    name: str = ".env fallback"
    secure: bool = False

    def get(self, key: str) -> str | None:
        value = dotenv_values(self.path).get(key)
        normalized = str(value or "").strip()
        return normalized or None

    def set(self, key: str, value: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        set_key(str(self.path), key, str(value), quote_mode="always")

    def delete(self, key: str) -> None:
        if not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        prefix = key + "="
        filtered = [line for line in lines if not line.strip().startswith(prefix)]
        self.path.write_text("\n".join(filtered) + ("\n" if filtered else ""), encoding="utf-8")


class WindowsCredentialManagerProvider:
    name = "Windows Credential Manager"
    secure = True
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self, namespace: str = "MOEXResearchRobot") -> None:
        if os.name != "nt":
            raise RuntimeError("Windows Credential Manager is available only on Windows.")
        import ctypes
        from ctypes import wintypes

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._ctypes = ctypes
        self._credential_type = CREDENTIALW
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredWriteW.argtypes = [
            ctypes.POINTER(CREDENTIALW),
            wintypes.DWORD,
        ]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self.namespace = namespace

    def _target(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def get(self, key: str) -> str | None:
        ctypes = self._ctypes
        pointer = ctypes.POINTER(self._credential_type)()
        ctypes.set_last_error(0)
        ok = self._advapi.CredReadW(
            self._target(key), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        )
        if not ok:
            error = ctypes.get_last_error()
            if error == 1168:  # ERROR_NOT_FOUND is the only absence signal.
                return None
            raise ctypes.WinError(error)
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or not credential.CredentialBlobSize:
                # A present empty credential is malformed custody, not absence.
                return ""
            data = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            return data.decode("utf-16-le").rstrip("\x00")
        finally:
            self._advapi.CredFree(pointer)

    def set(self, key: str, value: str) -> None:
        ctypes = self._ctypes
        raw = str(value).encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = self._credential_type()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = self._target(key)
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "T-Invest Sandbox"
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            raise ctypes.WinError()

    def delete(self, key: str) -> None:
        ok = self._advapi.CredDeleteW(
            self._target(key), self.CRED_TYPE_GENERIC, 0
        )
        if not ok:
            error = self._ctypes.get_last_error()
            if error not in {0, 1168}:  # ERROR_NOT_FOUND
                raise self._ctypes.WinError(error)


def preferred_secret_provider(app_dir: str | Path) -> SecretProvider:
    offline_qualification = os.getenv(
        "MOEX_ROBOT_OFFLINE_QUALIFICATION", ""
    ).strip().upper() in {"1", "YES", "TRUE", "ON"}
    if offline_qualification:
        # CL8 standalone qualification must remain offline and must not inspect
        # the operator's Windows Credential Manager. The isolated runtime uses
        # an empty .env compatibility provider and never receives real values.
        return EnvFileSecretProvider(Path(app_dir) / ".env")
    if os.name == "nt":
        try:
            provider = WindowsCredentialManagerProvider()
            # A read probe confirms the API is callable without creating data.
            provider.get("TBANK_SANDBOX_TOKEN")
            return provider
        except Exception:
            pass
    return EnvFileSecretProvider(Path(app_dir) / ".env")


@dataclass(frozen=True, slots=True)
class SecretProviderProbe:
    """Metadata-only secret-store health result.

    The plaintext credential is intentionally never retained in this object,
    serialized, logged or returned to the GUI.
    """

    provider: str
    secure: bool
    available: bool
    credential_present: bool
    key: str
    error: str | None = None

    @property
    def status(self) -> str:
        if not self.available:
            return "provider_unavailable"
        if not self.credential_present:
            return "credential_absent"
        if self.secure:
            return "credential_present"
        return ".env_fallback"

    def to_dict(self) -> dict[str, object]:
        return {
            "credential_status": self.status,
            "secret_provider": self.provider,
            "secret_provider_secure": self.secure,
            "secret_provider_available": self.available,
            "secret_present": self.credential_present,
            "secret_key": self.key,
            "secret_probe_error": self.error,
        }


def probe_secret_provider(
    app_dir: str | Path,
    *,
    key: str = "TBANK_SANDBOX_TOKEN",
    provider: SecretProvider | None = None,
) -> SecretProviderProbe:
    """Check provider availability and credential presence without exposure.

    ``get`` is used only to determine whether a non-empty value exists.  The
    value is discarded immediately and never enters the result.
    """

    def _probe(selected: SecretProvider) -> SecretProviderProbe:
        try:
            value = selected.get(key)
            present = bool(str(value or "").strip())
            return SecretProviderProbe(
                provider=str(selected.name),
                secure=bool(selected.secure),
                available=True,
                credential_present=present,
                key=str(key),
            )
        except Exception as exc:  # provider availability is diagnostic, not fatal
            return SecretProviderProbe(
                provider=str(getattr(selected, "name", "unknown")),
                secure=bool(getattr(selected, "secure", False)),
                available=False,
                credential_present=False,
                key=str(key),
                error=f"{type(exc).__name__}: {exc}",
            )

    fallback = EnvFileSecretProvider(Path(app_dir) / ".env")
    if os.getenv("MOEX_ROBOT_OFFLINE_QUALIFICATION", "").strip().upper() in {
        "1", "YES", "TRUE", "ON",
    }:
        # An offline bootstrap must not inspect the operator's Credential
        # Manager, including when a protected provider was supplied explicitly.
        return _probe(fallback)
    if provider is not None:
        return _probe(provider)
    if os.name != "nt":
        return _probe(fallback)

    try:
        protected = _probe(WindowsCredentialManagerProvider())
    except Exception as exc:
        protected = SecretProviderProbe(
            provider="Windows Credential Manager",
            secure=True,
            available=False,
            credential_present=False,
            key=str(key),
            error=f"{type(exc).__name__}: {exc}",
        )
    if protected.credential_present:
        return protected
    env_probe = _probe(fallback)
    if env_probe.credential_present:
        return env_probe
    return protected


@dataclass(frozen=True, slots=True)
class ResolvedSecret:
    value: str | None
    provider: str
    secure: bool


class WindowsDpapiSecretStore:
    """Small DPAPI-backed file store used by portable builds.

    Tests can inject reversible protector/unprotector callables.  On Windows the
    default backend uses CryptProtectData/CryptUnprotectData.  The encrypted
    blob may live in runtime/, while plaintext never reaches that file.
    """

    def __init__(self, path: str | Path, *, protector=None, unprotector=None) -> None:
        self.path = Path(path)
        self._protector = protector or self._protect_windows
        self._unprotector = unprotector or self._unprotect_windows

    def save(self, value: str) -> None:
        raw = str(value).encode("utf-8")
        protected = self._protector(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {"format": "DPAPI", "version": 1, "blob": base64.b64encode(protected).decode("ascii")}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        document = json.loads(self.path.read_text(encoding="utf-8"))
        protected = base64.b64decode(document["blob"])
        return self._unprotector(protected).decode("utf-8") or None

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

    @staticmethod
    def _protect_windows(data: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("DPAPI is available only on Windows.")
        return _dpapi_transform(data, protect=True)

    @staticmethod
    def _unprotect_windows(data: bytes) -> bytes:
        if os.name != "nt":
            raise RuntimeError("DPAPI is available only on Windows.")
        return _dpapi_transform(data, protect=False)


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    input_blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32.dll")
    kernel32 = ctypes.WinDLL("kernel32.dll")
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob), None, None, None, None, 0,
            ctypes.byref(output_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None, 0,
            ctypes.byref(output_blob),
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def resolve_tbank_token(
    runtime_dir: str | Path,
    *,
    env_value: str | None = None,
    store: WindowsDpapiSecretStore | None = None,
) -> ResolvedSecret:
    root = Path(runtime_dir)
    selected_store = store
    if selected_store is None and os.name == "nt":
        selected_store = WindowsDpapiSecretStore(root / "sandbox_token.dpapi")
    if selected_store is not None:
        try:
            value = selected_store.load()
        except Exception:
            value = None
        if value:
            return ResolvedSecret(value=value, provider="Windows DPAPI", secure=True)
    fallback = str(env_value or "").strip() or EnvFileSecretProvider(root / ".env").get("TBANK_SANDBOX_TOKEN")
    return ResolvedSecret(value=fallback, provider=".env fallback", secure=False)


def _strict_identity_pair(key_hex: str | None, key_id: str | None) -> tuple[bytes, str]:
    key_text = str(key_hex or "").strip()
    id_text = str(key_id or "").strip()
    if key_hex is None and key_id is None:
        raise Q7SecretError("IDENTITY_KEY_REQUIRED")
    if (key_hex is None) != (key_id is None):
        raise Q7SecretError("IDENTITY_CUSTODY_PARTIAL")
    if not key_text or not id_text:
        raise Q7SecretError("IDENTITY_KEY_INVALID")
    if _IDENTITY_HEX_RE.fullmatch(key_text) is None or len(key_text) % 2:
        raise Q7SecretError("IDENTITY_KEY_INVALID")
    if _IDENTITY_ID_RE.fullmatch(id_text) is None:
        raise Q7SecretError("IDENTITY_KEY_INVALID")
    try:
        decoded = bytes.fromhex(key_text)
    except ValueError as exc:  # defensive; the regex already excludes this path
        raise Q7SecretError("IDENTITY_KEY_INVALID") from exc
    if not 32 <= len(decoded) <= 64:
        raise Q7SecretError("IDENTITY_KEY_INVALID")
    return decoded, id_text


def resolve_q7_protected_secrets(
    *,
    provider: SecretProvider | None = None,
    allow_environment: bool = False,
    environ: Mapping[str, str] | None = None,
    expected_identity_key_id: str | None = None,
) -> Q7ProtectedSecrets:
    """Resolve one complete Q7 secret tuple through the canonical boundary.

    Production callers get Windows Credential Manager. Environment values are
    reachable only through an explicit test/development opt-in and never form
    an automatic fallback.
    """

    selected = provider
    if selected is None:
        if allow_environment:
            selected = MappingSecretProvider(environ or os.environ)
        elif os.name == "nt":
            selected = WindowsCredentialManagerProvider()
        else:
            raise Q7SecretError("SECRET_PROVIDER_UNAVAILABLE")
    if not bool(getattr(selected, "secure", False)) and not allow_environment:
        raise Q7SecretError("PROTECTED_SECRET_PROVIDER_REQUIRED")
    try:
        token = str(selected.get("TBANK_SANDBOX_TOKEN") or "").strip()
        account_id = str(selected.get("TBANK_SANDBOX_ACCOUNT_ID") or "").strip()
        key_hex = selected.get("V310_CL_IDENTITY_KEY_HEX")
        key_id = selected.get("V310_CL_IDENTITY_KEY_ID")
    except Q7SecretError:
        raise
    except Exception as exc:
        raise Q7SecretError("SECRET_PROVIDER_UNAVAILABLE") from exc
    if not token:
        raise Q7SecretError("SANDBOX_TOKEN_REQUIRED")
    if not account_id or len(account_id) > 256 or any(
        0xD800 <= ord(char) <= 0xDFFF for char in account_id
    ):
        raise Q7SecretError("SANDBOX_ACCOUNT_REQUIRED")
    key, normalized_id = _strict_identity_pair(key_hex, key_id)
    expected_id = str(expected_identity_key_id or "").strip()
    if expected_id and expected_id != normalized_id:
        raise Q7SecretError("IDENTITY_KEY_MISMATCH")
    return Q7ProtectedSecrets(
        token=token,
        account_id=account_id,
        identity_key=key,
        identity_key_id=normalized_id,
        provider=str(getattr(selected, "name", "unknown")),
        secure=bool(getattr(selected, "secure", False)),
    )


def provision_q7_identity(
    *,
    identity_key_id: str,
    confirmation: str,
    provider: SecretProvider | None = None,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    mutex_factory: Callable[[], AbstractContextManager[None]] = WindowsIdentityProvisioningMutex,
) -> IdentityProvisioningResult:
    """Create the protected CL7 identity pair exactly once.

    The function deliberately returns metadata only. It serializes every
    accepted product writer and compensates only values created by this call.
    """

    requested_id = str(identity_key_id or "").strip()
    if str(confirmation) != Q7_IDENTITY_CONFIRMATION:
        raise Q7SecretError("IDENTITY_PROVISIONING_CONFIRMATION_REQUIRED")
    if _IDENTITY_ID_RE.fullmatch(requested_id) is None:
        raise Q7SecretError("IDENTITY_KEY_INVALID")
    selected = provider or WindowsCredentialManagerProvider()
    if not bool(getattr(selected, "secure", False)):
        raise Q7SecretError("PROTECTED_SECRET_PROVIDER_REQUIRED")

    def read_pair() -> tuple[str | None, str | None]:
        return (
            selected.get("V310_CL_IDENTITY_KEY_HEX"),
            selected.get("V310_CL_IDENTITY_KEY_ID"),
        )

    def compensate(created_key: str, created_id: str | None) -> None:
        safe = True
        for logical_key, created_value in reversed(
            (
                ("V310_CL_IDENTITY_KEY_HEX", created_key),
                ("V310_CL_IDENTITY_KEY_ID", created_id),
            )
        ):
            if created_value is None:
                continue
            try:
                current_value = selected.get(logical_key)
                if current_value is None:
                    continue
                if current_value != created_value:
                    safe = False
                    continue
                selected.delete(logical_key)
                if selected.get(logical_key) is not None:
                    safe = False
            except Exception:
                safe = False
        try:
            absent = read_pair() == (None, None)
        except Exception:
            absent = False
        if not safe or not absent:
            raise Q7SecretError("IDENTITY_CUSTODY_PARTIAL")
        raise Q7SecretError("IDENTITY_PROVISIONING_FAILED")

    try:
        mutex = mutex_factory()
        with mutex:
            try:
                existing_key, existing_id = read_pair()
            except Exception as exc:
                raise Q7SecretError("SECRET_PROVIDER_UNAVAILABLE") from exc
            if existing_key is not None or existing_id is not None:
                _strict_identity_pair(existing_key, existing_id)
                if existing_id != requested_id:
                    raise Q7SecretError("IDENTITY_KEY_MISMATCH")
                return IdentityProvisioningResult(
                    "ALREADY_PROVISIONED",
                    str(getattr(selected, "name", "unknown")),
                    True,
                    True,
                    requested_id,
                )

            generated = random_bytes(32)
            if type(generated) is not bytes or len(generated) != 32:
                raise Q7SecretError("IDENTITY_PROVISIONING_FAILED")
            created_key = generated.hex()
            created_id: str | None = None
            try:
                if selected.get("V310_CL_IDENTITY_KEY_HEX") is not None:
                    compensate(created_key, created_id)
                selected.set("V310_CL_IDENTITY_KEY_HEX", created_key)
                if selected.get("V310_CL_IDENTITY_KEY_HEX") != created_key:
                    compensate(created_key, created_id)
                if selected.get("V310_CL_IDENTITY_KEY_ID") is not None:
                    compensate(created_key, created_id)
                selected.set("V310_CL_IDENTITY_KEY_ID", requested_id)
                created_id = requested_id
                if read_pair() != (created_key, requested_id):
                    compensate(created_key, created_id)
            except Q7SecretError:
                raise
            except Exception:
                compensate(created_key, created_id)
            return IdentityProvisioningResult(
                "PROVISIONED",
                str(getattr(selected, "name", "unknown")),
                True,
                True,
                requested_id,
            )
    except Q7SecretError:
        raise
    except Exception as exc:
        raise Q7SecretError("IDENTITY_PROVISIONING_LOCK_FAILED") from exc
