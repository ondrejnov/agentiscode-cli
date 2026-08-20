PYTHON ?= /usr/bin/python3.13
PIPX ?= pipx

.PHONY: install

install:
	$(PIPX) install --force --python "$(PYTHON)" .
