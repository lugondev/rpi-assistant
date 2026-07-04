from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LedConfig:
    enabled: bool = True
    yellow_pin: int = 13
    red_pin: int = 22
    green_pin: int = 17


class LedStatusController:
    def __init__(self, config: LedConfig, logger) -> None:
        self.config = config
        self.logger = logger
        self._enabled = False
        self._yellow = None
        self._red = None
        self._green = None

        if not self.config.enabled:
            return

        try:
            from gpiozero import LED
        except Exception as exc:  # noqa: BLE001
            self.logger(f"LED status disabled: {exc}")
            return

        try:
            self._yellow = LED(self.config.yellow_pin)
            self._red = LED(self.config.red_pin)
            self._green = LED(self.config.green_pin)
            self._enabled = True
            self.stopped()
        except Exception as exc:  # noqa: BLE001
            self.logger(f"LED hardware unavailable: {exc}")
            self.close()

    def _all_off(self) -> None:
        for led in (self._yellow, self._red, self._green):
            if led is not None:
                led.off()

    def stopped(self) -> None:
        if not self._enabled:
            return
        self._all_off()

    def connecting(self) -> None:
        if not self._enabled:
            return
        self._all_off()
        self._yellow.blink(on_time=0.25, off_time=0.25, background=True)

    def warming(self) -> None:
        """STT/TTS models are still cold-loading server-side — don't speak yet.
        Slower blink than connecting() so the two are visually distinguishable."""
        if not self._enabled:
            return
        self._all_off()
        self._yellow.blink(on_time=0.6, off_time=0.6, background=True)

    def ready(self) -> None:
        if not self._enabled:
            return
        self._all_off()
        self._green.on()

    def listening(self) -> None:
        if not self._enabled:
            return
        self._all_off()
        self._green.on()

    def processing(self) -> None:
        if not self._enabled:
            return
        self._all_off()
        self._yellow.on()

    def speaking(self) -> None:
        if not self._enabled:
            return
        self._all_off()
        self._red.on()

    def error(self) -> None:
        if not self._enabled:
            return
        self._all_off()
        self._red.blink(on_time=0.2, off_time=0.2, background=True)

    def close(self) -> None:
        self._enabled = False
        for led in (self._yellow, self._red, self._green):
            if led is not None:
                try:
                    led.close()
                except Exception:
                    pass
        self._yellow = None
        self._red = None
        self._green = None
