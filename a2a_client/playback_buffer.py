from __future__ import annotations

import threading

import numpy as np


class PlaybackBuffer:
    """Thread-safe jitter buffer of int16 mono PCM for callback-mode playback.

    The network/decoder side ``push()``es decoded PCM as it arrives; the audio output
    callback ``pull()``s fixed-size blocks at the device's steady rate. This decouples
    *when frames arrive over the network* from *when they are played*, which is what
    makes playback seamless instead of underrunning on every bit of WiFi jitter.

    Playback is gated by a pre-roll: ``pull()`` returns silence until ``prime_samples``
    have accumulated, then drains continuously. A momentary underrun (buffer ran dry)
    is padded with silence — a brief gap — but does NOT re-arm the pre-roll, so a single
    late packet costs a tiny gap, not a stall or a fresh buffering delay. ``reset()``
    clears the buffer and re-arms the pre-roll; call it at the end of an assistant turn
    or on barge-in so the next reply primes cleanly.
    """

    def __init__(self, prime_samples: int, max_samples: int) -> None:
        self.prime_samples = max(0, int(prime_samples))
        self.max_samples = max(1, int(max_samples))
        self._buf = np.zeros(0, dtype=np.int16)
        self._primed = False
        self._lock = threading.Lock()
        self.underrun_samples = 0

    def push(self, samples: np.ndarray) -> None:
        if samples.dtype != np.int16:
            samples = samples.astype(np.int16)
        with self._lock:
            self._buf = np.concatenate([self._buf, samples])
            if len(self._buf) > self.max_samples:  # bound memory (runaway producer)
                self._buf = self._buf[-self.max_samples :]

    def pull(self, n: int) -> np.ndarray:
        with self._lock:
            if not self._primed:
                if len(self._buf) >= self.prime_samples and len(self._buf) >= n:
                    self._primed = True
                else:
                    return np.zeros(n, dtype=np.int16)
            if len(self._buf) >= n:
                out = self._buf[:n]
                self._buf = self._buf[n:]
                return out
            # underrun: emit what we have, pad the rest with silence (a brief gap)
            missing = n - len(self._buf)
            out = np.concatenate([self._buf, np.zeros(missing, dtype=np.int16)])
            self.underrun_samples += missing
            self._buf = np.zeros(0, dtype=np.int16)
            return out

    def reset(self) -> None:
        with self._lock:
            self._buf = np.zeros(0, dtype=np.int16)
            self._primed = False

    def pending(self) -> int:
        with self._lock:
            return len(self._buf)
