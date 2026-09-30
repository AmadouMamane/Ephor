#!/usr/bin/env bash
# Stop — point de reprise SANS polluer l'historique.
#
# La version d'origine faisait `git add -A && git commit` après CHAQUE réponse de l'agent :
# l'historique de la branche se remplissait de commits « wip », sur main compris.
# Ici, l'état complet du dossier (fichiers suivis et nouveaux fichiers non ignorés) est
# figé dans une référence privée refs/ephor/checkpoints/<session>. Votre branche, votre index
# et vos fichiers ne sont pas touchés. `make checkpoints` les liste ; pour récupérer :
#   git restore --source=refs/ephor/checkpoints/<session> -- <fichier>
# Un point de reprise vit 30 jours ; un fichier de plus de 5 Mo n'y entre pas (son message le
# nomme). `make checkpoints-purge` les supprime tous, tout de suite.
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
# Au-delà de 30 jours, un point de reprise part, à chaque fin de tour, dossier propre compris.
# Rien ne les supprimait : tout fichier présent à une fin de tour restait dans .git, même
# effacé, même après `git gc` — un secret écrit un instant compris.
LIMITE=$(( $(date +%s) - 30 * 86400 ))
git for-each-ref --format='%(committerdate:unix) %(refname)' refs/ephor/checkpoints 2>/dev/null \
  | awk -v limite="$LIMITE" '$1 < limite { print "delete " $2 }' \
  | git update-ref --stdin 2>/dev/null
# Sans verrou optionnel : `git status` ne réécrit pas votre index pendant que vous travaillez.
export GIT_OPTIONAL_LOCKS=0
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
# Une copie de l'index réel, pas un index vide : git sait déjà quels fichiers n'ont pas bougé,
# et ne relit que les autres. Repartir de `git read-tree HEAD` relisait tout le dépôt à chaque
# fin de tour : 3 à 4 s sur 20 000 fichiers et 600 Mo (audit du 29/09/2026).
INDEX="$(git rev-parse --git-path index 2>/dev/null)"
export GIT_INDEX_FILE="$TMP/index"
if ! { [ -n "$INDEX" ] && cp -p "$INDEX" "$GIT_INDEX_FILE" 2>/dev/null; }; then
  git read-tree HEAD 2>/dev/null || exit "$ALERTE"
fi
# Au-delà de 5 Mo, un fichier nouveau ou modifié n'entre pas : il resterait dans .git (un jeu
# de données, une archive, un binaire construit). Le point de reprise garde la version de
# l'index — commitée, ou indexée —, et nomme le fichier dans son message.
EXCLUS=()
GROS=""
if command -v python3 >/dev/null 2>&1; then
  while IFS= read -r -d '' chemin; do
    EXCLUS+=(":(exclude,literal)$chemin")
    GROS="$GROS  $chemin"$'\n'
  done < <(git ls-files -z -o -m --exclude-standard 2>/dev/null | python3 -c 'import os, sys
vus = set()
for chemin in sys.stdin.buffer.read().split(b"\0"):
    if chemin and chemin not in vus and os.path.isfile(chemin) and not os.path.islink(chemin):
        vus.add(chemin)
        if os.path.getsize(chemin) > 5 * 1024 * 1024:
            sys.stdout.buffer.write(chemin + b"\0")' 2>/dev/null)
fi
# `${EXCLUS[@]+…}` : sous le bash 3.2 de macOS, un tableau vide est « non défini » pour set -u.
git add -A -- . ${EXCLUS[@]+"${EXCLUS[@]}"} >/dev/null 2>&1 || exit "$ALERTE"
git rm -r --cached --ignore-unmatch -q -- .verify >/dev/null 2>&1 || true
TREE="$(git write-tree 2>/dev/null)" || exit "$ALERTE"
[ "$(git rev-parse "HEAD^{tree}" 2>/dev/null)" = "$TREE" ] && exit "$ALERTE"

REF="refs/ephor/checkpoints/$SESSION"
PARENT="$(git rev-parse -q --verify "$REF" 2>/dev/null || git rev-parse HEAD)"
[ "$(git rev-parse "$PARENT^{tree}" 2>/dev/null)" = "$TREE" ] && exit "$ALERTE"

: "${GIT_AUTHOR_NAME:=$(git config user.name || echo ephor)}"
: "${GIT_AUTHOR_EMAIL:=$(git config user.email || echo ephor@localhost)}"
export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
COMMIT="$({
  printf 'point de reprise automatique (hook Stop)\n\n'
  [ -z "$GROS" ] || printf 'Pas sauvés, plus de 5 Mo (la version de l’index est gardée) :\n%s\n' "$GROS"
  printf 'Agent-Session: %s\n' "$SESSION"
} | git commit-tree "$TREE" -p "$PARENT" 2>/dev/null)" || exit "$ALERTE"
git update-ref "$REF" "$COMMIT" 2>/dev/null || true
exit "$ALERTE"
