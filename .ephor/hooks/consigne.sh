#!/usr/bin/env bash
# La consigne de l'opérateur (STEER.md), donnée à l'agent UNE fois par changement.
#
# Trois portes, un seul texte :
#   - début de tour (UserPromptSubmit, par steer.sh) : le texte, s'il a changé depuis ce que
#     CETTE session a reçu ;
#   - après chaque appel d'outil (PostToolUse, Claude Code) : pareil, en cours de tour. Le
#     guide promettait de « corriger le cap en route », et sous Claude Code la consigne
#     n'arrivait qu'au message suivant de l'opérateur (audit du 29/09/2026) ;
#   - `consigne.sh toujours` (greffon d'opencode, à chaque requête) : le texte tant qu'il
#     existe. Là, il entre dans le prompt système, refait à chaque requête : il ne s'y
#     accumule pas, et doit y rester pour compter.
# Avant, le texte revenait à CHAQUE tour tant qu'on ne vidait pas le fichier, et s'empilait
# dans la conversation.
#
# STEER.md n'est écrit que par l'opérateur : c'est une zone de contrôle (.ephor/zones.txt),
# que les gardes, le juge et, par Edit(STEER.md), le bac à sable de Claude Code refusent à
# l'agent. Sans quoi un texte glissé par une page web devenait une « consigne opérateur »
# permanente. La sentinelle, seule, le laisse passer : l'opérateur l'écrit PENDANT le tour.
set -uo pipefail
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
F="$ROOT/STEER.md"
CORPS=""
[ -f "$F" ] && CORPS="$(grep -v '^[[:space:]]*>' "$F" | grep -v '^# STEER' | sed '/^[[:space:]]*$/d')"
GITDIR="$(git -C "$ROOT" rev-parse --absolute-git-dir 2>/dev/null)" || GITDIR=""
VUES="${GITDIR:+$GITDIR/ephor-consigne-}"
# Le cas courant, et il est rapide : rien à dire. Les sessions oublient ce qu'elles ont reçu,
# pour qu'une consigne reposée plus tard, même identique, soit redonnée.
if [ -z "$CORPS" ]; then
  [ -z "$VUES" ] || rm -f "$VUES"*
  exit 0
fi
TEXTE="CONSIGNE OPÉRATEUR EN COURS (STEER.md) — applique-la avant toute autre chose :
$CORPS"
if [ "${1:-}" = toujours ]; then
  printf '%s\n' "$TEXTE"
  exit 0
fi
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || exit 0
EVENEMENT="$(_charge evenement)"
if [ -z "$VUES" ]; then
  # Hors d'un dépôt git, pas de mémoire : au début du tour seulement, comme avant.
  [ "$EVENEMENT" = PostToolUse ] || printf '%s\n' "$TEXTE"
  exit 0
fi
SESSION="$(_charge session | tr -cd 'A-Za-z0-9._-' | cut -c1-64)"
[ -n "$SESSION" ] || SESSION=inconnue
EMPREINTE="$(printf '%s' "$CORPS" | git hash-object --stdin 2>/dev/null)"
[ -n "$EMPREINTE" ] && [ "$(cat "$VUES$SESSION" 2>/dev/null)" = "$EMPREINTE" ] && exit 0
printf '%s' "$EMPREINTE" > "$VUES$SESSION" 2>/dev/null
if [ "$EVENEMENT" = PostToolUse ]; then
  # En cours de tour, Claude Code donne au modèle le `additionalContext` d'un JSON.
  case "$MOTEUR" in
    jq) jq -n --arg t "$TEXTE" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $t}}' ;;
    python3) printf '%s' "$TEXTE" | python3 -c 'import json, sys
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": sys.stdin.read()}}, ensure_ascii=False))' ;;
  esac
  exit 0
fi
printf '%s\n' "$TEXTE"
exit 0
