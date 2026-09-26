---
name: grilling
description: >
  Interview the operator relentlessly about a plan or design, one question at
  a time with a recommended answer for each, until there is a shared
  understanding. A helper the other resman skills call before they act; it
  writes no vault pages and has no operation of its own. Triggers on: grill
  me, grill this plan, grilling, stress-test the plan, interview me about the
  plan, walk the design tree.
metadata:
  role: helper
---

# grilling: interview the operator until the plan is shared

Interview the operator relentlessly about every aspect of the plan at hand
until you reach a shared understanding. Walk down each branch of the design
tree, resolving dependencies between decisions one by one. For each question,
provide your recommended answer.

Ask the questions **one at a time**, waiting for feedback on each question
before continuing. Asking multiple questions at once is bewildering.

If a question can be answered by exploring what is at hand, explore instead of
asking: in a vault that is `wiki/hot.md`, then `wiki/index.md`, then the pages
the question touches; in a repository it is the codebase.

## What this skill does not do

- It writes nothing: no page under `wiki/`, no `wiki/log.md` entry, no
  sidecar. The skill that called it does the writing, in its own conventions.
- It has no `settings.yaml` and no registry entry; it is never a task of its
  own. The Skills tab lists it under *Helpers*, never as a task.

## When another skill calls it

A resman skill that holds a plan (what it will write, in which order, with
which choices still open) may run this interview before it acts: state the
plan in a few lines, then follow the procedure above on that plan. In a
non-interactive run (`claude -p`, which is how resman tasks run) nobody
answers: take your recommended answer to every question, record the questions
and the answers taken where the calling skill reports its run (the page it
writes, or its `wiki/log.md` entry), and continue.
