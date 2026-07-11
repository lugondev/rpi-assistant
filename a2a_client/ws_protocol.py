from __future__ import annotations

from urllib.parse import urlencode

from .config import Config


def build_ws_url(config: Config, session_id: str | None = None) -> str:
    scheme = "wss" if config.secure else "ws"
    # STT engine + language, TTS voice/engine and the LLM all come from the profile
    # server-side; the client sends only the profile id plus audio transport params.
    query = urlencode(
        {
            "sample_rate": config.uplink_sample_rate,
            "audio_codec": "opus",
            "audio_out": "opus",
            "output_sample_rate": config.output_sample_rate,
            **({"profile": config.profile} if config.profile else {}),
            **({"session_id": session_id} if session_id else {}),
        }
    )
    return f"{scheme}://{config.host}:{config.port}/v1/conversation/stream?{query}"
