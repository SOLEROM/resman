// wiki-marks.js — reader highlights on a rendered wiki page.
//
// Select text → a small palette appears → one click writes
// `<mark class="hl-COLOR">…</mark>` into the page's markdown (the server
// builds the tag; this file only says where and which color). Click a
// highlight to recolor or remove it. Ctrl+click or Alt+click highlights the
// whole sentence under the pointer. Pen mode highlights every selection with
// the last color, no palette. With the palette open: 1–6 pick a color, 0 or
// Delete removes, Esc closes.
//
// Every edit is a two-step handshake with the app's highlight endpoint: a dry
// run returns the would-be markdown, it is rendered off-screen with the app's
// own renderer, and only if the page text is unchanged and the mark sits on
// the selected words is the real write sent. Anything else is refused with a
// short message — a selection can fail, a page cannot be damaged.
//
// App-agnostic (no app globals): the app calls
//
//   wikiMarks.attach(container, {
//     path, source, sha,            // the page as served (sha = concurrency token)
//     profile,                      // the app's renderer, for wiki-marks-core.js
//     renderHtml(markdown) → html,  // what the container shows for `markdown`:
//                                   //   sanitized, no side effects
//     save(body) → Promise<page>,   // POST to the highlight endpoint; rejects with .status
//     onSaved(page),                // re-render + re-attach with the fresh page
//     reload(),                     // optional: refetch after a 409
//   })
//   wikiMarks.detach(container)     // the container now shows something else
//   wikiMarks.cleanMarks(root)      // renderer hook: only hl-<color> survives on a <mark>
//
// Needs wiki-marks-core.js (selection → source offsets) and wiki-marks.css.
(function (global) {
  "use strict";

  const core = global.wikiMarksCore;
  const COLORS = ["yellow", "green", "blue", "pink", "orange", "purple"];
  const MARK_SEL = COLORS.map((c) => `mark.hl-${c}`).join(",");
  // Not part of the page text: drawn by the renderer from elsewhere, or not text.
  // `.hl-skip` is for whatever else an app decorates a rendered page with.
  const SKIP_SEL = ".wiki-frontmatter, .callout-title, .mermaid-box, code.language-mermaid, .hl-skip, script, style";
  const NO_MARK_SEL = "pre";            // code blocks: readable, never highlightable
  const BLOCK_SEL = "p, li, td, th, h1, h2, h3, h4, h5, h6, dt, dd, blockquote, figcaption";
  const PEN_KEY = "wikiMarks.pen";
  const LAST_KEY = "wikiMarks.last";
  const SELECTION_SETTLE_MS = 400;
  const SAME_SELECTION_MS = 2000;
  const FLASH_MS = 4500;
  const SHOW_TEXT = 4;                  // NodeFilter.SHOW_TEXT

  const containers = new Set();
  const ctls = new WeakMap();
  let pop = null, chip = null, flashBox = null, flashTimer = 0;
  let open = null;                      // {ctl, range, mark} while the palette shows
  let mouseDown = false;
  let settleTimer = 0;
  let handled = { text: "", at: 0 };       // the selection the last gesture acted on

  // ----- preferences (per browser; the page works without them) -----
  function pref(key) { try { return global.localStorage.getItem(key) || ""; } catch (_) { return ""; } }
  function setPref(key, value) { try { global.localStorage.setItem(key, value); } catch (_) { /* private mode */ } }
  function penColor() { const c = pref(PEN_KEY); return COLORS.includes(c) ? c : ""; }
  function lastColor() { const c = pref(LAST_KEY); return COLORS.includes(c) ? c : COLORS[0]; }

  // ----- the rendered page as text -----
  function cleanMarks(root) {
    root.querySelectorAll("mark").forEach((m) => {
      const ours = Array.from(m.classList).find((c) => COLORS.includes(c.slice(3)) && c.startsWith("hl-"));
      if (ours) m.className = ours; else m.removeAttribute("class");
    });
  }

  // Every visible non-space character in document order, with its text node.
  function stream(root) {
    const walker = root.ownerDocument.createTreeWalker(root, SHOW_TEXT);
    const nodes = [], offsets = [];
    let chars = "";
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.parentElement || n.parentElement.closest(SKIP_SEL)) continue;
      const text = n.nodeValue;
      for (let i = 0; i < text.length; i++) {
        if (/\s/.test(text[i])) continue;
        chars += text[i]; nodes.push(n); offsets.push(i);
      }
    }
    return { chars, nodes, offsets };
  }

  // The page text with, per char, the color of the highlight it sits in ("").
  function coverage(root) {
    const st = stream(root);
    return { chars: st.chars, colors: st.nodes.map((n) => {
      const mark = n.parentElement.closest(MARK_SEL);
      return mark ? mark.className.slice(3) : "";
    }) };
  }

  function marksOf(root) {
    return Array.from(root.querySelectorAll(MARK_SEL)).map((m) => ({
      color: m.className.slice(3), text: core.squashText(m.textContent) }));
  }

  function blockOf(root, el) {
    const block = el.closest(BLOCK_SEL);
    return block && root.contains(block) ? block : root;
  }

  // The selection as quotes, one per block it touches (inline HTML cannot
  // cross blocks, so neither can a highlight). Code blocks are left out.
  function quotesFromRange(root, range) {
    const st = stream(root);
    const touched = new Map();
    const groups = [];
    let group = null;
    for (let k = 0; k < st.chars.length; k++) {
      const node = st.nodes[k];
      if (!touched.has(node)) touched.set(node, range.intersectsNode(node));
      if (!touched.get(node)) continue;
      if (range.comparePoint(node, st.offsets[k]) !== 0 || range.comparePoint(node, st.offsets[k] + 1) !== 0) continue;
      if (node.parentElement.closest(NO_MARK_SEL)) { group = null; continue; }
      const block = blockOf(root, node.parentElement);
      if (group && group.block === block && group.to === k) group.to = k + 1;
      else { group = { block, from: k, to: k + 1 }; groups.push(group); }
    }
    return groups.map((g) => {
      const exact = st.chars.slice(g.from, g.to);
      let count = 0, ordinal = 0;
      for (let at = st.chars.indexOf(exact); at !== -1; at = st.chars.indexOf(exact, at + 1)) {
        if (at < g.from) ordinal += 1;
        count += 1;
      }
      return { exact, ordinal, count, from: g.from, to: g.to, page: st.chars,
               prefix: st.chars.slice(Math.max(0, g.from - core.CONTEXT_CHARS), g.from),
               suffix: st.chars.slice(g.to, g.to + core.CONTEXT_CHARS) };
    });
  }

  function caretAt(doc, x, y) {
    if (doc.caretPositionFromPoint) {
      const p = doc.caretPositionFromPoint(x, y);
      return p ? { node: p.offsetNode, offset: p.offset } : null;
    }
    const r = doc.caretRangeFromPoint ? doc.caretRangeFromPoint(x, y) : null;
    return r ? { node: r.startContainer, offset: r.startOffset } : null;
  }

  function sentences(text) {
    if (global.Intl && global.Intl.Segmenter) {
      return Array.from(new global.Intl.Segmenter(undefined, { granularity: "sentence" }).segment(text),
                        (s) => ({ index: s.index, length: s.segment.length }));
    }
    const out = [];
    let index = 0;
    for (const piece of text.split(/(?<=[.!?])\s+/)) {
      const at = text.indexOf(piece, index);
      out.push({ index: at, length: piece.length });
      index = at + piece.length;
    }
    return out;
  }

  // The sentence under a point, as a Range within its block — or null.
  function sentenceRange(root, x, y) {
    const doc = root.ownerDocument;
    const caret = caretAt(doc, x, y);
    if (!caret || caret.node.nodeType !== 3 || !root.contains(caret.node)) return null;
    const el = caret.node.parentElement;
    if (!el || el.closest(`${SKIP_SEL}, ${NO_MARK_SEL}, a`)) return null;
    const block = blockOf(root, el);
    const walker = doc.createTreeWalker(block, SHOW_TEXT);
    const parts = [];
    let text = "", caretIndex = -1;
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.parentElement || n.parentElement.closest(SKIP_SEL) || blockOf(root, n.parentElement) !== block) continue;
      if (n === caret.node) caretIndex = text.length + caret.offset;
      parts.push({ node: n, start: text.length });
      text += n.nodeValue;
    }
    if (caretIndex === -1) return null;
    const hit = sentences(text).find((s) => caretIndex >= s.index && caretIndex <= s.index + s.length);
    if (!hit) return null;
    let from = hit.index, to = hit.index + hit.length;
    while (from < to && /\s/.test(text[from])) from += 1;
    while (to > from && /\s/.test(text[to - 1])) to -= 1;
    if (from === to) return null;
    const point = (index, atEnd) => {
      const part = parts.filter((p) => (atEnd ? p.start < index : p.start <= index)).pop();
      return [part.node, index - part.start];
    };
    const range = doc.createRange();
    range.setStart(...point(from, false));
    range.setEnd(...point(to, true));
    return range;
  }

  // ----- the edit handshake -----
  function offscreen(html) {
    const body = new DOMParser().parseFromString(`<!doctype html><body>${html}`, "text/html").body;
    cleanMarks(body);
    return body;
  }

  function sameMarks(a, b) {
    return a.length === b.length && a.every((m, i) => m.color === b[i].color && m.text === b[i].text);
  }

  // Dry run → render both versions off-screen → same text, mark where we
  // meant it → the real write. Returns the fresh page.
  async function commit(opts, file, op, check) {
    const body = { path: opts.path, base_sha: file.sha, ...op };
    const dry = await opts.save({ ...body, dry_run: true });
    const before = offscreen(opts.renderHtml(file.content));
    const after = offscreen(opts.renderHtml(dry.content));
    if (stream(before).chars !== stream(after).chars) {
      throw new Error("This selection crosses formatting that cannot hold a highlight — try a smaller one.");
    }
    if (!check(marksOf(before), marksOf(after), coverage(after))) {
      throw new Error("The highlight would not land on the selected text — try selecting it again.");
    }
    return opts.save(body);
  }

  // One edit at a time per page. `busy` lives on the page's opts, so a save
  // still running for the page the reader left never blocks the next one.
  async function run(ctl, steps) {
    const opts = ctl.opts;
    if (!opts || opts.busy) return;
    opts.busy = true;
    ctl.container.classList.add("hl-busy");
    hidePop();
    let file = { content: opts.source, sha: opts.sha };
    let saved = null, failure = null;
    try {
      for (const step of steps) {
        saved = await step(file, opts);      // the page the gesture was made on
        file = saved;
      }
    } catch (err) {
      failure = err;
    }
    opts.busy = false;
    if (ctl.opts !== opts) return;                     // the reader moved on meanwhile
    ctl.container.classList.remove("hl-busy");
    const sel = global.getSelection && global.getSelection();
    if (sel && !failure) sel.removeAllRanges();
    if (saved) opts.onSaved(saved);
    if (failure) {
      flash(failure.status === 409 ? "The page changed on disk. It was reloaded — select again."
                                   : failure.message || "Could not save the highlight.");
      if (failure.status === 409 && !saved && opts.reload) opts.reload();
    }
  }

  function addHighlight(ctl, range, color) {
    const quotes = quotesFromRange(ctl.container, range);
    if (!quotes.length) { flash("Nothing here can be highlighted."); return; }
    setPref(LAST_KEY, color);
    run(ctl, quotes.map((quote) => (file, opts) => {
      const loc = core.locate(file.content, quote, opts.profile);
      if (loc.error) throw new Error(`Not highlighted: ${loc.error}.`);
      const op = { op: "add", color, expect: file.content.slice(loc.start, loc.end),
                   start: core.toCodePoints(file.content, loc.start),
                   end: core.toCodePoints(file.content, loc.end) };
      // The new highlight must cover the selected chars where they were
      // selected — not a same-worded twin elsewhere on the page.
      return commit(opts, file, op, (_before, _after, cov) => {
        if (cov.chars !== quote.page) return false;
        for (let i = quote.from; i < quote.to; i++) if (cov.colors[i] !== color) return false;
        return true;
      });
    }));
  }

  function markIndex(ctl, mark) {
    return Array.from(ctl.container.querySelectorAll(MARK_SEL)).indexOf(mark);
  }

  function recolorMark(ctl, mark, color) {
    const index = markIndex(ctl, mark);
    if (index === -1) return;
    setPref(LAST_KEY, color);
    run(ctl, [(file, opts) => commit(opts, file, { op: "recolor", index, color }, (before, after) =>
      sameMarks(after, before.map((m, i) => (i === index ? { ...m, color } : m))))]);
  }

  function removeMarks(ctl, marks) {
    const indexes = marks.map((m) => markIndex(ctl, m)).filter((i) => i !== -1).sort((a, b) => b - a);
    if (!indexes.length) return;
    run(ctl, indexes.map((index) => (file, opts) => commit(opts, file, { op: "remove", index },
      (before, after) => sameMarks(after, before.filter((_, i) => i !== index)))));
  }

  // ----- palette, pen chip, messages -----
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    (children || []).forEach((c) => node.append(c));
    return node;
  }

  function dots(className) {
    return COLORS.map((color, n) => el("button", {
      type: "button", class: `${className} hl-dot-${color}`, "data-color": color,
      title: `${color[0].toUpperCase()}${color.slice(1)} (${n + 1})`, "aria-label": `Highlight ${color}` }));
  }

  function build() {
    if (pop) return;
    const erase = el("button", { type: "button", class: "hl-btn hl-erase", title: "Remove highlight (0)",
                                 "aria-label": "Remove highlight" }, [el("span", { class: "codicon codicon-close" })]);
    const pen = el("button", { type: "button", class: "hl-btn hl-pen", "aria-pressed": "false",
                               title: "Pen mode: highlight every selection right away",
                               "aria-label": "Pen mode" }, [el("span", { class: "codicon codicon-edit" })]);
    pop = el("div", { class: "hl-pop hl-ui", role: "toolbar", "aria-label": "Highlight", hidden: "" },
             [...dots("hl-dot"), el("span", { class: "hl-sep" }), erase, pen]);
    chip = el("div", { class: "hl-penchip hl-ui", hidden: "" },
              [el("span", { class: "hl-penchip-label" }, ["Pen"]), ...dots("hl-dot hl-dot-sm"),
               el("button", { type: "button", class: "hl-btn hl-penchip-off", title: "Stop pen mode",
                              "aria-label": "Stop pen mode" }, [el("span", { class: "codicon codicon-close" })])]);
    flashBox = el("div", { class: "hl-flash hl-ui", role: "status", hidden: "" });
    document.body.append(pop, chip, flashBox);

    // Keep the reader's selection alive while a palette button is pressed.
    [pop, chip].forEach((box) => box.addEventListener("mousedown", (ev) => ev.preventDefault()));
    pop.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button");
      if (!btn || !open) return;
      if (btn.dataset.color) choose(btn.dataset.color);
      else if (btn.classList.contains("hl-erase")) eraseOpen();
      else if (btn.classList.contains("hl-pen")) togglePen();
    });
    chip.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button");
      if (!btn) return;
      setPen(btn.dataset.color || "");
    });
  }

  function flash(message) {
    build();
    flashBox.textContent = message;
    flashBox.hidden = false;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { flashBox.hidden = true; }, FLASH_MS);
  }

  function live(ctl) {
    return !!ctl && ctl.enabled && ctl.sentinel && ctl.sentinel.parentNode === ctl.container;
  }

  function refreshChip() {
    if (!chip) return;
    const color = penColor();
    const showing = Array.from(containers).some((c) => live(ctls.get(c)) && c.offsetParent !== null);
    chip.hidden = !(color && showing);
    chip.querySelectorAll("[data-color]").forEach((d) => d.classList.toggle("active", d.dataset.color === color));
  }

  function setPen(color) {
    setPref(PEN_KEY, color);
    if (color) setPref(LAST_KEY, color);
    refreshChip();
  }

  function marksInRange(ctl, range) {
    return Array.from(ctl.container.querySelectorAll(MARK_SEL)).filter((m) => range.intersectsNode(m));
  }

  function showPop(state, rect) {
    build();
    open = state;
    const current = state.mark ? state.mark.className.slice(3) : "";
    pop.querySelectorAll("[data-color]").forEach((d) => d.classList.toggle("active", d.dataset.color === current));
    const erasable = state.mark ? [state.mark] : marksInRange(state.ctl, state.range);
    pop.querySelector(".hl-erase").hidden = !erasable.length;
    pop.querySelector(".hl-pen").setAttribute("aria-pressed", penColor() ? "true" : "false");
    pop.hidden = false;
    const box = pop.getBoundingClientRect();
    const above = rect.top - box.height - 8;
    const top = above >= 8 ? above : Math.min(rect.bottom + 8, global.innerHeight - box.height - 8);
    const left = Math.min(Math.max(8, rect.left + rect.width / 2 - box.width / 2),
                          global.innerWidth - box.width - 8);
    pop.style.top = `${Math.max(8, top)}px`;
    pop.style.left = `${Math.max(8, left)}px`;
  }

  function hidePop() {
    open = null;
    if (pop) pop.hidden = true;
  }

  function choose(color) {
    if (!open || !live(open.ctl)) { hidePop(); return; }
    const { ctl, range, mark } = open;
    if (mark) recolorMark(ctl, mark, color); else addHighlight(ctl, range, color);
  }

  function eraseOpen() {
    if (!open || !live(open.ctl)) { hidePop(); return; }
    const { ctl, range, mark } = open;
    removeMarks(ctl, mark ? [mark] : marksInRange(ctl, range));
  }

  function togglePen() {
    if (penColor()) { setPen(""); hidePop(); return; }
    const color = lastColor();
    setPen(color);
    if (open && !open.mark) choose(color); else hidePop();
  }

  // ----- reader gestures -----
  function selectionIn(ctl) {
    const sel = global.getSelection && global.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    if (!ctl.container.contains(range.commonAncestorContainer)) return null;
    return core.squashText(range.toString()) ? range.cloneRange() : null;
  }

  function sameRange(a, b) {
    return a.compareBoundaryPoints(Range.START_TO_START, b) === 0
        && a.compareBoundaryPoints(Range.END_TO_END, b) === 0;
  }

  function onSelection(ctl, range) {
    handled = { text: range.toString(), at: Date.now() };
    const pen = penColor();
    if (pen) addHighlight(ctl, range, pen);
    else showPop({ ctl, range, mark: null }, range.getBoundingClientRect());
  }

  function onPointerUp(ctl, ev) {
    if (!live(ctl) || ctl.opts.busy) return;
    const range = selectionIn(ctl);
    if (range) { onSelection(ctl, range); return; }
    if ((ev.altKey || ev.ctrlKey) && ev.button === 0) {
      const sentence = sentenceRange(ctl.container, ev.clientX, ev.clientY);
      if (!sentence) return;
      const sel = global.getSelection();
      sel.removeAllRanges();
      sel.addRange(sentence);
      onSelection(ctl, sentence.cloneRange());
      return;
    }
    const mark = ev.target.closest ? ev.target.closest(MARK_SEL) : null;
    if (mark && ctl.container.contains(mark) && !ev.target.closest("a")) {
      showPop({ ctl, range: null, mark }, mark.getBoundingClientRect());
    }
  }

  // Touch and keyboard selections never fire a mouseup on the page.
  function onSelectionSettled() {
    if (mouseDown || open) return;
    for (const container of containers) {
      const ctl = ctls.get(container);
      const range = live(ctl) && !ctl.opts.busy ? selectionIn(ctl) : null;
      if (!range) continue;
      // A mouse gesture already acted on this very selection (it may have
      // failed and left it standing): do not act twice.
      if (range.toString() === handled.text && Date.now() - handled.at < SAME_SELECTION_MS) return;
      onSelection(ctl, range);
      return;
    }
  }

  function onKey(ev) {
    if (!open || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    const tag = ev.target && ev.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || (ev.target && ev.target.isContentEditable)) return;
    const n = "123456".indexOf(ev.key);
    if (ev.key === "Escape") hidePop();
    else if (ev.key.length === 1 && n !== -1) choose(COLORS[n]);
    else if ((ev.key === "0" || ev.key === "Delete" || ev.key === "Backspace")
             && !pop.querySelector(".hl-erase").hidden) eraseOpen();
    else return;
    ev.preventDefault();
  }

  let wired = false;
  function wireDocument() {
    if (wired) return;
    wired = true;
    document.addEventListener("mousedown", (ev) => {
      mouseDown = true;
      if (open && !pop.contains(ev.target)) hidePop();
    }, true);
    document.addEventListener("mouseup", () => { mouseDown = false; setTimeout(refreshChip, 0); }, true);
    document.addEventListener("selectionchange", () => {
      // A palette opened for a selection follows it: a keyboard or touch
      // adjustment closes it and the settled selection reopens it.
      if (open && open.range && !mouseDown) {
        const sel = global.getSelection();
        if (!sel.rangeCount || !sameRange(sel.getRangeAt(0), open.range)) hidePop();
      }
      clearTimeout(settleTimer);
      settleTimer = setTimeout(onSelectionSettled, SELECTION_SETTLE_MS);
    });
    document.addEventListener("keydown", onKey, true);
    global.addEventListener("scroll", () => { if (open) hidePop(); }, true);
    global.addEventListener("resize", hidePop);
  }

  function attach(container, opts) {
    if (!core || !container || !opts) return;
    build();
    wireDocument();
    let ctl = ctls.get(container);
    if (!ctl) {
      ctl = { container };
      ctls.set(container, ctl);
      containers.add(container);
      container.addEventListener("mouseup", (ev) => setTimeout(() => onPointerUp(ctl, ev), 0));
    }
    ctl.opts = opts;
    ctl.enabled = true;
    container.classList.remove("hl-busy");
    // Whoever replaces the container's content drops the sentinel with it, so
    // a stale controller can never write to the page that is no longer shown.
    ctl.sentinel = el("span", { class: "hl-sentinel", hidden: "" });
    container.append(ctl.sentinel);
    container.classList.add("hl-on");
    refreshChip();
  }

  function detach(container) {
    const ctl = container && ctls.get(container);
    if (!ctl) return;
    ctl.enabled = false;
    ctl.opts = null;
    container.classList.remove("hl-on");
    if (open && open.ctl === ctl) hidePop();
    refreshChip();
  }

  global.wikiMarks = { COLORS, attach, detach, cleanMarks,
                       _internals: { stream, quotesFromRange, sentenceRange, marksOf } };
})(window);
