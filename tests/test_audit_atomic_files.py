"""Atomic data stays intact while short Windows reader locks are retried."""
import os
import pytest

from src.services.atomic_files import replace_local


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_windows_denial_retries_same_atomic_operation_and_preserves_old_bytes(tmp_path, monkeypatch, winerror):
    source, target = tmp_path / "new", tmp_path / "current"
    source.write_bytes(b"new bytes")
    target.write_bytes(b"old bytes")
    original = os.replace
    calls = []
    def blocked_then_ok(a, b):
        calls.append((a, b))
        assert source.read_bytes() == b"new bytes"
        assert target.read_bytes() == b"old bytes"
        if len(calls) < 3:
            error = PermissionError("Synthetic reader lock")
            error.winerror = winerror
            raise error
        original(a, b)
    monkeypatch.setattr("src.services.atomic_files.os.replace", blocked_then_ok)
    monkeypatch.setattr("src.services.atomic_files.time.sleep", lambda _: None)
    replace_local(source, target)
    assert len(calls) == 3 and len(set(calls)) == 1
    assert target.read_bytes() == b"new bytes" and not source.exists()


@pytest.mark.parametrize("winerror,attempts", [(5, 8), (None, 1), (2, 1)])
def test_permanent_denial_is_bounded_and_preserves_source_and_destination(tmp_path, monkeypatch, winerror, attempts):
    source, target = tmp_path / "new", tmp_path / "current"
    source.write_bytes(b"new bytes")
    target.write_bytes(b"old bytes")
    calls = []
    def denied(*args):
        calls.append(args)
        error = PermissionError("Synthetic permanent denial")
        error.winerror = winerror
        raise error
    monkeypatch.setattr("src.services.atomic_files.os.replace", denied)
    monkeypatch.setattr("src.services.atomic_files.time.sleep", lambda _: None)
    with pytest.raises(PermissionError):
        replace_local(source, target)
    assert len(calls) == attempts
    assert source.read_bytes() == b"new bytes" and target.read_bytes() == b"old bytes"
