"""The Home Assistant integration ships its own copy of the library, it must not drift apart."""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def _read(path):
    # Line endings may differ, git converts them on Windows
    return path.read_bytes().replace(b"\r\n", b"\n")


@pytest.mark.parametrize("name", ["api.py", "analytics.py", "exceptions.py", "export.py"])
def test_vendored_copy_is_identical(name):
    original = _read(ROOT / "lidlplus" / name)
    vendored = _read(ROOT / "custom_components" / "lidl_plus" / "_lidlplus" / name)
    assert vendored == original, f"copy lidlplus/{name} to custom_components/lidl_plus/_lidlplus/{name}"
