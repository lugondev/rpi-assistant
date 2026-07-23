import numpy as np

from a2a_client.audio_io import AudioIO
from a2a_client.config import Config


def _config(**overrides) -> Config:
    defaults = dict(
        host="127.0.0.1",
        port=8000,
        secure=False,
        profile=None,
        input_sample_rate=16000,
        output_sample_rate=16000,
        uplink_sample_rate=16000,
        frame_ms=60,
        input_channels=1,
        output_channels=1,
        input_device=None,
        output_device=None,
        playback_preroll_ms=200,
        allow_barge_in=False,
        barge_in_rms_threshold=1200.0,
        barge_in_min_frames=5,
        reconnect_initial_seconds=1.0,
        reconnect_max_seconds=20.0,
        log_events=False,
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
        session_state_path="/tmp/does-not-matter",
        device_token=None,
        device_token_path="/tmp/device_token",
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


def test_play_tone_queues_audible_samples():
    audio = _audio()
    assert audio.play_buffer.pending() == 0

    audio.play_tone([440.0], tone_ms=150)

    assert audio.play_buffer.pending() > 0
    assert audio.is_playing() is True


def test_play_tone_multi_beep_is_longer_than_single_beep():
    single = _audio()
    single.play_tone([440.0], tone_ms=150, gap_ms=60)

    double = _audio()
    double.play_tone([440.0, 440.0], tone_ms=150, gap_ms=60)

    assert double.play_buffer.pending() > single.play_buffer.pending()


def test_play_tone_empty_list_is_a_noop():
    audio = _audio()
    audio.play_tone([])
    assert audio.play_buffer.pending() == 0
    assert audio.is_playing() is False


def test_play_tone_samples_are_within_int16_range_and_scaled_by_level():
    audio = _audio()
    audio.play_tone([440.0], tone_ms=50, level=0.2)
    n = audio.play_buffer.pending()
    samples = audio.play_buffer.pull(n)
    assert samples.dtype == np.int16
    assert np.max(np.abs(samples)) <= int(0.2 * 32767) + 1
