VENV   := .venv
PYTHON := $(VENV)/bin/python3

.PHONY: install run test clean

install: $(VENV)

$(VENV): requirements.txt
		python3 -m venv $(VENV)
		$(PYTHON) -m pip install -r requirements.txt
		touch $(VENV)

run: $(VENV)
		$(PYTHON) -m taskmaster.main $(CONFIG)

test: $(VENV)
		$(PYTHON) -m unittest

clean:
		rm -rf $(VENV) taskmaster.log
		find . -type d -name __pycache__ -prune -exec rm -rf {} +

