# Lab 2 — context engineering: the before/after

**The claim under test:** structure, not a smarter model, moves a workflow toward
reliable agentic engineering. Concretely: does giving the agent *good context* — an
`AGENTS.md` and an activated Skill — raise first-pass success on the same tasks,
with the same model, everything else held still?

**What was built for it**

| Artifact | Path | Role in the experiment |
| --- | --- | --- |
| Agent brief | [`AGENTS.md`](../AGENTS.md) | the treatment: setup, checks, style, guardrails, known traps |
| Reusable Skill | [`skills/write-agents-md/`](../skills/write-agents-md/SKILL.md) | the treatment: procedural knowledge, loaded on activation |
| Scorecard | [`experiment-scorecard.csv`](experiment-scorecard.csv) | raw data, one row per attempt |
| Scorer | [`scripts/score_experiment.py`](../scripts/score_experiment.py) | turns the scorecard into rates and a delta |

## The two arms

| | **cold** | **context** |
| --- | --- | --- |
| `AGENTS.md` in the repo | absent (rename it away) | present |
| Skill available | no | yes — `skills/write-agents-md/` reachable |
| Prompt | the bare task, one line, no hints | the bare task, one line, no hints |
| Everything else | same model, same tool permissions, same commit — *which the pilot broke, see the caveat* | same |

**Controls that matter.** Same starting commit for both arms; a fresh context for
every attempt (no memory of the previous arm); no human edits mid-attempt (an edit
ends the attempt and is recorded as a failure); and a counterbalanced third pass
(cold → context → cold) for any task whose result looks surprising, because you are
a better prompter the second time whether or not the treatment works.

## The task set

Three tasks, chosen so each needs a different kind of context to go right, and each
with **a check a machine can decide** — otherwise "first-pass success" is an opinion.

| Task | The prompt (one line) | The check that decides pass/fail |
| --- | --- | --- |
| **TASK-A** — conventions | "Add a check that fails if any module in `src/` is missing a docstring, and wire it into the existing checks." | `make check` green, *and* the rule enforced in `scripts/check_style.py` rather than a new one-off script |
| **TASK-B** — guardrails | "Prove that `make demo` can never write a file, and make that proof part of the checks." | a new test fails when the demo is handed `--yes`, and `make check` is green |
| **TASK-C** — standards | "Add a second skill that reports how a run's token budget was spent." | `python3 scripts/validate_skill.py skills/* --strict` is clean and `name` matches the directory |

Each is small enough for one session and large enough that a cold agent must guess
at least one thing it cannot read off the repository (TASK-A: which checker to
extend; TASK-B: that the demo is deliberately write-free; TASK-C: the frontmatter
rules).

## What gets measured

| Metric | Definition | Why it is in the table |
| --- | --- | --- |
| **First-pass success** | the task's check is green, the human edited none of the agent's code, and at most one clarification prompt was needed | the headline number; the strict "no help needed" bar |
| Check green | the command above exited 0 at the end of the attempt | separates "plausible" from "passes" |
| Human edits | times a human changed the agent's code before the check went green | the difference between *assisted* and *reliable* |
| Clarification prompts | times the human had to explain something twice | how much context the agent lacked |
| Agent turns | messages the agent took, failures included; `n/m` when it was not recorded | the cost side: a green run in 30 turns is not a win |

**Do not loosen the definition of first-pass success.** If the agent got there with
two hand-edits and a hint, the run is a failure with a note. That is the data.

## Protocol, in order

**Build both arms from one commit with the harness**, which is also how the next
person avoids the mistake described in the caveat below:

```bash
python3 scripts/run_experiment.py prepare --base 01bc767 --out /tmp/lab2-experiment
```

That clones the repository twice at the same revision — `context/` as it is,
`cold/` with the governance removed in a commit so no command can restore it — and
prints the prompts and the recording command. Then, by hand:

```bash
git status                      # in each arm, expect a clean tree
```

# 1. COLD arm
mv AGENTS.md /tmp/AGENTS.md.keep     # and make no skill reachable
#    run TASK-A, TASK-B, TASK-C — one fresh agent context per attempt
#    after each: record check_passed, human_edits, clarification_prompts, agent_turns
mv /tmp/AGENTS.md.keep AGENTS.md

# 2. CONTEXT arm
#    same three tasks, fresh context each, AGENTS.md present, skill reachable

# 3. score it
python3 scripts/score_experiment.py docs/experiment-scorecard.csv
```

Record into `docs/experiment-scorecard.csv` (the scorer skips `#` comments). Rows
for two arms on one task, one of them a hand-assisted failure:

```csv
arm,task_id,attempt,check_passed,human_edits,clarification_prompts,agent_turns,notes
cold,TASK-A,1,0,2,3,11,"invented a second linter instead of extending check_style.py"
context,TASK-A,1,1,0,0,4,"found scripts/check_style.py via AGENTS.md and extended it"
```

## Results

**Lead with the finding that is not in the table:** three early attempts to hold the
cold control *failed*, because the agent reconstructed or restored the treatment each
time. That is the durable result of this lab — a control arm is not a property of your
script, it is a property of everything the agent can reach. It is reported in full at
the end of this section.

### The study: TASK-A, both arms from one commit

Built with `scripts/run_experiment.py prepare --base 01bc767`, so the two arms differ
in exactly one thing: whether the governance files are present. One attempt per arm,
fresh agent session each, same one-line prompt.

| Arm | Attempts | First-pass | Check green | Turns | Human edits |
| --- | --- | --- | --- | --- | --- |
| cold (no `AGENTS.md`, no skill) | 1 | 1/1 (100%) | 100% | n/m | 0 |
| context (both present) | 1 | 1/1 (100%) | 100% | ≥ 10 | 0 |

**Both arms passed first try, with no human edits and no clarification prompts.** The
treatment did not change *correctness*. It changed what each run *maintained*:

| Dimension | cold (no brief) | context (brief present) |
| --- | --- | --- |
| Approach | extend `check_style.py`, add a `make` target, wire it into `check` | the same approach |
| Rule / target / flag | `src-docstring` / `make src-docstrings` / `--src-docstrings-only` | identical |
| Files changed | 6 (+127/−13) | 8 (+146/−23) |
| The brief | n/a — no `AGENTS.md` existed | `AGENTS.md` updated: the gate line, the new target, the test count, and the `run_check` allow-list entry |
| The reflection | n/a — absent | `REFLECTION.md` test counts corrected |
| Gate available | style + tests (118) | style + skills + tests (178) |

Read the last three rows together. The ungoverned run left the repository as it found
it, because nothing there recorded what the repository *is*. The governed run corrected
the brief that describes its own new gate, and the stale numbers elsewhere in the
documentation. **That work never shows up in a pass/fail column, and it is the work a
reviewer would otherwise do by hand.**

Limits of this study, stated plainly: one task, one attempt per arm. The gate counts
differ (118 vs 178) because the cold arm's gate drops the two checks whose subject
matter was removed with the governance — the code baseline is identical, the gate is
not. Turn counts are a floor for the context arm (`>10` in the session) and
unmeasured for the cold arm; the scorer reports that as `n/m`, because "not measured"
and "zero" are different facts.

### The pilot (superseded, kept for the record)

Before the harness existed, a pilot pair ran on **TASK-D** — "Add a check that fails if
any test module under `tests/` lacks a docstring" — with the two arms starting from
*different* trees (123 and 193 tests), because at that point TASK-A, TASK-B and TASK-C
had already been implemented in the repository and no longer worked as experiments. The
study above avoids both problems by starting from a base commit where the tasks are open
and by building both arms from it.

| Arm | Attempts | First-pass | Check green | Turns | Human edits |
| --- | --- | --- | --- | --- | --- |
| cold | 1 | 1/1 (100%) | 100% | n/m | 0 |
| context | 1 | 1/1 (100%) | 100% | n/m | 0 |

Its one observation, which the study strengthened rather than replaced: the cold run
named its gate `test-docstrings`, breaking the repository's existing `src-docstrings`
pattern, and had no brief to update. One observation, one task, mismatched bases — which
is why the numbers above are not used anywhere in this document's conclusions. Rows:
`docs/experiment-scorecard.csv` (pilot), `docs/study-scorecard.csv` (study).

### The bigger finding: the control condition is not achievable

Three earlier attempts to run TASK-A/B/C cold failed in three different ways, and
those failures are the most interesting result of this lab:

| Attempt | How the control leaked |
| --- | --- |
| TASK-A | the agent wrote `AGENTS.md` from memory before starting the task |
| TASK-B | the agent recreated it again, despite an explicit instruction not to touch docs |
| TASK-C | the whole governance layer (brief, skill, docs) reappeared mid-run, byte-identical to HEAD, from git history or editor state |

The fourth attempt fixed the method rather than the agent: the cold repository was
rebuilt with the governance files removed **from git history**, so no command could
restore them, and the README no longer pointed at files that did not exist. That run
stayed cold — no brief appeared — and it is the one reported above.

That is the durable lesson of this lab: when you measure an agent, you are measuring
the agent *plus everything it can reach*. Some of the context lives in the model, and
no file operation removes it.

The scorer for this table:

```bash
python3 scripts/score_experiment.py docs/experiment-scorecard.csv
```


## The reproducible half: the same question, offline

The session experiment above needs two tools and a human. `budgetloop` can show the
*mechanism* deterministically, in about a second, with no model at all:

```bash
# cold: a thin system prompt, no project knowledge
PYTHONPATH=src python3 -m budgetloop.cli run --task "Summarise the README" \
    --script scripts/example-run.json --json

# context: the project brief appended to the system prompt
PYTHONPATH=src python3 -m budgetloop.cli run --task "Summarise the README" \
    --script scripts/example-run.json --brief AGENTS.md --json
```

Compare `context.tokens`, `context.peak_tokens` and `context.ledger` in the two
results. The briefed run carries more in, so it spends more of its budget before it
acts: **context buys reliability with budget, and the ledger is where you see the
price.** This is a demonstration of the mechanism, not the experiment itself — the
verdict comes from real sessions.

## Threats to validity (put these in the write-up)

1. **n is small and the design is one cell, not six.** The study is one task with one
   attempt per arm; the reviewer's design was three tasks with two attempts each. Two
   runs are an anecdote with arithmetic attached. The pilot's base-commit defect is fixed
   — both arms now come from one revision via `scripts/run_experiment.py prepare` — but
   the *gate* still differs between arms (118 tests cold, 178 with the governance)
   because the cold arm drops the checks whose subject matter was removed. Same code
   baseline, different gate: say so whenever you quote the numbers.
2. **Model and tool drift.** A different model version, or a changed permission set,
   invalidates the comparison. Record the tool, the model and the commit hash.
3. **Learning effects.** You prompt better on the second arm. Counterbalance, or
   state which way you believe the bias pushes.
4. **Confounded treatment.** `AGENTS.md` and the skill are two changes at once. If
   the delta is large, re-run with only the skill to separate them.
5. **Check blindness.** The agent can read the check. A green check means the agent
   satisfied it, not that the change is good — which is why the per-task table's
   last column asks what the difference was about, in words.

## Conclusion

> First-pass success did not move: 1/1 in both arms of the study, 1/1 in both arms of the
> pilot, with no human edits and no clarification prompts in any of the four runs. What
> moved was *maintenance*. In the study the two arms converged on the same design — same
> rule name, same `make` target, same flag — and the governed run went further: it
> corrected the brief that describes its own new gate, and the test counts elsewhere in
> the documentation that its change had made stale. In the pilot, where the arms started
> from different trees, the ungoverned run invented a gate name that broke the
> repository's existing `src-docstrings` pattern and left nothing documented.
>
> The stronger result is methodological: three attempts to hold the cold control failed,
> because the agent reconstructed or restored the treatment each time. The fourth attempt
> fixed the *method* — governance removed from git history, not merely from the working
> tree — instead of asking the agent to behave, and that arm stayed cold. Measuring an
> agent means measuring the agent plus everything it can reach.
>
> The honest summary: with one task and one attempt per arm, this is not a study of the
> model. What it supports is narrower and still useful — in this repository, the context
> bought documentation upkeep and convention adherence, not raw capability. For rates,
> run the harness over three tasks × two attempts and publish that table.

