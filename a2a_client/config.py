from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass
class Config:
    host: str
    port: int
    secure: bool
    stt_engine: str
    tts_engine: str
    language: str
    voice: str | None
    output: str
    input_sample_rate: int
    output_sample_rate: int
    uplink_sample_rate: int
    frame_ms: int
    input_channels: int
    output_channels: int
    input_device: int | str | None
    output_device: int | str | None
    playback_preroll_ms: int
    allow_barge_in: bool
    barge_in_rms_threshold: float
    barge_in_min_frames: int
    reconnect_initial_seconds: float
    reconnect_max_seconds: float
    log_events: bool
    led_enabled: bool
    led_yellow_pin: int
    led_red_pin: int
    led_green_pin: int
    input_alsa_device: str | None
    output_alsa_device: str | None
    oled_enabled: bool
    oled_i2c_port: int
    oled_i2c_address: int


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    server = raw.get("server", {})
    session = raw.get("session", {})
    audio = raw.get("audio", {})
    service = raw.get("service", {})
    led = raw.get("led", {})
    oled = raw.get("oled", {})

    return Config(
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8000)),
        secure=bool(server.get("secure", False)),
        stt_engine=str(session.get("stt_engine", "whisper")),
        tts_engine=str(session.get("tts_engine", "vieneu")),
        language=str(session.get("language", "vi")),
        voice=session.get("voice"),
        output=str(session.get("output", "audio,text")),
        input_sample_rate=int(audio.get("input_sample_rate", 16000)),
        output_sample_rate=int(audio.get("output_sample_rate", 16000)),
        uplink_sample_rate=int(audio.get("uplink_sample_rate", 16000)),
        frame_ms=int(audio.get("frame_ms", 60)),
        input_channels=int(audio.get("input_channels", 1)),
        output_channels=int(audio.get("output_channels", 2)),
        input_device=audio.get("input_device"),
        output_device=audio.get("output_device"),
        playback_preroll_ms=int(audio.get("playback_preroll_ms", 200)),
        allow_barge_in=bool(session.get("allow_barge_in", False)),
        barge_in_rms_threshold=float(session.get("barge_in_rms_threshold", 1200.0)),
        barge_in_min_frames=int(session.get("barge_in_min_frames", 5)),
        reconnect_initial_seconds=float(service.get("reconnect_initial_seconds", 1.0)),
        reconnect_max_seconds=float(service.get("reconnect_max_seconds", 20.0)),
        log_events=bool(service.get("log_events", True)),
        led_enabled=bool(led.get("enabled", True)),
        led_yellow_pin=int(led.get("yellow_pin", 13)),
        led_red_pin=int(led.get("red_pin", 22)),
        led_green_pin=int(led.get("green_pin", 17)),
        input_alsa_device=audio.get("input_alsa_device"),
        output_alsa_device=audio.get("output_alsa_device"),
        oled_enabled=bool(oled.get("enabled", True)),
        oled_i2c_port=int(oled.get("i2c_port", 1)),
        oled_i2c_address=int(oled.get("i2c_address", 0x3C)),
    )
