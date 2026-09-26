#!/usr/bin/env bash
# Stop — point de reprise SANS polluer l'historique.
#
# La version d'origine faisait `git add -A && git commit` après CHAQUE réponse de l'agent :
# l'historique de la branche se remplissait de commits « wip », sur main compris.
# Ici, l'état complet du dossier (fichiers suivis et nouveaux fichiers non ignorés) est
# figé dans une référence privée refs/ephor/checkpoints/<session>. Votre branche, votre index
# et vos fichiers ne sont pas touchés. `make checkpoints` les liste ; pour récupérer :
#   git restore --source=refs/ephor/checkpoints/<session> -- <fichier>
set -uo pipefail
INPUT="$(cat)"
# La sentinelle du dernier commit, pas celle du disque : réécrite pendant le tour, elle ne
# pourrait pas se juger elle-même (sa liste de zones non plus). Un dépôt où Ephor n'est pas
# encore commité garde la sienne. Git durci, comme partout dans les hooks (voir _lib.sh).
git() { command git -c core.fsmonitor=false -c core.hooksPath=/dev/null "$@"; }
sentinelle() { # sentinelle <photo|controle> : rend le code de la sentinelle
  local depot copie script code
  depot="${CLAUDE_PROJECT_DIR:-$(pwd)}"
  script="$(dirname "$0")/sentinelle.sh"
  copie="$(mktemp -d "${TMPDIR:-/tmp}/ephor-sentinelle-head.XXXXXX" 2>/dev/null)" || copie=""
  if [ -n "$copie" ] \
    && git -C "$depot" archive HEAD .ephor/hooks/sentinelle.sh .ephor/hooks/_lib.sh .ephor/zones.txt 2>/dev/null \
       | tar -x -C "$copie" 2>/dev/null \
    && [ -f "$copie/.ephor/hooks/sentinelle.sh" ]; then
    script="$copie/.ephor/hooks/sentinelle.sh"
  fi
  printf '%s' "$INPUT" | bash "$script" "$1"
  code=$?
  [ -z "$copie" ] || rm -rf "$copie"
  return "$code"
}
# Fin du tour : la sentinelle compare les zones de contrôle à leur photo du début (voir
# sentinelle.sh). Son alerte devient le code de ce hook — 1, pour que l'outil la MONTRE —
# mais le point de reprise est pris quand même.
sentinelle controle
ALERTE=$?
[ "$ALERTE" = "1" ] || ALERTE=0
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$ROOT" || exit "$ALERTE"
git rev-parse --git-dir >/dev/null 2>&1 || exit "$ALERTE"
git rev-parse --verify -q HEAD >/dev/null || exit "$ALERTE"
[ -z "$(git status --porcelain 2>/dev/null)" ] && exit "$ALERTE"

# Claude Code nomme la session `session_id`, Copilot `sessionId`. Ne lire que le premier, et
# seulement avec jq, rangeait toutes les sessions de Copilot — et toutes celles d'un poste
# sans jq — dans un même point de reprise, « inconnue » (vu le 25/09/2026).
SESSION=""
if command -v jq >/dev/null 2>&1; then
  SESSION="$(printf '%s' "$INPUT" | jq -r '.session_id // .sessionId // empty' 2>/dev/null)"
elif command -v python3 >/dev/null 2>&1; then
  SESSION="$(printf '%s' "$INPUT" | python3 -c 'import json, sys
try:
    charge = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(charge, dict):
    valeur = charge.get("session_id") or charge.get("sessionId")
    if isinstance(valeur, str):
        print(valeur)' 2>/dev/null)"
fi
SESSION="$(printf '%s' "$SESSION" | tr -cd 'A-Za-z0-9._-' | cut -c1-64)"
[ -z "$SESSION" ] && SESSION=inconnue

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ephor-checkpoint.XXXXXX")" || exit "$ALERTE"
trap 'rm -rf "$TMP"' EXIT
export GIT_INDEX_FILE="$TMP/index"
git read-tree HEAD 2>/dev/null || exit "$ALERTE"
git add -A -- . >/dev/null 2>&1 || exit "$ALERTE"
git rm -r --cached --ignore-unmatch -q -- .verify >/dev/null 2>&1 || true
TREE="$(git write-tree 2>/dev/null)" || exit "$ALERTE"

REF="refs/ephor/checkpoints/$SESSION"
PARENT="$(git rev-parse -q --verify "$REF" 2>/dev/null || git rev-parse HEAD)"
[ "$(git rev-parse "$PARENT^{tree}" 2>/dev/null)" = "$TREE" ] && exit "$ALERTE"

: "${GIT_AUTHOR_NAME:=$(git config user.name || echo ephor)}"
: "${GIT_AUTHOR_EMAIL:=$(git config user.email || echo ephor@localhost)}"
export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
COMMIT="$(printf 'point de reprise automatique (hook Stop)\n\nAgent-Session: %s\n' "$SESSION" \
  | git commit-tree "$TREE" -p "$PARENT" 2>/dev/null)" || exit "$ALERTE"
git update-ref "$REF" "$COMMIT" 2>/dev/null || true
exit "$ALERTE"
