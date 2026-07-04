from __future__ import annotations

from pathlib import Path


def load_session_id(path: str | Path) -> str | None:
    """Read a previously persisted session_id, or None if unset/missing/blank.

    Lets a device resume the same server-side conversation (and its chat history)
    across reconnects and full restarts instead of minting a fresh session every
    time it (re)connects.
    """
    p = Path(path)
    if not p.is_file():
        return None
    value = p.read_text(encoding="utf-8").strip()
    return value or None


def save_session_id(path: str | Path, session_id: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(session_id, encoding="utf-8")
