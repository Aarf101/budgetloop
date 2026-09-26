# Budget report

Run: `python skills/report-token-budget/scripts/report_budget.py <trace>`

## Verdict

`{status}` — peak `{peak}` of `{usable}` usable tokens (`{pct}`),
final `{final}`, headroom `{headroom}`.

## Where it went

* Biggest step: step `{n}` (`{growth}` tokens) — `{why this step grew}`.
* Biggest tool: `{tool}` (`{chars}` observation chars across `{calls}`).
* Compaction: `{none | dropped X message(s), net Y tokens reclaimed}`.

## Recommendation

* `{raise the budget to N | shrink the task | read fewer bytes per step}`,
  because `{the number that forces it}`.
