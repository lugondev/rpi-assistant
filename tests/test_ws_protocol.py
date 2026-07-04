from a2a_client.config import Config
from a2a_client.ws_protocol import build_ws_url


def _base_config(**overrides) -> Config:
    defaults = dict(
        host="127.0.0.1",
        port=8000,
        secure=False,
        stt_engine="whisper",
        tts_engine="vieneu",
        language="vi",
        voice=None,
        profile=None,
        output="audio,text",
        input_sample_rate=16000,
        output_sample_rate=16000,
        uplink_sample_rate=16000,
        frame_ms=60,
        input_channels=1,
        output_channels=2,
        input_device=None,
        output_device=None,
        playback_preroll_ms=200,
        allow_barge_in=False,
        barge_in_rms_threshold=1200.0,
        barge_in_min_frames=5,
        reconnect_initial_seconds=1.0,
        reconnect_max_seconds=20.0,
        log_events=True,
        led_enabled=False,
        led_yellow_pin=13,
        led_red_pin=22,
        led_green_pin=17,
        input_alsa_device=None,
        output_alsa_device=None,
        oled_enabled=False,
        oled_i2c_port=1,
        oled_i2c_address=0x3C,
        oled_font_path="",
        session_state_path=None,
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_build_ws_url_omits_session_id_when_not_provided():
    url = build_ws_url(_base_config())
    assert "session_id" not in url


def test_build_ws_url_includes_session_id_when_provided():
    url = build_ws_url(_base_config(), session_id="abc-123")
    assert "session_id=abc-123" in url


def test_build_ws_url_omits_session_id_when_none_explicitly():
    url = build_ws_url(_base_config(), session_id=None)
    assert "session_id" not in url
