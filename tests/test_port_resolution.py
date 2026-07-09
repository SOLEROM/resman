"""Port resolution: --port › .port file › resman.yaml (app.port) › DEFAULT_PORT."""
import pytest


@pytest.fixture
def srv():
    import server
    return server


class _FakeConfig:
    """Duck-typed stand-in for ConfigManager: only `.app` is read here."""
    def __init__(self, app=None):
        self.app = app or {}


# ── _read_port_file ──────────────────────────────────────────────────────────

def test_read_port_file_missing_returns_none(srv, tmp_path):
    assert srv._read_port_file(tmp_path / "nope") is None


def test_read_port_file_valid(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("6001\n")
    assert srv._read_port_file(p) == 6001


def test_read_port_file_skips_blank_lines_and_whitespace(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("\n   \n  6543  \n7000\n")
    assert srv._read_port_file(p) == 6543


def test_read_port_file_empty_returns_none(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("\n   \n")
    assert srv._read_port_file(p) is None


def test_read_port_file_non_numeric_returns_none(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("not-a-port\n")
    assert srv._read_port_file(p) is None


@pytest.mark.parametrize("bad", ["0", "-1", "70000", "99999"])
def test_read_port_file_out_of_range_returns_none(srv, tmp_path, bad):
    p = tmp_path / ".port"
    p.write_text(bad + "\n")
    assert srv._read_port_file(p) is None


def test_read_port_file_boundaries_are_valid(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("1")
    assert srv._read_port_file(p) == 1
    p.write_text("65535")
    assert srv._read_port_file(p) == 65535


# ── _resolve_port precedence ─────────────────────────────────────────────────

def test_resolve_prefers_cli_over_everything(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("6001")
    cfg = _FakeConfig({"port": 5091})
    assert srv._resolve_port(cfg, cli_port=8080, port_file=p) == 8080


def test_resolve_uses_port_file_over_yaml(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("6001")
    cfg = _FakeConfig({"port": 5091})
    assert srv._resolve_port(cfg, cli_port=None, port_file=p) == 6001


def test_resolve_falls_back_to_yaml_when_no_port_file(srv, tmp_path):
    cfg = _FakeConfig({"port": 5091})
    assert srv._resolve_port(cfg, cli_port=None, port_file=tmp_path / "nope") == 5091


def test_resolve_falls_back_to_default_when_nothing_set(srv, tmp_path):
    cfg = _FakeConfig({})
    assert srv._resolve_port(cfg, cli_port=None, port_file=tmp_path / "nope") == srv.DEFAULT_PORT


def test_resolve_ignores_malformed_port_file_and_uses_yaml(srv, tmp_path):
    p = tmp_path / ".port"
    p.write_text("garbage")
    cfg = _FakeConfig({"port": 5091})
    assert srv._resolve_port(cfg, cli_port=None, port_file=p) == 5091
