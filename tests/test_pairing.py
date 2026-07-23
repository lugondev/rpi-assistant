import io
import json
import urllib.error
import pytest
from a2a_client import pairing


class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def _json_resp(payload):
    return FakeResp(json.dumps(payload).encode("utf-8"))


def test_pair_init_returns_code_and_token():
    def opener(req, timeout=0):
        assert req.full_url.endswith("/v1/devices/pair/init")
        assert json.loads(req.data) == {"serial": "srl"}
        return _json_resp({"success": True, "data": {"code": "123456", "poll_token": "pt"}})
    assert pairing.pair_init("http://h:8000", "srl", opener=opener) == ("123456", "pt")


def test_pair_status_404_returns_none():
    def opener(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)
    assert pairing.pair_status("http://h:8000", "pt", opener=opener) is None


def test_run_pairing_shows_code_polls_then_returns_token():
    shown = []
    calls = {"n": 0}

    def opener(req, timeout=0):
        if req.full_url.endswith("/pair/init"):
            return _json_resp({"success": True, "data": {"code": "999000", "poll_token": "pt"}})
        calls["n"] += 1
        if calls["n"] < 2:
            return _json_resp({"success": True, "data": {"claimed": False}})
        return _json_resp({"success": True, "data": {"claimed": True, "device_id": "d1", "token": "TOK"}})

    token = pairing.run_pairing(
        "http://h:8000", "srl",
        show_code=shown.append, sleep=lambda s: None, opener=opener, poll_interval=0,
    )
    assert token == "TOK"
    assert shown == ["999000"]


def test_run_pairing_reinits_on_expiry():
    shown = []
    seq = iter([
        ("init", {"code": "111111", "poll_token": "p1"}),
        ("404", None),                                   # p1 expired
        ("init", {"code": "222222", "poll_token": "p2"}),
        ("claimed", {"claimed": True, "device_id": "d", "token": "TOK2"}),
    ])
    state = {"cur": None}

    def opener(req, timeout=0):
        import urllib.error
        kind, data = next(seq)
        if kind == "init":
            return _json_resp({"success": True, "data": data})
        if kind == "404":
            raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)
        return _json_resp({"success": True, "data": data})

    token = pairing.run_pairing(
        "http://h:8000", "srl",
        show_code=shown.append, sleep=lambda s: None, opener=opener, poll_interval=0,
    )
    assert token == "TOK2"
    assert shown == ["111111", "222222"]
