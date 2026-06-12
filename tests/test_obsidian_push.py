from pathlib import Path
from modules.obsidian_push import compute_health, render_status_md, ObsidianPush
from modules.vault_registry import Vault


def test_priority_red_wins():
    assert compute_health("v", ["failed", "running"], True) == "red"


def test_yellow_for_running():
    assert compute_health("v", ["running"], True) == "yellow"


def test_green_for_session_when_idle():
    assert compute_health("v", [], True) == "green"


def test_gray_for_idle():
    assert compute_health("v", [], False) == "gray"


def test_render_contains_color_and_name():
    out = render_status_md("alpha", "green", True)
    assert "alpha" in out and "green" in out and "Terminal session active" in out


def test_push_creates_resman_dir(tmp_path):
    vp = tmp_path / "vault"
    vp.mkdir()
    v = Vault(name="alpha", path=str(vp))
    push = ObsidianPush(
        vault_iter=lambda: [v],
        get_task_states=lambda n: [],
        has_session_for=lambda n: False,
    )
    push.push_all_vaults()
    status = vp / "_resman" / "status.md"
    assert status.exists()
    text = status.read_text()
    assert "alpha" in text


def test_push_handles_oserror_gracefully(tmp_path):
    # Path that doesn't exist
    v = Vault(name="ghost", path=str(tmp_path / "nope"), path_exists=False)
    push = ObsidianPush(
        vault_iter=lambda: [v],
        get_task_states=lambda n: [],
        has_session_for=lambda n: False,
    )
    out = push.push_all_vaults()
    # path_exists=False is filtered upstream — counts as 0/0
    assert out["ok"] == 0
