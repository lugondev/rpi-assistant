from __future__ import annotations

import asyncio
import io
import queue
import shutil
import subprocess
import threading
from typing import Any
import wave
import time

import numpy as np
import opuslib
import sounddevice as sd

from .config import Config
from .playback_buffer import PlaybackBuffer


class AudioIO:
    def __init__(self, config: Config, loop: asyncio.AbstractEventLoop, logger) -> None:
        self.config = config
        self.logger = logger

        self.loop = loop
        self.mic_pcm_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        # Jitter buffer: decoded reply PCM is pushed here; a callback-mode output stream
        # pulls steady blocks, so network jitter never starves the speaker. Pre-roll
        # (~150ms) before playback starts; capacity capped at ~5s.
        self.out_frame_samples = int(self.config.output_sample_rate * self.config.frame_ms / 1000)
        prime = int(self.config.output_sample_rate * self.config.playback_preroll_ms / 1000)
        self.play_buffer = PlaybackBuffer(
            prime_samples=prime, max_samples=self.config.output_sample_rate * 5
        )
        # True between audio_start and turn_done/abort; OR while audio is still queued.
        self._speaking_flag = False
        self._last_status_log_at = 0.0
        self._capture_process: subprocess.Popen[bytes] | None = None
        self._capture_thread: threading.Thread | None = None
        self._capture_stop = threading.Event()
        self._capture_frames_seen = 0

        self.input_stream: sd.InputStream | None = None
        self.output_stream: sd.OutputStream | None = None
        self.encoder: opuslib.Encoder | None = None
        self.decoder: opuslib.Decoder | None = None
        self.negotiated_sample_rate = self.config.input_sample_rate

        self.in_frame_samples = int(self.config.input_sample_rate * self.config.frame_ms / 1000)
        self.uplink_frame_samples = int(self.config.uplink_sample_rate * self.config.frame_ms / 1000)
        self.in_frame_bytes = self.in_frame_samples * self.config.input_channels * 2

    def is_playing(self) -> bool:
        """True while reply audio is playing or still queued — used to gate the mic."""
        return self._speaking_flag or self.play_buffer.pending() > 0

    def set_speaking(self, value: bool) -> None:
        self._speaking_flag = value

    def reset_playback(self) -> None:
        """Stop playback immediately (barge-in / abort): drop queued audio + re-arm."""
        self._speaking_flag = False
        self.play_buffer.reset()

    def on_input_audio(self, indata: np.ndarray, frames: int, _time: Any, status: sd.CallbackFlags) -> None:
        if status:
            now = time.monotonic()
            if now - self._last_status_log_at >= 2.0:
                self._last_status_log_at = now
                self.logger(f"mic callback status: {status}")
        if frames <= 0:
            return
        # Always enqueue; the sender decides whether to drop (half-duplex) or use the
        # frame for barge-in detection. This keeps the gating logic in one place.
        payload = indata.copy().tobytes()
        try:
            self.mic_pcm_queue.put_nowait(payload)
        except queue.Full:
            try:
                _ = self.mic_pcm_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.mic_pcm_queue.put_nowait(payload)
            except queue.Full:
                pass

    def _start_arecord_capture(self) -> bool:
        arecord = shutil.which("arecord")
        if not arecord or not self.config.input_alsa_device:
            return False

        command = [
            arecord,
            "-q",
            "-D",
            self.config.input_alsa_device,
            "-f",
            "S16_LE",
            "-c",
            str(self.config.input_channels),
            "-r",
            str(self.config.input_sample_rate),
            "-t",
            "raw",
        ]
        try:
            self._capture_process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            self.logger(f"arecord capture unavailable: {exc}")
            self._capture_process = None
            return False

        def _reader() -> None:
            assert self._capture_process is not None
            assert self._capture_process.stdout is not None
            frame_size = self.in_frame_bytes
            self.logger(
                f"arecord capture started (device={self.config.input_alsa_device}, rate={self.config.input_sample_rate}Hz, frame={frame_size}B)"
            )
            while not self._capture_stop.is_set():
                chunk = self._capture_process.stdout.read(frame_size)
                if not chunk:
                    break
                self._capture_frames_seen += 1
                if self._capture_frames_seen == 1 or self._capture_frames_seen % 100 == 0:
                    self.logger(f"captured mic frames: {self._capture_frames_seen}")
                try:
                    self.mic_pcm_queue.put_nowait(chunk)
                except queue.Full:
                    try:
                        _ = self.mic_pcm_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.mic_pcm_queue.put_nowait(chunk)
                    except queue.Full:
                        pass

        self._capture_thread = threading.Thread(target=_reader, daemon=True)
        self._capture_thread.start()
        return True

    def start(self) -> None:
        self.encoder = opuslib.Encoder(
            self.config.uplink_sample_rate,
            self.config.input_channels,
            opuslib.APPLICATION_VOIP,
        )
        self.decoder = opuslib.Decoder(
            self.config.output_sample_rate,
            1,
        )

        self._open_output_stream()

        used_arecord = self._start_arecord_capture()
        if not used_arecord:
            self.input_stream = sd.InputStream(
                samplerate=self.config.input_sample_rate,
                channels=self.config.input_channels,
                dtype="int16",
                blocksize=self.in_frame_samples,
                callback=self.on_input_audio,
                device=self.config.input_device,
            )
            self.input_stream.start()

    def on_output_audio(self, outdata: np.ndarray, frames: int, _time: Any, status: sd.CallbackFlags) -> None:
        """PortAudio pulls a steady block from the jitter buffer (silence if underrun)."""
        mono = self.play_buffer.pull(frames)
        if self.config.output_channels == 1:
            outdata[:, 0] = mono
        else:
            for ch in range(self.config.output_channels):
                outdata[:, ch] = mono

    def _open_output_stream(self) -> bool:
        if self.output_stream is not None:
            return True

        output_devices = [self.config.output_device, None]
        last_error: Exception | None = None
        for device in output_devices:
            try:
                self.output_stream = sd.OutputStream(
                    samplerate=self.config.output_sample_rate,
                    channels=self.config.output_channels,
                    dtype="int16",
                    blocksize=self.out_frame_samples,
                    device=device,
                    callback=self.on_output_audio,  # callback mode: pulls from jitter buffer
                )
                self.output_stream.start()
                self.logger(f"sounddevice playback ready (device={device!r})")
                return True
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self.logger(f"output device fallback from {device!r}: {exc}")
                self.output_stream = None

        if last_error is not None:
            self.logger(f"unable to open output stream: {last_error}")
        return False

    def stop(self) -> None:
        self._capture_stop.set()
        if self._capture_process is not None:
            try:
                self._capture_process.terminate()
            except Exception:
                pass
            self._capture_process = None
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2.0)
            self._capture_thread = None
        if self.input_stream is not None:
            self.input_stream.stop()
            self.input_stream.close()
            self.input_stream = None
        if self.output_stream is not None:
            self.output_stream.stop()
            self.output_stream.close()
            self.output_stream = None

    def encode_frame(self, pcm_frame: bytes) -> bytes:
        if self.encoder is None:
            raise RuntimeError("encoder is not initialized")
        return self.encoder.encode(pcm_frame, self.uplink_frame_samples)

    def set_negotiated_sample_rate(self, sample_rate: int) -> None:
        self.negotiated_sample_rate = max(8000, int(sample_rate))

    def get_mic_frame(self, timeout: float = 0.5) -> bytes:
        return self.mic_pcm_queue.get(timeout=timeout)

    def resample_pcm16_mono(self, pcm_bytes: bytes, source_rate: int, target_rate: int) -> bytes:
        if source_rate == target_rate:
            return pcm_bytes

        samples = np.frombuffer(pcm_bytes, dtype=np.int16)
        if len(samples) == 0:
            return pcm_bytes

        duration = len(samples) / float(source_rate)
        target_len = max(1, int(round(duration * target_rate)))
        src_x = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        dst_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        resampled = np.interp(dst_x, src_x, samples.astype(np.float32)).astype(np.int16)
        return resampled.tobytes()

    def play_opus_frame(self, packet: bytes) -> None:
        """Decode one Opus packet and queue it (the output callback plays it)."""
        if self.decoder is None:
            return
        pcm_bytes = self.decoder.decode(packet, self.out_frame_samples)
        self.play_buffer.push(np.frombuffer(pcm_bytes, dtype=np.int16))

    def play_wav_bytes(self, wav_data: bytes) -> None:
        """Decode a whole WAV (url/RIFF reply), resample to the output rate, and queue it
        into the jitter buffer — same playback path as Opus frames (callback-driven)."""
        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            pcm = wf.readframes(wf.getnframes())

        if sample_width != 2:
            self.logger(f"skip wav with unsupported sample width: {sample_width}")
            return

        pcm_arr = np.frombuffer(pcm, dtype=np.int16)
        if channels > 1:
            pcm_arr = pcm_arr.reshape(-1, channels)[:, 0]

        if sample_rate != self.config.output_sample_rate and len(pcm_arr) > 0:
            duration = len(pcm_arr) / float(sample_rate)
            target_len = max(1, int(duration * self.config.output_sample_rate))
            src_x = np.linspace(0.0, 1.0, num=len(pcm_arr), endpoint=False)
            dst_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            pcm_arr = np.interp(dst_x, src_x, pcm_arr.astype(np.float32)).astype(np.int16)

        self.play_buffer.push(pcm_arr)
