// resman SPA — the pure half of the Tasks view: grouping the operation
// registry for the picker and filtering the queue. No DOM, no fetch, so
// tests/js/tasks-core.test.mjs runs it in node. app.js owns the rendering.
//
// The registry (GET /api/operations) is a list of operations, each with a
// `provider` (obsidian | resman | adhoc) — the source of its skill. Tasks
// carry the same `provider`, derived server-side; a task whose operation the
// registry no longer has reports "unknown". Nothing here spells an operation
// key (docs/design/17-skills.md).
(function (root) {
  "use strict";

  // Picker groups, top to bottom: the most-used research operations first,
  // then wiki maintenance, then the ad-hoc ones. Unlisted groups follow in
  // registry order.
  const GROUP_ORDER = ["Research", "Wiki", "Custom"];
  const ACTIVE_STATES = ["running", "pending", "deferred", "scheduled"];
  const PROVIDER_SHORT = { obsidian: "obsidian", resman: "resman", adhoc: "ad hoc" };

  function byKey(ops) {
    const map = {};
    for (const op of ops || []) map[op.key] = op;
    return map;
  }

  // The operations the picker shows: every one, or one provider's.
  function visibleOps(ops, provider) {
    return (ops || []).filter((op) => !provider || op.provider === provider);
  }

  // [[group, [opKey, …]], …] in display order, for one provider or all.
  function orderedOpGroups(ops, provider) {
    const groups = {};
    for (const op of visibleOps(ops, provider)) (groups[op.group] ||= []).push(op.key);
    const order = GROUP_ORDER.filter((g) => groups[g])
      .concat(Object.keys(groups).filter((g) => !GROUP_ORDER.includes(g)));
    return order.map((g) => [g, groups[g]]);
  }

  // The operation selected by default: the first card in display order.
  function firstOpKey(ops, provider) {
    const grouped = orderedOpGroups(ops, provider);
    if (grouped.length) return grouped[0][1][0];
    return ops && ops.length ? ops[0].key : null;
  }

  // Providers that have at least one operation, in the registry's order —
  // the source switch does not offer a source with nothing to pick.
  function providersWithOps(providers, ops) {
    const have = new Set((ops || []).map((op) => op.provider));
    return (providers || []).filter((p) => have.has(p.id));
  }

  function providerShort(id) {
    return PROVIDER_SHORT[id] || "unknown";
  }

  // The queue filters, all at once: priority, the selected vault (its tasks
  // plus every ALL-vault task), the source, and the state bucket
  // (active | recent (24h) | all).
  function filterTasks(tasks, f) {
    const filter = f || {};
    const now = filter.now || Date.now();
    let items = (tasks || []).slice();
    if (filter.priority) items = items.filter((t) => t.priority === filter.priority);
    if (filter.vault) items = items.filter((t) => t.vault === filter.vault || t.vault === "ALL");
    if (filter.provider) items = items.filter((t) => (t.provider || "unknown") === filter.provider);
    const bucket = filter.state || "active";
    if (bucket === "active") {
      items = items.filter((t) => ACTIVE_STATES.includes(t.state));
    } else if (bucket === "recent") {
      const cutoff = now - 24 * 3600 * 1000;
      items = items.filter((t) => {
        const updated = new Date(t.updated_at).getTime();
        return updated && updated >= cutoff;
      });
    }
    return items;
  }

  root.tasksCore = {
    GROUP_ORDER, ACTIVE_STATES, byKey, visibleOps, orderedOpGroups, firstOpKey,
    providersWithOps, providerShort, filterTasks,
  };
})(typeof window !== "undefined" ? window : globalThis);
