"""Runs the dependency-free JS behavior suites (tests/js/*.test.mjs).

wiki-marks-core.js maps a reader's selection back to markdown source — logic
worth executing, not grepping. node is not an app prerequisite, so this skips
rather than fails when it is missing (the mainBench idiom). The same file ships
in every app with the highlighter.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SUITES = sorted((Path(__file__).resolve().parent / "js").glob("*.test.mjs"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("suite", SUITES, ids=lambda p: p.stem)
def test_js_behavior_suite(suite):
    proc = subprocess.run(["node", str(suite)], capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, f"\n{proc.stdout}\n{proc.stderr}"
    assert "passed" in proc.stdout


def test_every_js_suite_is_collected():
    # a new tests/js/*.test.mjs must not sit there unrun
    assert {p.stem for p in SUITES} >= {"wiki-marks-core.test"}
