from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

TOOL_DEFS: list[dict] = [
    {
        "name": "self.get_device_status",
        "description": "Get current device status: speaker volume percent and uptime in seconds.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "self.audio.set_volume",
        "description": (
            "Set the speaker volume. Provide either volume (absolute percentage 0-100) "
            "or delta (relative change, e.g. +10 or -10) — not both."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "volume": {"type": "integer", "minimum": 0, "maximum": 100},
                "delta": {"type": "integer", "minimum": -100, "maximum": 100},
            },
        },
    },
    {
        "name": "self.device.idle",
        "description": "Mute the microphone and show an idle indicator until a loud sound wakes the device.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "self.screen.show_text",
        "description": "Show up to two lines of text on the device's screen temporarily.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "line1": {"type": "string"},
                "line2": {"type": "string"},
            },
            "required": ["line1"],
        },
    },
]

_TOOL_NAMES = {t["name"] for t in TOOL_DEFS}


@dataclass
class McpToolContext:
    get_volume_pct: Callable[[], int]
    set_volume_pct: Callable[[int], None]
    uptime_seconds: Callable[[], float]
    go_idle: Callable[[], None]
    show_text: Callable[[str, str], None]


def _error_result(message: str) -> dict:
    return {
        "isError": True,
        "error": message,
        "content": [{"type": "text", "text": message}],
    }


def _ok_result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _call_tool(name: str, args: dict, ctx: McpToolContext) -> dict:
    if name not in _TOOL_NAMES:
        return _error_result(f"unknown tool: {name}")
    try:
        if name == "self.get_device_status":
            text = f"volume={ctx.get_volume_pct()}% uptime={ctx.uptime_seconds():.0f}s"
        elif name == "self.audio.set_volume":
            has_volume = "volume" in args
            has_delta = "delta" in args
            if has_volume and has_delta:
                return _error_result("provide either volume or delta, not both")
            if not has_volume and not has_delta:
                return _error_result("missing volume or delta")
            if has_volume:
                new_volume = max(0, min(100, int(args["volume"])))
            else:
                new_volume = max(0, min(100, ctx.get_volume_pct() + int(args["delta"])))
            ctx.set_volume_pct(new_volume)
            text = f"volume set to {new_volume}%"
        elif name == "self.device.idle":
            ctx.go_idle()
            text = "device is now idle"
        else:  # self.screen.show_text
            line1 = str(args["line1"])
            line2 = str(args.get("line2", ""))
            ctx.show_text(line1, line2)
            text = "screen updated"
    except (KeyError, ValueError, TypeError) as exc:
        return _error_result(f"bad arguments: {exc}")
    return _ok_result(text)


def handle_mcp_request(payload: dict, ctx: McpToolContext) -> dict:
    mid = payload.get("id")
    method = payload.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "agent-assistant", "version": "1.0.0"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOL_DEFS}}
    if method == "tools/call":
        params = payload.get("params") or {}
        result = _call_tool(params.get("name", ""), params.get("arguments") or {}, ctx)
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method: {method}"}}
