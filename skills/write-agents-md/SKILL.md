---
name: write-agents-md
description: Write or refresh a repository's AGENTS.md by discovering its real setup, test, lint and style commands and recording them where coding agents will read them. Use when a repository has no AGENTS.md, when the existing one is stale, vague or aspirational, or when an agent keeps repeating the same setup mistakes.
license: MIT
compatibility: Requires git and a POSIX shell; scripts/scan_repo.py needs Python 3.10+. No network access.
metadata:
  author: budgetloop labs
  version: "1.0"
allowed-tools: Bash(git:*) Bash(python:*) Read Write
---

# Write an AGENTS.md

AGENTS.md is a README for agents: setup commands, checks, style rules and
guardrails that a newcomer cannot infer from the code. Its value is not the prose.
It is that **every line is true and runnable**, because the agent will run it.

## Non-negotiables

1. **Run every command before you write it down.** A command that was not executed
   from a clean checkout is a rumor. If it fails, either fix the command or record
   the failure and the workaround.
2. **One canonical command per job.** `make check` beats a four-item menu, because
   an agent will pick the cheapest-looking item on a menu.
3. **Guardrails are prohibitions, not aspirations.** "Never edit files under
   `src/gen/`; regenerate with `make gen`" is followable. "Keep the code clean" is not.
4. **Never invent.** Unknown → write `unknown — find out with <command>`.
5. **Budget the length.** Aim under ~150 lines. AGENTS.md is context the agent
   carries on *every* task; every line you add is charged to every future run.
6. **Do not put secrets in it.** Name the environment variable; never its value.

## Steps

### 1. Look before you write

```bash
python scripts/scan_repo.py .
```

The scan reports marker files, existing agent files, CI workflow commands,
candidate test directories, the newest commits and a suggested command set. Treat
the suggestions as hypotheses to verify, never as facts.

### 2. Verify each command by running it

Run, at minimum: dependency install (if any), the test command, the lint/format
command, and the "check everything" command. Capture the exact invocation that
worked, including the working directory and any environment variable. Note the
runtime: an agent that knows `make check` takes 90 seconds will not run it after
every keystroke.

### 3. Write the file, in this order

Order matters more than wording: agents read top-down and stop when they have what
they need.

1. **What this is** — one line, plus the layout in five lines or fewer.
2. **Setup** — exact commands, from clean checkout to first test run.
3. **Checks** — the command(s) that decide "done", and what they cover.
4. **Code style** — rules a linter cannot enforce; point at the ones that are enforced.
5. **Guardrails** — what must never happen, and how to recover when it does.
6. **PR / commit conventions** — only if they exist.
7. **Known traps** — the two or three things that bite everyone once.

Start from `assets/AGENTS.md.template` so no section is silently forgotten.

### 4. Check yourself before finishing

* Would a competent newcomer, following only this file, reach a green test run on
  the first try?
* Does every command exist, and is it the one CI actually runs?
* Is every guardrail stated as something an agent can *do* or *avoid*?
* Is anything in the file aspirational rather than true? Mark those explicitly as
  goals, or delete them.
* Run the file's own check command one last time. Then run `git diff` and read it.

## Output shape

A single `AGENTS.md` at the repository root, standard Markdown, no required
fields. For a monorepo, add a nested `AGENTS.md` per package: agents read the
nearest file in the directory tree, so the closest one wins.

## Edge cases

* **No build system** — say so in one line ("no build step; the modules run
  directly"), because silence makes agents invent one.
* **Commands that need a service** — record the dependency and a mock/test
  fallback (`make test-unit` runs without Docker).
* **Generated code** — name the generator command and forbid hand-edits; this is
  the most common agent-inflicted repository wound.
* **Conflicting instructions** — the nearest AGENTS.md wins, and an explicit
  prompt from the user overrides everything. Record your repo's exceptions.
* **An existing AGENTS.md** — diff its claims against reality first; keep the true
  lines, delete or fix the rest, and say what you changed.

## Reference

* [Best practices](references/best-practices.md) — the conventions and the
  precedence rules, condensed from the AGENTS.md project.
* [Template](assets/AGENTS.md.template) — the section skeleton this skill writes.
