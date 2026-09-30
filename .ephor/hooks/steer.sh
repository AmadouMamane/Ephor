#!/usr/bin/env bash
# UserPromptSubmit — le début du tour : photo de la sentinelle, autre session dans le même
# dossier (sessions.sh), consigne de l'opérateur (STEER.md, voir consigne.sh).
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
# `steer.sh consigne` ne rend que la consigne : le greffon d'opencode la relit à chaque
# requête, et une photo reprise à chaque requête effacerait ce que le tour a changé.
if [ "${1:-}" != consigne ]; then
  ALERTE="$(sentinelle photo 2>&1 >/dev/null)"
  if [ $? -eq 1 ]; then
    printf '%s\n' "$ALERTE" >&2
    exit 2
  fi
  # Une autre session d'agent dans ce même dossier ? Un avertissement, une fois (sessions.sh).
  printf '%s' "$INPUT" | bash "$(dirname "$0")/sessions.sh" 2>/dev/null
  # `steer.sh photo` (greffon d'opencode) s'arrête là : il lit la consigne par `consigne`.
  [ "${1:-}" = photo ] && exit 0
  # La consigne de l'opérateur, si elle a changé depuis ce que cette session a reçu.
  printf '%s' "$INPUT" 2>/dev/null | bash "$(dirname "$0")/consigne.sh"
  exit 0
fi
# `steer.sh consigne` (greffon d'opencode, à chaque requête) : la consigne tant qu'elle existe.
bash "$(dirname "$0")/consigne.sh" toujours </dev/null
exit 0
