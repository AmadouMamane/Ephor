#!/usr/bin/env bash
# Deux sessions d'agent dans un même dossier se marchent dessus : elles partagent la branche et
# les fichiers. L'une change de branche, et le travail de l'autre part sur la mauvaise ; l'une
# commite ce que l'autre n'a pas fini (vécu en session réelle, le 30/09/2026 : plusieurs tâches
# lancées dans le même dossier). Au début de chaque tour, steer.sh appelle ce script : il note
# la session dans le dossier git de CET arbre de travail, et prévient une fois quand une autre
# session y tourne encore. Le remède est un worktree par tâche : `make task-worktree ID=<ID>`.
#
# Une session se reconnaît à son processus d'agent (claude, opencode, VS Code) : le premier
# ancêtre du hook qui n'est pas un shell. Vivant, la session tourne ; mort, elle est finie.
# Pas de fenêtre de temps : un /clear ouvre une nouvelle session dans le MÊME processus, et ne
# doit rien dire ; une session fermée il y a cinq minutes non plus. Limite : sous Copilot, deux
# conversations d'une même fenêtre VS Code partagent son processus, et ne se voient pas.
#
# Écrit sur la sortie standard, que l'outil donne à l'agent (Claude Code, Copilot) ou montre à
# l'opérateur (greffon d'opencode). Ne refuse jamais rien : c'est un avertissement.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || exit 0
ROOT="$(racine)"
cd "$ROOT" 2>/dev/null || exit 0
GITDIR="$(git rev-parse --absolute-git-dir 2>/dev/null)" || exit 0
DOSSIER="$GITDIR/ephor-sessions"
mkdir -p "$DOSSIER" 2>/dev/null || exit 0

nom_de() { ps -o comm= -p "$1" 2>/dev/null | sed 's|.*/||'; }

agent() { # le premier ancêtre qui n'est pas un shell : le processus de l'outil
  local pid=$PPID nom
  while [ "${pid:-0}" -gt 1 ]; do
    nom="$(nom_de "$pid")"
    case "$nom" in
      bash | sh | zsh | dash | ksh | -bash | -zsh | -sh | env | timeout) ;;
      *) printf '%s' "$pid"; return 0 ;;
    esac
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
  done
  printf '%s' "$PPID"
}

MOI="$(agent)"
SESSION="$(_charge session)"
SESSION="$(printf '%s' "$SESSION" | tr -cd 'A-Za-z0-9._-' | cut -c1-64)"
BRANCHE="$(git branch --show-current 2>/dev/null)"
printf 'session=%s\nbranche=%s\noutil=%s\n' "${SESSION:-inconnue}" "${BRANCHE:-détachée}" \
  "$(nom_de "$MOI")" > "$DOSSIER/$MOI" 2>/dev/null || exit 0

for entree in "$DOSSIER"/*; do
  pid="${entree##*/}"
  case "$pid" in "" | *[!0-9]* | "$MOI") continue ;; esac
  # Fini, ou numéro repris par un autre programme : la session n'est plus là.
  if ! kill -0 "$pid" 2>/dev/null || [ "$(sed -n 's/^outil=//p' "$entree")" != "$(nom_de "$pid")" ]; then
    rm -f "$entree" "$DOSSIER"/vu-*-"$pid" "$DOSSIER"/vu-"$pid"-*
    continue
  fi
  [ -e "$DOSSIER/vu-$MOI-$pid" ] && continue
  : > "$DOSSIER/vu-$MOI-$pid"
  printf '%s\n' "ATTENTION — une autre session d'agent travaille dans ce dossier ($(nom_de "$pid"), processus $pid, branche $(sed -n 's/^branche=//p' "$entree")). Deux sessions dans un même dossier partagent la branche et les fichiers : un changement de branche ou un commit de l'une emporte le travail de l'autre. Dis-le à l'opérateur avant toute chose, et ne change pas de branche. Pour travailler en parallèle : une tâche par worktree (make task-worktree ID=<ID>, par l'opérateur), et une session dans chacun."
done
exit 0
