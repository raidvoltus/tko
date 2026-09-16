"""Windows-only placeholders — auto-skipped on Linux CI/dev hosts."""
import pytest

pytestmark = [pytest.mark.windows, pytest.mark.integration]


def test_platform_is_windows(win32_only):
    import platform

    assert platform.system() == "Windows"
