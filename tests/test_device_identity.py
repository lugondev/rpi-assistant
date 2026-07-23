import os
import pytest
from a2a_client.device_identity import (
    read_device_serial, load_device_token, save_device_token, clear_device_token,
)


def test_read_serial_from_machine_id(tmp_path):
    p = tmp_path / "machine-id"
    p.write_text("abc123\n", encoding="utf-8")
    assert read_device_serial(str(p)) == "abc123"


def test_read_serial_missing_raises(tmp_path):
    with pytest.raises(RuntimeError):
        read_device_serial(str(tmp_path / "nope"))


def test_token_roundtrip_and_perms(tmp_path):
    path = str(tmp_path / "sub" / "device_token")
    assert load_device_token(path) is None
    save_device_token(path, "tok-xyz")
    assert load_device_token(path) == "tok-xyz"
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_clear_token(tmp_path):
    path = str(tmp_path / "device_token")
    save_device_token(path, "tok")
    clear_device_token(path)
    assert load_device_token(path) is None
    clear_device_token(path)  # idempotent, no raise
