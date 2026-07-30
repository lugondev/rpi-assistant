from __future__ import annotations

import urllib.parse

from .config import Config


def build_ws_url(config: Config, device_token: str | None = None) -> str:
    scheme = "wss" if config.secure else "ws"
    url = f"{scheme}://{config.host}:{config.port}/v1/lugo/stream"
    if device_token:
        url += "?" + urllib.parse.urlencode({"device_token": device_token})
    return url


def build_new_session_message() -> dict:
    """Ask the gateway to end this conversation and start a fresh one.

    Without this the device's session is effectively permanent: it keeps one
    socket open indefinitely, and `session_state.py` persists the id and resumes
    it after a restart, so every word it ever says accumulates in a single
    server-side conversation.

    Carries no session_id: the gateway mints the new one and answers with
    `{"type": "session_new", ...}`, which the caller must persist.
    """
    return {"type": "new_session"}


def build_wakeup_message(config: Config, session_id: str | None) -> dict:
    message: dict = {
        "type": "wakeup",
        "audio_params": {
            "sample_rate": config.uplink_sample_rate,
            "output_sample_rate": config.output_sample_rate,
        },
        "features": {"mcp": True},
    }
    if config.profile:
        message["profile"] = config.profile
    if session_id:
        message["session_id"] = session_id
    return message
