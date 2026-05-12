"""Test fixtures and path setup."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))


@pytest.fixture(autouse=True)
def _isolate_user_resman_yaml(monkeypatch, tmp_path):
    """Stop tests from picking up a real ~/.resman.yaml on the host.

    ConfigManager prefers a per-user override at ~/.resman.yaml over the
    repo-shipped config. Without isolation, a developer's real file would
    silently replace the test fixtures — flaky and a privacy footgun.
    """
    from modules import config_manager
    monkeypatch.setattr(
        config_manager,
        "_default_user_override_path",
        lambda: tmp_path / ".no-such-resman.yaml",
    )
