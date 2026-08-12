from __future__ import annotations

from pathlib import Path
import zipfile

from trading_robot.secret_provider import WindowsDpapiSecretStore, resolve_tbank_token
from trading_robot.security import redact_sensitive_text, scan_text, scan_zip


def test_redaction_and_canary_scan():
    canary = "RC1_SUPER_SECRET_CANARY_123"
    text = f"Authorization: Bearer {canary}\nTBANK_SANDBOX_TOKEN={canary}"
    redacted = redact_sensitive_text(text)
    assert canary not in redacted
    assert scan_text(text, canaries=[canary])
    assert not scan_text(redacted, canaries=[canary])


def test_dpapi_store_round_trip_with_injected_backend(tmp_path: Path):
    def protect(data: bytes) -> bytes:
        return bytes(value ^ 0x5A for value in data)

    store = WindowsDpapiSecretStore(
        tmp_path / "sandbox_token.dpapi",
        protector=protect,
        unprotector=protect,
    )
    store.save("token-canary")
    assert store.load() == "token-canary"
    assert "token-canary" not in store.path.read_text(encoding="utf-8")
    resolved = resolve_tbank_token(tmp_path, env_value="fallback", store=store)
    assert resolved.value == "token-canary"
    assert resolved.secure is True


def test_secret_scan_finds_canary_in_zip(tmp_path: Path):
    canary = "ZIP_SECRET_CANARY_456"
    target = tmp_path / "bundle.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("log.txt", f"hello {canary}")
    findings = scan_zip(target, canaries=[canary])
    assert any(item.kind == "CANARY" for item in findings)
