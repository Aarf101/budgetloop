# CLAUDE.md

This repository keeps one source of truth for agent instructions: `AGENTS.md`.

@AGENTS.md

Claude-specific notes that are not in AGENTS.md:

- Skills in this repository live in `skills/<name>/SKILL.md`, and
  `.claude/skills/<name>` is a symlink to each of them, so Claude Code discovers
  them without a copy step. Edit the file under `skills/`, never the symlink.
- `make check` is the gate. It must be green before you report a change as done.
- Never run the live provider (`--provider anthropic`); the offline providers cover
  every test and demo in this repository.
