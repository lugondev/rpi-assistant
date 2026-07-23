from a2a_client.disconnect import classify_disconnect, RECONNECT, REPAIR


def test_handshake_403_is_repair():
    assert classify_disconnect(403, None) == REPAIR


def test_handshake_401_is_repair():
    assert classify_disconnect(401, None) == REPAIR


def test_goodbye_account_disabled_is_repair():
    assert classify_disconnect(None, "account_disabled") == REPAIR


def test_idle_timeout_goodbye_is_reconnect():
    assert classify_disconnect(None, "idle_timeout") == RECONNECT


def test_plain_network_drop_is_reconnect():
    assert classify_disconnect(None, None) == RECONNECT


def test_server_5xx_handshake_is_reconnect():
    # a 500 during handshake is an outage, not a revoke — keep the token
    assert classify_disconnect(500, None) == RECONNECT
