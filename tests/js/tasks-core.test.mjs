/**
 * Behavioral tests for static/js/tasks-core.js — the pure half of the Tasks
 * view: grouping the operation registry for the picker (by provider) and
 * filtering the queue. Dependency-free: node's assert; run by
 * tests/test_js_behavior.py (skipped when node is absent).
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const read = (rel) => readFileSync(new URL(`../../control-plane/static/${rel}`, import.meta.url), 'utf8');
const ctx = vm.createContext({});
ctx.window = ctx;
vm.runInContext(read('js/tasks-core.js'), ctx);
const core = ctx.tasksCore;
assert.ok(core, 'tasksCore must load');
// Arrays built inside the vm context have their own Array prototype;
// compare structure, not identity.
const plain = (v) => JSON.parse(JSON.stringify(v));

let passed = 0;
function test(name, fn) {
  try { fn(); passed += 1; }
  catch (err) { console.error(`FAIL ${name}\n`, err); process.exitCode = 1; }
}

const op = (key, provider, group) => ({ key, provider, group, label: key });
const OPS = [
  op('wiki-ingest', 'obsidian', 'Research'),
  op('wiki-lint', 'obsidian', 'Wiki'),
  op('rs-deep-list', 'resman', 'Research'),
  op('run-prompt', 'adhoc', 'Custom'),
  op('run-shell', 'adhoc', 'Custom'),
  op('x-odd', 'adhoc', 'Odd'),
];
const PROVIDERS = [
  { id: 'obsidian', label: 'claude-obsidian' },
  { id: 'resman', label: 'resman skills' },
  { id: 'adhoc', label: 'ad hoc' },
];

test('groups follow Research, Wiki, Custom, then unlisted groups', () => {
  assert.deepEqual(plain(core.orderedOpGroups(OPS, '')), [
    ['Research', ['wiki-ingest', 'rs-deep-list']],
    ['Wiki', ['wiki-lint']],
    ['Custom', ['run-prompt', 'run-shell']],
    ['Odd', ['x-odd']],
  ]);
});

test('a provider filter keeps only its operations and drops empty groups', () => {
  assert.deepEqual(plain(core.orderedOpGroups(OPS, 'resman')), [['Research', ['rs-deep-list']]]);
  assert.deepEqual(plain(core.orderedOpGroups(OPS, 'nobody')), []);
});

test('the default operation is the first card, per filter', () => {
  assert.equal(core.firstOpKey(OPS, ''), 'wiki-ingest');
  assert.equal(core.firstOpKey(OPS, 'adhoc'), 'run-prompt');
  assert.equal(core.firstOpKey(OPS, 'nobody'), 'wiki-ingest');   // falls back to the first op
  assert.equal(core.firstOpKey([], ''), null);
});

test('byKey indexes the registry', () => {
  assert.equal(core.byKey(OPS)['wiki-lint'].group, 'Wiki');
  assert.deepEqual(plain(core.byKey([])), {});
});

test('the source switch offers only providers that have operations', () => {
  const some = OPS.filter((o) => o.provider !== 'resman');
  assert.deepEqual(core.providersWithOps(PROVIDERS, some).map((p) => p.id), ['obsidian', 'adhoc']);
  assert.deepEqual(core.providersWithOps(PROVIDERS, OPS).map((p) => p.id), ['obsidian', 'resman', 'adhoc']);
});

test('provider short labels, unknown for anything else', () => {
  assert.equal(core.providerShort('obsidian'), 'obsidian');
  assert.equal(core.providerShort('adhoc'), 'ad hoc');
  assert.equal(core.providerShort('unknown'), 'unknown');
  assert.equal(core.providerShort(undefined), 'unknown');
});

const NOW = Date.parse('2026-09-24T12:00:00Z');
const task = (id, extra) => ({
  id, vault: 'alpha', priority: 'high', state: 'completed', provider: 'obsidian',
  updated_at: '2026-09-24T11:00:00Z', ...extra,
});
const TASKS = [
  task('t1', { state: 'running' }),
  task('t2', { state: 'completed', provider: 'adhoc', priority: 'low' }),
  task('t3', { state: 'failed', updated_at: '2026-09-20T11:00:00Z', vault: 'beta' }),
  task('t4', { state: 'scheduled', vault: 'ALL' }),
  task('t5', { state: 'completed', provider: undefined }),
];
const ids = (list) => list.map((t) => t.id);

test('the active bucket keeps running, pending, deferred, scheduled', () => {
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'active', now: NOW })), ['t1', 't4']);
  assert.deepEqual(ids(core.filterTasks(TASKS, { now: NOW })), ['t1', 't4']);   // default
});

test('recent is anything updated in the last 24h; all is everything', () => {
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'recent', now: NOW })), ['t1', 't2', 't4', 't5']);
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'all', now: NOW })), ['t1', 't2', 't3', 't4', 't5']);
});

test('priority, vault (plus ALL) and source filters combine', () => {
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'all', priority: 'low' })), ['t2']);
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'all', vault: 'beta' })), ['t3', 't4']);
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'all', provider: 'adhoc' })), ['t2']);
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'all', provider: 'unknown' })), ['t5']);
  assert.deepEqual(ids(core.filterTasks(TASKS, { state: 'all', provider: 'obsidian', vault: 'alpha' })), ['t1', 't4']);
});

test('filtering never mutates the input', () => {
  const before = JSON.stringify(TASKS);
  core.filterTasks(TASKS, { state: 'active' });
  assert.equal(JSON.stringify(TASKS), before);
});

console.log(`${passed} passed`);
