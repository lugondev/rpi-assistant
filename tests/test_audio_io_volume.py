import numpy as np

from a2a_client.audio_io import AudioIO
from a2a_client.config import Config


def _config(**overrides) -> Config:
    defaults = dict(
        host="127.0.0.1", port=8000, secure=False, profile=None,
        input_sample_rate=16000, output_sample_rate=16000,
        uplink_sample_rate=16000, frame_ms=60, input_channels=1, output_channels=1,
        input_device=None, output_device=None, playback_preroll_ms=200,
        allow_barge_in=False, barge_in_rms_threshold=1200.0, barge_in_min_frames=5,
        reconnect_initial_seconds=1.0, reconnect_max_seconds=20.0, log_events=False,
        led_enabled=False, led_yellow_pin=13, led_red_pin=22, led_green_pin=17,
        input_alsa_device=None, output_alsa_device=None, oled_enabled=False,
        oled_i2c_port=1, oled_i2c_address=0x3C, oled_font_path="",
        session_state_path="/tmp/does-not-matter",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _audio() -> AudioIO:
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return AudioIO(config=_config(), loop=loop, logger=lambda msg: None)
    finally:
        loop.close()


def test_default_volume_is_100():
    audio = _audio()
    assert audio.get_volume_pct() == 100


def test_set_volume_pct_clamps_above_100():
    audio = _audio()
    audio.set_volume_pct(150)
    assert audio.get_volume_pct() == 100


def test_set_volume_pct_clamps_below_0():
    audio = _audio()
    audio.set_volume_pct(-10)
    assert audio.get_volume_pct() == 0


def test_on_output_audio_at_full_volume_is_unchanged():
    audio = _audio()
    audio.play_buffer.push(np.full(4000, 1234, dtype=np.int16))
    outdata = np.zeros((100, 1), dtype=np.int16)
    audio.on_output_audio(outdata, 100, None, None)
    assert np.array_equal(outdata[:, 0], np.full(100, 1234, dtype=np.int16))


def test_on_output_audio_applies_half_volume_gain():
    audio = _audio()
    audio.set_volume_pct(50)
    audio.play_buffer.push(np.full(4000, 1000, dtype=np.int16))
    outdata = np.zeros((100, 1), dtype=np.int16)
    audio.on_output_audio(outdata, 100, None, None)
    assert np.allclose(outdata[:, 0], 500, atol=1)


def test_on_output_audio_zero_volume_is_silent():
    audio = _audio()
    audio.set_volume_pct(0)
    audio.play_buffer.push(np.full(4000, 1000, dtype=np.int16))
    outdata = np.zeros((100, 1), dtype=np.int16)
    audio.on_output_audio(outdata, 100, None, None)
    assert np.all(outdata[:, 0] == 0)
