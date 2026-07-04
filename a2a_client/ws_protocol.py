from __future__ import annotations

from urllib.parse import urlencode

from .config import Config


def build_ws_url(config: Config, session_id: str | None = None) -> str:
    scheme = "wss" if config.secure else "ws"
    query = urlencode(
        {
            "stt_engine": config.stt_engine,
            "tts_engine": config.tts_engine,
            "language": config.language,
            "sample_rate": config.uplink_sample_rate,
            "audio_codec": "opus",
            "output": config.output,
            "audio_out": "opus",
            "output_sample_rate": config.output_sample_rate,
            **({"voice": config.voice} if config.voice else {}),
            **({"profile": config.profile} if config.profile else {}),
            **({"session_id": session_id} if session_id else {}),
        }
    )
    return f"{scheme}://{config.host}:{config.port}/v1/conversation/stream?{query}"
