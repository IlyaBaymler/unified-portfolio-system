from __future__ import annotations

import os
import platform
import sys

from dotenv import load_dotenv

from trading_robot.tls_support import (
    enable_system_trust_store,
    resolve_ca_bundle,
    system_trust_store_enabled,
)

# Enable native trust before importing Requests through the API client.
enable_system_trust_store()

import requests

from trading_robot.tbank_sandbox import TBankAPIError, TBankSandboxClient


def main() -> int:
    load_dotenv()
    token = os.getenv("TBANK_SANDBOX_TOKEN", "").strip()
    ca_bundle = os.getenv("TBANK_CA_BUNDLE", "").strip() or None

    print("T-Invest TLS diagnostics")
    print("=" * 50)
    print("Python:", sys.version.replace("\n", " "))
    print("OS:", platform.platform())
    print("Requests:", requests.__version__)
    print("System truststore active:", system_trust_store_enabled())
    try:
        print("Verify source:", resolve_ca_bundle(ca_bundle))
    except ValueError as exc:
        print("Configuration error:", exc)
        return 2
    print("Endpoint:", TBankSandboxClient.BASE_URL)
    print()

    if not token:
        print("TBANK_SANDBOX_TOKEN is absent in .env; TLS-only probe will be used.")
        try:
            response = requests.get(
                TBankSandboxClient.BASE_URL,
                timeout=15,
                verify=resolve_ca_bundle(ca_bundle),
            )
            print("TLS handshake succeeded; HTTP status:", response.status_code)
            return 0
        except requests.exceptions.SSLError as exc:
            print("TLS verification failed:", exc)
            return 3
        except requests.RequestException as exc:
            print("Network request failed after TLS setup:", exc)
            return 4

    try:
        with TBankSandboxClient(token, ca_bundle_path=ca_bundle, max_retries=0) as api:
            accounts = api.get_accounts()
        print("Connection succeeded. Open Sandbox accounts:", len(accounts))
        return 0
    except TBankAPIError as exc:
        print(str(exc))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
