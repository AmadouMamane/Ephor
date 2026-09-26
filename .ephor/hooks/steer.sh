#!/usr/bin/env bash
# UserPromptSubmit — injecte une consigne opérateur en cours de route, sans redémarrer.
# Écris dans STEER.md, l'agent la reçoit au tour suivant. Vide-le une fois appliquée.
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
# Début du tour : la sentinelle photographie les zones de contrôle (voir sentinelle.sh).
# Rien sur la sortie standard : ce que ce hook y écrit, l'agent le lit comme une consigne.
# Si le tour PRÉCÉDENT n'a jamais été contrôlé (interrompu) et a touché une zone, la
# demande est refusée (code 2) : l'opérateur doit le voir avant que l'agent ne reparte.
ALERTE="$(sentinelle photo 2>&1 >/dev/null)"
if [ $? -eq 1 ]; then
  printf '%s\n' "$ALERTE" >&2
  exit 2
fi
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
F="$ROOT/STEER.md"
[ -f "$F" ] || exit 0
BODY=$(grep -v '^[[:space:]]*>' "$F" | grep -v '^# STEER' | sed '/^[[:space:]]*$/d')
[ -z "$BODY" ] && exit 0
echo "CONSIGNE OPÉRATEUR EN COURS (STEER.md) — applique-la avant toute autre chose :"
echo "$BODY"
exit 0
