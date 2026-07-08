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
from .session_state import load_session_id, save_session_id
from .ws_protocol import build_ws_url

# Local beeps (no network/TTS involved — audible even while those are cold-loading).
_WARMING_TONE_HZ = [440.0, 440.0]  # two flat beeps: "still starting up, please wait"
_READY_TONE_HZ = [523.0, 784.0]  # short rising chime: "ready now"
_WARMING_REMINDER_SECONDS = 8.0  # repeat the cue while the user might keep talking


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
                font_path=config.oled_font_path,
            ),
            logger=self.log,
        )
        self._session_ready = asyncio.Event()
        self._negotiated_audio_codec = "opus"
        self._negotiated_audio_out = "opus"
        self._uplink_frames_sent = 0
        self._last_uplink_log_at = 0.0
        self._idle_reset_handle: asyncio.TimerHandle | None = None
        self._barge_frames = 0  # consecutive loud mic frames during playback (barge-in)
        # Resume the same server-side session (and its chat history) across
        # reconnects and full restarts instead of minting a fresh one every time.
        self._session_id: str | None = load_session_id(config.session_state_path)
        self._warming_reminder_task: asyncio.Task | None = None
        # False until session_started confirms both engines warm (or engines_ready
        # arrives later). Gates every "things are fine" LED/OLED state so the first
        # utterance — detected by the server's VAD, which needs no model at all —
        # can't flip the display to "listening"/ready and hide that STT/TTS are
        # still cold-loading underneath.
        self._engines_ready = False

    def log(self, message: str) -> None:
        # Millisecond precision: diagnosing first-turn latency needs sub-second
        # deltas between events (connected -> session_started -> speech_end ->
        # first response), which second-resolution timestamps can't show.
        now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
        print(f"[{now}] {message}", flush=True)

    def _cancel_idle_reset(self) -> None:
        if self._idle_reset_handle is not None:
            self._idle_reset_handle.cancel()
            self._idle_reset_handle = None

    def _set_ready(self) -> None:
        self._idle_reset_handle = None
        if not self._engines_ready:
            self.leds.warming()
            self.oled.warming()
            return
        self.leds.ready()
        self.oled.ready()

    def _status(self, kind: str) -> None:
        """Reflect a turn-taking state on LED/OLED — unless engines are still
        cold-loading, in which case keep showing "warming up" instead. Needed
        because speech_start/speech_end etc. are driven by the server's VAD
        endpointer, which needs no STT/TTS model and fires even on the very
        first (still-cold) utterance."""
        if not self._engines_ready:
            self.leds.warming()
            self.oled.warming()
            return
        getattr(self.leds, kind)()
        getattr(self.oled, kind)()

    def _schedule_ready_reset(self, delay: float = 1.5) -> None:
        self._cancel_idle_reset()
        self._idle_reset_handle = self.loop.call_later(delay, self._set_ready)

    async def _warming_reminder_loop(self) -> None:
        """Repeat the "please wait" beep while engines are still cold-loading —
        the user may keep talking without it, having no other feedback (LED/OLED
        are easy to miss on a voice-first device)."""
        try:
            while True:
                await asyncio.sleep(_WARMING_REMINDER_SECONDS)
                await asyncio.to_thread(self.audio.play_tone, _WARMING_TONE_HZ)
        except asyncio.CancelledError:
            raise

    def _start_warming_reminder(self) -> None:
        if self._warming_reminder_task is None or self._warming_reminder_task.done():
            self._warming_reminder_task = asyncio.create_task(self._warming_reminder_loop())

    def _stop_warming_reminder(self) -> None:
        if self._warming_reminder_task is not None:
            self._warming_reminder_task.cancel()
            self._warming_reminder_task = None

    def _build_absolute_url(self, maybe_relative_url: str) -> str:
        if maybe_relative_url.startswith("http://") or maybe_relative_url.startswith("https://"):
            return maybe_relative_url
        scheme = "https" if self.config.secure else "http"
        base = f"{scheme}://{self.config.host}:{self.config.port}"
        return urllib.parse.urljoin(base, maybe_relative_url)

    def _warm_stt_engine(self) -> None:
        # The gateway resolves which STT model to warm from the profile (STT/TTS/LLM
        # all live in the profile now), so we pass the profile, not an engine name.
        # With no profile the server warms its own default engine.
        label = self.config.profile or "server default"
        query = f"?profile={urllib.parse.quote(self.config.profile)}" if self.config.profile else ""
        warm_url = self._build_absolute_url(f"/v1/stt/warm{query}")

        try:
            self.log(f"warming stt for profile: {label}")
            req = urllib.request.Request(warm_url, method="POST")
            with urllib.request.urlopen(req, timeout=60) as response:  # nosec B310
                _ = response.read()
            self.log(f"stt warmed for profile: {label}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"stt warm failed: {exc}")

    async def _play_audio_url(self, maybe_relative_url: str) -> None:
        audio_url = self._build_absolute_url(maybe_relative_url)

        def _download() -> bytes:
            with urllib.request.urlopen(audio_url, timeout=20) as response:  # nosec B310
                return response.read()

        self.audio.set_speaking(True)
        try:
            wav_data = await asyncio.to_thread(_download)
            # Queue into the jitter buffer; the output callback plays it out (the buffer
            # staying non-empty keeps is_playing() true until it actually finishes).
            await asyncio.to_thread(self.audio.play_wav_bytes, wav_data)
        except Exception as exc:  # noqa: BLE001
            self.log(f"audio url playback error: {exc}")
            self.oled.error("AUDIO ERR")

    async def sender(self, ws: websockets.WebSocketClientProtocol) -> None:
        await self._session_ready.wait()
        self.log("mic uplink starting")  # timing anchor for first-turn latency diagnosis

        buffer = bytearray()
        while not self.stop_event.is_set():
            try:
                chunk = await asyncio.to_thread(self.audio.get_mic_frame, 0.5)
            except queue.Empty:
                continue
            buffer.extend(chunk)

            while len(buffer) >= self.audio.in_frame_bytes:
                pcm_frame = bytes(buffer[: self.audio.in_frame_bytes])
                del buffer[: self.audio.in_frame_bytes]

                # Half-duplex / barge-in gating. While the assistant is playing we
                # normally drop mic frames (the speaker would bleed into the mic). If
                # barge-in is enabled, a run of LOUD mic frames (above the speaker
                # bleed) is treated as the user interrupting: stop playback, tell the
                # server, and resume uplinking from this frame on.
                if self.audio.is_playing():
                    if not self.config.allow_barge_in:
                        continue
                    samples = np.frombuffer(pcm_frame, dtype=np.int16)
                    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) else 0.0
                    if rms >= self.config.barge_in_rms_threshold:
                        self._barge_frames += 1
                    else:
                        self._barge_frames = 0
                    if self._barge_frames < self.config.barge_in_min_frames:
                        continue
                    self._barge_frames = 0
                    self.audio.reset_playback()
                    try:
                        await ws.send(json.dumps({"type": "abort"}))
                    except Exception:  # noqa: BLE001
                        pass
                    self.log("barge-in: user interrupted playback")
                else:
                    self._barge_frames = 0

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
                if message.startswith(b"RIFF"):
                    self.audio.set_speaking(True)
                    await asyncio.to_thread(self.audio.play_wav_bytes, message)
                    continue
                if self._negotiated_audio_out != "opus":
                    continue
                # Decode + queue the Opus frame; the output callback plays it from the
                # jitter buffer at a steady rate (seamless across network jitter).
                self.audio.set_speaking(True)
                self.audio.play_opus_frame(message)
                continue

            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                self.log(f"non-json message: {message}")
                continue

            name = event.get("event")
            if name == "speech_start":
                self._cancel_idle_reset()
                self._status("listening")
                self._schedule_ready_reset()
            elif name == "speech_end":
                self._cancel_idle_reset()
                self._status("processing")
                self._schedule_ready_reset()
            elif name == "audio_start":
                self._cancel_idle_reset()
                self.audio.set_speaking(True)
                self._status("speaking")
                self._schedule_ready_reset(delay=3.0)
            elif name == "audio_chunk":
                audio_url = event.get("audio_url")
                if audio_url:
                    self._cancel_idle_reset()
                    self._status("speaking")
                    await self._play_audio_url(str(audio_url))
                    self._schedule_ready_reset(delay=3.0)
            elif name == "audio_end":
                # End of one sentence — let the queued tail finish playing; the next
                # sentence (if any) keeps the buffer fed. Don't stop here.
                self._cancel_idle_reset()
                self._schedule_ready_reset(delay=3.0)
            elif name == "turn_done":
                self._cancel_idle_reset()
                self.audio.set_speaking(False)  # buffer drains naturally; mic re-enables when empty
                if self.config.log_events:
                    # Diagnostic: rising underrun = playback was starved (network/pacing);
                    # ~0 = smooth. Helps tell client-side starvation from server-side gaps.
                    self.log(f"playback underrun total: {self.audio.play_buffer.underrun_samples} samples")
                self._set_ready()
            elif name in {"aborted", "reset"}:
                self._cancel_idle_reset()
                self.audio.reset_playback()  # interrupt: drop queued audio immediately
                self._set_ready()
            elif name == "error":
                self._cancel_idle_reset()
                self.log(f"server error: {event.get('message', 'unknown')}")
                self.leds.error()
                self.oled.error("SERVER ERR")
            elif name == "processing":
                self._cancel_idle_reset()
                self._status("processing")
                self._schedule_ready_reset()
            elif name == "session_started":
                self._cancel_idle_reset()
                self._negotiated_audio_codec = str(event.get("audio_codec", "pcm16"))
                self._negotiated_audio_out = str(event.get("audio_out", "url"))
                self.audio.set_negotiated_sample_rate(int(event.get("sample_rate") or self.config.input_sample_rate))
                new_session_id = event.get("session_id")
                if new_session_id and new_session_id != self._session_id:
                    self._session_id = str(new_session_id)
                    try:
                        save_session_id(self.config.session_state_path, self._session_id)
                    except Exception as exc:  # noqa: BLE001 - persistence must not break the session
                        self.log(f"session_id persist failed: {exc}")
                self._session_ready.set()
                self.log(
                    "session started: "
                    f"stt={event.get('stt_engine')} tts={event.get('tts_engine')} "
                    f"codec={self._negotiated_audio_codec} audio_out={self._negotiated_audio_out} "
                    f"sample_rate={self.audio.negotiated_sample_rate}"
                )
                # Missing keys (older server) default to ready, so behavior is
                # unchanged against a server that doesn't send them yet.
                self._engines_ready = event.get("stt_ready", True) and event.get("tts_ready", True)
                if self._engines_ready:
                    self._set_ready()
                else:
                    self.log("engines still warming up server-side — please wait before speaking")
                    self.leds.warming()
                    self.oled.warming()
                    # Audible cue: LED/OLED are easy to miss on a voice-first device,
                    # so beep now and keep reminding until engines_ready arrives —
                    # otherwise the user has no idea why nothing is responding and
                    # keeps talking into a pipeline that isn't ready yet.
                    await asyncio.to_thread(self.audio.play_tone, _WARMING_TONE_HZ)
                    self._start_warming_reminder()
            elif name == "engines_ready":
                self._cancel_idle_reset()
                self._stop_warming_reminder()
                self._engines_ready = True
                self.log("engines ready")
                await asyncio.to_thread(self.audio.play_tone, _READY_TONE_HZ)
                self._set_ready()
            elif name == "user_transcript":
                text = (event.get("text") or "").strip()
                if text:
                    self.log(f"you: {text}")
                    self._cancel_idle_reset()
                    self._status("listening")
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
                    self._status("speaking")
                    await self._play_audio_url(str(audio_url))
                    self._schedule_ready_reset(delay=3.0)
                elif text:
                    self._cancel_idle_reset()
                    self._status("processing")
                    self._schedule_ready_reset()
                else:
                    self._set_ready()
            elif self.config.log_events:
                self.log(f"event: {name} payload={event}")

    async def run_forever(self) -> None:
        self.log(f"target host: {self.config.host}:{self.config.port}")
        if self._session_id:
            self.log(f"resuming session: {self._session_id}")

        # Connect + start audio immediately; warm STT in the background instead of
        # blocking startup on it. The server also warms STT/TTS on WS connect, so this
        # is just a best-effort nudge and must not delay "connected".
        asyncio.create_task(asyncio.to_thread(self._warm_stt_engine))
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
                ws_url = build_ws_url(self.config, session_id=self._session_id)
                try:
                    async with websockets.connect(ws_url, max_size=None, ping_interval=20, ping_timeout=20) as ws:
                        self.log(f"connected: {ws_url}")
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
                finally:
                    # Don't let a stale reminder beep fire after we've disconnected —
                    # the next connection's session_started will restart it if needed.
                    self._stop_warming_reminder()

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
