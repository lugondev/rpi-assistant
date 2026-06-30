import numpy as np

from a2a_client.playback_buffer import PlaybackBuffer


def test_returns_silence_until_primed():
    b = PlaybackBuffer(prime_samples=10, max_samples=1000)
    b.push(np.arange(1, 6, dtype=np.int16))  # 5 < 10 -> not primed yet
    assert np.array_equal(b.pull(4), np.zeros(4, dtype=np.int16))


def test_drains_in_order_after_prime():
    b = PlaybackBuffer(prime_samples=8, max_samples=1000)
    b.push(np.arange(1, 11, dtype=np.int16))  # 10 >= 8 -> primes
    assert np.array_equal(b.pull(4), np.array([1, 2, 3, 4], dtype=np.int16))
    assert np.array_equal(b.pull(4), np.array([5, 6, 7, 8], dtype=np.int16))


def test_underrun_pads_with_silence_and_counts():
    b = PlaybackBuffer(prime_samples=4, max_samples=1000)
    b.push(np.arange(1, 7, dtype=np.int16))  # 6 >= 4 -> primes
    b.pull(4)  # consumes 1..4
    out = b.pull(4)  # only 5,6 left -> padded
    assert np.array_equal(out, np.array([5, 6, 0, 0], dtype=np.int16))
    assert b.underrun_samples == 2


def test_underrun_does_not_rearm_priming():
    # After an underrun, a single later packet should play immediately (still primed),
    # not wait for a fresh pre-roll.
    b = PlaybackBuffer(prime_samples=4, max_samples=1000)
    b.push(np.arange(1, 5, dtype=np.int16))
    b.pull(4)  # drains to empty (primed)
    b.pull(4)  # underrun (silence)
    b.push(np.array([9, 9], dtype=np.int16))
    assert np.array_equal(b.pull(2), np.array([9, 9], dtype=np.int16))


def test_reset_rearms_priming():
    b = PlaybackBuffer(prime_samples=4, max_samples=1000)
    b.push(np.arange(1, 9, dtype=np.int16))
    b.pull(4)
    b.reset()
    b.push(np.arange(1, 4, dtype=np.int16))  # 3 < 4 -> not primed
    assert np.array_equal(b.pull(4), np.zeros(4, dtype=np.int16))


def test_capacity_is_bounded_keeping_newest():
    b = PlaybackBuffer(prime_samples=2, max_samples=10)
    b.push(np.arange(20, dtype=np.int16))
    assert b.pending() == 10
