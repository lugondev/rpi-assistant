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
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)


def clear_device_token(path: str) -> None:
    """Clear device token; called by factory-reset button."""
    Path(path).unlink(missing_ok=True)
