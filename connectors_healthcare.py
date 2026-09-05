"""
A real connector — reads live appointment/scheduling data from Epic's
public "open" FHIR sandbox via Epic's Backend Services (system-to-system)
OAuth2 flow. This is a genuine external system, the same as
connectors_github.py and connectors_retail.py are for their domains — not
fabricated data — but pointed at Epic's own synthetic test patients rather
than a real clinic. That's deliberate, not a shortcut: real patient data
(PHI) requires a signed Business Associate Agreement with whatever AI
provider processes it, encryption, audit logging, and access controls well
beyond what this project has — see the "Known simplifications" note below.

Setup (see .env.example) — verified against fhir.epic.com and
implementation guides as of 2026-08-22; if Epic has changed anything,
check https://fhir.epic.com/Documentation?docId=oauth2 directly rather
than trusting this comment forever:

  1. Create a free account at fhir.epic.com and register a new app
     ("Build Apps" -> "Create My First App"). Choose the app type that
     enables Backend Systems / backend OAuth2 (system-to-system access) —
     that's the whole point here, since nobody is logging in as a
     patient or clinician for this demo.

  2. Generate an RSA keypair for JWT signing (run locally, not in this
     project's shared files):
       openssl genrsa -out epic_private_key.pem 2048
       openssl req -new -x509 -key epic_private_key.pem -out epic_public_key.pem -days 3650 -subj "/CN=pilant-agent"
     Keep epic_private_key.pem OUT of git, exactly like any other secret.

     Epic's current Backend Systems app form (as of 2026-08-22) asks for a
     "Non-Production JWK Set URL" — a public HTTPS URL serving your key in
     JWK Set JSON format — rather than accepting a pasted key directly.
     Run `python3 make_epic_jwks.py` (in this project) to turn
     epic_private_key.pem into jwks.json in that exact format, then host
     that file's contents somewhere public over HTTPS — a public GitHub
     Gist's "Raw" link is the fastest option for a demo — and paste that
     URL into the form. If Epic's form changes back to a direct key
     upload, paste epic_public_key.pem's contents instead.

  3. Save the app — Epic auto-generates a non-production (sandbox)
     Client ID immediately; there's no approval wait for sandbox access,
     though a change can take up to ~1 hour to sync.

  4. Find at least one sandbox test patient's FHIR ID. Epic's own
     documentation lists the open sandbox's named test patients (e.g.
     "Camila Lopez", "Derrick Lin," and others) along with their FHIR
     IDs — look this up directly on fhir.epic.com rather than trusting a
     hardcoded ID here, since these can change. Listing more than one,
     comma-separated, gets you more than one row of demo data — each
     sandbox test patient typically only has one or two appointments on
     record (see "Known simplifications" below).

  EPIC_CLIENT_ID          required — the non-production Client ID from step 3.
  EPIC_PRIVATE_KEY_PATH   required — path to the private key file from step 2.
  EPIC_TEST_PATIENT_IDS   required — comma-separated FHIR patient ID(s) from step 4.
  EPIC_FHIR_BASE_URL      optional, defaults to the open sandbox's R4 base URL.
  EPIC_TOKEN_URL          optional, defaults to Epic's interconnect token endpoint.
  EPIC_SCOPE              optional, defaults to "system/Appointment.read" — must
                          match whatever FHIR resource scope(s) you selected
                          when registering the app; check the app's page if
                          token requests get rejected for an invalid scope.

Known simplifications, called out honestly rather than silently:
  - This is Epic's OPEN, non-production sandbox with synthetic test
    patients — never real PHI. It's genuinely real Epic infrastructure
    and a real OAuth2/FHIR exchange, but the data itself is fake, on
    purpose.
  - Epic's open sandbox is deliberately sparse — each test patient
    usually has only one or two appointments/encounters on record, not a
    realistic day's schedule. That's a limitation of the sandbox itself,
    not this connector; querying multiple test patients (EPIC_TEST_PATIENT_IDS
    above) is the practical workaround for demo purposes.
  - The backend-services access token is short-lived (about an hour) by
    design. _get_access_token() below signs a fresh JWT and re-exchanges
    it automatically once the cached token is close to expiring — same
    caching pattern as connectors_retail.py uses for Shopify.
  - Appointments aren't filtered by date. The sandbox's fixed, sparse
    test data usually isn't dated "today," so a strict date filter would
    just return nothing — status filtering (below) is what get_appointments()
    supports instead.
"""

import os
import json
import time
import uuid
from pathlib import Path
import urllib.request
import urllib.error
import urllib.parse

import jwt  # PyJWT — already a dependency; see requirements.txt

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DEFAULT_TOKEN_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"
DEFAULT_FHIR_BASE_URL = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/"
DEFAULT_SCOPE = "system/Appointment.read"

# In-memory only, same pattern and same reasoning as connectors_retail.py's
# _token_cache — re-fetched automatically on the next call after a restart
# or once it's stale.
_token_cache = {"token": None, "expires_at": 0}


def _build_client_assertion(client_id, token_url, private_key_path):
    """
    Sign the JWT client assertion Epic's backend OAuth2 flow requires,
    per the SMART Backend Services spec: RS384, iss/sub = your Client ID,
    aud = the token endpoint, a unique jti, and an exp no more than 5
    minutes in the future (we use 4 to leave margin for clock skew).
    """
    key_path = Path(private_key_path)
    if not key_path.exists():
        raise RuntimeError(
            f"EPIC_PRIVATE_KEY_PATH points at '{private_key_path}', which doesn't "
            "exist. Generate one with: openssl genrsa -out epic_private_key.pem 2048 "
            "— see connectors_healthcare.py's module docstring for the full setup."
        )
    private_key = key_path.read_text()
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": token_url,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "nbf": now,
        "exp": now + 240,
    }
    try:
        # kid must match the "kid" in the JWK Set you hosted and registered
        # with Epic (see make_epic_jwks.py) — that's how Epic knows which
        # key in your published set to verify this JWT against.
        return jwt.encode(
            claims, private_key, algorithm="RS384",
            headers={"kid": os.environ.get("EPIC_JWK_KID", "").strip() or "pilant-agent-1"},
        )
    except Exception as e:
        raise RuntimeError(
            f"Couldn't sign the Epic backend-services JWT: {e}. Check that "
            "EPIC_PRIVATE_KEY_PATH points at a valid RSA PRIVATE key PEM file "
            "(not the public key uploaded to Epic by mistake)."
        ) from e


def _get_access_token():
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    client_id = os.environ.get("EPIC_CLIENT_ID", "").strip()
    key_path = os.environ.get("EPIC_PRIVATE_KEY_PATH", "").strip()
    token_url = os.environ.get("EPIC_TOKEN_URL", "").strip() or DEFAULT_TOKEN_URL
    scope = os.environ.get("EPIC_SCOPE", "").strip() or DEFAULT_SCOPE

    if not client_id or not key_path:
        raise RuntimeError(
            "EPIC_CLIENT_ID and EPIC_PRIVATE_KEY_PATH are not both set in .env. "
            "Register a Backend Systems app at fhir.epic.com and generate an RSA "
            "keypair for it — see connectors_healthcare.py's module docstring for "
            "the full setup."
        )

    assertion = _build_client_assertion(client_id, token_url, key_path)
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": scope,
    }).encode("utf-8")
    req = urllib.request.Request(token_url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Epic token exchange failed ({e.code}): {err_body[:400]} — check "
            "EPIC_CLIENT_ID, that the public key uploaded on the app's fhir.epic.com "
            "page matches EPIC_PRIVATE_KEY_PATH, and that EPIC_SCOPE matches an API "
            "your app is actually registered for."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Epic's token endpoint: {e.reason}") from e

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Epic token exchange succeeded but returned no access_token: {data}")

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return token


def _fhir_get(resource_path, params=None):
    base = os.environ.get("EPIC_FHIR_BASE_URL", "").strip() or DEFAULT_FHIR_BASE_URL
    token = _get_access_token()
    url = f"{base.rstrip('/')}/{resource_path.lstrip('/')}"
    if params:
        query = "&".join(
            f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items() if v is not None
        )
        if query:
            url = f"{url}?{query}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/fhir+json",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Epic FHIR API error {e.code} for {resource_path}: {body[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Epic's FHIR API: {e.reason}") from e


def _appointment_status(raw_status):
    """
    Normalize FHIR's Appointment status codes (proposed/pending/booked/
    arrived/fulfilled/cancelled/noshow/entered-in-error/checked-in/waitlist)
    into the smaller set the agent reasons about: "booked", "checked_in",
    "fulfilled", "cancelled", or "no_show".
    """
    mapping = {
        "proposed": "booked",
        "pending": "booked",
        "booked": "booked",
        "waitlist": "booked",
        "arrived": "checked_in",
        "checked-in": "checked_in",
        "fulfilled": "fulfilled",
        "cancelled": "cancelled",
        "entered-in-error": "cancelled",
        "noshow": "no_show",
    }
    return mapping.get(raw_status, raw_status or "booked")


def get_appointments(status=None):
    """
    Fetch appointments for the configured sandbox test patient(s)
    (EPIC_TEST_PATIENT_IDS in .env), optionally filtered by status:
    "booked", "checked_in", "fulfilled", "cancelled", or "no_show".
    Returns real Appointment data pulled live from Epic's open FHIR
    sandbox — patient name, provider, start/end time, and status — not
    fabricated.
    """
    patient_ids = [
        p.strip() for p in os.environ.get("EPIC_TEST_PATIENT_IDS", "").split(",") if p.strip()
    ]
    if not patient_ids:
        raise RuntimeError(
            "EPIC_TEST_PATIENT_IDS is not set in .env. Add at least one sandbox "
            "test patient FHIR ID — see connectors_healthcare.py's module "
            "docstring for how to find one on fhir.epic.com."
        )

    appointments = []
    for patient_id in patient_ids:
        raw = _fhir_get("Appointment", {"patient": patient_id})

        if isinstance(raw, dict) and raw.get("resourceType") == "OperationOutcome":
            issues = raw.get("issue") or [{}]
            detail = issues[0].get("diagnostics") or (issues[0].get("details") or {}).get("text") or raw
            raise RuntimeError(f"Epic FHIR API returned an error for patient {patient_id}: {detail}")

        for entry in raw.get("entry", []):
            res = entry.get("resource", {})
            if res.get("resourceType") != "Appointment":
                continue

            patient_name = None
            provider_name = None
            for participant in res.get("participant", []):
                actor = participant.get("actor") or {}
                ref = actor.get("reference", "")
                if ref.startswith("Patient/"):
                    patient_name = actor.get("display")
                elif ref.startswith("Practitioner/"):
                    provider_name = actor.get("display")

            appointments.append({
                "id": res.get("id"),
                "patient": patient_name or "unknown patient",
                "provider": provider_name or "unassigned",
                "start": res.get("start"),
                "end": res.get("end"),
                "status": _appointment_status(res.get("status")),
                "reason": (res.get("appointmentType") or {}).get("text") or res.get("description"),
            })

    if status:
        appointments = [a for a in appointments if a["status"] == status]
    appointments.sort(key=lambda a: a.get("start") or "")
    return appointments


if __name__ == "__main__":
    # Quick manual check: python3 connectors_healthcare.py
    import sys
    print(
        f"EPIC_CLIENT_ID = {os.environ.get('EPIC_CLIENT_ID') or '(not set)'}, "
        f"EPIC_TEST_PATIENT_IDS = {os.environ.get('EPIC_TEST_PATIENT_IDS') or '(not set)'}",
        file=sys.stderr,
    )
    print(json.dumps(get_appointments(), indent=2))
