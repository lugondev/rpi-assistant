SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT := $(abspath ..)
PY := $(ROOT)/.venv/bin/python
PIP := $(ROOT)/.venv/bin/pip
APP := $(CURDIR)/rpi_audio_service.py
CONFIG := $(CURDIR)/config.yaml
SERVICE_FILE := $(CURDIR)/rpi-a2a.service
SYSTEMD_UNIT := rpi-a2a.service

.PHONY: help venv install check list-devices run service-install service-start service-stop service-restart service-status logs

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Basic:"
	@echo "  make venv          - Create virtual environment in repo root (.venv)"
	@echo "  make install       - Install Python dependencies for agent-assistant"
	@echo "  make check         - Syntax check the service app"
	@echo "  make list-devices  - Show PortAudio devices"
	@echo "  make run           - Run the audio-to-audio client"
	@echo ""
	@echo "Service:"
	@echo "  make service-install - Install/update systemd unit"
	@echo "  make service-start   - Start rpi-a2a.service"
	@echo "  make service-stop    - Stop rpi-a2a.service"
	@echo "  make service-restart - Restart rpi-a2a.service"
	@echo "  make service-status  - Show service status"
	@echo "  make logs            - Tail service logs"

venv:
	python3 -m venv $(ROOT)/.venv

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r $(CURDIR)/requirements.txt

check:
	$(PY) -m py_compile $(APP)

list-devices:
	$(PY) $(APP) --list-devices

run:
	$(PY) $(APP) --config $(CONFIG)

service-install:
	sudo cp $(SERVICE_FILE) /etc/systemd/system/$(SYSTEMD_UNIT)
	sudo systemctl daemon-reload
	sudo systemctl enable --now $(SYSTEMD_UNIT)

service-start:
	sudo systemctl start $(SYSTEMD_UNIT)

service-stop:
	sudo systemctl stop $(SYSTEMD_UNIT)

service-restart: service-install
	sudo systemctl restart $(SYSTEMD_UNIT)

service-status:
	sudo systemctl --no-pager --full status $(SYSTEMD_UNIT)

logs:
	sudo journalctl -u $(SYSTEMD_UNIT) -f --no-pager
