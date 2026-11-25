"""Shared Kalshi API request signing for REST and WebSocket.

One signer so the REST scanner and the WebSocket handshake never drift into
two signing implementations. Kalshi's scheme signs
``<timestamp><METHOD><path>`` — for REST, ``path`` is the request path
WITHOUT the query string.
"""

from __future__ import annotations

import base64
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class KalshiAuthError(RuntimeError):
    """Raised when Kalshi API credentials are missing/blank or a request
    cannot be signed — never silently send an unsigned request."""


def kalshi_auth_headers(
    api_key_id: str, private_key_pem: str, method: str, path: str
) -> dict[str, str]:
    """Signed headers for one Kalshi API request; the scheme follows the
    loaded key type (Ed25519 or RSA-PSS/SHA-256).

    ``path`` must exclude the query string. The private key is never
    interpolated into any log or error message.
    """
    if not api_key_id.strip():
        raise KalshiAuthError(
            "KALSHI_API_KEY_ID is not set (or is empty) — cannot sign Kalshi API requests"
        )
    if not private_key_pem.strip():
        raise KalshiAuthError(
            "KALSHI_PRIVATE_KEY is not set (or is empty) — cannot sign Kalshi API requests"
        )
    # A PEM stored in a single env var carries literal "\n" escapes; restore them.
    pem = private_key_pem.replace("\\n", "\n")
    private_key = serialization.load_pem_private_key(pem.encode(), password=None)
    timestamp = str(int(time.time() * 1000))
    message = f"{timestamp}{method}{path}".encode()
    if isinstance(private_key, Ed25519PrivateKey):
        signature = private_key.sign(message)  # Ed25519: no padding/digest params
    else:
        signature = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }
