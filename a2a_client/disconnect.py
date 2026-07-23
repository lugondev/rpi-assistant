from __future__ import annotations

RECONNECT = "reconnect"
REPAIR = "repair"

_AUTH_REJECT_STATUS = (401, 403)


def classify_disconnect(handshake_status: int | None, goodbye_reason: str | None) -> str:
    """Decide what a disconnect means. REPAIR (wipe token, re-pair) only on the
    two revoke signals; everything else is a recoverable network drop."""
    if handshake_status in _AUTH_REJECT_STATUS:
        return REPAIR
    if goodbye_reason == "account_disabled":
        return REPAIR
    return RECONNECT
