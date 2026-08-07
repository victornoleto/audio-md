# audio-md — dependencies and web-service operation (user-level systemd).
# `make help` (or plain `make`) lists the targets. Details in README.md.
# Target descriptions (##) are user-facing `make help` output — kept in pt-BR.

UNIT     := audio-md.service
UNIT_DIR := $(HOME)/.config/systemd/user
EXTRAS   ?= --extra transcribe --extra gpu

.DEFAULT_GOAL := help

help:  ## lista os alvos disponíveis
	@grep -E '^[a-z][a-z /|-]*:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-28s\033[0m %s\n", $$1, $$2}'

deps:  ## instala dependências (CPU-only: make deps EXTRAS="--extra transcribe")
	uv sync $(EXTRAS)

run:  ## roda o servidor em foreground (desenvolvimento)
	uv run audio-md-web

test:  ## roda os testes
	uv run pytest -q

install: deps  ## instala e ativa o serviço systemd (user-level, sem sudo)
	mkdir -p $(UNIT_DIR)
	sed 's|__DIR__|$(CURDIR)|g' $(UNIT) > $(UNIT_DIR)/$(UNIT)
	systemctl --user daemon-reload
	systemctl --user enable --now $(UNIT)
	@loginctl enable-linger $(USER) 2>/dev/null || echo "→ para subir no boot sem login: sudo loginctl enable-linger $(USER)"
	@echo "✓ serviço ativo — http://127.0.0.1:8765 (porta: WEB_PORT no .env)"

uninstall:  ## para, desativa e remove o serviço
	-systemctl --user disable --now $(UNIT)
	rm -f $(UNIT_DIR)/$(UNIT)
	systemctl --user daemon-reload

start stop restart status:  ## controla o serviço (make start|stop|restart|status)
	systemctl --user $@ $(UNIT)

logs:  ## segue os logs do serviço (journalctl)
	journalctl --user -u $(UNIT) -f

# `=` (lazy), not `:=`: only the docker-* targets pay for the `docker info` call.
GPU_RUNTIME = $(shell docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -o nvidia | head -1)
DC = APP_UID=$(shell id -u) APP_GID=$(shell id -g) docker compose -f compose.yaml $(if $(GPU_RUNTIME),-f compose.gpu.yaml)

docker-up:  ## sobe o serviço em container (detecta GPU sozinho)
	$(DC) up -d --build
	@echo "✓ audio-md em http://127.0.0.1:$$($(DC) port audio-md 8765 | cut -d: -f2)$(if $(GPU_RUNTIME), · GPU habilitada,)"

docker-down:  ## para e remove o container
	$(DC) down

docker-logs:  ## segue os logs do container
	$(DC) logs -f

docker-login:  ## login do CLI de resumo dentro do container (uma vez só)
	$(DC) run --rm audio-md claude /login

docker-cli:  ## roda o CLI no container (ex.: make docker-cli ARGS="/app/inputs/a.mp3")
	$(DC) run --rm audio-md audio-md $(ARGS)

.PHONY: help deps run test install uninstall start stop restart status logs \
        docker-up docker-down docker-logs docker-login docker-cli
