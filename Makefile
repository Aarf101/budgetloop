# Development entry points. Everything here is stdlib-only and offline.
#
# `make check` is the gate an agent must pass before calling a change done:
# style, docstring gates, skill validation, docs links, and tests. It is the single
# command named in AGENTS.md, and CI runs it on every push.

PYTHON ?= python3
export PYTHONPATH := src

.PHONY: help test demo demo-gif style src-docstrings tests-docstrings skill-lint docs check run traces score clean

help:
	@echo "make test       run the unit test suite (stdlib unittest)"
	@echo "make demo       offline scripted run: shows compaction and a refused write"
	@echo "make demo-gif   re-render docs/demo.gif from real demo output (needs ImageMagick)"
	@echo "make check      style + docstrings + skills + docs links + tests (the gate)"
	@echo "make style      enforce the house style rules"
	@echo "make docs       fail when a Markdown link or path points at nothing"
	@echo "make src-docstrings  fail if any module under src/ lacks a docstring"
	@echo "make tests-docstrings  fail if any module under tests/ lacks a docstring"
	@echo "make skill-lint validate every skills/*/SKILL.md against the Agent Skills spec"
	@echo "make run TASK='...'  drive the loop once, offline, from scripts/example-run.json"
	@echo "make score      score the pilot scorecard (docs/experiment-scorecard.csv)"
	@echo "make score-study  score the one-commit study (docs/study-scorecard.csv)"
	@echo "make traces     list trace files written by recent runs"
	@echo "make clean      remove caches and runtime artifacts"

test:
	$(PYTHON) -m unittest discover -s tests -t .

demo:
	$(PYTHON) -m budgetloop.cli demo

demo-gif:
	bash scripts/render_demo_gif.sh

style:
	$(PYTHON) scripts/check_style.py

docs:
	$(PYTHON) scripts/check_docs.py

src-docstrings:
	$(PYTHON) scripts/check_style.py src --src-docstrings-only

tests-docstrings:
	$(PYTHON) scripts/check_style.py tests --test-docstrings-only

skill-lint:
	$(PYTHON) scripts/validate_skill.py skills/*

check: style src-docstrings tests-docstrings skill-lint docs test

run:
	@test -n "$(TASK)" || (echo "usage: make run TASK='what you want done'" && exit 2)
	$(PYTHON) -m budgetloop.cli run --task "$(TASK)" --script scripts/example-run.json

score:
	$(PYTHON) scripts/score_experiment.py docs/experiment-scorecard.csv

score-study:
	$(PYTHON) scripts/score_experiment.py docs/study-scorecard.csv

traces:
	@ls -1t .budgetloop/traces 2>/dev/null || echo "no traces yet"

clean:
	rm -rf .budgetloop .pytest_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
