// resman SPA — Skills tab: a read-only view of the claude-obsidian plugin
// resman drives (installed per user, not shipped here). Tree on the left:
// Overview, New vault process, then the plugin's skills (the ones resman
// sends first), commands and docs; the page on the right. The activity-bar
// badge counts warnings only — commands resman sends that the installed
// plugin lacks, or the plugin missing altogether.
// Globals from app.js: state, $, esc, api, renderWikiMarkdown.
"use strict";

state.skills = null;             // /api/skills/summary payload
state.skillsPage = "overview";   // "overview" | "new-vault" | "missing:<name>" | a plugin-relative .md path

function updateSkillsBadge() {
  const badge = $("#skills-badge");
  if (!badge) return;
  const n = (state.skills && state.skills.warnings || []).length;
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
      `<div class="wiki-error">Could not read the plugin summary.</div>`;
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

function skillsDir(label, leaves) {
  if (!leaves.length) return "";
  return `<li class="help-dir"><span class="help-label">${esc(label)}</span>
    <ul>${leaves.join("")}</ul></li>`;
}

function renderSkillsTree() {
  const root = $("#skills-tree-list");
  if (!root) return;
  const s = state.skills;
  const warn = s && s.warnings && s.warnings.length > 0;
  const items = [
    skillsLeaf("overview", "Overview", { warn }),
    skillsLeaf("new-vault", "New vault process"),
  ];
  if (s && s.plugin.installed) {
    const used = s.skills.filter((k) => k.used);
    const other = s.skills.filter((k) => !k.used);
    const missing = s.uses.filter((u) => !u.provided);
    items.push(skillsDir("Used by resman", [
      ...used.map((k) => skillsLeaf(k.path, k.name, { title: k.description })),
      ...missing.map((u) => skillsLeaf("missing:" + u.name, u.name,
        { title: "missing from the installed plugin", warn: true })),
    ]));
    items.push(skillsDir("Other skills",
      other.map((k) => skillsLeaf(k.path, k.name, { title: k.description }))));
    items.push(skillsDir("Commands",
      s.commands.map((c) => skillsLeaf(c.path, "/" + c.name, { title: c.description }))));
    items.push(skillsDir("Plugin docs",
      s.docs.map((d) => skillsLeaf(d, d.replace(/\.md$/, "")))));
  }
  root.innerHTML = `<ul>${items.join("")}</ul>`;
  root.querySelectorAll(".help-file > .help-label").forEach((el) => {
    el.addEventListener("click", () => openSkillsPage(el.dataset.page));
  });
}

function openSkillsPage(target) {
  const page = target || "overview";
  state.skillsPage = page;
  renderSkillsTree();
  // A skill resman sends but the plugin lacks has no file: show the overview,
  // whose warning names it.
  if (page === "overview" || page.startsWith("missing:")) renderSkillsOverview();
  else if (page === "new-vault") loadSkillsNewVault();
  else loadSkillsFile(page);
}

function skillsRow(label, valueHtml) {
  return valueHtml ? `<tr><td>${esc(label)}</td><td>${valueHtml}</td></tr>` : "";
}

function skillsWarningsHtml(warnings) {
  if (!warnings.length) return "";
  return `<div class="skills-warnings">${warnings.map((w) =>
    `<p><span class="codicon codicon-warning"></span> ${esc(w)}</p>`).join("")}</div>`;
}

function renderSkillsOverview() {
  const box = $("#skills-content");
  const s = state.skills;
  const p = s.plugin;
  if (!p.installed) {
    box.innerHTML = `<h1>${esc(p.name)}</h1>
      <p><span class="pill warn">not found</span> <code>${esc(p.id)}</code></p>
      ${skillsWarningsHtml(s.warnings)}
      <pre>claude plugin marketplace add AgriciDaniel/claude-obsidian
claude plugin install ${esc(p.id)}</pre>
      <p class="muted">Then reload this tab. Setup notes: <code>docs/obsidian-plugin.md</code> in the resman repo.</p>`;
    return;
  }
  const date = (iso) => (iso ? esc(iso.replace("T", " ").replace(/\.\d+Z$|Z$/, " UTC")) : "");
  const home = p.homepage
    ? `<a href="${esc(p.homepage)}" target="_blank" rel="noopener">${esc(p.homepage)}</a>` : "";
  const companion = s.companion.installed
    ? `<span class="pill ok">installed</span> ${esc(s.companion.version || "")}`
    : `<span class="pill">not installed</span> <span class="muted">optional canvas companion</span>`;
  const uses = s.uses.map((u) => `<tr>
      <td><code>${esc(p.name)}:${esc(u.name)}</code></td>
      <td>${esc(u.where)}</td>
      <td>${u.provided
        ? `<span class="pill ok">${esc(u.kind)}</span>`
        : `<span class="pill warn">missing</span>`}</td>
    </tr>`).join("");
  box.innerHTML = `
    <h1>${esc(p.name)} <span class="pill ok">${esc(p.version)}</span></h1>
    <p>${esc(p.description)}</p>
    ${skillsWarningsHtml(s.warnings)}
    <h2>Installed plugin</h2>
    <table class="data-table skills-table"><tbody>
      ${skillsRow("Version", esc(p.version))}
      ${skillsRow("Scope", esc(p.scope))}
      ${skillsRow("Installed", date(p.installed_at))}
      ${skillsRow("Last updated", date(p.last_updated))}
      ${skillsRow("Commit", p.commit ? `<code>${esc(p.commit.slice(0, 12))}</code>` : "")}
      ${skillsRow("Folder", `<code>${esc(p.path)}</code>`)}
      ${skillsRow("Claude dir", `<code>${esc(p.claude_dir)}</code>`)}
      ${skillsRow("Found through", esc(p.located_by === "registry"
        ? "Claude Code's plugin registry" : "the plugin cache (newest version folder)"))}
      ${skillsRow("Homepage", home)}
      ${skillsRow("claude-canvas", companion)}
      ${skillsRow("Contents", esc(`${s.skills.length} skills, ${s.commands.length} commands`))}
    </tbody></table>
    <h2>What resman sends to it</h2>
    <table class="data-table skills-table">
      <thead><tr><th>Skill / command</th><th>Sent by</th><th>In the plugin</th></tr></thead>
      <tbody>${uses}</tbody>
    </table>
    ${skillsUpdateHtml(p)}`;
}

// How to update the plugin, with the id and marketplace this machine really
// registered it under (the marketplace name differs between machines).
function skillsUpdateHtml(p) {
  const market = (p.id || "").split("@")[1] || "";
  const scope = p.scope && p.scope !== "user" ? ` --scope ${esc(p.scope)}` : "";
  return `
    <h2>Update the plugin</h2>
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

async function loadSkillsFile(rel) {
  const box = $("#skills-content");
  box.innerHTML = `<p class="muted">Loading…</p>`;
  let data;
  try {
    data = await api("/api/skills/file?path=" + encodeURIComponent(rel));
  } catch (err) {
    box.innerHTML = `<div class="wiki-empty"><p>${esc(err.message)}</p></div>`;
    return;
  }
  const skill = (state.skills.skills || []).find((k) => k.files.includes(rel));
  const files = skill && skill.files.length > 1
    ? `<p class="skills-files">${skill.files.map((f) =>
        `<a href="#" data-page="${esc(f)}" class="${f === rel ? "active" : ""}">${
          esc(f.slice(skill.path.lastIndexOf("/") + 1))}</a>`).join(" · ")}</p>`
    : "";
  box.innerHTML = `<p class="muted small"><code>${esc(rel)}</code></p>${files}`
    + renderWikiMarkdown(data.content || "", { wikilinks: false });
  box.querySelectorAll(".skills-files a[data-page]").forEach((a) => {
    a.addEventListener("click", (ev) => { ev.preventDefault(); openSkillsPage(a.dataset.page); });
  });
  // Relative links to other .md files stay inside the plugin view.
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
      openSkillsPage(parts.join("/"));
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const refresh = $("#btn-skills-refresh");
  if (refresh) refresh.addEventListener("click", loadSkillsTab);
  refreshSkillsSummary();
});
