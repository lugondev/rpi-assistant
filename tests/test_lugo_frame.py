import pytest

from a2a_client.lugo_frame import LUGO_FRAME_OPUS, LUGO_FRAME_JSON, decode_frame, encode_frame


def test_encode_frame_produces_expected_bytes():
    assert encode_frame(LUGO_FRAME_OPUS, b"ab") == b"\x00\x00\x00\x02ab"


def test_encode_decode_round_trip():
    frame_type, payload = decode_frame(encode_frame(LUGO_FRAME_OPUS, b"hello opus"))
    assert frame_type == LUGO_FRAME_OPUS
    assert payload == b"hello opus"


def test_encode_decode_round_trip_json_type():
    frame_type, payload = decode_frame(encode_frame(LUGO_FRAME_JSON, b"{}"))
    assert frame_type == LUGO_FRAME_JSON
    assert payload == b"{}"


def test_encode_decode_empty_payload():
    frame_type, payload = decode_frame(encode_frame(LUGO_FRAME_OPUS, b""))
    assert frame_type == LUGO_FRAME_OPUS
    assert payload == b""


def test_decode_frame_shorter_than_header_raises():
    with pytest.raises(ValueError, match="shorter than header"):
        decode_frame(b"\x00\x00")


def test_decode_frame_payload_size_mismatch_raises():
    # Header claims 5 bytes of payload but only 2 are present.
    with pytest.raises(ValueError, match="size mismatch"):
        decode_frame(b"\x00\x00\x00\x05ab")


def test_encode_frame_rejects_oversized_payload():
    with pytest.raises(ValueError, match="too large"):
        encode_frame(LUGO_FRAME_OPUS, b"x" * 65536)
