# rpi-assistant

Raspberry Pi voice client for the [LUGO gateway](https://github.com/lugondev/lugo-gateway).

The Pi is a **thin client**: it captures the microphone, streams Opus at 16 kHz to the
gateway over WebSocket, and plays the reply back through the speaker. Speech
recognition, the language model and speech synthesis all run on the **server** — the
device needs only audio I/O, Opus and a WebSocket.

```
+------------- Raspberry Pi -------------+        +---------------- Gateway ----------------+
| mic  -> Opus(16k) ---------------------|--WS--->| decode -> VAD -> STT -> LLM -> TTS      |
| spkr <- Opus(16k) <--------------------|--WS----| -> encode -> reply frames               |
+----------------------------------------+        +-----------------------------------------+
```

## What is here

| File | What |
| --- | --- |
| `rpi_audio_service.py` | Entry point |
| `a2a_client/` | The client itself — WebSocket protocol, audio I/O, pairing, playback jitter buffer, LED + OLED status, MCP tools |
| `config.example.yaml` | Config template (copy to `config.yaml`) |
| `rpi-a2a.service` | systemd unit template |
| `docs.md` | Install and run instructions |
| `integration.md` | Device integration guide — the wire protocol, for writing your own client |

## Install

```bash
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
sudo apt install -y swig liblgpio-dev libopus0 portaudio19-dev build-essential python3-dev

cp config.example.yaml config.yaml   # then set server.host
```

See [`docs.md`](docs.md) for the full walkthrough, including the systemd unit.

## Configure

Everything lives in `config.yaml`. The pieces worth knowing:

- **`server.host` / `server.port`** — where the gateway is.
- **`session.profile`** — a named gateway profile owns the STT engine and language, TTS
  voice, LLM model and system prompt, MCP tools and memory. The device chooses *which
  profile to connect to*, not which engines to run. `null` falls back to the server
  defaults.
- **`session.allow_barge_in`** — let the user interrupt the assistant mid-sentence.
  Off by default: it needs a mic well isolated from the speaker (or hardware AEC),
  otherwise speaker bleed self-triggers it.
- **`session.session_state_path`** — caches the last server-assigned session id so a
  WiFi blip or a service restart resumes the same conversation instead of starting a
  new one. Delete the file to force a fresh session.
- **`led` / `oled`** — optional GPIO status LEDs and an I2C status display.

Pairing is automatic: on first connect the device shows a pairing code to claim in the
gateway's admin UI. Setting `server.device_token` skips pairing (dev/legacy override).

## Test

```bash
../.venv/bin/python -m pytest tests -q
```

---

## Part of LUGO

**LUGO** is a self-hosted AI companion platform — models supply the intelligence, LUGO
supplies the experience: one assistant that talks, remembers and acts across the browser,
ESP32 boards and a Raspberry Pi.

This repository is one piece of it. Every client and service talks to the gateway:

| Repo | Role |
| --- | --- |
| [lugo-gateway](https://github.com/lugondev/lugo-gateway) | The hub — STT/TTS/LLM engines, auth, device pairing, MCP tools, per-user chat memory. Everything below talks to this. |
| [lugo-web-client](https://github.com/lugondev/lugo-web-client) | React + TypeScript web client: talk, devices, history, tools. |
| [esp32-assistant](https://github.com/lugondev/esp32-assistant) | ESP-IDF firmware for ESP32-S3 / ESP32-C3 — a hands-free voice terminal. |
| **rpi-assistant** &nbsp;&larr; you are here | Raspberry Pi voice client (mic capture, Opus duplex, systemd unit). |
| [knowledge-api](https://github.com/lugondev/knowledge-api) | **kbase** — RAG knowledge base: documents in, retrievable chunks out. |
| [router-memory-services](https://github.com/lugondev/router-memory-services) | **memgw** — one API in front of any AI memory provider (Mem0, Zep, pgvector). |
| [mcp-basic-tools](https://github.com/lugondev/mcp-basic-tools) | Remote MCP tool server (timedate, fetch, ipinfo, web search). |
| [livehost-api](https://github.com/lugondev/livehost-api) | TikTok Live AI co-host, an out-of-process gateway plugin. |
| [voiceprint-api](https://github.com/lugondev/voiceprint-api) | Speaker recognition (3D-Speaker), forked from [xinnan-tech/voiceprint-api](https://github.com/xinnan-tech/voiceprint-api). |
| [lugo-landing](https://github.com/lugondev/lugo-landing) | Marketing landing page for the platform, bilingual (Tiếng Việt / English). |
