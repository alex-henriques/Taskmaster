VENV   := .venv
PYTHON := $(VENV)/bin/python3

.PHONY: install run daemon ctl test clean

install: $(VENV)

$(VENV): requirements.txt
		python3 -m venv $(VENV)
		$(PYTHON) -m pip install -r requirements.txt
		-git config core.hooksPath .githooks
		touch $(VENV)

run: $(VENV)
		$(PYTHON) -m taskmaster.main $(CONFIG)

daemon: $(VENV)
		$(PYTHON) -m taskmaster.daemon $(CONFIG)

ctl: $(VENV)
		$(PYTHON) -m taskmaster.ctl $(ARGS)

test: $(VENV)
		$(PYTHON) -m unittest

clean:
		rm -rf $(VENV) taskmaster.log
		find . -type d -name __pycache__ -prune -exec rm -rf {} +

