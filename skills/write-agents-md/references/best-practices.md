# AGENTS.md best practices

Condensed from the AGENTS.md project (https://agents.md/) and the Agent Skills
specification (https://agentskills.io/specification). Both are open standards;
prefer their wording when they disagree with this summary.

## What AGENTS.md is for

A README is written for humans deciding whether to use a project. An AGENTS.md is
written for an agent that has already decided to change it. The difference is
operational: commands, checks, conventions, traps -- the context that clutters a
README but that an agent cannot infer from the code.

## The rules that matter most

1. **No required fields.** It is standard Markdown. Any headings work; the agent
   reads the text. Structure exists to help the reader, not to satisfy a schema.
2. **Nearest file wins.** Agents read the closest AGENTS.md in the directory tree.
   In a monorepo, put one at the root and one per package; a package file
   overrides the root for files under it.
3. **An explicit user prompt overrides everything.** Do not try to use AGENTS.md
   as a hard constraint; use it to make the *correct* path the easy path.
4. **Agents will run what you write.** Listing a test command means the agent will
   attempt it and try to fix failures before finishing. Never list a command that
   destroys state, and never list a command you have not run.
5. **Living document.** Update it when the commands change; a stale AGENTS.md is
   worse than none, because it is followed confidently.

## Sections that earn their place

| Section | Why it earns the lines |
| --- | --- |
| One-line description + layout | Stops the agent guessing where things live |
| Setup commands | The most common source of wasted agent turns |
| Checks ("done" means) | Gives the agent a stop condition it can verify |
| Code style | The 20% a formatter cannot enforce |
| Testing instructions | How to run one test, not just all of them |
| Guardrails | Prohibitions that prevent the expensive mistakes |
| PR / commit conventions | Cheap to state, annoying to fix later |
| Known traps | The two or three things that bite everyone once |

## Precedence, migration and neighbours

* Existing `AGENT.md` files: rename to `AGENTS.md` and symlink for compatibility
  (`mv AGENT.md AGENTS.md && ln -s AGENTS.md AGENT.md`).
* Some clients read a vendor-specific file as well (for example `CLAUDE.md`, or
  `.github/copilot-instructions.md`). Keep one source of truth and point the
  others at it, rather than duplicating instructions that will drift apart.
* Aider users can set `read: AGENTS.md` in `.aider.conf.yml`; Gemini CLI accepts
  `{"context": {"fileName": "AGENTS.md"}}` in `.gemini/settings.json`.

## What not to put in it

* Secrets, tokens, or internal URLs that do not belong in a repository.
* Aspirational rules nobody follows -- mark goals as goals or leave them out.
* Long prose about architecture: link to the documents that already exist.
* Anything the linter already enforces, beyond a pointer to the linter.

## Keeping it honest

The failure mode of AGENTS.md is drift: the file says `make check`, the target was
renamed three months ago, and every agent run wastes its first minute. Cheap
insurance:

* Put the check command in CI too, so the file and reality fail together.
* Re-read the file when you change the build, not later.
* When an agent gets something wrong that the file should have prevented, that is
  a bug in the file: fix it then, while you still remember the case.
