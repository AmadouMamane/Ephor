#!/usr/bin/env bash
# Fin de tour : PROGRESS.md, tenu à jour (#31). La règle vit dans .ephor/progres.py.
# Sous Claude Code, l'agent continue le temps de l'écrire (`decision: block`, jamais deux fois
# de suite) ; sous opencode et Copilot, l'opérateur le lit. EPHOR_RAPPEL_PROGRES=0 l'éteint.
set -uo pipefail
[ "${EPHOR_RAPPEL_PROGRES:-1}" = "0" ] && exit 0
command -v python3 >/dev/null 2>&1 || exit 0
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
[ -f "$ROOT/PROGRESS.md" ] || exit 0
GITDIR="$(git -C "$ROOT" -c core.fsmonitor=false rev-parse --absolute-git-dir 2>/dev/null)" || exit 0
python3 "$(dirname "$0")/../progres.py" "$ROOT" "$GITDIR" 2>/dev/null || true
exit 0
