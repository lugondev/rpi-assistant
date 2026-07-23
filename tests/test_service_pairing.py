from a2a_client.disconnect import REPAIR, RECONNECT


class FakeService:
    """Mirrors the two extracted helpers so we can test them without asyncio/audio."""
    def __init__(self, token_path, override=None, stored=None):
        from a2a_client import device_identity
        self._di = device_identity
        self.token_path = token_path
        self.override = override
        self._device_token = None
        if stored:
            device_identity.save_device_token(token_path, stored)

    # copies of the real logic (kept in sync with service.py)
    def resolve_device_token(self, run_pairing):
        if self.override:
            self._device_token = self.override
            return self._device_token
        tok = self._di.load_device_token(self.token_path)
        if tok:
            self._device_token = tok
            return tok
        tok = run_pairing()
        self._di.save_device_token(self.token_path, tok)
        self._device_token = tok
        return tok

    def on_disconnect(self, handshake_status, goodbye_reason):
        from a2a_client.disconnect import classify_disconnect, REPAIR
        action = classify_disconnect(handshake_status, goodbye_reason)
        if action == REPAIR:
            self._di.clear_device_token(self.token_path)
            self._device_token = None
        return action


def test_resolve_uses_override(tmp_path):
    s = FakeService(str(tmp_path / "t"), override="OVR")
    assert s.resolve_device_token(run_pairing=lambda: "PAIRED") == "OVR"


def test_resolve_uses_stored(tmp_path):
    s = FakeService(str(tmp_path / "t"), stored="STORED")
    assert s.resolve_device_token(run_pairing=lambda: "PAIRED") == "STORED"


def test_resolve_pairs_and_persists(tmp_path):
    from a2a_client import device_identity
    path = str(tmp_path / "t")
    s = FakeService(path)
    assert s.resolve_device_token(run_pairing=lambda: "PAIRED") == "PAIRED"
    assert device_identity.load_device_token(path) == "PAIRED"


def test_on_disconnect_revoke_wipes_token(tmp_path):
    path = str(tmp_path / "t")
    s = FakeService(path, stored="STORED")
    s.resolve_device_token(run_pairing=lambda: "x")
    assert s.on_disconnect(403, None) == REPAIR
    from a2a_client import device_identity
    assert device_identity.load_device_token(path) is None
    assert s._device_token is None


def test_on_disconnect_network_drop_keeps_token(tmp_path):
    path = str(tmp_path / "t")
    s = FakeService(path, stored="STORED")
    s.resolve_device_token(run_pairing=lambda: "x")
    assert s.on_disconnect(None, None) == RECONNECT
    from a2a_client import device_identity
    assert device_identity.load_device_token(path) == "STORED"
