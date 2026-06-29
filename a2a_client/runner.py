from __future__ import annotations

import argparse
import asyncio
import signal

import sounddevice as sd

from .config import load_config
from .service import AudioToAudioService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Raspberry Pi audio-to-audio service")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available PortAudio input/output devices and exit",
    )
    return parser.parse_args()


def install_signal_handlers(service: AudioToAudioService) -> None:
    def _request_stop() -> None:
        service.log("stop requested")
        service.stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _request_stop())


async def main() -> int:
    args = parse_args()
    if args.list_devices:
        print(sd.query_devices())
        return 0

    config = load_config(args.config)
    service = AudioToAudioService(config)
    install_signal_handlers(service)
    await service.run_forever()
    return 0
