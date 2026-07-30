# Device integration guide (Raspberry Pi / ESP32)

How to build a voice device that talks to the gateway. The device is a **thin client**:
it captures the mic, sends audio, and plays back the reply. All STT / LLM / TTS run on
the **server** — the device needs only audio I/O, Opus, and a WebSocket.

```
┌──────────── Raspberry Pi ────────────┐         ┌──────────────── Gateway server ────────────────┐
│ mic → Opus(16k) ──────────────────────┼──WS────▶│ decode → VAD → STT → LLM → TTS                  │
│ speaker ◀── Opus(16k) ◀────────────────┼──WS─────│ → encode → push reply frames                    │
└───────────────────────────────────────┘         └─────────────────────────────────────────────────┘
```

## 1. Endpoint

```
ws://<server-host>:8000/v1/conversation/stream?<params>
```

Recommended params for a duplex voice device:

| param | value | meaning |
|-------|-------|---------|
| `stt_engine` | `whisper_mlx` | server-side STT (Vietnamese). `qwen_omni` = audio-native (server answers audio directly) |
| `tts_engine` | `vieneu` | server-side Vietnamese TTS |
| `language` | `vi` | STT language hint |
| `sample_rate` | `16000` | **uplink** audio rate (Hz) |
| `audio_codec` | `opus` | uplink codec — raw Opus packets |
| `output` | `audio,text` | what to receive: `audio` (+ `text` for subtitles/debug) |
| `audio_out` | `opus` | reply audio delivered as **pushed Opus frames** (not a URL) |
| `output_sample_rate` | `16000` | **downlink** Opus rate (Hz) |
| `profile` | *(optional)* | named **chatllm profile** (`POST /v1/profiles`) — bundles LLM model/system prompt/TTS/MCP tools/memory; see [`../docs/device-integration.md`](../docs/device-integration.md#1a-profiles-connect-a-device-as-a-preset-chatllm-persona) |

Full example:
```
ws://192.168.1.50:8000/v1/conversation/stream?stt_engine=whisper_mlx&tts_engine=vieneu&language=vi&sample_rate=16000&audio_codec=opus&output=audio,text&audio_out=opus&output_sample_rate=16000
```

Full example with a profile:
```
ws://192.168.1.50:8000/v1/conversation/stream?profile=kitchen&sample_rate=16000&audio_codec=opus&output=audio,text&audio_out=opus&output_sample_rate=16000
```

On connect the server sends one `session_started` JSON with the negotiated config
(`stt_engine`, `tts_engine`, `llm_model`, `audio_codec`, `audio_out`,
`output_sample_rate`). Always read it first.

## 2. Audio formats

| direction | codec | rate | channels | frame |
|-----------|-------|------|----------|-------|
| **uplink** (device → server) | Opus | 16 000 Hz | mono | 20–60 ms (use **60 ms = 960 samples**) |
| **downlink** (server → device) | Opus | 16 000 Hz | mono | 60 ms (960 samples) |

- Each Opus packet is **one binary WebSocket frame** (no extra header).
- PCM is signed 16-bit little-endian before/after Opus.
- **Downlink pacing:** the server sends the first ~5 packets of each sentence immediately
  (fills your jitter buffer fast → low first-audio latency), then paces the rest at one
  frame (60 ms) so it emits at playback rate and a small device buffer never overflows on
  long replies. Just play packets as they arrive; keep ~100–200 ms of jitter buffer.
- If you cannot do Opus, use `audio_codec=pcm16` (uplink) and the default
  `audio_out=wav` (downlink) — the server pushes each sentence's audio as one
  binary WebSocket frame (a complete WAV or MP3 file), bracketed by `audio_start`/
  `audio_end` JSON events, instead of paced Opus packets. Simpler, but no per-frame
  pacing and ~10× more bandwidth. This is a fallback for non-Opus clients only —
  the RPi assistant always negotiates Opus and is unaffected by this choice.

## 3. Protocol

### Device → server
- **Binary frame** = one Opus packet of mic audio (stream continuously).
- **Text JSON**:
  - `{"type":"text","text":"…"}` — text input turn (no mic).
  - `{"type":"flush"}` — force end-of-turn now (push-to-talk: send audio, then flush).
  - `{"type":"abort"}` — cancel the current reply.
  - `{"type":"new_session"}` — end this conversation and start a fresh one on the
    same socket. The server replies with the new `session_id` (see below). Use this,
    not `reset`, whenever the user asks to start over: a device that keeps one socket
    open for days otherwise accumulates its entire life into a single conversation —
    one History entry, an ever-growing LLM context, and memory extraction that never
    runs (it only runs when a conversation ends).
  - `{"type":"reset"}` — clear the in-memory conversation context. **Caveat:** it does
    NOT end the stored session, so messages from before and after a reset stay in the
    same row with a continuous turn counter. Kept as-is for compatibility; prefer
    `new_session`.
  - `{"type":"end"}` — finalize and close.

### Server → device (JSON `{"event": …}`, plus binary frames)
| event | meaning | fields |
|-------|---------|--------|
| `session_started` | handshake | engines, `audio_out`, `output_sample_rate`, … |
| `speech_start` | server detected you started speaking | — |
| `speech_end` | end of your turn (VAD) | `speech_ms` |
| `processing` | transcribing + generating | `turn` |
| `user_transcript` | what you said (STT) | `text` |
| `response_text` | reply text (subtitle) | `text`, `chunk_index` |
| `audio_start` | **next N binary frames are reply audio** | `chunk_index`, `codec:"opus"`, `sample_rate`, `frames` |
| _(binary)_ | one Opus packet of reply audio | — |
| `audio_end` | end of this sentence's audio | `chunk_index` |
| `turn_done` | reply finished | `turn` |
| `aborted` | reply cancelled (barge-in) | `reason` |
| `error` | failure (keeps the socket open) | `message` |

**Reply audio framing:** for each reply sentence the server sends
`audio_start {frames: N}` → exactly `N` binary Opus packets → `audio_end`. Decode and
play the packets in order. A reply has several sentences (several start/end groups),
then `turn_done`.

## 4. Turn lifecycle

**Always-on VAD (hands-free):** stream mic Opus continuously. The server endpoints on
~700 ms of trailing silence and replies. No control messages needed.

```
device:  ──opus──opus──opus──(silence)──────────────────────────
server:  speech_start … speech_end → processing → user_transcript
         → response_text + audio_start/▮▮▮/audio_end (×sentences) → turn_done
```

**Push-to-talk:** send mic Opus only while the button is held; on release send
`{"type":"flush"}` to end the turn immediately.

**Half-duplex (important):** while you are playing the reply (`audio_start` … `turn_done`),
**stop uplinking mic audio** — otherwise the speaker bleeds into the mic and the server
treats it as barge-in and cancels the reply. To support barge-in (user interrupts),
keep uplinking; a `speech_start` mid-reply yields `aborted` — stop playback on it.

## 5. Reference client

A runnable Python client is in [`scripts/rpi_voice_client.py`](../scripts/rpi_voice_client.py):

```bash
# on the Raspberry Pi
sudo apt install -y libopus0 portaudio19-dev
pip install websockets sounddevice opuslib numpy

python scripts/rpi_voice_client.py --host <server-ip> --port 8000
# activate a saved profile instead of --stt/--tts:
python scripts/rpi_voice_client.py --host <server-ip> --profile kitchen
```

It captures the mic at 16 kHz, encodes 60 ms Opus frames, streams them, decodes the
16 kHz reply frames between `audio_start`/`audio_end`, and plays them — with
half-duplex mic muting during playback.

**ESP32-S3 firmware:** a native ESP-IDF firmware speaking this same protocol lives in
[`../esp32-assistant`](../esp32-assistant/README.md). Hands-free, ES8311 codec, Opus
uplink/downlink, half-duplex — the on-device equivalent of the reference client above.

## 5a. Raspberry Pi Service Setup

A production-ready Raspberry Pi service is available in this repository:

```bash
# Install dependencies
pip install -r requirements.txt
sudo apt install -y libopus0 libportaudio2

# Configure the service
cp config.example.yaml config.yaml
# Edit config.yaml with your server host and audio device IDs
# Use `python -m a2a_client.runner --list-devices` to find audio device IDs

# Run the service
python -m a2a_client.runner --config config.yaml

# Or install as systemd service
sudo cp rpi-a2a.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rpi-a2a
sudo systemctl start rpi-a2a
```

### Configuration (config.yaml)

```yaml
server:
  host: <gateway-server-ip>      # e.g., 192.168.1.50
  port: 8000
  secure: false                  # Set to true for wss://

session:
  profile: my-assistant          # named profile (POST /v1/profiles or control panel).
                                  # Owns STT engine + language, TTS voice, LLM model/
                                  # system prompt, MCP tools and memory. null = server
                                  # .env defaults.
  output: audio,text             # Get both audio reply and text

audio:
  input_sample_rate: 16000       # Mic sample rate (Hz)
  output_sample_rate: 16000      # Speaker sample rate (Hz)
  uplink_sample_rate: 16000      # Frame rate for uplink Opus
  frame_ms: 60                   # Frame duration
  input_channels: 1              # Mono mic
  output_channels: 1             # Mono or 2 for stereo
  input_device: <device_id>      # From --list-devices
  output_device: <device_id>
  input_alsa_device: hw:3,0      # Alternative: direct ALSA device
  output_alsa_device: plughw:2,0

service:
  reconnect_initial_seconds: 1.0
  reconnect_max_seconds: 20.0
  log_events: true

led:
  enabled: true                  # GPIO status LEDs
  yellow_pin: 13                 # Listening
  red_pin: 22                    # Error
  green_pin: 17                  # Ready

oled:
  enabled: true                  # I2C OLED display
  i2c_port: 1                    # /dev/i2c-1
  i2c_address: 60                # 0x3C in hex
```

### Protocol (Actual Implementation)

The service streams **Opus-encoded audio** (not PCM16):

- **Uplink** (device → server): Binary WebSocket frames of Opus packets @ 16 kHz
- **Downlink** (server → device): Binary Opus frames @ 16 kHz between `audio_start`/`audio_end` events
- **Duplex control**: Mic is auto-muted while speaker plays to prevent barge-in

The service handles:
- Automatic reconnection with exponential backoff
- LED feedback (listening → processing → speaking → ready)
- OLED status display
- Half-duplex audio (stops mic input during playback)
- Graceful shutdown on SIGINT/SIGTERM

## 6. Browser client (WebCodecs Opus downlink)

Browsers can receive the streamed Opus reply (instead of fetching WAV URLs) using the
**WebCodecs** `AudioDecoder` — ~10× less downlink bandwidth, gapless playback. Connect
with `audio_out=opus&output_sample_rate=16000`, set `ws.binaryType = "arraybuffer"`:

```js
const dec = new AudioDecoder({
  output: (audioData) => {
    // copy planar f32 -> AudioBuffer -> schedule on an AudioContext timeline
    const buf = ctx.createBuffer(1, audioData.numberOfFrames, audioData.sampleRate);
    const arr = new Float32Array(audioData.numberOfFrames);
    audioData.copyTo(arr, { planeIndex: 0, format: "f32-planar" });
    buf.copyToChannel(arr, 0);
    audioData.close();
    /* createBufferSource → start at max(ctx.currentTime, nextTime) → advance nextTime */
  },
  error: (e) => console.error(e),
});
dec.configure({ codec: "opus", sampleRate: 16000, numberOfChannels: 1 });

let ts = 0; // microseconds
ws.onmessage = (ev) => {
  if (typeof ev.data !== "string") {            // binary = one 60 ms Opus packet
    dec.decode(new EncodedAudioChunk({ type: "key", timestamp: ts, data: ev.data }));
    ts += 60000;                                // 60 ms frames
    return;
  }
  const m = JSON.parse(ev.data);                // audio_start / audio_end / response_text / …
};
```

- Each Opus packet is self-contained → use `type:"key"` for every frame.
- On `aborted` (barge-in), close + recreate the decoder and reset `ts` so stale audio can't play.
- Needs a WebCodecs-capable browser (Chromium, Safari 16.4+, recent Firefox); fall back to
  the default `audio_out=wav` otherwise — the server pushes the complete WAV/MP3 as one
  binary WebSocket frame per sentence (bracketed by `audio_start`/`audio_end`), which you
  can hand straight to `decodeAudioData` — no fetch, no URL.
- The built-in playground (`/ui` → Conversation) has an **"Opus downlink"** checkbox that
  does exactly this — use it to verify before writing your own client.

## 7. Other modes (same endpoint)

- **Audio → text only** (transcription service): `?output=text` (no `audio_out`). You
  get `user_transcript` + `response_text`, no audio.
- **Text → audio**: `?output=audio&audio_out=opus`, then send `{"type":"text","text":"…"}`
  to hear a spoken reply (no mic).
- **Text → text** (chatbot): `?output=text` + `{"type":"text",…}`.

## 8. Notes for the device dev

- The server's TTS/LLM run remotely; first use of a heavy STT (`qwen_omni`) loads a
  model — call `POST /v1/stt/warm?engine=qwen_omni` once at boot if you use it.
- libopus must be present on the device (`apt install libopus0`).
- Reconnect with backoff on socket close; re-read `session_started` each time.
- Keep ~100–200 ms of jitter buffer on playback for smooth audio over WiFi.
- Browser playground at `/ui` (Conversation tab) is the easiest way to sanity-check the
  server before wiring the device.

See [api.md](api.md) for the complete REST/WebSocket reference.
