"""No host path may be baked into tracked code (solBench planUpdatePath.md §2.4)."""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".sh", ".js", ".mjs", ".ts", ".rs", ".toml", ".yaml", ".yml",
            ".json", ".html", ".css", ".txt", ".template", ".example", ".service", ".conf"}
# The family's host roots, old AND new: a literal of the destination is as
# wrong as one of the origin. Lookbehind excludes `~/proj` and `:/proj`
# (container mounts); `/home/user` alone is the cldlab container user.
HOST_PATH = re.compile(
    r"(?<![\w.~-])(?:/data/(?:proj|agents|aproj)"
    r"|/proj/(?:agents|aproj|wikval|myGits|academic)"
    r"|/home/vlad|/home/user/proj)(?![\w-])")


def test_no_host_paths():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    hits = []
    for raw in filter(None, out.split(b"\0")):
        path = REPO / raw.decode()
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        if path.resolve() == Path(__file__).resolve():  # the pattern above names the roots
            continue
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if HOST_PATH.search(line):
                hits.append(f"{raw.decode()}:{n}: {line.strip()[:100]}")
    assert not hits, "host paths in tracked files:\n" + "\n".join(hits)
