from pathlib import Path

from a2a_client.session_state import load_session_id, save_session_id


def test_load_returns_none_for_missing_file(tmp_path: Path):
    assert load_session_id(tmp_path / "does-not-exist") is None


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "nested" / "session_id"
    save_session_id(path, "abc-123")
    assert load_session_id(path) == "abc-123"


def test_save_overwrites_previous_value(tmp_path: Path):
    path = tmp_path / "session_id"
    save_session_id(path, "first")
    save_session_id(path, "second")
    assert load_session_id(path) == "second"


def test_load_strips_whitespace(tmp_path: Path):
    path = tmp_path / "session_id"
    path.write_text("  padded-id  \n")
    assert load_session_id(path) == "padded-id"


def test_load_treats_blank_file_as_none(tmp_path: Path):
    path = tmp_path / "session_id"
    path.write_text("   \n")
    assert load_session_id(path) is None
