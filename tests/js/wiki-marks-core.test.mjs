/**
 * Behavioral tests for static/js/wiki-marks-core.js — the pure half of the
 * highlighter: project markdown source to the text a reader sees, and locate
 * a selected quote back in the source (UTF-16 offsets, snapped so the
 * <mark> pair nests cleanly).
 *
 * App-agnostic (the same file in every app that ships the highlighter): the
 * core reads pages with the app's vendored marked, run here in a vm context
 * whose window is its global. OBSIDIAN below is a profile in the style of
 * interpreta's and gridar's renderers; each app pins its own profile against
 * its own renderer elsewhere. Dependency-free: node's assert. The pytest
 * wrapper (tests/test_js_behavior.py) skips when node is absent.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (rel) => readFileSync(new URL(`../../control-plane/static/${rel}`, import.meta.url), 'utf8');
const ctx = vm.createContext({});
ctx.window = ctx;
vm.runInContext(read('vendor/marked.min.js'), ctx);
vm.runInContext(read('js/wiki-marks-core.js'), ctx);
const core = ctx.wikiMarksCore;
assert.ok(ctx.marked && core, 'marked and the core must load');

const esc = (v) => String(v).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
/* frontmatter split + [[target#heading|alias]] outside code + callouts */
const OBSIDIAN = {
  bodyStart(text) {
    if (!text.startsWith('---\n')) return 0;
    const end = text.indexOf('\n---', 4);
    if (end === -1) return 0;
    return text.charAt(end + 4) === '\n' ? end + 5 : end + 4;
  },
  wikilinks: {
    re: /\[\[([^\[\]\|#]+)(?:#([^\[\]\|]*))?(?:\|([^\[\]]*))?\]\]/g,
    split: /(```[\s\S]*?```|`[^`\n]*`)/g,
    render([, target, heading, alias]) {
      const t = target.trim();
      const label = (alias || '').trim() || (heading ? `${t} › ${heading.trim()}` : t);
      return { open: '<a class="wikilink" href="#" title="x">', labelHtml: esc(label), close: '</a>' };
    },
  },
  callouts: true,
  breaks: false,
};

let passed = 0;
function test(name, fn) {
  try { fn(); passed += 1; }
  catch (err) { console.error(`FAIL ${name}\n`, err); process.exitCode = 1; }
}

const sq = (s) => core.squashText(s);
const project = (src, profile = OBSIDIAN) => core.project(src, profile);
/* A quote the way the DOM side builds one: squashed strings, plus which
   occurrence of `exact` it is among `count` in the rendered page. */
function quote(exact, { prefix = '', suffix = '', ordinal = 0, count = 1 } = {}) {
  return { exact: sq(exact), prefix: sq(prefix), suffix: sq(suffix), ordinal, count };
}
/* The source slice a quote resolves to. */
function found(src, q, profile = OBSIDIAN) {
  const loc = core.locate(src, q, profile);
  assert.equal(loc.error, undefined, `locate failed: ${loc.error}`);
  return src.slice(loc.start, loc.end);
}

test('plain text resolves to its exact source span', () => {
  const src = 'Attention is computed over all pairs, which costs a lot.\n';
  assert.equal(found(src, quote('computed over all pairs')), 'computed over all pairs');
});

test('the visible text drops markdown syntax', () => {
  const p = project('# Title\n\nOne **bold** and _it_ and `code` and [text](http://x/y).\n');
  assert.equal(sq(p.text), sq('Title One bold and it and code and text.'));
});

test('a selection equal to the bold text goes inside the delimiters', () => {
  assert.equal(found('One **bold** move here.\n', quote('bold')), 'bold');
});

test('a selection that cuts into bold text grows to the whole bold run', () => {
  const src = 'One **bold words** move here.\n';
  assert.equal(found(src, quote('words move')), '**bold words** move');
  assert.equal(found(src, quote('One bold')), 'One **bold words**');
});

test('a selection around the whole bold run keeps it whole', () => {
  assert.equal(found('One **bold** move here.\n', quote('One bold move')), 'One **bold** move');
});

test('nested emphasis snaps outward until it nests', () => {
  const src = 'a **bold _it_ more** tail end\n';
  assert.equal(found(src, quote('it')), 'it');
  assert.equal(found(src, quote('it more tail')), '**bold _it_ more** tail');
});

test('link text can be highlighted inside; across its edge takes the whole link', () => {
  const src = 'see [the docs](http://x.y/z "t") now\n';
  assert.equal(found(src, quote('docs')), 'docs');
  assert.equal(found(src, quote('docs now')), '[the docs](http://x.y/z "t") now');
});

test('a wikilink is atomic and shows its label', () => {
  const src = 'see [[Self-Attention|attn]] and [[Page#Head]] here\n';
  assert.equal(sq(project(src).text), sq('see attn and Page › Head here'));
  assert.equal(found(src, quote('att')), '[[Self-Attention|attn]]');
  assert.equal(found(src, quote('see attn')), 'see [[Self-Attention|attn]]');
  assert.equal(found(src, quote('Head here')), '[[Page#Head]] here');
});

test('a code span is atomic', () => {
  assert.equal(found('call `fit()` twice\n', quote('fit')), '`fit()`');
  assert.equal(found('call `fit()` twice\n', quote('call fit()')), 'call `fit()`');
});

test('fenced code is not highlightable', () => {
  const src = 'intro\n\n```python\nprint("hi")\n```\n\nafter\n';
  assert.match(core.locate(src, quote('print'), OBSIDIAN).error, /code/);
  assert.equal(found(src, quote('after')), 'after');
});

test('mermaid source is not part of the visible text', () => {
  const p = project('a\n\n```mermaid\ngraph TD; A-->B\n```\n\nb\n');
  assert.equal(sq(p.text), 'ab');
});

test('frontmatter is invisible', () => {
  const src = '---\ntitle: alpha\n---\nalpha body\n';
  const loc = core.locate(src, quote('alpha'), OBSIDIAN);
  assert.equal(loc.start, src.indexOf('alpha body'));
});

test('block markers are not part of the text', () => {
  assert.equal(found('## Results here\n', quote('Results')), 'Results');
  assert.equal(found('- [ ] task text\n', quote('task')), 'task');
  assert.equal(found('1. first point\n', quote('first point')), 'first point');
  assert.equal(found('> quoted words\n', quote('quoted')), 'quoted');
  const p = project('## Results here\n- item\n> quote\n');
  assert.equal(sq(p.text), sq('Results here item quote'));
});

test('a callout header line is skipped, its body is text', () => {
  const src = '> [!note] The Title\n> body text here\n';
  assert.equal(sq(project(src).text), sq('body text here'));
  assert.equal(found(src, quote('body text')), 'body text');
});

test('a soft-wrapped paragraph matches across the line break', () => {
  const src = 'alpha beta\ngamma delta\n';
  assert.equal(found(src, quote('beta gamma')), 'beta\ngamma');
  assert.equal(found('> one two\n> three four\n', quote('two three')), 'two\n> three');
});

test('table cells: pipes and the delimiter row are not text', () => {
  const src = '| a | b |\n|---|:-:|\n| one two | three |\n';
  assert.equal(sq(project(src).text), sq('a b one two three'));
  assert.equal(found(src, quote('one two')), 'one two');
});

test('duplicates: the context picks the right one', () => {
  const src = 'the cat sat. the dog ran. the cat slept.\n';
  const loc = core.locate(src, quote('the cat', { prefix: 'the dog ran.', suffix: 'slept.',
                                                   ordinal: 1, count: 2 }), OBSIDIAN);
  assert.equal(loc.start, src.lastIndexOf('the cat'));
});

test('duplicates: equal context falls back to the ordinal', () => {
  const src = 'go go go\n';
  const loc = core.locate(src, quote('go', { prefix: '', suffix: '', ordinal: 1, count: 3 }), OBSIDIAN);
  assert.equal(loc.start, 3);
});

test('duplicates: a count that disagrees with the source is refused', () => {
  const src = 'go go go\n';
  assert.ok(core.locate(src, quote('go', { ordinal: 1, count: 2 }), OBSIDIAN).error);
});

test('text that is not in the source is refused', () => {
  assert.ok(core.locate('alpha beta\n', quote('gamma'), OBSIDIAN).error);
  assert.ok(core.locate('alpha beta\n', quote(''), OBSIDIAN).error);
});

test('existing highlight tags are invisible', () => {
  const src = 'foo <mark class="hl-yellow">bar</mark> baz\n';
  assert.equal(sq(project(src).text), 'foobarbaz');
  assert.equal(found(src, quote('bar baz')), 'bar</mark> baz');
});

test('escapes and entities map back to their source', () => {
  const src = 'a \\* b &amp; c &#169; d\n';
  assert.equal(sq(project(src).text), 'a*b&c©d');
  assert.equal(found(src, quote('* b & c')), '\\* b &amp; c');
});

test('literal underscores and lone stars stay text', () => {
  const src = 'use snake_case_name and 2 * 3 * 4 here\n';
  assert.equal(sq(project(src).text), sq('use snake_case_name and 2 * 3 * 4 here'));
});

test('emphasis that wraps a line break is still one run', () => {
  const src = 'say **bold\nwords** end\n';
  assert.equal(found(src, quote('words end')), '**bold\nwords** end');
});

test('images have no text; autolinks show their url', () => {
  const src = 'x ![alt text](img.png) y <https://a.b/c> z\n';
  assert.equal(sq(project(src).text), sq('x y https://a.b/c z'));
});

test('offsets convert to code points for the server', () => {
  assert.equal(core.toCodePoints('🔴 word', 3), 2);
  assert.equal(core.toCodePoints('plain', 3), 3);
});

test('squashText drops every kind of whitespace', () => {
  assert.equal(sq(' a b\n\tc '), 'abc');
});

test('a single tilde is strikethrough (GFM): its tildes are not text', () => {
  const src = 'outcome ~61% infection (~1% spreader-fail, ~38% target-fail) done\n';
  // marked pairs tildes left to right; the third has no partner and stays text
  assert.equal(sq(project(src).text), sq('outcome 61% infection (1% spreader-fail, ~38% target-fail) done'));
  assert.equal(found(src, quote('infection (1%')), '~61% infection (~1%');
});

test('bold that a strikethrough cuts through stays literal', () => {
  const src = 'versus (~4.5x on pass). Cost was\n**$4.39/run (~$24 each)**, all\n';
  const text = sq(project(src).text);
  assert.ok(text.includes(sq('Cost was **$4.39/run')), text);
});

test('a callout marker inside a list item is plain quoted text', () => {
  const src = '- point\n\n   > [!stale] External caveat\n   > body line\n';
  assert.equal(sq(project(src).text), sq('point [!stale] External caveat body line'));
  assert.equal(found(src, quote('External caveat')), 'External caveat');
});

test('wikilink labels are markdown too: escapes and emphasis inside them', () => {
  const a = 'presented [[Talk (2024)|Patch Different on \\*OS]], a method\n';
  assert.equal(sq(project(a).text), sq('presented Patch Different on *OS, a method'));
  assert.equal(found(a, quote('on *OS')), '[[Talk (2024)|Patch Different on \\*OS]]');
  const b = '## Concepts\n_See [[concepts/_index]]._\n';
  assert.equal(sq(project(b).text), sq('Concepts _See concepts/index.'));
});

test('a link whose text wraps onto the next line', () => {
  const src = 'principles (["automatic / perfect\nverification"](https://x.y/z)) here\n';
  assert.equal(sq(project(src).text), sq('principles ("automatic / perfect verification") here'));
  assert.equal(found(src, quote('perfect verification')), 'perfect\nverification');
});

test('lists nested in quotes, loose items and tables with escaped pipes', () => {
  const src = '> - one **two**\n>   three\n>\n> - four\n\n| k | v |\n|---|---|\n| a \\| b | c |\n';
  assert.equal(sq(project(src).text), sq('one two three four k v a | b c'));
  assert.equal(found(src, quote('two three')), '**two**\n>   three');
  assert.equal(found(src, quote('four')), 'four');
  assert.equal(found(src, quote('c')), 'c');
});

test('an HTML block shows the text between its tags', () => {
  const src = '<div class="x">\nraw <b>html</b> &amp; text\n</div>\n\nafter\n';
  assert.equal(sq(project(src).text), sq('raw html & text after'));
  assert.equal(found(src, quote('after')), 'after');
});

test('without marked the core refuses instead of guessing', () => {
  assert.match(core.locate('alpha\n', quote('alpha'), { marked: { lexer: null } }).error, /renderer/);
  const bare = vm.createContext({});
  bare.window = bare;
  vm.runInContext(read('js/wiki-marks-core.js'), bare);
  assert.match(bare.wikiMarksCore.locate('alpha\n', quote('alpha')).error, /renderer/);
});

test('no profile: plain marked over the whole text', () => {
  const src = '---\ntitle: T\n---\nsee [[Page|alias]]\n\n> [!note] Title\n> body\n';
  const text = sq(core.project(src).text);
  assert.ok(text.includes(sq('title: T')), text);            // no frontmatter split
  assert.ok(text.includes('[[Page|alias]]'), text);          // no wikilink rewrite
  assert.ok(text.includes('[!note]Title'), text);            // no callouts
  assert.equal(core.locate(src, quote('see [[Page')).start, src.indexOf('see'));
});

test('bodyStart hides whatever the app draws apart', () => {
  const src = 'lead prose\n# Heading\n\nbody words\n';
  const profile = { bodyStart: (t) => t.indexOf('# ') };
  assert.equal(sq(core.project(src, profile).text), sq('Heading body words'));
  assert.match(core.locate(src, quote('lead'), profile).error, /could not find/);
  assert.equal(found(src, quote('body words'), profile), 'body words');
});

test('wikilinks without a split are rewritten inside code too (shown as markup)', () => {
  const src = 'a `[[x]]` b\n';
  const profile = { wikilinks: { ...OBSIDIAN.wikilinks, split: null } };
  assert.equal(sq(core.project(src, profile).text), sq('a <a class="wikilink" href="#" title="x">x</a> b'));
  assert.equal(sq(project(src).text), sq('a [[x]] b'));
});

test('breaks: a soft line break still is no text', () => {
  const src = 'one\ntwo three\n';
  const profile = { breaks: true };
  assert.equal(sq(core.project(src, profile).text), 'onetwothree');
  assert.equal(found(src, quote('one two'), profile), 'one\ntwo');
});

test('wikilink markup with a line break inside the tag still maps', () => {
  const src = 'see [[Page]] now\n';
  const profile = { wikilinks: { ...OBSIDIAN.wikilinks,
    render: () => ({ open: '<a class="wikilink" href="#"\n    title="Page">', labelHtml: 'Page', close: '</a>' }) } };
  assert.equal(sq(core.project(src, profile).text), sq('see Page now'));
  assert.equal(found(src, quote('Page now'), profile), '[[Page]] now');
});

test('emoji outside the BMP in code, code spans and entities keep later text in place', () => {
  const src = '```yaml\nicon: "🦎"\n```\n\nuse `🍯 jar` and &#x1FAB6; then the words after\n';
  assert.equal(found(src, quote('the words after')), 'the words after');
  assert.equal(found(src, quote('jar')), '`🍯 jar`');
  const p = project(src);
  assert.equal(p.text.length, p.starts.length);
  assert.equal(p.text.length, p.blocked.length);
});

test('a table cell with an escaped pipe: the pipe and its backslash stay together', () => {
  const src = '| k | v |\n|---|---|\n| a \\| b | c |\n';
  assert.equal(found(src, quote('|b')), '\\| b');
  assert.equal(found(src, quote('a|')), 'a \\|');
  assert.equal(found(src, quote('c')), 'c');
});

if (!process.exitCode) console.log(`${passed} passed`);
