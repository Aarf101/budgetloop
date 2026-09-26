---
name: report-token-budget
description: Report how a run's token budget was spent from a trace file. Use when a run finished and you need to explain peak versus final tokens, which steps or tools cost the most, or what compaction reclaimed.
license: MIT
compatibility: Requires Python 3.10+ and a budgetloop trace JSON file. Stdlib only, no network access.
metadata:
  author: budgetloop labs
  version: "1.0"
allowed-tools: Read Bash(python:*)
---

# Report a token budget

A trace is the evidence a run left behind. This skill turns that
evidence into a one-page answer to "where did the budget go", so the
next run spends deliberately instead of by accident.

Token counts are a 4-characters-per-token estimate, never a vendor
invoice. Compare runs to each other, never to a bill.

## Non-negotiables

1. **Read the trace, never invent it.** Every number in the report
   comes from the trace file. An absent field is reported as absent.
2. **Peak decides, final merely remains.** Size budgets against `peak`,
   not `final`: compaction can make the final window look cheap.
3. **Name the most expensive step and tool.** A report without a
   biggest spender is a summary, not a diagnosis.
4. **Say what compaction cost and saved.** Dropped messages, reclaimed
   tokens, and the summary's own cost, or the state "no compaction".

## Steps

### 1. Locate the trace

```bash
ls -1t .budgetloop/traces | head -5
```

Traces are generated evidence. Never edit one; copy it first if you
must annotate. If there is no trace, re-run with a trace directory
instead of guessing.

### 2. Run the reporter, not your eyes

```bash
python skills/report-token-budget/scripts/report_budget.py <trace> [--json]
```

The script reads one trace and prints the whole breakdown: budget and
headroom, per-step growth, per-tool observation cost, and compactions.
Prefer `--json` when another tool consumes the answer.

### 3. Read the four sections in order

1. **Budget** — `budget`, `usable_budget`, `peak`, `final`, headroom,
   and utilisation. Over 85% peak utilisation means the next similar
   run needs a bigger budget or a smaller task.
2. **Steps** — which step grew the window most. A single huge jump
   points at one observation; steady growth points at the task size.
3. **Tools** — which tool's observations cost the most characters and
   which ones failed. Failures still occupy the window.
4. **Compactions** — what was summarised away and the net reclaimed.
   Zero compactions on a tight budget is a finding, not an omission.

Field meanings live in
[budget fields](references/budget-fields.md).

### 4. Write the verdict in one paragraph

Start from [the template](assets/report-template.md): status, peak
versus usable, the biggest step and tool, compaction net, and the one
budget change you recommend. One paragraph, numbers included.

## Output shape

A short report (terminal text or JSON with `--json`): header, budget,
steps, tools, compactions, verdict. No required schema beyond naming
the biggest spender and the compaction net.

## Edge cases

* **No compactions** — say so explicitly; do not leave the section out.
* **Budget-exceeded runs** — the overflow step is the finding. Report
  the fixed cost versus the observation that could not fit.
* **Failed observations** — they cost window space too. List them
  under tools, not as a footnote.
* **Hand-written or partial traces** — report which fields are absent
  rather than filling them with zeros.

## Reference

* [Budget fields](references/budget-fields.md) — where each number
  comes from in the trace, and what the ledger actions mean.
* [Report template](assets/report-template.md) — the verdict skeleton.
* Script: `scripts/report_budget.py` — the only reader you need.
