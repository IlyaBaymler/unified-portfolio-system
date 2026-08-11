from __future__ import annotations

import logging
import os
from pathlib import Path


logger = logging.getLogger(__name__)
_SYSTEM_TRUST_STORE_ENABLED = False
_SYSTEM_TRUST_STORE_ATTEMPTED = False


def enable_system_trust_store() -> bool:
    """Make Python HTTPS clients use the native OS certificate store.

    On Windows this delegates certificate-chain validation to CryptoAPI.  The
    function is intentionally best-effort so the application still starts when
    the optional ``truststore`` package is missing; Requests will then use its
    normal CA bundle and emit a clear TLS error if validation fails.
    """
    global _SYSTEM_TRUST_STORE_ENABLED, _SYSTEM_TRUST_STORE_ATTEMPTED
    if _SYSTEM_TRUST_STORE_ATTEMPTED:
        return _SYSTEM_TRUST_STORE_ENABLED
    _SYSTEM_TRUST_STORE_ATTEMPTED = True

    try:
        import truststore
    except ImportError:
        logger.warning(
            "truststore is not installed; HTTPS will use the default Python CA bundle"
        )
        return False

    try:
        truststore.inject_into_ssl()
    except Exception as exc:  # pragma: no cover - platform-specific defensive path
        logger.warning("Could not enable the system certificate store: %s", exc)
        return False

    _SYSTEM_TRUST_STORE_ENABLED = True
    logger.info("Native operating-system certificate store enabled")
    return True


def resolve_ca_bundle(explicit_path: str | None = None) -> bool | str:
    """Return Requests' ``verify`` value.

    Priority: explicit GUI/client argument, ``TBANK_CA_BUNDLE``, then the
    standard Requests environment variables.  With no explicit bundle, TLS
    verification remains enabled and uses the active system/default trust
    store.  Verification is never disabled by this helper.
    """
    raw = (
        explicit_path
        or os.getenv("TBANK_CA_BUNDLE")
        or os.getenv("REQUESTS_CA_BUNDLE")
        or os.getenv("CURL_CA_BUNDLE")
        or ""
    ).strip()
    if not raw:
        return True

    path = Path(os.path.expandvars(raw)).expanduser()
    if not path.is_file():
        raise ValueError(
            "Файл доверенных сертификатов не найден: "
            f"{path}. Укажите существующий PEM/CA-bundle или очистите поле."
        )
    return str(path.resolve())


def system_trust_store_enabled() -> bool:
    return _SYSTEM_TRUST_STORE_ENABLED
