from __future__ import annotations

"""Secret-provider abstraction for T-Invest Sandbox credentials.

Windows Credential Manager is preferred when available.  The existing .env
file remains a compatibility fallback and is explicitly reported as less safe.
"""

from dataclasses import dataclass
import base64
import json
import os
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values, set_key


class SecretProvider(Protocol):
    name: str
    secure: bool

    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


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
        self._advapi = ctypes.WinDLL("Advapi32.dll")
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
        ok = self._advapi.CredReadW(
            self._target(key), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        )
        if not ok:
            return None
        try:
            credential = pointer.contents
            if not credential.CredentialBlob or not credential.CredentialBlobSize:
                return None
            data = ctypes.string_at(
                credential.CredentialBlob, credential.CredentialBlobSize
            )
            return data.decode("utf-16-le").rstrip("\x00") or None
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

    if provider is not None:
        return _probe(provider)

    fallback = EnvFileSecretProvider(Path(app_dir) / ".env")
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
