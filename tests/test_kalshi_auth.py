"""Behavior tests for shared Kalshi request signing (kalshi_testing.auth).

Keys are generated at runtime — no key material lives in the repo. Signatures
are verified cryptographically against the exact bytes Kalshi documents:
``<timestamp><METHOD><path>`` with the query string excluded.
"""

import base64
from urllib.parse import urlsplit

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kalshi_testing.auth import KalshiAuthError, kalshi_auth_headers


def _generate_ed25519() -> tuple[Ed25519PrivateKey, str]:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return key, pem


def test_all_three_auth_headers_are_produced() -> None:
    _, pem = _generate_ed25519()
    headers = kalshi_auth_headers("key-id-1", pem, "GET", "/trade-api/v2/events")
    assert headers["KALSHI-ACCESS-KEY"] == "key-id-1"
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()
    base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])  # valid base64


def test_ed25519_signature_verifies_over_timestamp_method_path() -> None:
    key, pem = _generate_ed25519()
    headers = kalshi_auth_headers("key-id-1", pem, "GET", "/trade-api/v2/events")
    message = f"{headers['KALSHI-ACCESS-TIMESTAMP']}GET/trade-api/v2/events".encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(signature, message)  # raises InvalidSignature on mismatch


def test_query_string_is_never_part_of_the_signed_message() -> None:
    """Kalshi signs the path only: a signature over path+query is rejected."""
    key, pem = _generate_ed25519()
    url = "https://demo-api.kalshi.co/trade-api/v2/events?series_ticker=KXNBAGAME&status=open"
    headers = kalshi_auth_headers("key-id-1", pem, "GET", urlsplit(url).path)
    timestamp = headers["KALSHI-ACCESS-TIMESTAMP"]
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(signature, f"{timestamp}GET/trade-api/v2/events".encode())
    with pytest.raises(InvalidSignature):
        key.public_key().verify(
            signature,
            f"{timestamp}GET/trade-api/v2/events?series_ticker=KXNBAGAME&status=open".encode(),
        )


@pytest.mark.parametrize(
    ("api_key_id", "private_key_pem"),
    [("", "some-pem"), ("   ", "some-pem"), ("key-id-1", ""), ("key-id-1", "  \n ")],
)
def test_blank_credentials_raise_before_any_signing(api_key_id: str, private_key_pem: str) -> None:
    """Defense in depth: no code path can silently send an unsigned request."""
    with pytest.raises(KalshiAuthError):
        kalshi_auth_headers(api_key_id, private_key_pem, "GET", "/trade-api/v2/events")
