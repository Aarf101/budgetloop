# budgetloop

> A tiny agent loop that treats **context as a budget, not a bucket**.

`budgetloop` is a dependency-free Python harness that runs a coding agent's
perceive → plan → act → observe → iterate loop under four properties:

1. it **cannot run forever** — `--max-steps` is a hard stop;
2. it **cannot silently overspend context** — the window has a budget and a ledger;
3. it **cannot touch anything it was not given** — tools are declared, sandboxed and gated;
4. it **leaves evidence** — every run can be written to a JSON trace you can reopen.

It is small enough to read end to end in one sitting, and strict enough that its
guarantees are covered by tests.

## Why this exists

This repository is the Lab 1 prototype and the Lab 2 context-engineering target
for an agentic-engineering course. The prototype is deliberately modest; the
interesting part is that every design decision here is one a real agent framework
makes somewhere — and each one is a place where "vibe" and "production" diverge.

The two questions the labs ask of this code are:

* **Lab 1:** where on the spectrum (vibe → structured → agentic) does my workflow
  sit, and why? → [REFLECTION.md](REFLECTION.md)
* **Lab 2:** does giving the agent *better context* (not a better model) change
  first-pass success? → [docs/LAB2-BEFORE-AFTER.md](docs/LAB2-BEFORE-AFTER.md)

## Quickstart

```bash
make demo     # offline, no API key: a scripted run that compacts and refuses a write
make test     # the unit test suite (stdlib unittest, no pytest required)
make check    # style + skill validation + tests: the gate before "done"
make run TASK='summarise the README'   # drive the loop once, offline
```

Nothing here needs the network, an API key, or a package install: Python 3.10+
and the standard library. To use a live model instead:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m budgetloop.cli run --provider anthropic --allow-network \
    --model claude-sonnet-4-5 --brief AGENTS.md --task "Explain the compaction policy"
```

The live provider is opt-in twice over (a flag *and* a key), because "the agent
suddenly called a paid API" should never be an accident.

## The loop

```
 task
  │
  ▼
 [ system prompt ][ tool catalogue ][ task ]        ← what the window starts with
  │
  ▼
 provider.complete(messages, tools) ──────────────── PERCEIVE
  │
  ├── no tool calls ──▶ final answer ──────────────── PLAN (stop condition)
  │
  └── tool calls ──▶ approval gate? ──▶ tool handler ─ ACT
                          │ denied              │
                          ▼                     ▼
                    ERROR observation ──▶ context window ── OBSERVE
                          │                     │
                          └─────────┬───────────┘
                                    ▼
                        budget check + compaction ──── ITERATE
```

Guards, and where they live:

| Guardrail | Behaviour when it triggers | Code |
| --- | --- | --- |
| Step cap | run ends `step_limit`, answer explains why | `loop.AgentLoop.run` |
| Token budget | oldest tool exchanges are summarised; if nothing is evictable the run ends `budget_exceeded` | `context.ContextWindow.append` |
| Workspace sandbox | path escapes, absolute paths and `.git/` raise `SandboxViolation` | `tools.Workspace.resolve` |
| Approval gate | mutating tools need an explicit policy; **no policy means refusal** | `tools.ToolRegistry.run` |
| Tool allow-list | only registered tools run, and `run_check` only runs pre-registered argv | `tools.default_registry` |
| Output caps | oversized reads/observations are truncated with a visible notice | `tools.Workspace.read_file` |
| Fail-closed network | the live provider needs `--allow-network` *and* a key | `cli.build_provider` |
| Evidence | every run writes a JSON trace (`inspect` reopens it) | `loop.AgentLoop._record_trace` |

## Context as a budget

`ContextWindow` is the part worth reading twice:

* **Everything is charged.** Each message reports its own token cost, including a
  per-message framing overhead, so the loop decides *before* it overflows.
* **Compaction is grouped, not line-by-line.** An assistant turn that requests
  tools is welded to the observations it produced: evicting half an exchange would
  leave a transcript a real provider rejects, so eviction happens per exchange.
* **Some things are never evicted.** The system prompt, the current task, and the
  newest tool exchanges are protected. If the protected set alone exceeds the
  budget the run stops loudly instead of quietly forgetting the task.
* **Eviction is lossy and honest.** Dropped messages become one bounded excerpt
  summary (capped in tokens, so compaction can never *grow* the window), and every
  eviction is recorded in the ledger with what was dropped and what it reclaimed.
* **The ledger is the artifact.** `make demo` prints it; the trace file stores it.

That is the whole point of the lecture's phrase: a bucket just overflows, a budget
tells you what you spent and what you gave up.

## Tools and the sandbox

Four tools ship by default:

| Tool | Mutates | Notes |
| --- | --- | --- |
| `read_file` | no | UTF-8 only, refuses binary, truncated at `max_read_bytes` |
| `list_files` | no | glob, capped entry count, skips `.git/`, `.venv/`, `node_modules/` |
| `write_file` | **yes** | needs an approval policy; supports create and overwrite |
| `run_check` | no | runs one **pre-registered** argv command by name — no shell, no arbitrary command |

`run_check` is the interesting one. The model cannot invent a command; a human
registered `tests` (see `tools.default_checks`) and the model may only ask for
that. "The agent can run the tests" and "the agent can run anything" are different
features, and only the first one belongs in a loop you leave unattended.

## Providers

| Provider | Network | Purpose |
| --- | --- | --- |
| `scripted` | no | replays a JSON script (`scripts/example-run.json`); deterministic runs and CI |
| `echo` | no | reports what it can see and stops; a smoke test |
| `anthropic` | yes, opt-in | the real thing, over HTTPS with `urllib` (no SDK dependency) |

The provider seam is one method wide, which is why the entire test suite can
exercise the guards — budget, eviction, refusals, termination — with zero network
calls and zero cost. `to_anthropic_messages()` is unit-tested separately, so the
wire format is verified even when no key is present.

## Repository layout

```
.
├── AGENTS.md                     # Lab 2: the README for agents (setup, tests, guardrails)
├── REFLECTION.md                 # Lab 1: the 5-question workflow rubric, answered
├── README.md                     # this file (for humans)
├── Makefile                      # test / demo / check / run
├── docs/
│   ├── LAB2-BEFORE-AFTER.md      # Lab 2: the measurement protocol and results table
│   └── SETUP-AGENTS.md           # installing and driving Claude Code / Codex CLI
├── scripts/
│   ├── check_style.py            # house style rules (a real check, not a suggestion)
│   ├── validate_skill.py         # validates skills/* against the Agent Skills spec
│   ├── score_experiment.py       # turns the before/after scorecard into rates
│   └── example-run.json          # the scripted run behind `make run`
├── skills/
│   └── write-agents-md/          # Lab 2: one reusable Skill
│       ├── SKILL.md
│       ├── scripts/scan_repo.py
│       ├── references/best-practices.md
│       └── assets/AGENTS.md.template
├── src/budgetloop/
│   ├── messages.py               # roles, tool calls, per-message token accounting
│   ├── context.py                # the budget window: ledger + grouped compaction
│   ├── tools.py                  # tool registry, workspace sandbox, approval gate
│   ├── providers.py              # scripted / echo / live Anthropic provider
│   ├── loop.py                   # the loop itself, with the stopping conditions
│   └── cli.py                    # run / demo / inspect
└── tests/                        # unittest suite covering every guardrail
```

## Reading order for a newcomer

1. `src/budgetloop/loop.py` — how a turn is assembled and when it stops.
2. `src/budgetloop/context.py` — what the budget does when it fills.
3. `src/budgetloop/tools.py` — what the agent is allowed to touch, and who approves it.
4. `tests/test_guardrails.py` — the guarantees, written as assertions.
5. `AGENTS.md` — how to work in this repo if you are an agent.

## Scope and limitations (honest list)

* Token counts are a **4-characters-per-token estimate**, not a real tokenizer.
  Deliberately conservative and deliberately dependency-free; the ledger is
  comparable across runs, not identical to a vendor's bill.
* Compaction summarises by **truncated excerpt**, not by asking a model to
  summarise. It is cheap, deterministic and auditable — and lossy in a way a
  model-written summary would not be.
* The sandbox defends against path escapes and accidental damage, **not** against
  a hostile model: a granted write can still overwrite a file you wanted.
* The live provider has been exercised against the payload builder and error
  paths, not against a paid account in this repository. Model ids rotate; pass
  `--model` or set `BUDGETLOOP_MODEL`.
* Single-process, single-agent, in-memory state. No persistence, no parallelism,
  no MCP. Those are the next rungs, not this rung.

