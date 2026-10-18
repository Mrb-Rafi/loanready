# Thin wrapper; tasks.py is the real runner (works on Windows without make).
PY ?= python

.PHONY: setup lint test sandbox gen seed ui demo evidence docs integrate clean doctor freeze run

setup lint test sandbox gen seed ui demo evidence docs integrate clean doctor freeze run:
	$(PY) tasks.py $@
