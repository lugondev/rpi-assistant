#!/usr/bin/env python3
"""Audio-to-audio Raspberry Pi client entrypoint."""

from __future__ import annotations

import asyncio

from a2a_client.runner import main


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
