# How budgetloop works

The depth that the [README](../README.md) only summarises: the loop, the guards
around it, and how the context budget spends and reclaims tokens.

## The loop, and the guards around it

```
[ system prompt ][ tool catalogue ][ task ] ──▶ provider.complete(messages, tools)
        ▲                                                     │
        │                                            tool calls?
 observations ◀── approval gate ◀── tool handler ─────────────┤
        │                                                     │
        └── budget check + compaction ◀──────────────────────┘     no ──▶ final answer
```

| Guardrail | When it triggers | Code |
| --- | --- | --- |
| Step cap | the run ends `step_limit`, with a message explaining the cap | `loop.py` |
| Token budget | the oldest tool exchange is summarised; if nothing is evictable, `budget_exceeded` | `context.py` |
| Workspace sandbox | escapes, absolute paths, symlinks and `.git/` raise `SandboxViolation` | `tools.py` |
| Approval gate | mutating tools need a policy; **no policy means refusal** | `tools.py` |
| Tool allow-list | only registered tools run, and `run_check` runs pre-registered argv only | `tools.py` |
| Output caps | oversized reads and observations are truncated with a visible notice | `tools.py` |
| Fail-closed network | the live provider needs `--allow-network` *and* a key | `cli.py` |
| Evidence | every run writes a trace; `inspect` reopens it later | `loop.py` |

## Context as a budget

`ContextWindow` is the part worth reading twice:

* **Everything is charged.** Each message reports its own token cost, framing
  overhead included, so the loop decides *before* it overflows.
* **Compaction is grouped, not line-by-line.** A tool request is welded to the
  observations it produced; evicting half an exchange would leave a transcript a
  real provider rejects, so eviction happens per exchange.
* **Some things are never evicted** — the system prompt, the current task, the
  newest exchanges — and if the protected set alone exceeds the budget, the run
  stops loudly rather than quietly forgetting the task.
* **Eviction is lossy and honest.** Dropped messages become one bounded excerpt
  summary (capped, so compaction can never *grow* the window), and the ledger
  records what was dropped and what it reclaimed.

A bucket just overflows. A budget tells you what you spent and what you gave up.

## Reading order

1. `src/budgetloop/loop.py` — how a turn is assembled, and when it stops.
2. `src/budgetloop/context.py` — what the budget does when it fills.
3. `src/budgetloop/tools.py` — what the agent may touch, and who approves it.
4. `tests/test_loop.py` and `tests/test_context.py` — the guarantees as assertions.
5. `AGENTS.md` — how to work in this repository if you are an agent.


## Where to look in the code

- `src/budgetloop/loop.py` assembles each turn and decides when to stop.
- `src/budgetloop/context.py` is the budget: charging, eviction, ledger.
- `src/budgetloop/tools.py` is the sandbox and the approval gate.
- `src/budgetloop/providers.py` is the one-method seam to a model.
- Working in this repository: [AGENTS.md](../AGENTS.md).
- The Lab 2 measurement: [LAB2-BEFORE-AFTER.md](LAB2-BEFORE-AFTER.md).
