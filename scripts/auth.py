#!/usr/bin/env python3
"""Audible OAuth in two steps (the flow requires browser interaction).

Usage:
    auth.py step1            # prints the login URL + saves verifier state
    auth.py step2 <redirect> # completes OAuth with the post-login redirect URL

After step2 succeeds, auth tokens land at ~/.audible-ingest/config/auth.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import audible
import httpx
from audible.login import build_oauth_url, create_code_verifier, extract_code_from_url
from audible.register import register as register_device


ROOT = Path.home() / ".audible-ingest"
CONFIG_DIR = ROOT / "config"
AUTH_FILE = CONFIG_DIR / "auth.json"
STATE_FILE = CONFIG_DIR / "auth_state.json"

# US marketplace constants (audible-cli ships locale maps; we keep this simple
# and assume US since that's >90% of users. Other locales: PR to add a flag.)
DOMAIN = "com"
MARKETPLACE_ID = "AF2M0KC94RCEA"
COUNTRY_CODE = "us"


def step1() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    code_verifier = create_code_verifier()
    oauth_url, serial = build_oauth_url(
        country_code=COUNTRY_CODE,
        domain=DOMAIN,
        market_place_id=MARKETPLACE_ID,
        code_verifier=code_verifier,
        with_username=False,
    )
    state = {
        "code_verifier": (
            code_verifier.decode() if isinstance(code_verifier, bytes) else code_verifier
        ),
        "serial": serial,
    }
    STATE_FILE.write_text(json.dumps(state))

    print("Open this URL in your browser and log in to Audible:")
    print()
    print(oauth_url)
    print()
    print("After login you'll be redirected to a URL that begins with")
    print("  https://www.amazon.com/ap/maplanding...")
    print("Copy that FULL URL (from your browser's address bar — it will show an")
    print("error page, that's fine) and run:")
    print()
    print("    auth.py step2 '<paste full redirect URL>'")


def step2(redirect_url: str) -> None:
    if not STATE_FILE.exists():
        sys.exit(f"No auth state at {STATE_FILE}. Run `auth.py step1` first.")

    state = json.loads(STATE_FILE.read_text())
    code_verifier = state["code_verifier"].encode()
    serial = state["serial"]

    auth_code = extract_code_from_url(httpx.URL(redirect_url))
    print(f">> Extracted authorization code: {auth_code}")

    print(">> Registering device with Audible...")
    registration = register_device(
        authorization_code=auth_code,
        code_verifier=code_verifier,
        domain=DOMAIN,
        serial=serial,
    )

    auth = audible.Authenticator()
    auth.locale = audible.localization.Locale(COUNTRY_CODE)
    for key, value in registration.items():
        setattr(auth, key, value)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    auth.to_file(str(AUTH_FILE))
    print(f">> Auth saved to {AUTH_FILE}")

    # Verify with a test library call.
    print(">> Verifying auth with a test library call...")
    with audible.Client(auth=auth) as client:
        resp = client.get("1.0/library", num_results=1, response_groups="product_desc")
    items = resp.get("items", [])
    if items:
        print(f"   SUCCESS — fetched 1 item: {items[0].get('title', '?')}")
    else:
        print("   WARNING — library call returned 0 items (empty library?)")

    # State file served its purpose.
    STATE_FILE.unlink(missing_ok=True)
    print("\nAuthentication complete.")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("step1", "step2"):
        sys.exit(__doc__)
    if sys.argv[1] == "step1":
        step1()
    else:
        if len(sys.argv) < 3:
            sys.exit("auth.py step2 requires the redirect URL as the next argument")
        step2(sys.argv[2])


if __name__ == "__main__":
    main()
