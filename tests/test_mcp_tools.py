from a2a_client.mcp_tools import McpToolContext, handle_mcp_request


class _FakeCtx:
    def __init__(self) -> None:
        self.volume = 100
        self.idle_calls = 0
        self.shown: list[tuple[str, str]] = []
        self.new_session_calls = 0

    def get_volume_pct(self) -> int:
        return self.volume

    def set_volume_pct(self, pct: int) -> None:
        self.volume = pct

    def uptime_seconds(self) -> float:
        return 42.0

    def go_idle(self) -> None:
        self.idle_calls += 1

    def show_text(self, line1: str, line2: str) -> None:
        self.shown.append((line1, line2))

    def new_session(self) -> None:
        self.new_session_calls += 1


def _ctx() -> McpToolContext:
    fake = _FakeCtx()
    return McpToolContext(
        get_volume_pct=fake.get_volume_pct,
        set_volume_pct=fake.set_volume_pct,
        uptime_seconds=fake.uptime_seconds,
        go_idle=fake.go_idle,
        show_text=fake.show_text,
        new_session=fake.new_session,
    ), fake


def test_initialize_returns_result_with_matching_id():
    ctx, _ = _ctx()
    resp = handle_mcp_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, ctx)
    assert resp["id"] == 1
    assert "result" in resp


def test_tools_list_returns_every_tool():
    ctx, _ = _ctx()
    resp = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ctx)
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "self.get_device_status",
        "self.audio.set_volume",
        "self.device.idle",
        "self.screen.show_text",
        "self.session.new",
    }


def test_tools_list_tools_have_no_confirm_annotation():
    ctx, _ = _ctx()
    resp = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ctx)
    for tool in resp["result"]["tools"]:
        assert not (tool.get("annotations") or {}).get("requiresConfirm")


def test_get_device_status_reports_volume_and_uptime():
    ctx, fake = _ctx()
    fake.volume = 77
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "self.get_device_status", "arguments": {}}},
        ctx,
    )
    text = resp["result"]["content"][0]["text"]
    assert "77" in text
    assert "42" in text


def test_set_volume_clamps_and_updates_context():
    ctx, fake = _ctx()
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {"volume": 150}}},
        ctx,
    )
    assert fake.volume == 100
    assert "100" in resp["result"]["content"][0]["text"]
    assert not resp["result"].get("isError")


def test_set_volume_missing_argument_returns_error():
    ctx, fake = _ctx()
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {}}},
        ctx,
    )
    assert resp["result"]["isError"] is True
    assert fake.volume == 100  # unchanged


def test_set_volume_missing_argument_error_has_error_key():
    ctx, _ = _ctx()
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {}}},
        ctx,
    )
    result = resp["result"]
    assert result["isError"] is True
    assert "error" in result
    assert result["error"] == result["content"][0]["text"]


def test_set_volume_delta_increases_from_current_volume():
    ctx, fake = _ctx()
    fake.volume = 50
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 20, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {"delta": 10}}},
        ctx,
    )
    assert fake.volume == 60
    assert "60" in resp["result"]["content"][0]["text"]
    assert not resp["result"].get("isError")


def test_set_volume_delta_decreases_from_current_volume():
    ctx, fake = _ctx()
    fake.volume = 50
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 21, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {"delta": -20}}},
        ctx,
    )
    assert fake.volume == 30
    assert "30" in resp["result"]["content"][0]["text"]


def test_set_volume_delta_clamps_at_100():
    ctx, fake = _ctx()
    fake.volume = 95
    handle_mcp_request(
        {"jsonrpc": "2.0", "id": 22, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {"delta": 10}}},
        ctx,
    )
    assert fake.volume == 100


def test_set_volume_delta_clamps_at_0():
    ctx, fake = _ctx()
    fake.volume = 5
    handle_mcp_request(
        {"jsonrpc": "2.0", "id": 23, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {"delta": -20}}},
        ctx,
    )
    assert fake.volume == 0


def test_set_volume_both_volume_and_delta_returns_error():
    ctx, fake = _ctx()
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 24, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {"volume": 50, "delta": 10}}},
        ctx,
    )
    assert resp["result"]["isError"] is True
    assert fake.volume == 100  # unchanged


def test_set_volume_neither_volume_nor_delta_returns_error():
    ctx, fake = _ctx()
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 25, "method": "tools/call",
         "params": {"name": "self.audio.set_volume", "arguments": {}}},
        ctx,
    )
    assert resp["result"]["isError"] is True
    assert fake.volume == 100  # unchanged


def test_device_idle_calls_context():
    ctx, fake = _ctx()
    handle_mcp_request(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "self.device.idle", "arguments": {}}},
        ctx,
    )
    assert fake.idle_calls == 1


def test_screen_show_text_calls_context_with_both_lines():
    ctx, fake = _ctx()
    handle_mcp_request(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "self.screen.show_text",
                    "arguments": {"line1": "hello", "line2": "world"}}},
        ctx,
    )
    assert fake.shown == [("hello", "world")]


def test_screen_show_text_defaults_line2_to_empty():
    ctx, fake = _ctx()
    handle_mcp_request(
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
         "params": {"name": "self.screen.show_text", "arguments": {"line1": "hi"}}},
        ctx,
    )
    assert fake.shown == [("hi", "")]


def test_unknown_tool_returns_is_error():
    ctx, _ = _ctx()
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": "self.nonexistent", "arguments": {}}},
        ctx,
    )
    assert resp["result"]["isError"] is True


def test_unknown_method_returns_json_rpc_error():
    ctx, _ = _ctx()
    resp = handle_mcp_request({"jsonrpc": "2.0", "id": 10, "method": "bogus"}, ctx)
    assert resp["id"] == 10
    assert "error" in resp


def test_new_session_tool_records_the_request():
    ctx, fake = _ctx()
    resp = handle_mcp_request(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "self.session.new", "arguments": {}}},
        ctx,
    )
    assert fake.new_session_calls == 1
    assert not resp["result"].get("isError")


def test_new_session_tool_description_bounds_when_the_model_may_use_it():
    """This tool wipes the model's own context, so a vague description invites it
    to fire on a topic change. The wording must pin it to an explicit user ask."""
    ctx, _ = _ctx()
    resp = handle_mcp_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ctx)
    tool = next(t for t in resp["result"]["tools"] if t["name"] == "self.session.new")
    assert "ONLY" in tool["description"]
    assert "Never" in tool["description"]
