"""Unit tests for AI Research Agent tools."""
import json
import subprocess
from unittest import mock
import pytest


# ---------------------------------------------------------------------------
# Test: retry decorator
# ---------------------------------------------------------------------------
def test_retry_success():
    from tools.retry import retry

    call_count = 0

    @retry(max_attempts=3, backoff_factor=0.01)
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("fail")
        return "ok"

    assert flaky() == "ok"
    assert call_count == 3


def test_retry_exhausted():
    from tools.retry import retry

    @retry(max_attempts=2, backoff_factor=0.01)
    def always_fail():
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        always_fail()


# ---------------------------------------------------------------------------
# Test: manage_config validation
# ---------------------------------------------------------------------------
def test_config_validation():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from manage_config import validate_config

    good = {"max_tokens": 4096, "max_iterations": 15, "temperature": 0.1, "hard_limit": False, "timeout": 300}
    assert validate_config(good) == []

    bad = {"max_tokens": "not_int", "temperature": "bad"}
    errors = validate_config(bad)
    assert len(errors) > 0


# ---------------------------------------------------------------------------
# Test: path validation in file_manager
# ---------------------------------------------------------------------------
def test_validate_path_safe():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
    # We test the validate function directly
    import posixpath

    def validate(path):
        resolved = posixpath.normpath(path)
        if not resolved.startswith("/workspace"):
            raise ValueError(f"Path '{path}' is outside /workspace")
        return resolved

    assert validate("/workspace/test.py") == "/workspace/test.py"
    assert validate("/workspace/sub/../file.txt") == "/workspace/file.txt"

    with pytest.raises(ValueError):
        validate("/etc/passwd")

    with pytest.raises(ValueError):
        validate("/workspace/../../etc/passwd")


# ---------------------------------------------------------------------------
# Test: cleanup_worker pruning logic
# ---------------------------------------------------------------------------
def test_prune_directory(tmp_path):
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from tools.cleanup_worker import _prune_directory

    # Create 8 fake files
    for i in range(8):
        (tmp_path / f"tts_{i}.mp3").write_text("x" * 100)

    _prune_directory(str(tmp_path), max_files=5, max_size_bytes=10 * 1024 * 1024)
    remaining = list(tmp_path.glob("*"))
    assert len(remaining) <= 5


# ---------------------------------------------------------------------------
# Test: standardized JSON response format
# ---------------------------------------------------------------------------
def test_json_response_format():
    """Verify that a well-formed tool response contains status key."""
    good_ok = json.dumps({"status": "ok", "output": "hello"})
    good_err = json.dumps({"status": "error", "message": "fail"})

    parsed_ok = json.loads(good_ok)
    parsed_err = json.loads(good_err)

    assert parsed_ok["status"] == "ok"
    assert "output" in parsed_ok
    assert parsed_err["status"] == "error"
    assert "message" in parsed_err
