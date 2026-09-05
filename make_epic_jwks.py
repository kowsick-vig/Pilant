"""
One-time helper: converts epic_public_key.pem (generated per
connectors_healthcare.py's setup instructions) into the JWK Set JSON
format Epic's app registration form wants for its "Non-Production JWK
Set URL" / "Production JWK Set URL" fields.

Only reads your PRIVATE key locally to derive the public numbers — it
never sends the private key anywhere, and the output file (jwks.json)
contains only public information, safe to host publicly (that's the
whole point of a JWK Set URL: Epic fetches it over HTTPS to get your
public key).

Usage: python3 make_epic_jwks.py
Reads:  epic_private_key.pem  (from the openssl commands in connectors_healthcare.py)
Writes: jwks.json             (upload this file's contents somewhere public — see below)
"""

import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization

PRIVATE_KEY_PATH = Path(__file__).parent / "epic_private_key.pem"
OUTPUT_PATH = Path(__file__).parent / "jwks.json"
KEY_ID = "pilant-agent-1"


def _b64url(n_bytes):
    return base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode("ascii")


def _int_to_bytes(n):
    length = (n.bit_length() + 7) // 8
    return n.to_bytes(length, "big")


def main():
    if not PRIVATE_KEY_PATH.exists():
        raise SystemExit(
            f"Couldn't find {PRIVATE_KEY_PATH}. Run the openssl commands in "
            "connectors_healthcare.py's module docstring first to generate "
            "epic_private_key.pem, then run this script again."
        )

    private_key = serialization.load_pem_private_key(
        PRIVATE_KEY_PATH.read_bytes(), password=None
    )
    public_numbers = private_key.public_key().public_numbers()

    jwk = {
        "kty": "RSA",
        "n": _b64url(_int_to_bytes(public_numbers.n)),
        "e": _b64url(_int_to_bytes(public_numbers.e)),
        "kid": KEY_ID,
        "use": "sig",
        "alg": "RS384",
    }
    jwks = {"keys": [jwk]}

    OUTPUT_PATH.write_text(json.dumps(jwks, indent=2))
    print(f"Wrote {OUTPUT_PATH}")
    print(
        "\nThis file contains only PUBLIC key material — safe to host publicly. "
        "Next: host it somewhere reachable over HTTPS (a public GitHub Gist's "
        "'Raw' link is the fastest option — see the chat for step-by-step "
        "instructions), then paste that URL into Epic's 'Non-Production JWK "
        "Set URL' field."
    )


if __name__ == "__main__":
    main()
