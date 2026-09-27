# Jarvis: the everyday commands. Run from the repo root.

PY ?= python3

.PHONY: help install auth collect sample dashboard brain search hermes test

help:
	@echo "make install    install python deps"
	@echo "make auth       authorize every Google account in collectors/accounts.yaml"
	@echo "make collect    pull everything into dashboard/data/today.json"
	@echo "make sample     write a fictional today.json for design work"
	@echo "make dashboard  serve the dashboard at http://localhost:8765"
	@echo "make brain      file everything in brain/inbox into the library"
	@echo "make search Q=\"waitlist\"   search the second brain"
	@echo "make hermes     install skills, persona, and cron jobs into Hermes"
	@echo "make test       compile check, schema check, brain round trip"

install:
	$(PY) -m pip install -r requirements.txt

auth:
	$(PY) -m collectors.google_auth

collect:
	$(PY) -m collectors.build_today

sample:
	$(PY) -m collectors.build_today --sample

dashboard:
	cd dashboard && $(PY) -m http.server 8765

brain:
	$(PY) -m brain.ingest

search:
	$(PY) -m brain.search "$(Q)"

hermes:
	bash hermes/install.sh

test:
	$(PY) -m compileall -q collectors brain
	$(PY) -m collectors.build_today --sample --dry-run > /dev/null && echo "sample validates"
	$(PY) -m brain.ingest --dry-run
