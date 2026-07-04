from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OledConfig:
    enabled: bool = True
    i2c_port: int = 1
    i2c_address: int = 0x3C
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


class OledStatusController:
    def __init__(self, config: OledConfig, logger) -> None:
        self.config = config
        self.logger = logger
        self._enabled = False
        self._dev = None
        self._font = None
        self._err = 0

        if not self.config.enabled:
            return

        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            from PIL import Image, ImageDraw, ImageFont
        except Exception as exc:  # noqa: BLE001
            self.logger(f"OLED disabled: {exc}")
            return

        try:
            serial = i2c(port=self.config.i2c_port, address=self.config.i2c_address)
            self._dev = ssd1306(serial)
            self._font = ImageFont.truetype(self.config.font_path, 16)
            self._enabled = True
            self.show("ready", "speak now")
        except Exception as exc:  # noqa: BLE001
            self.logger(f"OLED hardware unavailable: {exc}")
            self.close()

    def _render(self, line1: str, line2: str = "") -> None:
        if not self._enabled or self._dev is None or self._font is None:
            return

        from PIL import Image, ImageDraw

        img = Image.new("1", (self._dev.width, self._dev.height))
        draw = ImageDraw.Draw(img)
        draw.text((8, 8), line1, font=self._font, fill="white")
        if line2:
            draw.text((8, 34), line2, font=self._font, fill="white")
        self._dev.display(img)

    def show(self, line1: str, line2: str = "") -> None:
        if not self._enabled:
            return
        try:
            self._render(line1, line2)
            self._err = 0
        except Exception as exc:  # noqa: BLE001
            self._err += 1
            self.logger(f"OLED render error: {exc}")
            if self._err >= 3:
                self.close()

    def stopped(self) -> None:
        self.show("stopped", "")

    def connecting(self) -> None:
        self.show("connecting", "")

    def warming(self) -> None:
        self.show("warming up", "please wait...")

    def ready(self) -> None:
        self.show("ready", "speak now")

    def listening(self) -> None:
        self.show("listening", "mic open")

    def processing(self) -> None:
        self.show("processing", "thinking...")

    def speaking(self) -> None:
        self.show("speaking", "replying...")

    def error(self, message: str = "ERROR") -> None:
        self.show(message[:16], "")

    def close(self) -> None:
        self._enabled = False
        self._dev = None
        self._font = None
