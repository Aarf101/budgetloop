# Development entry points. Everything here is stdlib-only and offline.
#
# `make check` is the gate an agent must pass before calling a change done:
# style, skill validation, and tests. It is the single command named in AGENTS.md.

PYTHON ?= python3
export PYTHONPATH := src

.PHONY: help test demo style skill-lint check run traces score clean

help:
	@echo "make test       run the unit test suite (stdlib unittest)"
	@echo "make demo       offline scripted run: shows compaction and a refused write"
	@echo "make check      style + skill validation + tests (the gate before 'done')"
	@echo "make style      enforce the house style rules"
	@echo "make skill-lint validate every skills/*/SKILL.md against the Agent Skills spec"
	@echo "make run TASK='...'  drive the loop once, offline, from scripts/example-run.json"
	@echo "make score      score the Lab 2 scorecard (docs/experiment-scorecard.csv)"
	@echo "make traces     list trace files written by recent runs"
	@echo "make clean      remove caches and runtime artifacts"

test:
	$(PYTHON) -m unittest discover -s tests -t .

demo:
	$(PYTHON) -m budgetloop.cli demo

style:
	$(PYTHON) scripts/check_style.py

skill-lint:
	$(PYTHON) scripts/validate_skill.py skills/*

check: style skill-lint test

run:
	@test -n "$(TASK)" || (echo "usage: make run TASK='what you want done'" && exit 2)
	$(PYTHON) -m budgetloop.cli run --task "$(TASK)" --script scripts/example-run.json

score:
	$(PYTHON) scripts/score_experiment.py docs/experiment-scorecard.csv

traces:
	@ls -1t .budgetloop/traces 2>/dev/null || echo "no traces yet"

clean:
	rm -rf .budgetloop .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
