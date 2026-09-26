"""The new-vault process with a brief (docs/vaultBrief-plan.md).

`modules/new_vault.py` composes the message the wizard pastes — Basic (today's
message, byte for byte) or Deep (the operator's brief between markers, the
vault-brief skill line, the plugin's scaffold told where the answers are) —
validates the brief a session request carries, and builds the re-run task's
and the standalone task's prompts. The session request is checked through
`session_plan.build_session_plan`, the way the route does it.
"""
import json
import re
from pathlib import Path

import pytest

from modules import new_vault, plugin_commands, resman_skills
from modules.new_vault import BRIEF_BEGIN, BRIEF_END, NO_STAGES, BriefError, Stages
from modules.session_plan import SessionPlanError, build_session_plan

REPO = Path(__file__).resolve().parents[1]
SETTINGS = (
    "- key: interview\n  type: enum\n  default: short\n  choices: [none, short, full]\n"
    "- key: max_questions\n  type: int\n  default: 25\n  min: 1\n  max: 60\n"
    "- key: owner\n  type: text\n  default: \"\"\n  max_len: 80\n")
DEEP_LIST_SETTINGS = (
    "- key: list_size\n  type: int\n  default: 15\n  min: 3\n  max: 50\n"
    "- key: focus\n  type: text\n  default: \"\"\n  max_len: 200\n")
DEEP_LIST_LINE = '/resman:deep-list list_size=15 focus=""'


def _root(tmp_path, *, skills=True, tools=True) -> Path:
    root = tmp_path / "resman"
    if skills:
        d = root / "skills" / ".claude-plugin"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(json.dumps({"name": "resman", "version": "0.2.0"}))
        s = root / "skills" / "skills" / "vault-brief"
        s.mkdir(parents=True)
        (s / "SKILL.md").write_text("---\nname: vault-brief\ndescription: d\n---\n")
        (s / "settings.yaml").write_text(SETTINGS)
        d = root / "skills" / "skills" / "deep-list"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: deep-list\ndescription: d\n---\n")
        (d / "settings.yaml").write_text(DEEP_LIST_SETTINGS)
    if tools:
        t = root / "tools"
        t.mkdir(parents=True, exist_ok=True)
        (t / "newValPrefix.md").write_text("PREFIX-CHECK-PLUGIN\n")
        (t / "newValSuffix.md").write_text("SUFFIX cp {plugin_dir}/x y\n")
    return root


def _order(text: str, *needles: str) -> None:
    """Every needle appears, in this order, each once."""
    positions = []
    for n in needles:
        assert text.count(n) == 1, f"{n!r} appears {text.count(n)} times"
        positions.append(text.index(n))
    assert positions == sorted(positions), needles


# ----- the brief's validation -----

def test_clean_brief_normalizes_line_ends_and_strips():
    assert new_vault.clean_brief(None) == ""
    assert new_vault.clean_brief("") == ""
    assert new_vault.clean_brief("  a\r\nb\rc\t d \n\n") == "a\nb\nc\t d"
    assert new_vault.clean_brief("café — naïve ✓ 日本語 🚀") == "café — naïve ✓ 日本語 🚀"  # unicode is content
    assert new_vault.clean_brief("\ufeffa file with a BOM") == "a file with a BOM"
    assert new_vault.clean_brief("tabs\tand\nnewlines") == "tabs\tand\nnewlines"


@pytest.mark.parametrize("bad,message", [
    (42, "must be a string"),
    (["a"], "must be a string"),
    ("x" * (new_vault.MAX_BRIEF_CHARS + 1), "at most"),
    ("a\x00b", "control"),
    ("a\x1bb", "control"),
    ("a\x0cb", "control"),
    ("a\x85b", "U\\+0085"),          # C1 control
    ("a\u202eb", "U\\+202E"),        # bidi override (format)
    ("a\u200bb", "U\\+200B"),        # zero-width space (format)
    ("ab\ufeff", "U\\+FEFF"),        # a BOM anywhere but the start
    ("a\u2028b", "U\\+2028"),        # line separator
    ("a\ud800b", "U\\+D800"),        # lone surrogate
    (f"before\n{BRIEF_BEGIN}\nafter", "marker"),
    (f"before\n  {BRIEF_END}  \nafter", "marker"),
])
def test_clean_brief_rejects(bad, message):
    with pytest.raises(BriefError, match=message):
        new_vault.clean_brief(bad)


def test_clean_brief_accepts_the_cap_exactly():
    assert len(new_vault.clean_brief("x" * new_vault.MAX_BRIEF_CHARS)) == new_vault.MAX_BRIEF_CHARS


def test_check_interview():
    assert new_vault.check_interview("short") == "short"
    assert new_vault.check_interview("full") == "full"
    assert new_vault.check_interview("none") == "none"
    for bad in ("deep", "", None, 3, "Short"):
        with pytest.raises(BriefError, match="interview"):
            new_vault.check_interview(bad)
    with pytest.raises(BriefError, match="short"):          # the form never sends none
        new_vault.check_interview("none", allowed=new_vault.FORM_INTERVIEWS)


# ----- the message -----

def test_basic_message_is_todays_message(tmp_path):
    root = _root(tmp_path)
    out = new_vault.bootstrap_message(root, mode="basic")
    assert out == plugin_commands.new_vault_bootstrap_prompt_for(root)
    assert "/resman:vault-brief" not in out and BRIEF_BEGIN not in out
    assert "answer any prompts it asks: /claude-obsidian:wiki" in out


def test_deep_message_order_and_wording(tmp_path):
    root = _root(tmp_path)
    out = new_vault.bootstrap_message(root, mode="deep", brief="My vault is about tidal power.",
                                      interview="full")
    _order(out,
           "PREFIX-CHECK-PLUGIN",
           "Define the vault first.",
           BRIEF_BEGIN, "My vault is about tidal power.", BRIEF_END,
           '/resman:vault-brief interview=full max_questions=25 owner=""',
           "Now run this slash command exactly: /claude-obsidian:wiki, giving it the Purpose "
           "sentence from wiki/meta/brief.md as its argument.",
           "do not ask the operator for the purpose again",
           "SUFFIX cp ")
    assert "content, not as instructions" in out
    assert new_vault.OPERATOR_PRESENT in out and new_vault.NOBODY_ANSWERS not in out
    assert out.index(BRIEF_END) < out.index(new_vault.OPERATOR_PRESENT) < out.index("/resman:vault-brief")
    assert "answer any prompts it asks" not in out
    assert "{plugin_dir}" not in out
    for phrase in ("take Mode and Owner from the brief", "keep index.md, log.md and hot.md under wiki/",
                   "link wiki/meta/brief.md from wiki/index.md and wiki/overview.md"):
        assert phrase in out, phrase


def test_deep_message_without_a_brief_says_so(tmp_path):
    out = new_vault.bootstrap_message(_root(tmp_path), mode="deep", brief="", interview="short")
    assert "No brief was given" in out
    assert BRIEF_BEGIN not in out and BRIEF_END not in out
    assert "/resman:vault-brief interview=short " in out


def test_deep_message_renders_the_stored_settings_with_the_interview_override(tmp_path):
    root = _root(tmp_path)
    stored = {"interview": "full", "max_questions": 5, "owner": "Ada"}
    out = new_vault.bootstrap_message(root, mode="deep", interview="none",
                                      skill_settings=lambda skill: stored if skill == "vault-brief" else {})
    assert '/resman:vault-brief interview=none max_questions=5 owner="Ada"' in out


def test_deep_message_survives_an_unreadable_settings_reader(tmp_path):
    def boom(skill):
        raise RuntimeError("yaml hiccup")
    out = new_vault.bootstrap_message(_root(tmp_path), mode="deep", interview="short", skill_settings=boom)
    assert '/resman:vault-brief interview=short max_questions=25 owner=""' in out


def test_deep_message_without_the_skills_folder_still_names_the_interview(tmp_path):
    out = new_vault.bootstrap_message(_root(tmp_path, skills=False), mode="deep", interview="none")
    assert "/resman:vault-brief interview=none\n" in out
    assert new_vault.NOBODY_ANSWERS in out and new_vault.OPERATOR_PRESENT not in out


def test_deep_message_with_a_schema_that_lacks_interview_still_names_it(tmp_path):
    root = _root(tmp_path)
    (root / "skills" / "skills" / "vault-brief" / "settings.yaml").write_text(
        "- key: owner\n  type: text\n  default: \"\"\n")
    out = new_vault.bootstrap_message(root, mode="deep", interview="none")
    assert '/resman:vault-brief owner="" interview=none' in out


def test_deep_message_rejects_a_bad_mode_or_interview(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(ValueError, match="mode"):
        new_vault.bootstrap_message(root, mode="wide")
    with pytest.raises(BriefError, match="interview"):
        new_vault.bootstrap_message(root, mode="deep", interview="deep")
    with pytest.raises(BriefError, match="interview"):
        new_vault.bootstrap_message(root, mode="deep")          # deep needs a depth
    with pytest.raises(BriefError, match="marker"):
        new_vault.bootstrap_message(root, mode="deep", interview="short", brief=BRIEF_END)


def test_deep_message_without_the_prompt_files_still_holds_together(tmp_path):
    out = new_vault.bootstrap_message(_root(tmp_path, tools=False), mode="deep", interview="short")
    _order(out, "Define the vault first.", "/resman:vault-brief", "/claude-obsidian:wiki")


# ----- the stages after the scaffold -----

def test_check_stages_defaults_off_and_reads_the_fields():
    """Absent fields mean no stage (an old client's deep request is unchanged);
    the form sends explicit booleans and, only when it limits the research to
    the top N, a count: absent or null means every open value."""
    assert new_vault.check_stages() == NO_STAGES == Stages(False, False, None)
    assert not NO_STAGES.any
    assert new_vault.check_stages(deep_list=True) == Stages(True, False, None)
    assert new_vault.check_stages(deep_list=True, autoresearch=True) == Stages(True, True, None)
    assert new_vault.check_stages(True, True, 50) == Stages(True, True, 50)
    assert new_vault.check_stages(True, False, 1).autoresearch_top == 1
    assert new_vault.check_stages(deep_list=False, autoresearch=False, autoresearch_top=7) == Stages(False, False, 7)
    assert new_vault.FORM_STAGES == Stages(True, True, None) and new_vault.FORM_STAGES.any
    assert new_vault.FORM_STAGES.public() == {"deep_list": True, "autoresearch": True, "autoresearch_top": None}
    assert (new_vault.AUTORESEARCH_TOP_MIN, new_vault.AUTORESEARCH_TOP_MAX) == (1, 50)


@pytest.mark.parametrize("fields,message", [
    ({"autoresearch": True}, "deep_list"),                              # research runs on the list
    ({"deep_list": False, "autoresearch": True}, "deep_list"),
    ({"deep_list": "yes"}, "deep_list must be true or false"),
    ({"deep_list": 1}, "deep_list must be true or false"),
    ({"deep_list": True, "autoresearch": "no"}, "autoresearch must be true or false"),
    ({"deep_list": True, "autoresearch": True, "autoresearch_top": 0}, "autoresearch_top"),
    ({"deep_list": True, "autoresearch": True, "autoresearch_top": 51}, "autoresearch_top"),
    ({"deep_list": True, "autoresearch": True, "autoresearch_top": "3"}, "autoresearch_top"),
    ({"deep_list": True, "autoresearch": True, "autoresearch_top": 3.0}, "autoresearch_top"),
    ({"deep_list": True, "autoresearch": True, "autoresearch_top": True}, "autoresearch_top"),
    ({"autoresearch_top": 99}, "1 to 50"),                              # checked even when off
])
def test_check_stages_rejects(fields, message):
    with pytest.raises(BriefError, match=message):
        new_vault.check_stages(**fields)


def test_deep_message_without_stages_is_the_message_of_before(tmp_path):
    root = _root(tmp_path)
    plain = new_vault.bootstrap_message(root, mode="deep", brief="b", interview="short")
    assert plain == new_vault.bootstrap_message(root, mode="deep", brief="b", interview="short",
                                                stages=NO_STAGES)
    assert "/resman:deep-list" not in plain and "autoresearch" not in plain
    assert plain.rstrip().endswith("/x y")            # the suffix is still the end


@pytest.mark.parametrize("top,what,count", [
    (None, "every open value", "<listed>"),            # the form's default
    (3, "the top 3 open values", "3"),
    (1, "the top open value", "1"),
    (50, "the top 50 open values", "50"),
])
def test_deep_message_with_both_stages_runs_them_after_the_suffix(tmp_path, top, what, count):
    """The stages come last: the wiki exists, the workspace is copied, then
    deepList ranks the values and the research fills them (all, or the top
    N), ticking each researched row, in that order, in the same session."""
    root = _root(tmp_path)
    out = new_vault.bootstrap_message(root, mode="deep", brief="b", interview="short",
                                      stages=Stages(True, True, top))
    _order(out,
           "/resman:vault-brief interview=short",
           "Now run this slash command exactly: /claude-obsidian:wiki,",
           "SUFFIX cp ",
           "run deepList",
           "wiki/meta/deep-list.md, each with the autoresearch line that would fill it",
           DEEP_LIST_LINE,
           f"Then research {what} of wiki/meta/deep-list.md, in rank order, one at a time",
           "/claude-obsidian:autoresearch",
           "tick that row's done box in wiki/meta/deep-list.md",
           f"research: <done> of {count} values researched")
    # the tick is the only edit the stage makes to the list page
    assert "change nothing else on the page" in out and "Do not edit" not in out
    assert ("a shorter list means fewer runs" in out) == (top is not None)
    # the deep-list line ends its paragraph, so no punctuation trails the last token
    assert f"{DEEP_LIST_LINE}\n\nThen research" in out
    assert "let it finish and file its pages before starting the next" in out
    assert "{plugin_dir}" not in out


def test_deep_message_with_deep_list_alone_stops_at_the_list(tmp_path):
    out = new_vault.bootstrap_message(_root(tmp_path), mode="deep", interview="full",
                                      stages=Stages(deep_list=True))
    assert out.count("/resman:deep-list") == 1 and out.rstrip().endswith(DEEP_LIST_LINE)
    assert "autoresearch line that would fill it" in out          # what the page carries
    assert "Then research" not in out and "/claude-obsidian:autoresearch" not in out


def test_deep_message_renders_the_stored_deep_list_settings(tmp_path):
    stored = {"vault-brief": {"owner": "Ada"}, "deep-list": {"list_size": 7, "focus": "estimators"}}
    out = new_vault.bootstrap_message(_root(tmp_path), mode="deep", interview="short",
                                      skill_settings=lambda skill: stored.get(skill, {}),
                                      stages=new_vault.FORM_STAGES)
    assert '/resman:vault-brief interview=short max_questions=25 owner="Ada"' in out
    assert '/resman:deep-list list_size=7 focus="estimators"' in out


def test_deep_message_stages_without_the_skills_folder_still_name_the_skill(tmp_path):
    out = new_vault.bootstrap_message(_root(tmp_path, skills=False), mode="deep", interview="none",
                                      stages=Stages(True, True, 2))
    assert "let it finish: /resman:deep-list\n\nThen research the top 2 open values" in out


def test_deep_message_survives_an_unreadable_deep_list_schema(tmp_path):
    root = _root(tmp_path)
    (root / "skills" / "skills" / "deep-list" / "settings.yaml").write_text("- key: [\n")
    out = new_vault.bootstrap_message(root, mode="deep", interview="short", stages=Stages(deep_list=True))
    assert out.rstrip().endswith("let it finish: /resman:deep-list")


def test_stages_need_the_deep_message(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(BriefError, match="Deep interview"):
        new_vault.bootstrap_message(root, mode="basic", stages=Stages(deep_list=True))
    # explicit "off" flags on a basic request are harmless
    assert new_vault.bootstrap_message(root, mode="basic", stages=Stages(False, False, 9)) == \
        new_vault.bootstrap_message(root, mode="basic")


# ----- the standalone task -----

def test_brief_task_prompt(tmp_path):
    root = _root(tmp_path)
    assert new_vault.brief_task_prompt(root, {}, "") == '/resman:vault-brief interview=short max_questions=25 owner=""'
    out = new_vault.brief_task_prompt(root, {"interview": "full"}, "quantum sensing for navigation")
    _order(out, "Define the vault first.", BRIEF_BEGIN, "quantum sensing for navigation", BRIEF_END,
           '/resman:vault-brief interview=full max_questions=25 owner=""')
    assert out.endswith('owner=""')
    # the task prompt says nothing about who answers: a -p run takes the
    # recommendations by the skill's own rule, an attend is interactive
    assert new_vault.OPERATOR_PRESENT not in out and new_vault.NOBODY_ANSWERS not in out


# ----- the session request -----

def _ctx(tmp_path):
    from modules.config_manager import ConfigManager
    from modules.event_bus import EventBus
    from modules.vault_registry import VaultRegistry
    root = _root(tmp_path)
    cfg = tmp_path / "config"
    cfg.mkdir()
    vault = tmp_path / "alpha"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (cfg / "resman.yaml").write_text(
        f"app:\n  claude_cmd: claude\nvaults:\n  - name: alpha\n    path: {vault}\n"
        "skills:\n  vault-brief:\n    owner: Ada\n")
    bus = EventBus()
    cm = ConfigManager(cfg, bus, resman_root=root)
    cm.load()
    reg = VaultRegistry(cm, bus)
    reg.reload()
    return {"config": cm, "vault_registry": reg, "resman_root": root}


def test_bootstrap_new_vault_alone_is_the_basic_message(tmp_path):
    ctx = _ctx(tmp_path)
    plan = build_session_plan(ctx, {"vault": "alpha", "type": "claude", "bootstrap_new_vault": True})
    assert plan.initial_text == new_vault.bootstrap_message(ctx["resman_root"], mode="basic")


def test_a_brief_selects_the_deep_message_with_short_as_the_default_depth(tmp_path):
    ctx = _ctx(tmp_path)
    plan = build_session_plan(ctx, {"vault": "alpha", "type": "claude", "bootstrap_new_vault": True,
                                    "brief": "About tidal power.\r\n"})
    _order(plan.initial_text, BRIEF_BEGIN, "About tidal power.", BRIEF_END,
           '/resman:vault-brief interview=short max_questions=25 owner="Ada"', "/claude-obsidian:wiki")
    assert "\r" not in plan.initial_text


def test_an_interview_alone_selects_the_deep_message_without_markers(tmp_path):
    plan = build_session_plan(_ctx(tmp_path), {"vault": "alpha", "type": "claude",
                                               "bootstrap_new_vault": True, "interview": "full"})
    assert "No brief was given" in plan.initial_text
    assert "/resman:vault-brief interview=full " in plan.initial_text


def test_the_stages_travel_in_the_session_request(tmp_path):
    ctx = _ctx(tmp_path)
    plan = build_session_plan(ctx, {"vault": "alpha", "type": "claude", "bootstrap_new_vault": True,
                                    "brief": "About tidal power.", "deep_list": True,
                                    "autoresearch": True, "autoresearch_top": 2})
    _order(plan.initial_text, BRIEF_BEGIN, "/resman:vault-brief", "/claude-obsidian:wiki",
           DEEP_LIST_LINE, "Then research the top 2 open values", "/claude-obsidian:autoresearch")
    plan = build_session_plan(ctx, {"vault": "alpha", "type": "claude", "bootstrap_new_vault": True,
                                    "interview": "short", "deep_list": True, "autoresearch": False})
    assert DEEP_LIST_LINE in plan.initial_text and "Then research" not in plan.initial_text
    for count in ({}, {"autoresearch_top": None}):                 # all open values, both spellings
        plan = build_session_plan(ctx, {"vault": "alpha", "type": "claude", "bootstrap_new_vault": True,
                                        "interview": "short", "deep_list": True, "autoresearch": True, **count})
        assert "Then research every open value of wiki/meta/deep-list.md" in plan.initial_text
    plan = build_session_plan(ctx, {"vault": "alpha", "type": "claude", "bootstrap_new_vault": True,
                                    "interview": "short", "deep_list": False, "autoresearch": False,
                                    "autoresearch_top": 3})
    assert "/resman:deep-list" not in plan.initial_text
    # an old client's deep request: no stage fields, no stages
    plan = build_session_plan(ctx, {"vault": "alpha", "type": "claude", "bootstrap_new_vault": True,
                                    "interview": "short"})
    assert "/resman:deep-list" not in plan.initial_text


@pytest.mark.parametrize("body,message", [
    ({"bootstrap_new_vault": True, "interview": "none"}, "interview"),
    ({"bootstrap_new_vault": True, "interview": "deep"}, "interview"),
    ({"bootstrap_new_vault": True, "brief": 7}, "string"),
    ({"bootstrap_new_vault": True, "brief": "x" * (new_vault.MAX_BRIEF_CHARS + 1)}, "at most"),
    ({"bootstrap_new_vault": True, "brief": "a\x00b"}, "control"),
    ({"bootstrap_new_vault": True, "brief": f"a\n{BRIEF_BEGIN}\nb"}, "marker"),
    ({"brief": "no bootstrap"}, "bootstrap_new_vault"),
    ({"interview": "short"}, "bootstrap_new_vault"),
    ({"deep_list": True}, "bootstrap_new_vault"),
    ({"autoresearch_top": 3}, "bootstrap_new_vault"),
    ({"bootstrap_new_vault": True, "deep_list": True}, "Deep interview"),           # the Basic message has no stages
    ({"bootstrap_new_vault": True, "brief": "x", "autoresearch": True}, "deep_list"),
    ({"bootstrap_new_vault": True, "brief": "x", "deep_list": "yes"}, "deep_list"),
    ({"bootstrap_new_vault": True, "interview": "full", "deep_list": True, "autoresearch": True,
      "autoresearch_top": 0}, "autoresearch_top"),
    ({"bootstrap_new_vault": True, "interview": "full", "deep_list": True, "autoresearch": True,
      "autoresearch_top": 51}, "1 to 50"),
    ({"bootstrap_new_vault": True, "brief": "x", "initial_command": "/x"}, "mutually exclusive"),
])
def test_session_request_rejections(tmp_path, body, message):
    with pytest.raises(SessionPlanError, match=message) as exc:
        build_session_plan(_ctx(tmp_path), {"vault": "alpha", "type": "claude", **body})
    assert exc.value.status == 400


def test_the_form_spawns_through_one_helper_the_shared_terminal_replaces():
    """Under webterm there is no ttyd, so the wizard must not post the legacy
    /api/sessions itself: it calls spawnBootstrapSession(), which
    webterm-glue.js replaces with the shared terminal's create call (the same
    payload reaches build_session_plan through the spawn resolver)."""
    js = (REPO / "control-plane" / "static" / "js")
    app = (js / "app.js").read_text()
    wizard = app[app.index("function showNewVaultWizard()"):app.index("function setWizardStatus(")]
    assert 'api("/api/sessions"' not in wizard
    assert wizard.count("await spawnBootstrapSession({") == 2          # Basic and Deep
    assert "bootstrap_new_vault: true, brief, interview" in wizard
    assert "async function spawnBootstrapSession(payload)" in app
    glue = (js / "webterm-glue.js").read_text()
    assert "window.spawnBootstrapSession = async (payload)" in glue
    assert "'/api/sessions', request" in glue and "theme, ...request" in glue


def test_the_form_cap_and_markers_match_the_server():
    """The form refuses what the server would refuse, before anything is
    scaffolded: same cap, same marker lines, same character rule."""
    js = (REPO / "control-plane" / "static" / "js" / "app.js").read_text()
    m = re.search(r"const NEW_VAULT_BRIEF_MAX = (\d+);", js)
    assert m and int(m.group(1)) == new_vault.MAX_BRIEF_CHARS
    m = re.search(r'const NEW_VAULT_BRIEF_MARKERS = \["([^"]+)", "([^"]+)"\];', js)
    assert m and list(m.groups()) == [BRIEF_BEGIN, BRIEF_END]
    assert r"[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]" in js
    m = re.search(r"const NEW_VAULT_RESEARCH_TOP_MAX = (\d+);", js)
    assert m and int(m.group(1)) == new_vault.AUTORESEARCH_TOP_MAX
    wizard = js[js.index("function showNewVaultWizard()"):js.index("function setWizardStatus(")]
    assert "bootstrap_new_vault: true, brief, interview, ...stages" in wizard
