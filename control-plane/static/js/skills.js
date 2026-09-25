// resman SPA — Skills tab: the two skill providers resman runs, side by side
// (docs/design/17-skills.md). Tree on the left: Overview, New vault process,
// then one node per provider — the claude-obsidian plugin (installed per
// user) and resman's own skills/ folder (loaded per run) — each with
// the same sub-structure, then the Custom skill guide; the page on the right.
// A resman skill that ships settings.yaml gets a Settings card under its
// SKILL.md: the values live under skills.<name> in resman.yaml. The
// activity-bar badge counts warnings across both providers.
// Globals from app.js: state, $, esc, api, renderWikiMarkdown.
"use strict";

state.skills = null;             // /api/skills/summary payload: {providers, warnings}
// "overview" | "new-vault" | "guide" | "<provider>:<relative .md path>"
// | "missing:<provider>:<name>"
state.skillsPage = "overview";

function skillsProvider(id) {
  return ((state.skills && state.skills.providers) || []).find((p) => p.id === id) || null;
}

function updateSkillsBadge() {
  const badge = $("#skills-badge");
  if (!badge) return;
  const n = ((state.skills && state.skills.warnings) || []).length;
  badge.textContent = n > 0 ? String(n) : "";
  badge.hidden = n === 0;
}

async function refreshSkillsSummary() {
  try {
    state.skills = await api("/api/skills/summary");
  } catch (_) {
    state.skills = null;
  }
  updateSkillsBadge();
}

async function loadSkillsTab() {
  await refreshSkillsSummary();
  renderSkillsTree();
  if (!state.skills) {
    $("#skills-content").innerHTML =
      `<div class="wiki-error">Could not read the skills summary.</div>`;
    return;
  }
  openSkillsPage(state.skillsPage);
}

function skillsLeaf(page, label, { title = "", warn = false } = {}) {
  const active = page === state.skillsPage ? "active" : "";
  const mark = warn ? ` <span class="codicon codicon-warning skills-warn-mark"></span>` : "";
  return `<li class="help-file ${active}">
    <span class="help-label" data-page="${esc(page)}" title="${esc(title)}">${esc(label)}${mark}</span>
  </li>`;
}

function skillsDir(label, leaves, { warn = false, title = "" } = {}) {
  if (!leaves.length) return "";
  const mark = warn ? ` <span class="codicon codicon-warning skills-warn-mark"></span>` : "";
  return `<li class="help-dir"><span class="help-label" title="${esc(title)}">${esc(label)}${mark}</span>
    <ul>${leaves.join("")}</ul></li>`;
}

// The sub-tree of one provider: what resman sends (plus what it sends but
// the provider lacks), the rest of its skills, its commands, its docs.
function providerLeaves(p) {
  const used = p.skills.filter((k) => k.used);
  const other = p.skills.filter((k) => !k.used);
  const missing = p.uses.filter((u) => !u.provided);
  const file = (k) => skillsLeaf(`${p.id}:${k.path}`, k.name,
    { title: k.description || k.path });
  const wired = p.id === "resman" ? "Wired to an operation" : "Used by resman";
  const rest = p.id === "resman" ? "Not wired yet" : "Other skills";
  return [
    skillsDir(wired, [
      ...used.map(file),
      ...missing.map((u) => skillsLeaf(`missing:${p.id}:${u.name}`, u.name,
        { title: `missing from ${p.label}`, warn: true })),
    ]),
    skillsDir(rest, other.map(file)),
    skillsDir("Commands", p.commands.map((c) => skillsLeaf(`${p.id}:${c.path}`, "/" + c.name,
      { title: c.description || c.path }))),
    skillsDir(p.id === "resman" ? "Docs" : "Plugin docs",
      p.docs.map((d) => skillsLeaf(`${p.id}:${d}`, d.replace(/\.md$/, "")))),
  ].filter(Boolean);
}

function providerNodeLabel(p) {
  if (!p.plugin.installed) return `${p.label} — not found`;
  return p.plugin.version ? `${p.label} ${p.plugin.version}` : p.label;
}

function renderSkillsTree() {
  const root = $("#skills-tree-list");
  if (!root) return;
  const s = state.skills;
  const warn = !!(s && s.warnings && s.warnings.length);
  const items = [
    skillsLeaf("overview", "Overview", { warn }),
    skillsLeaf("new-vault", "New vault process"),
  ];
  for (const p of (s && s.providers) || []) {
    const leaves = p.plugin.installed ? providerLeaves(p) : [];
    const node = skillsDir(providerNodeLabel(p), leaves,
      { warn: p.warnings.length > 0, title: p.plugin.path || "" });
    items.push(node || skillsLeaf("overview", providerNodeLabel(p),
      { warn: p.warnings.length > 0, title: "see the overview" }));
  }
  items.push(skillsLeaf("guide", "Custom skill guide",
    { title: "skills/README.md — how to write a resman skill" }));
  root.innerHTML = `<ul>${items.join("")}</ul>`;
  root.querySelectorAll(".help-file > .help-label").forEach((el) => {
    el.addEventListener("click", () => openSkillsPage(el.dataset.page));
  });
}

function openSkillsPage(target) {
  const page = target || "overview";
  state.skillsPage = page;
  renderSkillsTree();
  // A skill resman sends but the provider lacks has no file: show the
  // overview, whose warning names it.
  if (page === "overview" || page.startsWith("missing:")) renderSkillsOverview();
  else if (page === "new-vault") loadSkillsNewVault();
  else if (page === "guide") loadSkillsFile("resman", "README.md");
  else {
    const at = page.indexOf(":");
    if (at > 0) loadSkillsFile(page.slice(0, at), page.slice(at + 1));
    else renderSkillsOverview();
  }
}

function skillsRow(label, valueHtml) {
  return valueHtml ? `<tr><td>${esc(label)}</td><td>${valueHtml}</td></tr>` : "";
}

function skillsWarningsHtml(warnings) {
  if (!warnings.length) return "";
  return `<div class="skills-warnings">${warnings.map((w) =>
    `<p><span class="codicon codicon-warning"></span> ${esc(w)}</p>`).join("")}</div>`;
}

const skillsDate = (iso) => (iso ? esc(iso.replace("T", " ").replace(/\.\d+Z$|Z$/, " UTC")) : "");

function obsidianBlockHtml(p) {
  const pl = p.plugin;
  if (!pl.installed) {
    return `<h2>${esc(p.label)} <span class="pill warn">not found</span></h2>
      <p><code>${esc(pl.id)}</code> — the plugin resman drives every vault with.</p>
      ${skillsWarningsHtml(p.warnings)}
      <pre>claude plugin marketplace add AgriciDaniel/claude-obsidian
claude plugin install ${esc(pl.id)}</pre>
      <p class="muted">Then reload this tab. Setup notes: <code>docs/obsidian-plugin.md</code> in the resman repo.</p>`;
  }
  const home = pl.homepage
    ? `<a href="${esc(pl.homepage)}" target="_blank" rel="noopener">${esc(pl.homepage)}</a>` : "";
  const companion = p.companion && p.companion.installed
    ? `<span class="pill ok">installed</span> ${esc(p.companion.version || "")}`
    : `<span class="pill">not installed</span> <span class="muted">optional canvas companion</span>`;
  return `<h2>${esc(p.label)} <span class="pill ok">${esc(pl.version)}</span></h2>
    <p>${esc(pl.description)}</p>
    ${skillsWarningsHtml(p.warnings)}
    <table class="data-table skills-table"><tbody>
      ${skillsRow("Version", esc(pl.version))}
      ${skillsRow("Scope", esc(pl.scope))}
      ${skillsRow("Installed", skillsDate(pl.installed_at))}
      ${skillsRow("Last updated", skillsDate(pl.last_updated))}
      ${skillsRow("Commit", pl.commit ? `<code>${esc(pl.commit.slice(0, 12))}</code>` : "")}
      ${skillsRow("Folder", `<code>${esc(pl.path)}</code>`)}
      ${skillsRow("Claude dir", `<code>${esc(pl.claude_dir)}</code>`)}
      ${skillsRow("Found through", esc(pl.located_by === "registry"
        ? "Claude Code's plugin registry" : "the plugin cache (newest version folder)"))}
      ${skillsRow("Homepage", home)}
      ${skillsRow("claude-canvas", companion)}
      ${skillsRow("Contents", esc(`${p.skills.length} skills, ${p.commands.length} commands`))}
    </tbody></table>`;
}

function resmanBlockHtml(p) {
  const pl = p.plugin;
  if (!pl.installed) {
    return `<h2>${esc(p.label)} <span class="pill warn">not found</span></h2>
      ${skillsWarningsHtml(p.warnings)}`;
  }
  const withSettings = p.skills.filter((k) => k.has_settings).length;
  return `<h2>${esc(p.label)} <span class="pill ok">${esc(pl.version || "repo")}</span></h2>
    <p>${esc(pl.description || "resman's own skills, versioned with the repo.")}</p>
    ${skillsWarningsHtml(p.warnings)}
    <table class="data-table skills-table"><tbody>
      ${skillsRow("Version", esc(pl.version))}
      ${skillsRow("Folder", `<code>${esc(pl.path)}</code>`)}
      ${skillsRow("Loaded", esc("with --plugin-dir on every Claude run resman spawns (tasks, attend, Ops tab) — never installed"))}
      ${skillsRow("Invoke as", `<code>/resman:&lt;skill&gt;</code>`)}
      ${skillsRow("Contents", esc(`${p.skills.length} skills, ${p.commands.length} commands`
        + (withSettings ? `, ${withSettings} with settings` : "")))}
      ${skillsRow("Update", esc("git pull; a change takes effect on the next task run"))}
    </tbody></table>`;
}

function usesTableHtml(providers) {
  const rows = [];
  for (const p of providers) {
    const prefix = p.id === "resman" ? "resman" : "claude-obsidian";
    for (const u of p.uses) {
      rows.push(`<tr>
        <td><span class="pill provider-pill provider-${esc(p.id)}">${esc(tasksCore.providerShort(p.id))}</span></td>
        <td><code>${esc(prefix)}:${esc(u.name)}</code></td>
        <td>${esc(u.where)}</td>
        <td>${u.provided
          ? `<span class="pill ok">${esc(u.kind)}</span>`
          : `<span class="pill warn">missing</span>`}</td>
      </tr>`);
    }
    if (p.id === "resman" && !p.uses.length) {
      rows.push(`<tr><td><span class="pill provider-pill provider-resman">resman</span></td>
        <td colspan="3" class="muted">no resman operation yet — see the Custom skill guide</td></tr>`);
    }
  }
  return `<table class="data-table skills-table">
      <thead><tr><th>Source</th><th>Skill / command</th><th>Sent by</th><th>Provided</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>`;
}

function renderSkillsOverview() {
  const box = $("#skills-content");
  const s = state.skills;
  const providers = s.providers || [];
  const obsidian = providers.find((p) => p.id === "obsidian");
  const resman = providers.find((p) => p.id === "resman");
  box.innerHTML = `
    <h1>Skills</h1>
    <p>resman never edits a vault itself: every operation is a Claude Code skill that
      runs inside the vault and writes markdown pages under <code>wiki/</code>. The
      skills come from two sources; the Tasks view shows each task's source.</p>
    ${obsidian ? obsidianBlockHtml(obsidian) : ""}
    ${resman ? resmanBlockHtml(resman) : ""}
    <h2>What resman sends</h2>
    ${usesTableHtml(providers)}
    ${obsidian && obsidian.plugin.installed ? skillsUpdateHtml(obsidian.plugin) : ""}`;
}

// How to update the plugin, with the id and marketplace this machine really
// registered it under (the marketplace name differs between machines).
function skillsUpdateHtml(p) {
  const market = (p.id || "").split("@")[1] || "";
  const scope = p.scope && p.scope !== "user" ? ` --scope ${esc(p.scope)}` : "";
  return `
    <h2>Update the claude-obsidian plugin</h2>
    <p>Run these in a terminal on this machine, as the user resman runs as:</p>
    <pre># 1. refresh the marketplace's plugin list
claude plugin marketplace update ${esc(market)}
# 2. update the plugin to the newest version in it
claude plugin update ${esc(p.id)}${scope}
# 3. check the version now installed
claude plugin list | grep ${esc(p.name)}</pre>
    <ul>
      <li>Step 2 alone checks only the cached marketplace list; without step 1 it
        can report "already at the latest version" when a newer one is out.</li>
      <li>Claude Code sessions load plugins at start: restart open sessions (and
        running vault tasks pick it up on their next run).</li>
      <li>resman pins no version: reload this tab (<span class="codicon codicon-refresh"></span>)
        to see the new version, its skills and any new warnings.</li>
      <li>If the <code>marketplace add</code> step is refused ("source differs from the one
        declared … in settings"), the marketplace is already declared in
        <code>~/.claude/settings.json</code>; use its name as shown above instead.</li>
    </ul>`;
}

async function loadSkillsNewVault() {
  const box = $("#skills-content");
  box.innerHTML = `<p class="muted">Loading…</p>`;
  let data;
  try {
    data = await api("/api/skills/new-vault");
  } catch (err) {
    box.innerHTML = `<div class="wiki-error">${esc(err.message)}</div>`;
    return;
  }
  const folder = data.plugin_dir
    ? `<code>${esc(data.plugin_dir)}</code>`
    : `<span class="pill warn">plugin not found</span> — the message tells Claude where to look`;
  box.innerHTML = renderWikiMarkdown(data.doc || "", { wikilinks: false }) + `
    <h2>The message resman pastes now</h2>
    <p class="muted">Built from <code>${esc(data.prefix_file)}</code>, the bootstrap
      command and <code>${esc(data.suffix_file)}</code>; plugin folder: ${folder}.</p>
    <pre class="skills-prompt">${esc(data.prompt || "")}</pre>`;
}

async function loadSkillsFile(provider, rel) {
  const box = $("#skills-content");
  box.innerHTML = `<p class="muted">Loading…</p>`;
  let data;
  try {
    data = await api("/api/skills/file?provider=" + encodeURIComponent(provider)
      + "&path=" + encodeURIComponent(rel));
  } catch (err) {
    box.innerHTML = `<div class="wiki-empty"><p>${esc(err.message)}</p></div>`;
    return;
  }
  const p = skillsProvider(provider);
  const skill = p && (p.skills || []).find((k) => k.files.includes(rel));
  const files = skill && skill.files.length > 1
    ? `<p class="skills-files">${skill.files.map((f) =>
        `<a href="#" data-page="${esc(provider)}:${esc(f)}" class="${f === rel ? "active" : ""}">${
          esc(f.slice(skill.path.lastIndexOf("/") + 1))}</a>`).join(" · ")}</p>`
    : "";
  const source = p ? `<span class="pill provider-pill provider-${esc(p.id)}">${esc(tasksCore.providerShort(p.id))}</span> ` : "";
  const invoke = skill && skill.invoke ? ` · <code>${esc(skill.invoke)}</code>` : "";
  const settingsSlot = provider === "resman" && skill && skill.has_settings && rel === skill.path
    ? `<div id="skills-settings" class="skills-settings"></div>` : "";
  box.innerHTML = `<p class="muted small">${source}<code>${esc(rel)}</code>${invoke}</p>${files}`
    + renderWikiMarkdown(data.content || "", { wikilinks: false }) + settingsSlot;
  box.querySelectorAll(".skills-files a[data-page]").forEach((a) => {
    a.addEventListener("click", (ev) => { ev.preventDefault(); openSkillsPage(a.dataset.page); });
  });
  // Relative links to other .md files stay inside the provider's view.
  box.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (a.dataset.page || /^[a-z]+:/i.test(href) || !/\.md(?:#|$)/i.test(href)) return;
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      const [path] = href.split("#");
      const parts = rel.split("/").slice(0, -1);
      for (const seg of path.split("/")) {
        if (!seg || seg === ".") continue;
        if (seg === "..") parts.pop(); else parts.push(seg);
      }
      openSkillsPage(`${provider}:${parts.join("/")}`);
    });
  });
  if (settingsSlot) loadSkillSettings(skill.name);
}

// ----- a resman skill's settings (settings.yaml → skills.<name> in resman.yaml) -----

async function loadSkillSettings(name) {
  const box = $("#skills-settings");
  if (!box) return;
  box.innerHTML = `<p class="muted">Loading settings…</p>`;
  let data;
  try {
    data = await api("/api/skills/settings?skill=" + encodeURIComponent(name));
  } catch (err) {
    box.innerHTML = `<h2>Settings</h2><div class="wiki-error">${esc(err.body?.error || err.message)}</div>`;
    return;
  }
  renderSkillSettings(box, data);
}

function settingFieldHtml(s, value) {
  const id = "sk-" + s.key;
  const help = s.help ? `<span class="form-help">${esc(s.help)}</span>` : "";
  let control;
  if (s.type === "int") {
    control = `<input id="${esc(id)}" type="number" step="1" data-key="${esc(s.key)}" data-type="int"
      ${s.min != null ? `min="${s.min}"` : ""} ${s.max != null ? `max="${s.max}"` : ""} value="${esc(String(value))}">`;
  } else if (s.type === "bool") {
    control = `<label class="skills-check"><input id="${esc(id)}" type="checkbox" data-key="${esc(s.key)}" data-type="bool" ${value ? "checked" : ""}> ${esc(s.key)}</label>`;
    return `<div class="param-row param-row-checkbox">${control}${help}</div>`;
  } else if (s.type === "enum") {
    control = `<select id="${esc(id)}" data-key="${esc(s.key)}" data-type="enum">${(s.choices || []).map((c) =>
      `<option value="${esc(c)}" ${c === value ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
  } else if (s.type === "list") {
    control = `<textarea id="${esc(id)}" data-key="${esc(s.key)}" data-type="list" rows="3"
      placeholder="one per line">${esc((value || []).join("\n"))}</textarea>`;
  } else {
    control = `<input id="${esc(id)}" type="text" data-key="${esc(s.key)}" data-type="text"
      ${s.max_len ? `maxlength="${s.max_len}"` : ""} value="${esc(String(value ?? ""))}">`;
  }
  return `<div class="param-row"><label for="${esc(id)}">${esc(s.key)}</label>${control}${help}</div>`;
}

// The form's values, keyed by setting; only those that differ from the
// defaults are stored (the yaml holds overrides, not a copy of the schema).
function collectSkillSettings(schema, defaults) {
  const values = {};
  for (const s of schema) {
    const el = $(`#sk-${CSS.escape(s.key)}`);
    if (!el) continue;
    let v;
    if (s.type === "int") {
      v = parseInt(el.value, 10);
      if (!Number.isFinite(v)) throw new Error(`${s.key}: enter a whole number`);
    } else if (s.type === "bool") v = !!el.checked;
    else if (s.type === "list") v = el.value.split("\n").map((x) => x.trim()).filter(Boolean);
    else v = el.value;
    if (JSON.stringify(v) !== JSON.stringify(defaults[s.key])) values[s.key] = v;
  }
  return values;
}

function renderSkillSettings(box, data) {
  const stored = Object.keys(data.values || {}).length;
  const fields = data.schema.map((s) => settingFieldHtml(s, data.effective[s.key])).join("");
  box.innerHTML = `
    <h2>Settings</h2>
    <p class="muted small">Stored under <code>skills.${esc(data.skill)}</code> in
      <code>${esc(data.resman_display_path || "resman.yaml")}</code>${stored ? "" : " (nothing stored: defaults apply)"}.
      The next run gets: <code class="skills-args">${esc(data.args || "")}</code></p>
    <form class="op-params skills-settings-form" id="skills-settings-form">${fields}</form>
    <div class="skills-settings-actions">
      <span class="trigger-error" id="skills-settings-error"></span>
      <span class="muted small" id="skills-settings-status"></span>
      <div class="spacer"></div>
      <button type="button" class="btn secondary btn-sm" id="btn-skill-reset" ${stored ? "" : "disabled"}
        title="Forget the stored values; the skill runs with its defaults">Reset to defaults</button>
      <button type="button" class="btn btn-sm" id="btn-skill-save" disabled>Save</button>
    </div>`;
  const form = $("#skills-settings-form");
  const save = $("#btn-skill-save");
  const reset = $("#btn-skill-reset");
  const err = $("#skills-settings-error");
  form.addEventListener("input", () => { save.disabled = false; err.textContent = ""; });
  form.addEventListener("change", () => { save.disabled = false; err.textContent = ""; });
  form.addEventListener("submit", (e) => e.preventDefault());
  const post = async (values) => {
    err.textContent = "";
    try {
      const out = await api("/api/skills/settings", {
        method: "POST", body: JSON.stringify({ skill: data.skill, values }),
      });
      renderSkillSettings(box, out);
      $("#skills-settings-status").textContent = "Saved.";
    } catch (e) {
      err.textContent = "Save failed: " + (e.body?.error || e.message);
    }
  };
  save.addEventListener("click", () => {
    let values;
    try { values = collectSkillSettings(data.schema, data.defaults); }
    catch (e) { err.textContent = e.message; return; }
    post(values);
  });
  reset.addEventListener("click", () => {
    if (!confirm(`Forget the stored settings of ${data.skill}? The skill will run with its defaults.`)) return;
    post({});
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const refresh = $("#btn-skills-refresh");
  if (refresh) refresh.addEventListener("click", loadSkillsTab);
  refreshSkillsSummary();
});
