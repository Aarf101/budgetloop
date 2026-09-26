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
| Everything else | same model, same starting commit, same tool permissions | same |

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

```bash
# 0. clean slate: the same starting point for both arms
git status                      # expect clean
git log -1 --oneline            # record this hash in the scorecard notes

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

Two runs, same task, same tool, one variable: whether `AGENTS.md` and the skill were
present. **TASK-A, TASK-B and TASK-C were all already implemented in this repository
before the experiment began**, so the pair below uses a fresh task of the same kind.

> **TASK-D** — "Add a check that fails if any test module under `tests/` lacks a
> docstring, and wire it into the existing checks."
>
> **Check:** `make check` green in that repository, *and* the rule implemented
> inside `scripts/check_style.py` rather than a new one-off script.

| Arm | Attempts | First-pass | Check green | Mean turns | Mean edits |
| --- | --- | --- | --- | --- | --- |
| cold (no `AGENTS.md`, no skill) | 1 | 1/1 (100%) | 100% | n/m | 0.0 |
| context (both present) | 1 | 1/1 (100%) | 100% | n/m | 0.0 |

Per task:

| Task | cold first-pass | context first-pass | What the difference was actually about |
| --- | --- | --- | --- |
| TASK-D | 1/1 | 1/1 | Both correct; the governed run stayed inside the repo's conventions and maintained the brief. See below. |

*n/m = not measured: these sessions ran through the editor's agent host, whose
transcripts are not readable from this machine, so the turn count was not recorded.
The scorer reports it as `n/m` rather than as 0, because "nobody measured it" and
"it was zero" are different facts. n = 1 per arm: treat every percentage here as a
direction of travel, not a measurement of the model.*

### The task had to be new, and that is itself data

**TASK-A, TASK-B and TASK-C were all already implemented in this repository before
the experiment began** — three earlier cold attempts had leaked the governance layer
and their work had been kept on merit. The pair above therefore uses a fresh task of
the same kind, with the same kind of machine-decidable check.

### What differed, when the outcome didn't

Both runs passed first try, with no human edits and no clarification prompts. The
treatment did not change *correctness*. It changed *consistency*:

| Dimension | cold (no brief) | context (brief present) |
| --- | --- | --- |
| `make` target name | `make test-docstrings` | `make tests-docstrings` |
| `run_check` key | `test-docstrings` | `tests-docstrings` |
| Existing convention | already `src-docstrings` (directory name + `-docstrings`) | matched that pattern |
| Brief maintained | n/a — no brief existed | `AGENTS.md` updated: new gate documented, test count 177 → 200 |
| Tests added | +6 (123 → 129) | +7 (193 → 200) |

Read the first three rows together. The repository already had a gate named
`src-docstrings`; the governed run produced `tests-docstrings` — the same pattern
applied to the new directory — and recorded it in the brief. The ungoverned run
produced `test-docstrings`, which works but breaks the pattern, and left nothing to
update because there was nothing there. **A reviewer has to catch that by hand; the
brief said it without being asked.**

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

1. **n is tiny.** Six attempts per arm is a direction of travel, not a measurement.
   Report the attempt count and the raw rows, not just the percentages.
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

> With the treatment, first-pass success did not move: 1/1 in both arms, no human
> edits and no clarification prompts either way. The useful difference was not the
> rate but the *shape* of the result: the ungoverned run invented a gate name
> (`test-docstrings`) that broke the repository's existing `src-docstrings` pattern
> and had no brief to update, while the governed run followed the pattern
> (`tests-docstrings`) and kept `AGENTS.md` current. Context engineering did not make
> the model smarter here — it removed a class of small mistakes that are expensive to
> catch in review, and it carried the documentation along with the change.
>
> The stronger result is methodological: three attempts to hold the control condition
> failed, because the agent reconstructed or restored the treatment each time. The
> fourth attempt fixed the *method* — governance removed from git history, not merely
> from the working tree — instead of asking the agent to behave, and that run stayed
> cold. Measuring an agent means measuring the agent plus everything it can reach.

