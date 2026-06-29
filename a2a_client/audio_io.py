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


class AudioIO:
    def __init__(self, config: Config, loop: asyncio.AbstractEventLoop, logger) -> None:
        self.config = config
        self.logger = logger

        self.loop = loop
        self.mic_pcm_queue: queue.Queue[bytes] = queue.Queue(maxsize=200)
        self.playback_expected_frames = 0
        self.playback_active = False
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
        self.out_frame_samples = int(self.config.output_sample_rate * self.config.frame_ms / 1000)
        self.in_frame_bytes = self.in_frame_samples * self.config.input_channels * 2

    def on_input_audio(self, indata: np.ndarray, frames: int, _time: Any, status: sd.CallbackFlags) -> None:
        if status:
            now = time.monotonic()
            if now - self._last_status_log_at >= 2.0:
                self._last_status_log_at = now
                self.logger(f"mic callback status: {status}")
        if frames <= 0 or self.playback_active:
            return

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

        if self.output_stream is not None:
            self.output_stream.start()

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
        if self.decoder is None or self.output_stream is None:
            return
        pcm_bytes = self.decoder.decode(packet, self.out_frame_samples)
        pcm_mono = np.frombuffer(pcm_bytes, dtype=np.int16)
        self.write_output_pcm_mono(pcm_mono)

    def write_output_pcm_mono(self, pcm_mono: np.ndarray) -> None:
        if self.output_stream is None and not self._open_output_stream():
            return
        if self.config.output_channels == 1:
            pcm = pcm_mono
        else:
            pcm = np.repeat(pcm_mono[:, np.newaxis], self.config.output_channels, axis=1)
        self.output_stream.write(pcm)

    def play_wav_bytes(self, wav_data: bytes) -> None:
        if self.output_stream is not None:
            with wave.open(io.BytesIO(wav_data), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                frames = wf.getnframes()
                pcm = wf.readframes(frames)

            if sample_width != 2:
                self.logger(f"skip wav with unsupported sample width: {sample_width}")
                return

            pcm_arr = np.frombuffer(pcm, dtype=np.int16)
            if channels > 1:
                pcm_arr = pcm_arr.reshape(-1, channels)
                pcm_arr = pcm_arr[:, 0]

            if sample_rate != self.config.output_sample_rate and len(pcm_arr) > 0:
                duration = len(pcm_arr) / float(sample_rate)
                target_len = max(1, int(duration * self.config.output_sample_rate))
                src_x = np.linspace(0.0, 1.0, num=len(pcm_arr), endpoint=False)
                dst_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
                pcm_arr = np.interp(dst_x, src_x, pcm_arr.astype(np.float32)).astype(np.int16)

            idx = 0
            chunk = max(1, self.out_frame_samples)
            while idx < len(pcm_arr):
                self.write_output_pcm_mono(pcm_arr[idx : idx + chunk])
                idx += chunk
            return

        aplay = shutil.which("aplay")
        device = self.config.output_alsa_device
        if aplay and device:
            command = [aplay, "-q", "-D", device, "-"]
            try:
                subprocess.run(
                    command,
                    input=wav_data,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=True,
                )
                return
            except subprocess.CalledProcessError as exc:
                stderr_text = exc.stderr.decode(errors="ignore").strip() if exc.stderr else ""
                self.logger(f"aplay playback failed: {stderr_text or exc}")
            except Exception as exc:  # noqa: BLE001
                self.logger(f"aplay playback unavailable: {exc}")

        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.getnframes()
            pcm = wf.readframes(frames)

        if sample_width != 2:
            self.logger(f"skip wav with unsupported sample width: {sample_width}")
            return

        pcm_arr = np.frombuffer(pcm, dtype=np.int16)
        if channels > 1:
            pcm_arr = pcm_arr.reshape(-1, channels)
            pcm_arr = pcm_arr[:, 0]

        if sample_rate != self.config.output_sample_rate and len(pcm_arr) > 0:
            duration = len(pcm_arr) / float(sample_rate)
            target_len = max(1, int(duration * self.config.output_sample_rate))
            src_x = np.linspace(0.0, 1.0, num=len(pcm_arr), endpoint=False)
            dst_x = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            pcm_arr = np.interp(dst_x, src_x, pcm_arr.astype(np.float32)).astype(np.int16)

        idx = 0
        chunk = max(1, self.out_frame_samples)
        while idx < len(pcm_arr):
            self.write_output_pcm_mono(pcm_arr[idx : idx + chunk])
            idx += chunk
