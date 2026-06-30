# Raspberry Pi Audio-to-Audio Service

This folder now contains a runnable Raspberry Pi client for:

- Mic capture on the Pi.
- Uplink to `WS /v1/conversation/stream` as Opus 16 kHz.
- Downlink reply Opus 24 kHz from server.
- Speaker playback on the Pi.

The service follows the protocol from `/agents-docs` and `/docs`.

## Files

- `rpi_audio_service.py`: main client service.
- `config.example.yaml`: service config template.
- `requirements.txt`: Python dependencies.
- `rpi-a2a.service`: systemd unit template.

## Install (Raspberry Pi)

```bash
cd /home/pi/code/agent-assistant
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
sudo apt install -y swig liblgpio-dev libopus0 portaudio19-dev build-essential python3-dev
cp config.example.yaml config.yaml
```

Update `config.yaml`:

- `server.host`: gateway IP (for example `192.168.30.133`).
- Optional `audio.input_device` / `audio.output_device` if default devices are not correct.
- Set `audio.input_channels` and `audio.output_channels` to match hardware.
	Example on this Pi: USB mic is mono (`1`), USB speaker is stereo (`2`).
- For triệt overflow capture, prefer `audio.input_alsa_device: hw:3,0` so the mic is read by `arecord` instead of PortAudio callback.
- For stable speaker playback under systemd, prefer `audio.output_alsa_device: plughw:2,0` so audio is sent through `aplay` instead of PortAudio.
- `audio.input_sample_rate` is the ALSA capture rate, while `audio.uplink_sample_rate` is the rate sent to the server for STT. Keep the uplink at `16000` for the most compatible conversation mode.

List device indexes:

```bash
/home/pi/code/led/.venv/bin/python rpi_audio_service.py --list-devices
```

Run manually:

```bash
/home/pi/code/led/.venv/bin/python rpi_audio_service.py --config config.yaml
```

## Enable as systemd service

```bash
sudo cp rpi-a2a.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rpi-a2a.service
sudo systemctl status rpi-a2a.service
```

If LED control is enabled, the bundled unit runs as `root` so GPIO and ALSA access are unrestricted. If you run the script manually, use `sudo` or add the current user to the `gpio` group.

View logs:

```bash
journalctl -u rpi-a2a.service -f
```

## Notes

- Service uses half-duplex guard: when playing assistant audio, mic uplink is paused to reduce speaker echo.
- Reconnect uses exponential backoff if WebSocket disconnects.
- Default session params: `audio_codec=opus`, `audio_out=opus`, `sample_rate=16000`, `output_sample_rate=24000`.
- LED status is optional and uses GPIO pins `13` (yellow), `22` (red), `17` (green) by default.
- Status mapping: connecting = yellow blink, ready/listening = green on, processing = yellow on, speaking = red on, error = red blink.
- OLED status is optional and follows the same `traffic-light.py` pattern via `luma.oled` on I2C `0x3C`.
- The venv now needs `lgpio` so `gpiozero` can use a real pin factory under systemd.
- Before `pip install -r requirements.txt`, install the system dependencies: `swig` and `liblgpio-dev`.
- If the service fails to start with `status=200/CHDIR`, update `rpi-a2a.service` to point to the actual `WorkingDirectory` and `ExecStart` path for this repository.
- The bundled systemd unit exports `PYTHONPATH=/usr/lib/python3/dist-packages` and `GPIOZERO_PIN_FACTORY=lgpio` so the venv sees the same GPIO backend as `python3 examples/traffic-light.py`.
