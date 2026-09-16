import pytest

from scrap_monitoring_visualizer.receiver import LineFramer, LineFramingError


def test_framer_assembles_split_and_merged_packets() -> None:
    framer = LineFramer(max_record_bytes=16)

    assert framer.feed(b'{"a"') == ()
    assert framer.feed(b':1}\n{"b":2}\n') == (b'{"a":1}\n', b'{"b":2}\n')
    assert framer.buffered_bytes == 0


def test_framer_accepts_exact_byte_limit() -> None:
    framer = LineFramer(max_record_bytes=5)

    assert framer.feed(b"1234\n") == (b"1234\n",)


def test_framer_rejects_limit_without_lf() -> None:
    framer = LineFramer(max_record_bytes=5)

    with pytest.raises(LineFramingError):
        framer.feed(b"12345")
    assert framer.buffered_bytes == 0


def test_framer_preserves_completed_prefix_before_limit_error() -> None:
    framer = LineFramer(max_record_bytes=5)

    with pytest.raises(LineFramingError) as raised:
        framer.feed(b"ok\n12345")

    assert raised.value.completed_records == (b"ok\n",)
    assert framer.buffered_bytes == 0


def test_framer_discards_partial_line() -> None:
    framer = LineFramer()
    framer.feed(b"partial")

    assert framer.discard_partial() is True
    assert framer.discard_partial() is False
