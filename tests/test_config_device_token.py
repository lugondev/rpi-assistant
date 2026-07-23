from a2a_client.config import load_config


def test_device_token_defaults(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("server:\n  host: h\n", encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.device_token is None
    assert cfg.device_token_path.endswith("device_token")


def test_device_token_override(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("server:\n  host: h\n  device_token: DEVTOK\n", encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.device_token == "DEVTOK"
