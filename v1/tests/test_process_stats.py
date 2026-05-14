"""Tests for modules.process_stats — exercises the /proc walker against a
fake filesystem so we don't depend on whatever PIDs happen to be live."""
from pathlib import Path

from modules import process_stats


def _make_proc(tmp_path: Path, procs: list[dict]) -> Path:
    """Build a minimal /proc tree under tmp_path/proc.

    Each proc dict is {pid, comm, ppid, rss_kb}. Only the `status` file is
    populated since that's all process_stats reads.
    """
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    for p in procs:
        pdir = proc_root / str(p["pid"])
        pdir.mkdir()
        rss_line = f"VmRSS:\t{p['rss_kb']} kB\n" if p.get("rss_kb") is not None else ""
        (pdir / "status").write_text(
            f"Name:\t{p['comm']}\n"
            f"PPid:\t{p['ppid']}\n"
            f"{rss_line}"
        )
    return proc_root


def test_read_proc_returns_fields(tmp_path):
    root = _make_proc(tmp_path, [
        {"pid": 100, "comm": "ttyd", "ppid": 1, "rss_kb": 5120},
    ])
    info = process_stats.read_proc(100, proc_root=root)
    assert info == {"pid": 100, "comm": "ttyd", "ppid": 1, "rss_kb": 5120}


def test_read_proc_returns_none_for_missing(tmp_path):
    root = _make_proc(tmp_path, [])
    assert process_stats.read_proc(999, proc_root=root) is None


def test_read_proc_treats_missing_rss_as_zero(tmp_path):
    """Kernel threads have no VmRSS line — we surface 0 rather than crash."""
    root = _make_proc(tmp_path, [
        {"pid": 2, "comm": "kthread", "ppid": 0, "rss_kb": None},
    ])
    info = process_stats.read_proc(2, proc_root=root)
    assert info["rss_kb"] == 0


def test_descendants_walks_full_tree(tmp_path):
    """Tree: ttyd(100) → tmux(200) → bash(300) → claude(400) + bash(301)."""
    root = _make_proc(tmp_path, [
        {"pid": 100, "comm": "ttyd", "ppid": 1, "rss_kb": 5000},
        {"pid": 200, "comm": "tmux", "ppid": 100, "rss_kb": 3000},
        {"pid": 300, "comm": "bash", "ppid": 200, "rss_kb": 1500},
        {"pid": 301, "comm": "bash", "ppid": 200, "rss_kb": 1500},
        {"pid": 400, "comm": "claude", "ppid": 300, "rss_kb": 250_000},
    ])
    index = process_stats.build_ppid_index(proc_root=root)
    desc = set(process_stats.descendants(200, index))
    assert desc == {300, 301, 400}


def test_descendants_handles_leaf(tmp_path):
    root = _make_proc(tmp_path, [
        {"pid": 100, "comm": "ttyd", "ppid": 1, "rss_kb": 5000},
    ])
    index = process_stats.build_ppid_index(proc_root=root)
    assert process_stats.descendants(100, index) == []


def test_process_tree_orders_root_first(tmp_path):
    root = _make_proc(tmp_path, [
        {"pid": 300, "comm": "bash", "ppid": 0, "rss_kb": 1500},
        {"pid": 400, "comm": "claude", "ppid": 300, "rss_kb": 250_000},
    ])
    tree = process_stats.process_tree(300, proc_root=root)
    assert tree[0]["pid"] == 300
    pids = {p["pid"] for p in tree}
    assert pids == {300, 400}


def test_process_tree_empty_when_root_missing(tmp_path):
    root = _make_proc(tmp_path, [])
    assert process_stats.process_tree(999, proc_root=root) == []
