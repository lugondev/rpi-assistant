from __future__ import annotations

from .config import Config


def build_ws_url(config: Config) -> str:
    scheme = "wss" if config.secure else "ws"
    return f"{scheme}://{config.host}:{config.port}/v1/lugo/stream"


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
