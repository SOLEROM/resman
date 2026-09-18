// wiki-marks-core.js — the pure half of the wiki highlighter. Text in, offsets
// out: no DOM needed, so it runs under plain `node` (tests/js).
//
// The reader selects *rendered* text; a highlight is written into the
// *markdown source*. This file bridges the two the way web annotation tools
// anchor a quote, then makes sure the <mark> pair will nest:
//
//   project(src, profile)  the text a reader sees, char by char, each char
//                 mapped back to its source span. It is built from the app's
//                 own render pipeline — where the body starts, its wikilink
//                 and callout rewrites, then marked's lexer — so emphasis,
//                 strikethrough (`~61%` included), links, code spans and table
//                 cells are exactly what the page shows.
//   locate(src, quote, profile)  find the selected quote in that text (whitespace is
//                 ignored on both sides; prefix/suffix context, then the
//                 occurrence ordinal, settle duplicates) and return source
//                 offsets, grown outward wherever they would cut a wikilink,
//                 a code span, an entity or an emphasis/link run in half.
//
// Mapping tokens back to the source: top-level tokens tile the text exactly;
// inside quotes, lists and tables marked strips prefixes, so each leaf's
// lines are found again in the text (suffix, then substring, then char by
// char). A char that cannot be placed is unmappable and a selection over it
// is refused. The caller renders every edit before writing it and refuses any
// that changes the page text: a wrong guess is a refused highlight, never a
// damaged page.
//
// The profile describes the app's renderer (every field optional):
//
//   { marked,                        // default: window.marked
//     bodyStart(src) → offset,       // where the rendered body begins
//     wikilinks: { re,               // the renderer's global [[…]] RegExp
//                  split,            // RegExp whose captured parts stay untouched
//                                    //   (code spans), or null
//                  render(match) → { open, labelHtml, close } },  // its exact markup
//     callouts: true,                // Obsidian `> [!type]` blocks → callout divs
//     breaks: false }                // marked's `breaks` option
//
// No profile: plain marked over the whole text. Each app's tests pin that its
// profile and its renderer agree. Offsets are UTF-16; toCodePoints() converts
// for the server.
(function (global) {
  "use strict";

  const CONTEXT_CHARS = 32;
  const CALLOUT_RE = /^>\s*\[!([a-zA-Z-]+)\]([+-]?)\s*(.*)$/;
  const ENTITY_RE = /&(#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);/g;
  const ENTITY_AT_RE = /^&(#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);/;
  const TAG_AT_RE = /^<(?:!--[\s\S]*?-->|\/?[A-Za-z][^<>]*>)/;
  const ENTITIES = {
    amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0", copy: "©", reg: "®",
    mdash: "—", ndash: "–", hellip: "…", rarr: "→", larr: "←",
    times: "×", deg: "°", lsquo: "‘", rsquo: "’", ldquo: "“",
    rdquo: "”", middot: "·", bull: "•",
  };
  const ALIGN_LINES = 64;             // lines a last-resort alignment may look ahead
  const UNMAPPED = "could not map this text back to the page source";
  const IN_CODE = "cannot highlight inside a code block";

  function isSpace(ch) { return /\s/.test(ch); }
  function squashText(s) { return String(s).replace(/\s+/g, ""); }

  function escapeHtml(value) {
    return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  function toCodePoints(src, utf16Offset) {
    let n = 0;
    for (let i = 0; i < utf16Offset && i < src.length; i++) {
      const c = src.charCodeAt(i);
      if (!(c >= 0xdc00 && c <= 0xdfff)) n += 1;     // low surrogates ride along
    }
    return n;
  }

  // One entity as the browser decodes it; unknown names stay literal.
  function decodeEntity(entity) {
    const doc = global.document;
    if (doc && typeof doc.createElement === "function") {
      const box = doc.createElement("textarea");
      box.innerHTML = entity;                 // ENTITY_RE-shaped: no markup can get in
      return box.value;
    }
    const body = entity.slice(1, -1);
    if (body[0] !== "#") return Object.prototype.hasOwnProperty.call(ENTITIES, body) ? ENTITIES[body] : entity;
    const code = /^#x/i.test(body) ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
    if (!Number.isFinite(code) || code <= 0 || code > 0x10ffff) return "\ufffd";
    return String.fromCodePoint(code);
  }

  function decodeAll(text) {
    return String(text).replace(ENTITY_RE, (m) => decodeEntity(m));
  }

  // ----- the renderer's input, with a source span for every char -----
  // text[i] came from source [s[i], e[i]); gen[i] marks markup the rewrites
  // made up (never visible). Separators the rewrites join lines with are gen
  // with no span.
  function Tracked() { this.text = ""; this.s = []; this.e = []; this.gen = []; }
  Tracked.prototype.add = function (str, s, e, gen) {
    for (let i = 0; i < str.length; i++) { this.s.push(s); this.e.push(e); this.gen.push(!!gen); }
    this.text += str;
  };
  Tracked.prototype.copyFrom = function (other, from, to) {
    for (let i = from; i < to; i++) {
      this.s.push(other.s[i]); this.e.push(other.e[i]); this.gen.push(other.gen[i]);
    }
    this.text += other.text.slice(from, to);
  };

  // The body as the renderer's wikilink rewrite leaves it: each [[…]] becomes
  // the app's own markup, whose label chars all map to the whole [[…]] (an
  // atom). Without wikilinks the body is copied as is.
  function rewriteWikilinks(src, start, atoms, wl) {
    const out = new Tracked();
    let offset = start;
    const copy = (seg, from, to) => {
      for (let k = from; k < to; k++) out.add(seg[k], offset + k, offset + k + 1, false);
    };
    const body = src.slice(start);
    (wl && wl.split ? body.split(wl.split) : [body]).forEach((seg, i) => {
      if (!wl || i % 2 === 1) {
        copy(seg, 0, seg.length);
      } else {
        let last = 0;
        for (const m of seg.matchAll(wl.re)) {
          copy(seg, last, m.index);
          const s = offset + m.index, e = s + m[0].length;
          const html = wl.render(m);
          atoms.push({ s, e });
          out.add(html.open, s, e, true);
          out.add(html.labelHtml, s, e, false);
          out.add(html.close, s, e, true);
          last = m.index + m[0].length;
        }
        copy(seg, last, seg.length);
      }
      offset += seg.length;
    });
    return out;
  }

  // The Obsidian callout rewrite (interpreta, gridar): `> [!type] Title` and
  // the `>` lines under it → callout divs around the de-quoted body. The title is drawn apart from the
  // page text, so it is generated markup here.
  function rewriteCallouts(t1) {
    const lines = [];
    for (let at = 0; ;) {
      const nl = t1.text.indexOf("\n", at);
      if (nl === -1) { lines.push([at, t1.text.length]); break; }
      lines.push([at, nl]);
      at = nl + 1;
    }
    const pieces = [];
    let i = 0;
    while (i < lines.length) {
      const [a, b] = lines[i];
      const m = t1.text.slice(a, b).match(CALLOUT_RE);
      if (!m) { pieces.push((out) => out.copyFrom(t1, a, b)); i += 1; continue; }
      const s = t1.s[a], e = t1.e[b - 1];
      const type = m[1].toLowerCase();
      const title = m[3].trim() || type.replace(/-/g, " ");
      const body = [];
      i += 1;
      while (i < lines.length && t1.text.charAt(lines[i][0]) === ">") {
        const [c, d] = lines[i];
        body.push([c + t1.text.slice(c, d).match(/^>\s?/)[0].length, d]);
        i += 1;
      }
      pieces.push((out) => out.add(`<div class="callout callout-${escapeHtml(type)}">`, s, e, true));
      pieces.push((out) => out.add(`<div class="callout-title"><span></span>${escapeHtml(title)}</div>`, s, e, true));
      pieces.push((out) => {
        out.add('<div class="callout-body">\n\n', s, e, true);
        body.forEach(([c, d], n) => { if (n) out.add("\n", -1, -1, true); out.copyFrom(t1, c, d); });
        out.add("\n\n</div></div>", s, e, true);
      });
    }
    const out = new Tracked();
    pieces.forEach((piece, n) => { if (n) out.add("\n", -1, -1, true); piece(out); });
    return out;
  }

  // The renderer's input for `src` under `profile`, char-tracked.
  function rewrite(src, profile) {
    const p = profile || {};
    const text = String(src);
    const start = p.bodyStart ? Math.max(0, Math.min(text.length, Number(p.bodyStart(text)) || 0)) : 0;
    const atoms = [];
    const t1 = rewriteWikilinks(text, start, atoms, p.wikilinks || null);
    return { t: p.callouts ? rewriteCallouts(t1) : t1, atoms };
  }

  // ----- lexer tokens → visible chars -----
  function joinedRaw(tokens) { return (tokens || []).map((tok) => tok.raw).join(""); }

  function Walker(t, out) {
    this.t = t;
    this.T = t.text;
    this.out = out;
    this.cursor = 0;
  }

  Walker.prototype.push = function (ch, s, e, reason) {
    const o = this.out;
    o.text += ch;
    o.starts.push(s); o.ends.push(e);
    o.blocked.push(isSpace(ch) ? "" : reason);
  };

  // A visible char at renderer-input offset `at` (-1: unplaced).
  Walker.prototype.emit = function (ch, at, reason) {
    if (at >= 0 && this.t.gen[at]) return;
    if (at < 0 || this.t.s[at] < 0) this.push(ch, -1, -1, UNMAPPED);
    else this.push(ch, this.t.s[at], this.t.e[at], reason || "");
  };

  // Source span covering renderer-input chars pos[a..b); null if none placed.
  Walker.prototype.span = function (pos, a, b) {
    let s = Infinity, e = -Infinity;
    for (let k = a; k < b; k++) {
      const at = pos[k];
      if (at === undefined || at < 0 || this.t.s[at] < 0) continue;
      s = Math.min(s, this.t.s[at]); e = Math.max(e, this.t.e[at]);
    }
    return s === Infinity ? null : { s, e };
  };

  // Text that stands for a stretch of source as a whole (code span, entity,
  // escape, autolink): every visible char maps to the whole stretch.
  Walker.prototype.atom = function (visible, pos, a, b) {
    const sp = this.span(pos, a, b);
    if (sp) this.out.atoms.push(sp);
    for (let i = 0; i < visible.length; i++) {     // UTF-16 units, like `text`
      if (sp) this.push(visible[i], sp.s, sp.e, ""); else this.push(visible[i], -1, -1, UNMAPPED);
    }
  };

  Walker.prototype.lineEnd = function (from, hi) {
    const end = this.T.indexOf("\n", from);
    return end === -1 || end > hi ? hi : end;
  };

  // Where `raw` (a leaf's text as the lexer saw it) sits in the renderer
  // input, char by char: forward from the cursor, never past `hi`. Container
  // prefixes come off the front of a line, so a suffix match wins — except in
  // table rows (`leftmost`), where cells sit side by side.
  Walker.prototype.align = function (raw, hi, leftmost) {
    const T = this.T;
    const pos = new Array(raw.length).fill(-1);
    let cursor = this.cursor;
    let k = 0;
    for (const line of raw.split("\n")) {
      const core = line.trim();
      if (core) {
        const lead = line.length - line.trimStart().length;
        let placed = false;
        for (let from = cursor; from < hi;) {
          const end = this.lineEnd(from, hi);
          const seg = T.slice(from, end).trimEnd();
          let at = !leftmost && seg.endsWith(core) ? seg.length - core.length : seg.indexOf(core);
          if (at !== -1) {
            at += from;
            for (let j = 0; j < core.length; j++) pos[k + lead + j] = at + j;
            cursor = at + core.length;
            placed = true;
            break;
          }
          from = end + 1;
        }
        if (!placed) cursor = this.alignChars(line, k, pos, cursor, hi);
      }
      k += line.length + 1;
    }
    this.cursor = cursor;
    return pos;
  };

  // Last resort for one line (a table cell with `\|`, a tab-expanded item):
  // its non-space chars in order, all within one line, trying the next few
  // lines from the cursor on. A `\|` the table lexer unescaped maps as one atom, so a
  // highlight never lands between the backslash and the pipe.
  Walker.prototype.alignChars = function (line, k, pos, cursor, hi) {
    for (let t = cursor, tries = 0; t < hi && tries < ALIGN_LINES; tries++) {
      const end = this.lineEnd(t, hi);
      const found = [];
      let at = t;
      for (let i = 0; i < line.length; i++) {
        if (isSpace(line[i])) continue;
        at = this.T.indexOf(line[i], at);
        if (at === -1 || at >= end) break;
        found.push([k + i, at]);
        at += 1;
      }
      const all = found.length === line.replace(/\s+/g, "").length;
      if (all && found.length) {
        found.forEach(([i, p], n) => {
          pos[i] = p;
          const prev = n ? found[n - 1][1] : -1;
          if (p > 0 && this.T[p - 1] === "\\" && prev !== p - 1 && this.t.s[p - 1] >= 0) {
            this.out.atoms.push({ s: this.t.s[p - 1], e: this.t.e[p] });
          }
        });
        return at;
      }
      t = end + 1;
    }
    return cursor;
  };

  // Top-level tokens tile the input exactly; nested ones are found from the cursor.
  Walker.prototype.blocks = function (tokens, hi, exact) {
    let start = this.cursor;
    for (const tok of tokens || []) {
      if (exact) this.cursor = start;
      this.block(tok, exact ? start + tok.raw.length : hi);
      if (exact) { start += tok.raw.length; this.cursor = start; }
    }
  };

  Walker.prototype.block = function (tok, hi) {
    switch (tok.type) {
      case "space": case "hr": case "def":
        return;
      case "heading": case "paragraph":
        return this.leaf(tok.tokens, hi, false);
      case "text":
        return this.leaf(tok.tokens || [{ type: "text", raw: tok.raw, text: tok.text }], hi, false);
      case "blockquote":
        return this.blocks(tok.tokens, hi, false);
      case "list":
        return (tok.items || []).forEach((item) => this.blocks(item.tokens, hi, false));
      case "table":
        (tok.header || []).forEach((cell) => this.leaf(cell.tokens, hi, true));
        return (tok.rows || []).forEach((row) => row.forEach((cell) => this.leaf(cell.tokens, hi, true)));
      case "code":
        return this.code(tok, hi);
      case "html":
        return this.markup(tok.raw, 0, tok.raw.length, this.align(tok.raw, hi, false), true);
      default:
        if (tok.tokens) this.leaf(tok.tokens, hi, false);
    }
  };

  // Code blocks show but never take a highlight; mermaid is drawn as a
  // picture (no page text). Either way the cursor moves past them.
  Walker.prototype.code = function (tok, hi) {
    const pos = this.align(tok.raw, hi, false);
    if (/^\s*mermaid\b/.test(tok.lang || "")) return;
    const sp = this.span(pos, 0, tok.raw.length);
    const text = tok.text || "";
    for (let i = 0; i < text.length; i++) {        // UTF-16 units, like `text`
      if (sp) this.push(text[i], sp.s, sp.e, IN_CODE); else this.push(text[i], -1, -1, IN_CODE);
    }
  };

  // Literal text with entities decoded; with `tags`, HTML tags and comments
  // are skipped (an HTML block shows only the text between its tags).
  Walker.prototype.markup = function (raw, from, to, pos, tags) {
    let i = from;
    while (i < to) {
      if (tags && raw[i] === "<") {
        const m = TAG_AT_RE.exec(raw.slice(i, Math.min(to, i + 4096)));
        if (m) { i += m[0].length; continue; }
      }
      if (raw[i] === "&") {
        const m = ENTITY_AT_RE.exec(raw.slice(i, Math.min(to, i + 64)));
        const decoded = m ? decodeEntity(m[0]) : null;
        if (m && decoded !== m[0]) {
          if (!(pos[i] >= 0 && this.t.gen[pos[i]])) this.atom(decoded, pos, i, i + m[0].length);
          i += m[0].length;
          continue;
        }
      }
      this.emit(raw[i], pos[i], "");
      i += 1;
    }
  };

  Walker.prototype.leaf = function (tokens, hi, leftmost) {
    if (!tokens || !tokens.length) return;
    this.inline(tokens, 0, this.align(joinedRaw(tokens), hi, leftmost));
  };

  Walker.prototype.inline = function (tokens, offset, pos) {
    let k = offset;
    for (const tok of tokens) {
      const len = tok.raw.length;
      switch (tok.type) {
        case "text":
          if (tok.tokens && tok.tokens.length && joinedRaw(tok.tokens) === tok.raw) this.inline(tok.tokens, k, pos);
          else this.markup(tok.raw, 0, len, pos.slice(k, k + len), false);
          break;
        case "escape":
          this.atom(decodeAll(tok.text), pos, k, k + len);
          break;
        case "codespan":
          this.atom(decodeAll(tok.text), pos, k, k + len);
          break;
        case "br": case "html": case "image":
          break;
        case "strong": case "em": case "del": case "link":
          this.wrap(tok, k, pos);
          break;
        default:
          if (tok.tokens) this.inline(tok.tokens, k + Math.max(0, tok.raw.indexOf(joinedRaw(tok.tokens))), pos);
          else this.markup(tok.raw, 0, len, pos.slice(k, k + len), false);
      }
      k += len;
    }
  };

  // Emphasis, strikethrough and links: the text inside may take a highlight
  // of its own; a highlight across the delimiters takes the whole run.
  // Autolinks are atoms (a tag inside a bare URL would change the link).
  Walker.prototype.wrap = function (tok, k, pos) {
    const inner = joinedRaw(tok.tokens);
    const len = tok.raw.length;
    let io = tok.type === "em" ? 1 : tok.type === "strong" ? 2 : tok.raw.indexOf(inner);
    if (tok.raw.substr(io, inner.length) !== inner) io = tok.raw.indexOf(inner);
    if (io <= 0 || tok.raw[0] === "<" || !inner.length) {
      this.atom(decodeAll(tok.text || ""), pos, k, k + len);
      return;
    }
    const outer = this.span(pos, k, k + len);
    const first = this.span(pos, k + io, k + io + 1);
    const last = this.span(pos, k + io + inner.length - 1, k + io + inner.length);
    if (outer && first && last) {
      this.out.wraps.push({ outerS: outer.s, outerE: outer.e, innerS: first.s, innerE: last.e });
    }
    this.inline(tok.tokens || [], k + io, pos);
  };

  function project(src, profile) {
    const p = profile || {};
    const out = { text: "", starts: [], ends: [], blocked: [], atoms: [], wraps: [], error: "" };
    const marked = p.marked || global.marked;
    if (!marked || typeof marked.lexer !== "function") {
      out.error = "the markdown renderer is not loaded";
      return out;
    }
    let t, tokens;
    try {
      const rewritten = rewrite(src, p);
      t = rewritten.t;
      out.atoms.push(...rewritten.atoms);
      tokens = marked.lexer(t.text, { gfm: true, breaks: !!p.breaks });
    } catch (err) {
      out.error = `could not read the page (${err.message || err})`;
      return out;
    }
    new Walker(t, out).blocks(tokens, t.text.length, true);
    return out;
  }

  // ----- quote → source range -----
  // The projection without whitespace, keeping each char's index into it.
  function squash(projection) {
    let text = "";
    const index = [];
    for (let i = 0; i < projection.text.length; i++) {
      if (isSpace(projection.text[i])) continue;
      text += projection.text[i];
      index.push(i);
    }
    return { text, index };
  }

  function occurrences(haystack, needle) {
    const out = [];
    for (let at = haystack.indexOf(needle); at !== -1; at = haystack.indexOf(needle, at + 1)) out.push(at);
    return out;
  }

  function commonSuffix(a, b) {
    let n = 0;
    while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n += 1;
    return n;
  }

  function commonPrefix(a, b) {
    let n = 0;
    while (n < a.length && n < b.length && a[n] === b[n]) n += 1;
    return n;
  }

  // Which occurrence of the quote is the selected one: alone → that one; else
  // the one whose surroundings match the selection's best; on a tie the
  // ordinal, but only when source and page agree on how many there are.
  function pick(flat, hits, quote) {
    if (hits.length === 1) return hits[0];
    const scores = hits.map((at) =>
      commonSuffix(flat.slice(Math.max(0, at - CONTEXT_CHARS), at), quote.prefix || "") +
      commonPrefix(flat.slice(at + quote.exact.length, at + quote.exact.length + CONTEXT_CHARS),
                   quote.suffix || ""));
    const best = Math.max(...scores);
    const leaders = hits.filter((_, n) => scores[n] === best);
    if (leaders.length === 1) return leaders[0];
    if (hits.length === quote.count && quote.ordinal >= 0 && quote.ordinal < hits.length) {
      return hits[quote.ordinal];
    }
    return -1;
  }

  // Grow [s, e) until it cuts nothing in half. An atom is all or nothing; a
  // wrap (emphasis, link) may hold the range inside it or sit whole within it.
  function snap(projection, s, e) {
    for (let changed = true; changed;) {
      changed = false;
      for (const a of projection.atoms) {
        if (a.s < e && a.e > s && (a.s < s || a.e > e)) {
          s = Math.min(s, a.s); e = Math.max(e, a.e); changed = true;
        }
      }
      for (const w of projection.wraps) {
        const inside = s >= w.innerS && e <= w.innerE;
        const around = s <= w.outerS && e >= w.outerE;
        if (!inside && !around && w.outerS < e && w.outerE > s) {
          s = Math.min(s, w.outerS); e = Math.max(e, w.outerE); changed = true;
        }
      }
    }
    return { start: s, end: e };
  }

  function locate(src, quote, profile) {
    if (!quote || !quote.exact) return { error: "nothing selected" };
    const projection = project(src, profile);
    if (projection.error) return { error: projection.error };
    const flat = squash(projection);
    const hits = occurrences(flat.text, quote.exact);
    if (!hits.length) return { error: "could not find the selected text in the page source" };
    const at = pick(flat.text, hits, quote);
    if (at === -1) return { error: "the selected text appears several times; select a little more" };
    let s = Infinity, e = -Infinity;
    for (let n = at; n < at + quote.exact.length; n++) {
      const i = flat.index[n];
      if (projection.blocked[i]) return { error: projection.blocked[i] };
      s = Math.min(s, projection.starts[i]);
      e = Math.max(e, projection.ends[i]);
    }
    return snap(projection, s, e);
  }

  global.wikiMarksCore = { CONTEXT_CHARS, project, rewrite, squash, squashText, locate, toCodePoints };
})(typeof window !== "undefined" ? window : globalThis);
