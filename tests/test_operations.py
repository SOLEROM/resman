"""The operation registry (docs/design/17-skills.md, plan phase 1).

One entry per operation, with a provider, a kind, Param specs and a builder.
task_manager, routes and (from phase 2) the SPA read it; nothing else may
know an operation key. These tests pin the registry's shape and the exact
command lines it produces for the ten operations that existed before it.
"""
import json
import re
from pathlib import Path

import pytest

from modules import operations, plugin_commands, resman_skills
from modules.operations import Operation, Param, RunContext

REPO = Path(__file__).resolve().parents[1]
OPS_BEFORE_REGISTRY = {
    "wiki-ingest", "wiki-ingest-prefix", "wiki-lint", "wiki-autoresearch",
    "wiki-canvas", "wiki-update-hot-cache", "wiki-bootstrap", "wiki-hint",
    "run-prompt", "run-shell",
}


def ctx(tmp_path, with_plugin=False) -> RunContext:
    root = tmp_path / "resman"
    if with_plugin:
        d = root / "skills" / ".claude-plugin"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": "resman", "version": "0.1.0"}))
    return RunContext(resman_root=root, vault_path=str(tmp_path / "alpha"), claude_exe="claude")


# ----- shape -----

def test_every_pre_registry_operation_is_registered():
    assert OPS_BEFORE_REGISTRY <= set(operations.REGISTRY)


def test_every_operation_has_a_known_provider_and_its_prefix():
    for key, op in operations.REGISTRY.items():
        assert op.key == key
        assert op.provider in operations.PROVIDERS, key
        assert key.startswith(operations.PROVIDER_PREFIX[op.provider]), key
        assert op.kind in ("prompt", "shell"), key
        assert (op.build_prompt is None) == (op.kind == "shell"), key
        assert (op.build_argv is None) == (op.kind == "prompt"), key


def test_every_obsidian_operation_names_a_plugin_skill_resman_declares():
    for op in operations.for_provider("obsidian"):
        assert op.skill in plugin_commands.PLUGIN_USES, op.key


def test_every_resman_operation_has_its_skill_folder():
    for op in operations.for_provider("resman"):
        assert (REPO / "skills" / "skills" / op.skill / "SKILL.md").is_file(), op.key


def test_adhoc_operations_have_no_skill():
    assert {op.key for op in operations.for_provider("adhoc")} == {"run-prompt", "run-shell"}
    assert all(op.skill == "" for op in operations.for_provider("adhoc"))


def test_public_view_has_no_callables_and_the_fields_the_spa_needs():
    for op in operations.REGISTRY.values():
        pub = operations.public(op)
        assert not any(callable(v) for v in pub.values()), op.key
        assert {"key", "label", "group", "provider", "kind", "attendable", "skill",
                "params", "desc", "note", "icon", "confirm", "remote"} <= set(pub)
        assert pub["attendable"] == (op.kind == "prompt")
        for p in pub["params"]:
            assert {"key", "type", "label", "required", "max_len", "placeholder"} == set(p)


def test_provider_of_unknown_key_is_unknown():
    assert operations.provider_of("wiki-lint") == "obsidian"
    assert operations.provider_of("run-shell") == "adhoc"
    assert operations.provider_of("gone-op") == "unknown"


def test_providers_are_listed_with_labels():
    assert [p["id"] for p in operations.providers()] == ["obsidian", "resman", "adhoc"]
    assert all(p["label"] for p in operations.providers())


# ----- validation: the rules _validate_params used to hold -----

@pytest.mark.parametrize("op,params,message", [
    ("wiki-ingest", {}, "'url' required"),
    ("wiki-ingest", {"url": "ftp://x"}, "must be http or https"),
    ("wiki-ingest-prefix", {"url": ""}, "'url' required"),
    ("wiki-ingest-prefix", {"url": "ftp://x"}, "must be http or https"),
    ("wiki-autoresearch", {}, "'topic' required"),
    ("wiki-autoresearch", {"topic": "a" * 250}, "printable ASCII"),
    ("wiki-canvas", {"description": 3}, "must be a string"),
    ("wiki-canvas", {"description": "x" * 201}, "printable ASCII"),
    ("run-prompt", {"prompt": ""}, "'prompt' required"),
    ("run-prompt", {"prompt": "é"}, "printable ASCII"),
    ("run-shell", {"cmd_parts": "rm -rf /"}, "non-empty list"),
    ("run-shell", {"cmd_parts": []}, "non-empty list"),
    ("run-shell", {"cmd_parts": ["ls", 3]}, "all be strings"),
])
def test_validate_rejects(op, params, message):
    with pytest.raises(ValueError, match=message):
        operations.validate(operations.REGISTRY[op], params)


def test_validate_normalizes_checkbox_and_optional_text():
    ingest = operations.validate(operations.REGISTRY["wiki-ingest"], {"url": "https://a"})
    assert ingest == {"url": "https://a", "update_canvas": False}
    ingest = operations.validate(operations.REGISTRY["wiki-ingest"],
                                 {"url": "https://a", "update_canvas": "yes"})
    assert ingest["update_canvas"] is True
    assert operations.validate(operations.REGISTRY["wiki-canvas"], {}) == {"description": ""}
    assert operations.validate(operations.REGISTRY["wiki-canvas"],
                               {"description": None}) == {"description": ""}


def test_validate_keeps_unknown_keys_and_does_not_mutate_input():
    src = {"url": "https://a", "extra": 1}
    out = operations.validate(operations.REGISTRY["wiki-ingest"], src)
    assert out["extra"] == 1 and "update_canvas" not in src


# ----- builders: the ten command lines, unchanged -----

def test_shell_builders(tmp_path):
    c = ctx(tmp_path)
    ingest = str(c.resman_root / "tools" / "ingest.sh")
    prefix = str(c.resman_root / "prompts" / "urlInjestPrefix.md")
    build = lambda k, p: operations.REGISTRY[k].build_argv(p, c)
    assert build("wiki-ingest", {"url": "https://a", "update_canvas": False}) == [ingest, c.vault_path, "https://a"]
    assert build("wiki-ingest", {"url": "https://a", "update_canvas": True}) == [ingest, c.vault_path, "https://a", "--can"]
    assert build("wiki-ingest-prefix", {"url": "https://a", "update_canvas": False}) == [ingest, c.vault_path, "https://a", "--prefix", prefix]
    assert build("wiki-ingest-prefix", {"url": "https://a", "update_canvas": True}) == [ingest, c.vault_path, "https://a", "--prefix", prefix, "--can"]
    assert build("run-shell", {"cmd_parts": ["echo", "hi"]}) == ["echo", "hi"]


def test_prompt_builders(tmp_path):
    c = ctx(tmp_path)
    build = lambda k, p: operations.REGISTRY[k].build_prompt(p, c)
    assert build("wiki-lint", {}) == "/claude-obsidian:wiki-lint"
    assert build("wiki-update-hot-cache", {}) == "/claude-obsidian:update-hot-cache"
    assert build("wiki-autoresearch", {"topic": "elixir"}) == "/claude-obsidian:autoresearch elixir"
    assert build("wiki-canvas", {"description": "hubs"}) == "/claude-obsidian:canvas hubs"
    assert build("wiki-canvas", {"description": ""}) == "/claude-obsidian:canvas"
    assert build("wiki-hint", {}) == plugin_commands.WIKI_HINT
    assert "/claude-obsidian:wiki" in build("wiki-bootstrap", {})
    assert build("run-prompt", {"prompt": "summarize wiki"}) == "summarize wiki"


def test_run_context_carries_the_plugin_dir_only_when_the_folder_exists(tmp_path):
    assert ctx(tmp_path).plugin_dir_args == []
    c = ctx(tmp_path, with_plugin=True)
    assert c.plugin_dir_args == ["--plugin-dir", str(c.resman_root / "skills")]


def test_a_resman_operation_builds_a_resman_prompt(tmp_path, monkeypatch):
    """The shape every rs-* entry takes: provider resman, kind prompt,
    build_prompt through resman_skills.skill_prompt."""
    echo = Operation(key="rs-echo", label="Echo", group="Wiki", provider="resman",
                     kind="prompt", skill="echo",
                     params=(Param("focus", "text", "Focus"),),
                     build_prompt=lambda p, c: resman_skills.skill_prompt("echo", p.get("focus", "")))
    monkeypatch.setitem(operations.REGISTRY, "rs-echo", echo)
    assert operations.provider_of("rs-echo") == "resman"
    assert echo.build_prompt({"focus": "x"}, ctx(tmp_path)) == "/resman:echo x"
    assert operations.public(echo)["params"][0]["required"] is False


# ----- the SPA reads the registry; it spells no operation key of its own -----

OP_KEY_LITERAL = re.compile(r"""["'](wiki-[a-z-]+|run-[a-z-]+|rs-[a-z-]+)["']""")
# The sidebar's single-purpose ↘ ingest buttons are the documented exception
# (10-frontend.md); everything else comes from GET /api/operations.
ALLOWED_JS_LITERALS = {"wiki-ingest", "wiki-ingest-prefix"}


def test_the_spa_spells_no_operation_keys():
    js = (REPO / "control-plane" / "static" / "js")
    found = {}
    for f in ("app.js", "tasks-core.js", "skills.js"):
        for m in OP_KEY_LITERAL.finditer((js / f).read_text()):
            if m.group(1) in operations.REGISTRY or m.group(1).startswith("rs-"):
                found.setdefault(f, set()).add(m.group(1))
    assert set().union(*found.values()) if found else set() <= ALLOWED_JS_LITERALS, found
    app_js = (js / "app.js").read_text()
    assert "const OPERATIONS" not in app_js and "ATTENDABLE_OPERATIONS" not in app_js


# ----- rs-deep-list: the first resman skill (docs/deepList-plan.md) -----
DEEP_LIST_DEFAULT_ARGS = ('list_size=15 candidates_per_run=30 min_score=40 w_importance=40 w_gap=30 '
                          'w_leverage=20 w_urgency=10 filled_min_pages=2 filled_min_words=300 '
                          'focus="" exclude=""')


def test_deep_list_is_a_resman_prompt_operation_with_a_folder():
    op = operations.REGISTRY["rs-deep-list"]
    assert (op.provider, op.kind, op.skill, op.group, op.remote) == ("resman", "prompt", "deep-list", "Research", True)
    assert [p.key for p in op.params] == ["focus"] and not op.params[0].required
    assert (REPO / "skills" / "skills" / "deep-list" / "settings.yaml").is_file()


def test_deep_list_prompt_carries_every_setting_in_schema_order():
    c = RunContext(resman_root=REPO, vault_path="/v", claude_exe="claude")
    assert operations.REGISTRY["rs-deep-list"].build_prompt({}, c) == "/resman:deep-list " + DEEP_LIST_DEFAULT_ARGS


def test_deep_list_prompt_precedence_yaml_then_task_focus():
    build = operations.REGISTRY["rs-deep-list"].build_prompt
    c = RunContext(resman_root=REPO, vault_path="/v", claude_exe="claude",
                   settings={"focus": "edge inference", "list_size": 20, "exclude": ["crypto payroll", "x"]})
    p = build({"focus": ""}, c)                      # an empty per-task focus keeps the stored one
    assert 'list_size=20 ' in p and 'focus="edge inference"' in p and 'exclude="crypto payroll|x"' in p
    p = build({"focus": "this run only"}, c)
    assert 'focus="this run only"' in p and 'list_size=20 ' in p
    # a checkout without the skill folder still produces a runnable command
    bare = RunContext(resman_root=Path("/nonexistent"), vault_path="/v", claude_exe="claude")
    assert build({}, bare) == "/resman:deep-list"
