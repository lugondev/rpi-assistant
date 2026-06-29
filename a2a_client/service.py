from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
import queue

import numpy as np
import websockets

from .audio_io import AudioIO
from .config import Config
from .led_status import LedConfig, LedStatusController
from .oled_status import OledConfig, OledStatusController
from .ws_protocol import build_ws_url


class AudioToAudioService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        self.audio = AudioIO(config=config, loop=self.loop, logger=self.log)
        self.leds = LedStatusController(
            LedConfig(
                enabled=config.led_enabled,
                yellow_pin=config.led_yellow_pin,
                red_pin=config.led_red_pin,
                green_pin=config.led_green_pin,
            ),
            logger=self.log,
        )
        self.oled = OledStatusController(
            OledConfig(
                enabled=config.oled_enabled,
                i2c_port=config.oled_i2c_port,
                i2c_address=config.oled_i2c_address,
            ),
            logger=self.log,
        )
        self._session_ready = asyncio.Event()
        self._negotiated_audio_codec = "opus"
        self._negotiated_audio_out = "opus"
        self._uplink_frames_sent = 0
        self._last_uplink_log_at = 0.0
        self._idle_reset_handle: asyncio.TimerHandle | None = None

    def log(self, message: str) -> None:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] {message}", flush=True)

    def _cancel_idle_reset(self) -> None:
        if self._idle_reset_handle is not None:
            self._idle_reset_handle.cancel()
            self._idle_reset_handle = None

    def _set_ready(self) -> None:
        self._idle_reset_handle = None
        self.leds.ready()
        self.oled.ready()

    def _schedule_ready_reset(self, delay: float = 1.5) -> None:
        self._cancel_idle_reset()
        self._idle_reset_handle = self.loop.call_later(delay, self._set_ready)

    def _build_absolute_url(self, maybe_relative_url: str) -> str:
        if maybe_relative_url.startswith("http://") or maybe_relative_url.startswith("https://"):
            return maybe_relative_url
        scheme = "https" if self.config.secure else "http"
        base = f"{scheme}://{self.config.host}:{self.config.port}"
        return urllib.parse.urljoin(base, maybe_relative_url)

    def _warm_stt_engine(self) -> None:
        warm_url = self._build_absolute_url(f"/v1/stt/warm?engine={urllib.parse.quote(self.config.stt_engine)}")

        try:
            self.log(f"warming stt engine: {self.config.stt_engine}")
            req = urllib.request.Request(warm_url, method="POST")
            with urllib.request.urlopen(req, timeout=60) as response:  # nosec B310
                _ = response.read()
            self.log(f"stt engine warmed: {self.config.stt_engine}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"stt warm failed: {exc}")

    async def _play_audio_url(self, maybe_relative_url: str) -> None:
        audio_url = self._build_absolute_url(maybe_relative_url)

        def _download() -> bytes:
            with urllib.request.urlopen(audio_url, timeout=20) as response:  # nosec B310
                return response.read()

        self.audio.playback_active = True
        try:
            wav_data = await asyncio.to_thread(_download)
            await asyncio.to_thread(self.audio.play_wav_bytes, wav_data)
        except Exception as exc:  # noqa: BLE001
            self.log(f"audio url playback error: {exc}")
            self.oled.error("AUDIO ERR")
        finally:
            self.audio.playback_active = False

    async def sender(self, ws: websockets.WebSocketClientProtocol) -> None:
        await self._session_ready.wait()

        buffer = bytearray()
        while not self.stop_event.is_set():
            try:
                chunk = await asyncio.to_thread(self.audio.get_mic_frame, 0.5)
            except queue.Empty:
                continue
            if self.audio.playback_active:
                continue
            buffer.extend(chunk)

            while len(buffer) >= self.audio.in_frame_bytes:
                pcm_frame = bytes(buffer[: self.audio.in_frame_bytes])
                del buffer[: self.audio.in_frame_bytes]
                try:
                    if self._negotiated_audio_codec == "opus":
                        pcm_frame = self.audio.resample_pcm16_mono(
                            pcm_frame,
                            source_rate=self.config.input_sample_rate,
                            target_rate=self.config.uplink_sample_rate,
                        )
                        packet = self.audio.encode_frame(pcm_frame)
                    else:
                        packet = self.audio.resample_pcm16_mono(
                            pcm_frame,
                            source_rate=self.config.input_sample_rate,
                            target_rate=self.config.uplink_sample_rate,
                        )
                    await ws.send(packet)
                    self._uplink_frames_sent += 1
                    if self.config.log_events:
                        now = time.monotonic()
                        if now - self._last_uplink_log_at >= 2.0:
                            self._last_uplink_log_at = now
                            samples = np.frombuffer(pcm_frame, dtype=np.int16)
                            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) else 0.0
                            self.log(
                                "uplink sent: "
                                f"frames={self._uplink_frames_sent} codec={self._negotiated_audio_codec} "
                                f"bytes={len(packet)} rms={rms:.1f}"
                            )
                except Exception as exc:  # noqa: BLE001
                    self.log(f"sender error: {exc}")
                    self.leds.error()
                    self.oled.error("SEND ERR")
                    return

    async def receiver(self, ws: websockets.WebSocketClientProtocol) -> None:
        while not self.stop_event.is_set():
            try:
                message = await ws.recv()
            except websockets.ConnectionClosed:
                self.log("websocket closed")
                self.leds.connecting()
                self.oled.connecting()
                return

            if isinstance(message, bytes):
                self.log(f"binary message: {len(message)} bytes head={message[:8].hex()}")
                if message.startswith(b"RIFF"):
                    self.audio.playback_active = True
                    try:
                        await asyncio.to_thread(self.audio.play_wav_bytes, message)
                    finally:
                        self.audio.playback_active = False
                    continue
                if self._negotiated_audio_out != "opus":
                    continue
                if self.audio.playback_expected_frames <= 0:
                    continue
                self.audio.play_opus_frame(message)
                self.audio.playback_expected_frames -= 1
                if self.audio.playback_expected_frames == 0:
                    self.audio.playback_active = False
                continue

            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                self.log(f"non-json message: {message}")
                continue

            name = event.get("event")
            if name == "speech_start":
                self._cancel_idle_reset()
                self.leds.listening()
                self.oled.listening()
                self._schedule_ready_reset()
            elif name == "speech_end":
                self._cancel_idle_reset()
                self.leds.processing()
                self.oled.processing()
                self._schedule_ready_reset()
            elif name == "audio_start":
                self._cancel_idle_reset()
                self.audio.playback_expected_frames = int(event.get("frames", 0))
                self.audio.playback_active = self.audio.playback_expected_frames > 0
                self.leds.speaking()
                self.oled.speaking()
                self._schedule_ready_reset(delay=3.0)
            elif name == "audio_chunk":
                audio_url = event.get("audio_url")
                if audio_url:
                    self._cancel_idle_reset()
                    self.leds.speaking()
                    self.oled.speaking()
                    await self._play_audio_url(str(audio_url))
                    self._schedule_ready_reset(delay=3.0)
            elif name == "audio_end":
                self._cancel_idle_reset()
                self.audio.playback_expected_frames = 0
                self.audio.playback_active = False
                self._set_ready()
            elif name in {"aborted", "turn_done", "reset"}:
                self._cancel_idle_reset()
                self.audio.playback_expected_frames = 0
                self.audio.playback_active = False
                self._set_ready()
            elif name == "error":
                self._cancel_idle_reset()
                self.log(f"server error: {event.get('message', 'unknown')}")
                self.leds.error()
                self.oled.error("SERVER ERR")
            elif name == "processing":
                self._cancel_idle_reset()
                self.leds.processing()
                self.oled.processing()
                self._schedule_ready_reset()
            elif name == "session_started":
                self._cancel_idle_reset()
                self._negotiated_audio_codec = str(event.get("audio_codec", "pcm16"))
                self._negotiated_audio_out = str(event.get("audio_out", "url"))
                self.audio.set_negotiated_sample_rate(int(event.get("sample_rate") or self.config.input_sample_rate))
                self._session_ready.set()
                self.log(
                    "session started: "
                    f"stt={event.get('stt_engine')} tts={event.get('tts_engine')} "
                    f"codec={self._negotiated_audio_codec} audio_out={self._negotiated_audio_out} "
                    f"sample_rate={self.audio.negotiated_sample_rate}"
                )
                self._set_ready()
            elif name == "user_transcript":
                text = (event.get("text") or "").strip()
                if text:
                    self.log(f"you: {text}")
                    self._cancel_idle_reset()
                    self.leds.listening()
                    self.oled.listening()
                    self._schedule_ready_reset()
                else:
                    self._set_ready()
            elif name == "response_text":
                text = (event.get("text") or "").strip()
                audio_url = event.get("audio_url")
                if text:
                    self.log(f"assistant: {text}")
                if audio_url:
                    self._cancel_idle_reset()
                    self.leds.speaking()
                    self.oled.speaking()
                    await self._play_audio_url(str(audio_url))
                    self._schedule_ready_reset(delay=3.0)
                elif text:
                    self._cancel_idle_reset()
                    self.leds.processing()
                    self.oled.processing()
                    self._schedule_ready_reset()
                else:
                    self._set_ready()
            elif self.config.log_events:
                self.log(f"event: {name} payload={event}")

    async def run_forever(self) -> None:
        ws_url = build_ws_url(self.config)
        self.log(f"target websocket: {ws_url}")

        await asyncio.to_thread(self._warm_stt_engine)
        self.audio.start()
        self.log(
            "audio started "
            f"(mic={self.config.input_sample_rate}Hz, speaker={self.config.output_sample_rate}Hz, frame={self.config.frame_ms}ms)"
        )
        backoff = self.config.reconnect_initial_seconds

        try:
            while not self.stop_event.is_set():
                self._session_ready.clear()
                self._negotiated_audio_codec = "opus"
                self._negotiated_audio_out = "opus"
                self.leds.connecting()
                self.oled.connecting()
                try:
                    async with websockets.connect(ws_url, max_size=None, ping_interval=20, ping_timeout=20) as ws:
                        self.log("connected")
                        backoff = self.config.reconnect_initial_seconds
                        sender_task = asyncio.create_task(self.sender(ws))
                        receiver_task = asyncio.create_task(self.receiver(ws))
                        done, pending = await asyncio.wait(
                            {sender_task, receiver_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in pending:
                            task.cancel()
                        for task in done:
                            _ = task.exception()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.log(f"connection error: {exc}")
                    self.leds.error()
                    self.oled.error("WS ERR")

                if self.stop_event.is_set():
                    break
                self.log(f"reconnect in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.config.reconnect_max_seconds)
        finally:
            self._cancel_idle_reset()
            self.audio.stop()
            self.leds.stopped()
            self.oled.stopped()
            self.leds.close()
            self.oled.close()
            self.log("audio stopped")
