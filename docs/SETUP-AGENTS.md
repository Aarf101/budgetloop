# Setting up a coding agent (Claude Code and Codex CLI)

Lab 1 asks you to place your workflow on a spectrum, which is easier to judge once
you have driven more than one agent. This page is the two-minute version of the
official setup docs, plus the part that actually matters: **where each tool looks
for the context you are about to write.**

Install commands change. Verify what you have with `--version`, and when a command
here has drifted, follow the linked official page — that is the source of truth.

## Where your context goes (the part worth memorising)

| What you write | Who reads it | Where it lives |
| --- | --- | --- |
| `AGENTS.md` | Codex, and a growing list of other agents | repository root (or the nearest one in the tree); the closest file wins |
| `CLAUDE.md` | Claude Code | repository root or `~/.claude/`; supports `@path` imports, so one line pointing at `AGENTS.md` avoids duplicating instructions |
| `SKILL.md` | Claude Code and other Agent Skills clients | project: `.claude/skills/<name>/SKILL.md` — personal: `~/.claude/skills/<name>/SKILL.md` |
| Slash commands | Claude Code | `.claude/commands/<name>.md` (these now behave like skills) |

This repository uses all four: [`AGENTS.md`](../AGENTS.md) is the source of truth,
[`CLAUDE.md`](../CLAUDE.md) imports it, the skill lives in
[`skills/write-agents-md/`](../skills/write-agents-md/SKILL.md), and
`.claude/skills/write-agents-md` is a symlink so Claude Code discovers it without a
copy step. Edit the file under `skills/`; never edit through the symlink.

## Claude Code

**Install** (pick one — the native installer is the recommended path):

```bash
# macOS, Linux, WSL
curl -fsSL https://claude.ai/install.sh | bash

# Homebrew
brew install --cask claude-code

# npm (if you prefer to manage it yourself)
npm install -g @anthropic-ai/claude-code
```

Requirements: macOS 13+, Windows 10 1809+ or a current Linux (Ubuntu 20.04+,
Debian 10+), 4 GB RAM, an internet connection. `ripgrep` ships with it.

**Authenticate and run:**

```bash
cd /path/to/your/project
claude            # first run walks you through signing in
claude --version  # prove which build you are on
```

**Drive it from an editor** if the terminal is not your home: the VS Code extension
and the JetBrains plugin both run the same agent against the same files.

**How skills behave here**, and why it is a context story: a skill's *body* loads
only when the skill is activated, so a long reference costs nothing until you need
it. After auto-compaction, Claude Code re-attaches recently invoked skills within a
budget — the first 5,000 tokens of each, 25,000 tokens combined. If a skill stops
influencing behaviour after a long session, that budget is usually why. This is the
same trade your Lab 1 prototype makes in `ContextWindow.compact`, at a smaller
scale: **loading is free, keeping is not.**

## Codex CLI

**Install** (Node 18+ for the npm route):

```bash
npm i -g @openai/codex
# or, on macOS
brew install codex
```

**Sign in and start:**

```bash
cd /path/to/your/project
codex                 # then choose "Sign in with ChatGPT"
```

**Generate the scaffold.** Inside a project, `/init` writes an `AGENTS.md`
scaffold — which is exactly the Lab 2 deliverable. Treat its output as a draft:
the skill in this repository exists because a scaffold does not know which
commands actually run.

```text
/init            # creates AGENTS.md for this repository
/permissions     # decide when it may edit files or run commands without asking
```

Useful to know before you trust it with a real repository:

```bash
codex resume      # return to a saved session
codex review      # review uncommitted changes or a branch
codex mcp         # connect external tools
```

**Plan details:** Codex is included in ChatGPT's Free plan, and the free allowance
is modest — enough for these labs, not for all-day use. The exact limits change
over time; check
[Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan).
Docs: [developers.openai.com/codex/cli](https://developers.openai.com/codex/cli).

## A ten-minute exercise in this repository

1. Start your agent in this repo and ask: *"What command decides whether a change
   is done, and how long does it take?"* A resumed answer naming `make check` and
   roughly half a second means it read `AGENTS.md`.
2. Ask it to explain `ContextWindow.compact` **without reading the file first**, and
   watch it refuse to guess — that refusal is the guardrail from Lab 1 working.
3. Move `AGENTS.md` aside and ask question 1 again. Note the difference in the
   answer; that difference is the data Lab 2 asks you to measure.
4. Ask the agent to run the `write-agents-md` skill on a *different* repository you
   own, and read what it produced before you trust it.

## If the agent ignores your context

| Symptom | Usual cause | Fix |
| --- | --- | --- |
| Never mentions your commands | the file is not where that tool looks | root of the repo, exact filename, and for Claude Code check the `@AGENTS.md` import in `CLAUDE.md` |
| Follows it once, then drifts | the instruction was in context but the model chose otherwise | make the rule enforceable (a check) rather than aspirational |
| Skill never activates | the `description` does not describe *when* to use it | rewrite the description; it is the text the agent matches against |
| Skill activates too often | description too broad | narrow it, and add concrete triggers |
| Works locally, fails in CI | the file assumes a tool that CI lacks | record the environment requirement in `compatibility`, or make the command conditional |
