# Interface unique entre l'agent et le projet.
# Règle : toute sortie longue va dans .verify/ ; stdout reste court.
SHELL := /bin/bash
V := .verify
PY := uv run
PYTHON ?= python3
# Le code d'Ephor ne lit ni n'écrit de cache compilé dans `.ephor/` (-B, et un préfixe qui ne
# peut pas exister) : écrit, il passait pour une retouche du juge ; lu, un cache fabriqué à
# la main s'exécutait à la place du vrai module.
EPHOR_PY = $(PYTHON) -B -X pycache_prefix=/dev/null/ephor-sans-cache
FAST_PCT ?= 10
RUNTIME ?= local
CI_IMAGE ?= ephor-ci:local
# Scanner de sécurité de la maison (SonarQube, Semgrep, Checkmarx...). Vide : on saute.
EPHOR_SAST ?=
export EPHOR_SAST

.DEFAULT_GOAL := help

help: ## Liste les cibles
	@# `.*?` est du PCRE : ni grep -E ni awk (ERE) ne le garantissent. Forme portable
	@# pour le grep/awk de BSD livré avec macOS.
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  %-14s %s\n",$$1,$$2}'

doctor: ## Diagnostic de l'environnement — à lancer AVANT setup, et quand verify échoue bizarrement
	@bash .ephor/scripts/doctor.sh

setup: ## Installe l'environnement (uv)
	@uv sync --all-extras

fmt: ## Formate
	@$(PY) ruff format . >/dev/null && $(PY) ruff check --fix-only . >/dev/null && echo "fmt ok"

lint: | $(V) ## Lint (ruff)
	@$(PY) ruff check --output-format=concise . > $(V)/lint.txt 2>&1; \
	 s=$$?; \
	 n=$$(grep -cE '^[^ ].*:[0-9]+:' $(V)/lint.txt 2>/dev/null || true); \
	 echo "lint: $${n:-0} finding(s) -> $(V)/lint.txt"; exit $$s

types: | $(V) ## Types
	@$(PY) mypy src tests > $(V)/types.txt 2>&1; \
	 s=$$?; echo "types: $$(grep -c 'error:' $(V)/types.txt || true) erreur(s) -> $(V)/types.txt"; exit $$s

arch: | $(V) ## Contrats d'architecture (direction des dépendances)
	@# import-linter écrit « <nom du contrat> KEPT » : compter les lignes qui COMMENCENT par
	@# KEPT annonçait « 0 contrat(s) » quand deux tenaient.
	@$(PY) lint-imports > $(V)/arch.txt 2>&1; \
	 s=$$?; echo "arch: $$(grep -cE ' (KEPT|BROKEN)$$' $(V)/arch.txt || true) contrat(s), $$(grep -cE ' BROKEN$$' $(V)/arch.txt || true) rompu(s) -> $(V)/arch.txt"; exit $$s

test: | $(V) ## Suite complète — mêmes règles que verify : PYTEST_MARK, et « aucun test » passe
	@$(PY) pytest -q --maxfail=1 $(if $(PYTEST_MARK),-m "$(PYTEST_MARK)",) > $(V)/test.txt 2>&1; \
	 s=$$?; if [ $$s -eq 5 ]; then s=0; fi; \
	 tail -n 1 $(V)/test.txt; echo "detail -> $(V)/test.txt"; exit $$s

test-fast: | $(V) ## Échantillon déterministe (FAST_PCT %) : régression pas chère
	@bash .ephor/scripts/test_sample.sh $(FAST_PCT)

mutants: | $(V) ## Score de mutation sur les fichiers CHANGÉS (jamais tout le dépôt)
	@bash .ephor/scripts/mutants.sh

sec: | $(V) ## Secrets, données personnelles, et le scanner de l'entreprise s'il est branché
	@s=0; manque=""; \
	 if command -v gitleaks >/dev/null 2>&1; then \
	   gitleaks detect --no-banner --redact -v > $(V)/sec.txt 2>&1 || s=1; \
	 else echo "gitleaks ABSENT : aucun secret n'a été cherché" > $(V)/sec.txt; manque="gitleaks"; fi; \
	 echo "--- données personnelles" >> $(V)/sec.txt; \
	 $(EPHOR_PY) .ephor/personal_data.py >> $(V)/sec.txt 2>&1 || s=1; \
	 if [ -f .ephor/projet/sast.sh ]; then \
	   echo "--- scanner maison : .ephor/projet/sast.sh" >> $(V)/sec.txt; \
	   bash .ephor/projet/sast.sh >> $(V)/sec.txt 2>&1 || s=1; \
	 elif [ -n "$${EPHOR_SAST}" ]; then \
	   echo "--- scanner maison : $${EPHOR_SAST}" >> $(V)/sec.txt; \
	   sh -c "$${EPHOR_SAST}" >> $(V)/sec.txt 2>&1 || s=1; \
	 else echo "--- scanner maison : ni .ephor/projet/sast.sh ni EPHOR_SAST (voir .ephor/docs/06-exploitation.md)" >> $(V)/sec.txt; fi; \
	 echo "sec -> $(V)/sec.txt"; \
	 if [ -n "$$manque" ]; then \
	   echo "  PARTIEL — $$manque n'a pas tourné : ce vert ne dit PAS « aucun secret »."; \
	   if [ "$${VERIFY_STRICT:-0}" = "1" ]; then s=1; fi; \
	 fi; \
	 exit $$s

ci-image: ## Construit l'image de CI et du juge (uv, git, make, gitleaks) : make ci-image [CI_IMAGE=<nom>] [EPHOR_CI_BASE=<image uv>] [EPHOR_CI_GITLEAKS=<image gitleaks>]
	@moteur="$(RUNTIME)"; \
	 case "$$moteur" in \
	   podman|docker) ;; \
	   *) if command -v podman >/dev/null 2>&1; then moteur=podman; else moteur=docker; fi ;; \
	 esac; \
	 echo "construction de $(CI_IMAGE) avec $$moteur"; \
	 $$moteur build -f .ephor/deploy/Containerfile.ci \
	   $(if $(EPHOR_CI_BASE),--build-arg BASE="$(EPHOR_CI_BASE)",) \
	   $(if $(EPHOR_CI_GITLEAKS),--build-arg GITLEAKS="$(EPHOR_CI_GITLEAKS)",) \
	   -t "$(CI_IMAGE)" .ephor/deploy

verify: | $(V) ## LA commande. Tout, dans l'ordre, résumé court.
	@bash .ephor/projet/verify.sh

judge: ## Juge final d'une tâche : make judge TASK=<ID> [BASE=main] [RUNTIME=local|podman|docker|auto] [IMAGE=<image>]
	@test -n "$(TASK)" || { echo "usage : make judge TASK=<ID> [BASE=main] [RUNTIME=podman] [IMAGE=<image>]"; exit 2; }
	@$(EPHOR_PY) .ephor/usage.py rafraichir --etat ".verify/usage/$(TASK).json" 2>/dev/null || true
	@$(EPHOR_PY) .ephor/judge.py check --task-id "$(TASK)" --runtime "$(RUNTIME)" $(if $(BASE),--base "$(BASE)") $(if $(IMAGE),--image "$(IMAGE)")

review: ## Revue proportionnée au risque du contrat : make review TASK=<ID>
	@test -n "$(TASK)" || { echo "usage : make review TASK=<ID>"; exit 2; }
	@bash .ephor/scripts/review.sh "$(TASK)"

task-new: ## Contrat de tâche, pour l'OPÉRATEUR : make task-new ID=FEAT-001 [FROM=plan.json]
	@test -n "$(ID)" || { echo "usage : make task-new ID=FEAT-001 [FROM=plan.json]"; exit 2; }
	@$(EPHOR_PY) .ephor/judge.py new --task-id "$(ID)" $(if $(FROM),--from "$(FROM)",)

fetch-docs: ## Aspire une page de doc pour les agents, pour l'OPÉRATEUR : make fetch-docs URL=https://... [NAME=nom]
	@test -n "$(URL)" || { echo "usage : make fetch-docs URL=https://exemple.fr/page [NAME=nom]"; exit 2; }
	@$(EPHOR_PY) .ephor/fetch_docs.py "$(URL)" $(if $(NAME),--name "$(NAME)",)

score: ## Les quatre indicateurs d'Ephor, d'après le journal des jugements
	@$(EPHOR_PY) .ephor/score.py $(if $(TASK),--task "$(TASK)",)

usage: ## Ce que les tâches ont coûté en tokens — mesuré, jamais deviné
	@$(EPHOR_PY) .ephor/usage.py rapport

adapters: ## Régénère les réglages d'opencode et de Copilot depuis .claude/ (OPÉRATEUR)
	@$(EPHOR_PY) .ephor/adapters.py write

copilot-install: ## Installe le greffon Copilot sur CETTE machine (OPÉRATEUR, une fois par poste)
	@bash .ephor/scripts/install_copilot_plugin.sh

task-check: ## Contrat prêt à approuver ? Sa forme, puis chaque critère doit ÉCHOUER sur le code actuel (écrit tasks/<ID>/rouge.json) : make task-check ID=FEAT-001 [RUNTIME=podman]
	@test -n "$(ID)" || { echo "usage : make task-check ID=FEAT-001 [RUNTIME=podman] [IMAGE=<image>]"; exit 2; }
	@$(EPHOR_PY) .ephor/judge.py rouge --task-id "$(ID)" --runtime "$(RUNTIME)" $(if $(IMAGE),--image "$(IMAGE)")

delivery-manifest: ## Génère le manifeste de livraison scellé : make delivery-manifest [TASK=<ID>] [DIGEST=<sha256:...>] [REPORT=<rapport du juge>]
	@$(EPHOR_PY) .ephor/deploy/manifest.py generate $(if $(TASK),--task-id "$(TASK)",) $(if $(DIGEST),--artifact-digest "$(DIGEST)",) $(if $(REPORT),--report "$(REPORT)",)

delivery-check: ## Vérifie la conformité du manifeste de livraison
	@$(EPHOR_PY) .ephor/deploy/manifest.py check

ephor-test: | $(V) ## Teste Ephor lui-même : juge, garde-fous, scripts
	@$(PY) ruff check .ephor > $(V)/ephor.txt 2>&1 && $(PY) mypy --strict .ephor/*.py .ephor/deploy/manifest.py >> $(V)/ephor.txt 2>&1 && \
	 $(PY) pytest -q -p no:randomly .ephor/tests >> $(V)/ephor.txt 2>&1; \
	 s=$$?; tail -n 1 $(V)/ephor.txt; echo "detail -> $(V)/ephor.txt"; exit $$s

gitlab-check: ## Les réglages GitLab qui font foi sont-ils en place ? (OPÉRATEUR, GITLAB_TOKEN en lecture)
	@$(EPHOR_PY) .ephor/gitlab_check.py

checkpoints: ## Liste les points de reprise automatiques (hors historique de branche)
	@git for-each-ref --sort=-committerdate --format='  %(committerdate:relative)  %(refname:short)' refs/ephor/checkpoints | head -20

map: ## Carte structurelle du dépôt, ou d'un dépôt de référence : make map [REPO=<nom>]
	@if [ -n "$(REPO)" ]; then $(EPHOR_PY) .ephor/refs.py carte --nom "$(REPO)"; else bash .ephor/scripts/repo_map.sh; fi

refs: ## Dépôts de référence déclarés : ce qu'on peut LIRE, jamais écrire
	@$(EPHOR_PY) .ephor/refs.py liste

stop: ## Arrêt d'urgence des agents
	@touch AGENT_STOP && echo "AGENT_STOP posé. Les appels d'outils sont bloqués."

resume: ## Lève l'arrêt d'urgence
	@rm -f AGENT_STOP && echo "AGENT_STOP levé."

clean: ## Efface les sorties, SAUF la mesure de tokens et le journal des jugements
	@find $(V) -mindepth 1 -maxdepth 1 ! -name usage ! -name journal.jsonl -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache

$(V):
	@mkdir -p $(V)

.PHONY: help doctor setup ci-image gitlab-check review delivery-manifest delivery-check fmt lint types arch test test-fast sec mutants verify judge task-new task-check adapters copilot-install usage ephor-test checkpoints map refs stop resume score fetch-docs clean
