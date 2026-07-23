from __future__ import annotations

import os
from pathlib import Path


def read_device_serial(machine_id_path: str = "/etc/machine-id") -> str:
    p = Path(machine_id_path)
    if not p.is_file():
        raise RuntimeError(f"machine-id not found at {machine_id_path}")
    value = p.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"machine-id at {machine_id_path} is empty")
    return value


def load_device_token(path: str) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    value = p.read_text(encoding="utf-8").strip()
    return value or None


def save_device_token(path: str, token: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token, encoding="utf-8")
    os.chmod(p, 0o600)


def clear_device_token(path: str) -> None:
    Path(path).unlink(missing_ok=True)
