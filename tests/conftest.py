"""Test fixtures and path setup."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control-plane"))


def solbench_home() -> Path:
    """The solBench checkout: $SOLBENCH_HOME › the sibling ../solBench (bench layout)."""
    env = os.environ.get("SOLBENCH_HOME")
    root = Path(env) if env else ROOT.parent / "solBench"
    if not (root / "webterm" / "pyproject.toml").is_file():
        if env:  # explicitly set but wrong: a broken host, not a missing kit
            pytest.fail(f"SOLBENCH_HOME={env} is not a solBench checkout")
        pytest.skip("solBench checkout not found beside this repo — set SOLBENCH_HOME",
                    allow_module_level=True)
    return root


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
