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
from .device_identity import read_device_serial, load_device_token, save_device_token, clear_device_token
from .disconnect import classify_disconnect, REPAIR, RECONNECT
from .led_status import LedConfig, LedStatusController
from .oled_status import OledConfig, OledStatusController
from .lugo_frame import LUGO_FRAME_OPUS, decode_frame
from .mcp_tools import McpToolContext, handle_mcp_request
from .pairing import run_pairing
from .session_state import load_session_id, save_session_id
from .ws_protocol import build_ws_url, build_wakeup_message

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
        self._start_time = time.monotonic()
        self._idle = False
        self._idle_wake_frames = 0
        self._device_token: str | None = None
        self._last_goodbye_reason: str | None = None

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

    def _mcp_context(self) -> McpToolContext:
        return McpToolContext(
            get_volume_pct=self.audio.get_volume_pct,
            set_volume_pct=self.audio.set_volume_pct,
            uptime_seconds=lambda: time.monotonic() - self._start_time,
            go_idle=self._go_idle,
            show_text=self._show_text_overlay,
        )

    def _go_idle(self) -> None:
        self._idle = True
        self._idle_wake_frames = 0
        self._cancel_idle_reset()
        self.leds.stopped()
        self.oled.show("idle", "say something")

    def _show_text_overlay(self, line1: str, line2: str) -> None:
        self._cancel_idle_reset()
        self.oled.show(line1, line2)
        self._schedule_ready_reset(delay=5.0)

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

    def resolve_device_token(self) -> str:
        if self.config.device_token:
            self._device_token = self.config.device_token
            return self._device_token
        tok = load_device_token(self.config.device_token_path)
        if tok:
            self._device_token = tok
            return tok
        serial = read_device_serial()
        base = f"{'https' if self.config.secure else 'http'}://{self.config.host}:{self.config.port}"

        def _show(code: str) -> None:
            self.log(f"pairing code: {code}")   # token never logged; code is safe
            self.oled.show("Pair code", code)

        tok = run_pairing(base, serial, show_code=_show, sleep=time.sleep)
        save_device_token(self.config.device_token_path, tok)
        self._device_token = tok
        return tok

    def on_disconnect(self, handshake_status: int | None, goodbye_reason: str | None) -> str:
        action = classify_disconnect(handshake_status, goodbye_reason)
        if action == REPAIR:
            if self.config.device_token:
                # Override mode: the configured token is static and was rejected by the
                # server. We cannot re-pair it away -- that is an operator/config problem.
                # Fall back to a throttled reconnect (RECONNECT) so we retry with backoff
                # instead of hot-looping, and log loudly.
                self.log("configured device_token was rejected by server -- check config; retrying with backoff")
                return RECONNECT
            clear_device_token(self.config.device_token_path)
            self._device_token = None
            self.log("device token revoked by server -- will re-pair")
            self.oled.show("Unpaired", "re-pairing")
        return action

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

                # Idle gate: while self.device.idle is active, drop uplink frames until a
                # loud sound (same RMS detector used for barge-in) wakes the device — there
                # is no physical wake button on this client, so voice is the only trigger.
                if self._idle:
                    samples = np.frombuffer(pcm_frame, dtype=np.int16)
                    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) else 0.0
                    if rms >= self.config.barge_in_rms_threshold:
                        self._idle_wake_frames += 1
                    else:
                        self._idle_wake_frames = 0
                    if self._idle_wake_frames < self.config.barge_in_min_frames:
                        continue
                    self._idle_wake_frames = 0
                    self._idle = False
                    self.log("idle: woke on loud sound")
                    self._set_ready()

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
                    pcm_frame = self.audio.resample_pcm16_mono(
                        pcm_frame,
                        source_rate=self.config.input_sample_rate,
                        target_rate=self.config.uplink_sample_rate,
                    )
                    packet = self.audio.encode_frame(pcm_frame)
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
                                f"frames={self._uplink_frames_sent} codec=opus "
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
                try:
                    frame_type, opus_payload = decode_frame(message)
                except ValueError as exc:
                    self.log(f"bad downlink frame: {exc}")
                    continue
                if frame_type != LUGO_FRAME_OPUS:
                    continue
                # Decode + queue the Opus frame; the output callback plays it from the
                # jitter buffer at a steady rate (seamless across network jitter).
                self.audio.set_speaking(True)
                self.audio.play_opus_frame(opus_payload)
                continue

            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                self.log(f"non-json message: {message}")
                continue

            name = event.get("type")
            if name == "speech_start":
                self._cancel_idle_reset()
                self._status("listening")
                self._schedule_ready_reset()
            elif name == "speech_end":
                self._cancel_idle_reset()
                self._status("processing")
                self._schedule_ready_reset()
            elif name == "processing":
                self._cancel_idle_reset()
                self._status("processing")
                self._schedule_ready_reset()
            elif name == "stt":
                text = (event.get("text") or "").strip()
                if text:
                    self.log(f"you: {text}")
                    self._cancel_idle_reset()
                    self._status("listening")
                    self._schedule_ready_reset()
                else:
                    self._set_ready()
            elif name == "tts":
                state = event.get("state")
                if state == "start":
                    self._cancel_idle_reset()
                    self.audio.set_speaking(True)
                    self._status("speaking")
                    self._schedule_ready_reset(delay=3.0)
                elif state == "sentence_start":
                    text = (event.get("text") or "").strip()
                    if text:
                        self.log(f"assistant: {text}")
                elif state == "stop":
                    self._cancel_idle_reset()
                    if event.get("reason"):
                        self.audio.reset_playback()  # interrupt: drop queued audio immediately
                    else:
                        self.audio.set_speaking(False)  # buffer drains naturally
                        if self.config.log_events:
                            self.log(f"playback underrun total: {self.audio.play_buffer.underrun_samples} samples")
                    self._set_ready()
            elif name == "welcome":
                self._cancel_idle_reset()
                new_session_id = event.get("session_id")
                if new_session_id and new_session_id != self._session_id:
                    self._session_id = str(new_session_id)
                    try:
                        save_session_id(self.config.session_state_path, self._session_id)
                    except Exception as exc:  # noqa: BLE001 - persistence must not break the session
                        self.log(f"session_id persist failed: {exc}")
                out_sr = int((event.get("audio_params") or {}).get("sample_rate") or self.config.output_sample_rate)
                self.audio.set_negotiated_sample_rate(out_sr)
                self._session_ready.set()
                self.log(f"session started: session_id={self._session_id} output_sample_rate={out_sr}")
                # Missing keys (older server) default to ready, so behavior is
                # unchanged against a server that doesn't send them yet.
                self._engines_ready = event.get("stt_ready", True) and event.get("tts_ready", True)
                if self._engines_ready:
                    self._set_ready()
                else:
                    self.log("engines still warming up server-side — please wait before speaking")
                    self.leds.warming()
                    self.oled.warming()
                    await asyncio.to_thread(self.audio.play_tone, _WARMING_TONE_HZ)
                    self._start_warming_reminder()
            elif name == "engines_ready":
                self._cancel_idle_reset()
                self._stop_warming_reminder()
                self._engines_ready = True
                self.log("engines ready")
                await asyncio.to_thread(self.audio.play_tone, _READY_TONE_HZ)
                self._set_ready()
            elif name == "mcp":
                payload = event.get("payload") or {}
                response = handle_mcp_request(payload, self._mcp_context())
                await ws.send(json.dumps({"type": "mcp", "payload": response}))
            elif name == "goodbye":
                self._last_goodbye_reason = event.get("reason")
                self.log(f"server goodbye: {event.get('reason', '')}")
            elif name == "error":
                self._cancel_idle_reset()
                self.log(f"server error: {event.get('message', 'unknown')}")
                self.leds.error()
                self.oled.error("SERVER ERR")
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
            self.resolve_device_token()

            while not self.stop_event.is_set():
                self._session_ready.clear()
                self._last_goodbye_reason = None
                handshake_status: int | None = None
                self.leds.connecting()
                self.oled.connecting()
                ws_url = build_ws_url(self.config, self._device_token)
                try:
                    async with websockets.connect(ws_url, max_size=None, ping_interval=20, ping_timeout=20) as ws:
                        self.log(f"connected: {ws_url}")
                        await ws.send(json.dumps(build_wakeup_message(self.config, self._session_id)))
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
                except websockets.exceptions.InvalidStatus as exc:
                    handshake_status = exc.response.status_code
                    self.log(f"handshake rejected: {handshake_status}")
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

                action = self.on_disconnect(handshake_status, self._last_goodbye_reason)
                if action == REPAIR:
                    self.resolve_device_token()   # blocks on the pairing flow, shows fresh code
                    backoff = self.config.reconnect_initial_seconds
                    continue

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
