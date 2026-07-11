from a2a_client.config import Config
from a2a_client.ws_protocol import build_ws_url, build_wakeup_message


def _base_config(**overrides) -> Config:
    defaults = dict(
        host="127.0.0.1", port=8000, secure=False, profile=None,
        input_sample_rate=16000, output_sample_rate=16000, uplink_sample_rate=16000,
        frame_ms=60, input_channels=1, output_channels=2, input_device=None,
        output_device=None, playback_preroll_ms=200, allow_barge_in=False,
        barge_in_rms_threshold=1200.0, barge_in_min_frames=5,
        reconnect_initial_seconds=1.0, reconnect_max_seconds=20.0, log_events=True,
        led_enabled=False, led_yellow_pin=13, led_red_pin=22, led_green_pin=17,
        input_alsa_device=None, output_alsa_device=None, oled_enabled=False,
        oled_i2c_port=1, oled_i2c_address=0x3C, oled_font_path="",
        session_state_path=None,
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_build_ws_url_has_no_query_params():
    url = build_ws_url(_base_config())
    assert url == "ws://127.0.0.1:8000/v1/lugo/stream"


def test_build_ws_url_uses_wss_when_secure():
    url = build_ws_url(_base_config(secure=True))
    assert url.startswith("wss://")


def test_build_wakeup_message_always_enables_mcp():
    msg = build_wakeup_message(_base_config(), session_id=None)
    assert msg["type"] == "wakeup"
    assert msg["features"] == {"mcp": True}


def test_build_wakeup_message_includes_audio_params():
    msg = build_wakeup_message(_base_config(uplink_sample_rate=16000, output_sample_rate=24000), session_id=None)
    assert msg["audio_params"] == {"sample_rate": 16000, "output_sample_rate": 24000}


def test_build_wakeup_message_omits_session_id_when_none():
    msg = build_wakeup_message(_base_config(), session_id=None)
    assert "session_id" not in msg


def test_build_wakeup_message_includes_session_id_when_provided():
    msg = build_wakeup_message(_base_config(), session_id="abc-123")
    assert msg["session_id"] == "abc-123"


def test_build_wakeup_message_omits_profile_when_not_set():
    msg = build_wakeup_message(_base_config(profile=None), session_id=None)
    assert "profile" not in msg


def test_build_wakeup_message_includes_profile_when_set():
    msg = build_wakeup_message(_base_config(profile="home"), session_id=None)
    assert msg["profile"] == "home"
