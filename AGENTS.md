# AGENTS.md

Agent-facing guide to this repository. Humans should start with
[README.md](README.md); the lab write-ups are [REFLECTION.md](REFLECTION.md) and
[docs/LAB2-BEFORE-AFTER.md](docs/LAB2-BEFORE-AFTER.md).

## What this is

`budgetloop` is a dependency-free Python agent loop that treats context as a
budget: perceive → plan → act → observe → iterate, with a hard step cap, a token
ledger, grouped compaction, a workspace sandbox, an approval gate and a JSON trace
per run. Layout:

- `src/budgetloop/` — the package: `messages`, `context`, `tools`, `providers`, `loop`, `cli`
- `tests/` — one module per boundary; `tests/test_style_tool.py` tests the checker itself
- `scripts/` — the repo's checks: `check_style.py`, `validate_skill.py`, `score_experiment.py`
- `skills/write-agents-md/` — the reusable Agent Skill (SKILL.md + scripts/ + references/ + assets/)
- `docs/` — the Lab 2 protocol and the agent-CLI setup notes
- `.budgetloop/traces/` — runtime artifacts, gitignored; never edit or commit them

## Setup

No install step and no dependencies: Python 3.10+ and the standard library.

```bash
python3 --version          # expect 3.10 or newer
make help                  # lists every entry point
make test                  # proves the checkout is healthy
```

`make` sets `PYTHONPATH=src` for you. If you invoke the package directly you must
do it yourself: `PYTHONPATH=src python3 -m budgetloop.cli --help`.

## Checks (done means these pass)

```bash
make check                 # the gate: style + skill validation + tests, in ~0.5s
make test                  # 177 tests: PYTHONPATH=src python3 -m unittest discover -s tests -t .
make style                 # python3 scripts/check_style.py .
make src-docstrings        # python3 scripts/check_style.py src --src-docstrings-only
make skill-lint            # python3 scripts/validate_skill.py skills/*
```

Narrower loops while working:

```bash
PYTHONPATH=src python3 -m unittest tests.test_loop -q                    # one module
PYTHONPATH=src python3 -m unittest tests.test_context.CompactionPolicyTests.test_oldest_exchange_is_evicted_first
python3 scripts/check_style.py src tests                                 # one subtree
python3 scripts/validate_skill.py skills/write-agents-md --strict        # warnings become errors
```

**Never claim a change is done while `make check` fails, and never delete, skip or
weaken a test to force it green.** If a check is wrong, fix the check and say why
in the commit message — the style checker's rules are code, not opinions.

## Code style

Enforced by `scripts/check_style.py`; the rest are conventions:

- 4-space indent, no tabs, no trailing whitespace, one trailing newline, 100 columns.
- Every module has a docstring; every public top-level class and function has one.
  Every module under `src/` is additionally covered by the `src-docstring` gate
  (`python3 scripts/check_style.py src --src-docstrings-only`, also available to
  the agent as `run_check` name `src-docstrings`).
- **Library code never prints.** `print` is allowed only in `src/budgetloop/cli.py`
  and under `scripts/`; the loop returns values and statuses instead of talking.
- Raise typed errors (`ToolError`, `SandboxViolation`, `ProviderError`,
  `ContextOverflow`), catch narrowly, never a bare `except`.
- No `TODO`/`FIXME` markers, no `NotImplementedError` left behind.
- Type hints on public functions; dataclasses for records; no new dependencies
  (adding one is a design change that needs a note in README.md).
- Comments explain *why*, not *what*.

## Guardrails

- **Stay in the repository.** File tools are confined to a workspace root; paths
  that escape it, absolute paths, and `.git/` raise `SandboxViolation`.
- **Mutating tools need approval.** No approval policy means refusal (fail closed).
  `--yes` is for a human-driven session, never for a test.
- **Never touch the network in tests.** The default providers are offline; the live
  provider requires both `--allow-network` and `ANTHROPIC_API_KEY`.
- **Keep the demo deterministic, offline and side-effect free.** `make demo` must
  never write a file; the refused write is part of what it demonstrates.
- **Do not edit `.budgetloop/`** — traces are generated evidence.
- **Skill frontmatter is validated, not decorative.** A skill's `name` must equal
  its directory name and match `[a-z0-9]+(-[a-z0-9]+)*`; run `make skill-lint`
  after touching anything under `skills/`.

## How this repository expects a change to be made

1. Read the module you are about to change, and the test module beside it.
2. Write or adjust the failing test first — tests here are the contract, and the
   interesting ones pin *failure* behaviour (refusals, limits, overflows).
3. Make it pass, then run `make check`.
4. Keep the change small and explain the *why* in the commit body. If you changed a
   rule instead of the code, say so explicitly and justify it.
5. Update `README.md` if behaviour changed, and `REFLECTION.md` only if a claim in
   it stopped being true.

## Skills

`skills/write-agents-md/` writes or refreshes an AGENTS.md for any repository:
scan it, verify the commands, then fill in the template. Use it when a repo has no
AGENTS.md, or when its file has drifted from reality. Validate skills with
`make skill-lint`; the conventions and precedence rules are in
`skills/write-agents-md/references/best-practices.md`.

## Known traps

- **`PYTHONPATH`.** `python3 -m budgetloop.cli` fails from a clean checkout without
  `PYTHONPATH=src`. `make` exports it, and the agent's `run_check` tool injects it.
- **The test suite is a package.** Run it from the repo root with `-t .`, exactly as
  `make test` does, or discovery fails with "start directory is not importable".
- **Budgets are estimates.** Token counts use a 4-characters-per-token heuristic;
  compare runs to each other, never to a vendor invoice.
- **The demo self-sizes its budget** from a measurement pass. If you change its
  script, re-run `make demo` and confirm it still reports at least one compaction.
- **Compaction is lossy by design.** Evicted tool exchanges become one bounded
  excerpt summary; if a test depends on an old observation surviving, it is wrong.

## Commits and pull requests

- Imperative subject under 72 characters; the body explains why, not what.
- Run `make check` before committing.
- Trailer your provenance if an agent generated the change (`Assisted-by: <tool>`).

