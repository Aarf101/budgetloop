# Budget fields

Where every number in the budget report comes from. All paths are
inside one trace JSON file: the envelope holds `task`, `provider`,
`budget` and `max_steps`, `result` holds the outcome, and
`result.context` holds the window state.

## Budget and headroom

| Report field | Trace path | Meaning |
| --- | --- | --- |
| `budget` | `budget` (or `result.context.budget`) | total tokens the run was allowed |
| `usable_budget` | `result.context.usable_budget` | budget minus the reserved output room |
| `reserve_output_tokens` | `result.context.reserve_output_tokens` | room kept for the final answer |
| `peak_tokens` | `result.peak_tokens` | highest window size reached; size against this |
| `final_tokens` | `result.final_tokens` | window size at the end; cheaper after compaction |
| `headroom_tokens` | `usable_budget - peak_tokens` | what was left at the tightest moment |
| `utilisation` | `peak_tokens / usable_budget` | pressure; above ~85% means raise the budget |

Counts are a 4-characters-per-token estimate. Comparable across runs,
never a vendor bill.

## Steps

`result.transcript` is one entry per turn with `step`, `note`,
`tool_calls` (each with `name`) and `window_tokens`. Growth is the
difference between consecutive `window_tokens`. The biggest positive
growth is the step to investigate first.

## Tools

Each transcript entry carries `observations` with `tool`, `ok` and
`chars`. The report sums `chars` per tool name; `ok: false` counts as
a failure but still occupies the window. `chars` measures observation
bytes, not tokens, so it ranks tools rather than pricing them.

## Compactions and the ledger

`result.context.compactions` holds one event per compaction: `step`,
`dropped_messages`, `dropped_tool_names`, `reclaimed_tokens`,
`summary_tokens`, `net_reclaimed` and `reason`. Net is reclaimed
minus summary cost; the summary itself occupies the window.
`result.context.ledger` is the append-by-append audit trail the
numbers were derived from.
