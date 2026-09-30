#!/usr/bin/env bash
# Plusieurs garde-fous sur UN appel d'outil, lancés en même temps ; le verdict est celui qu'ils
# rendraient l'un après l'autre.
#   usage : ensemble.sh <garde.sh>...      (la charge de l'appel sur l'entrée standard)
#
# Sous Copilot, les sept gardes d'un appel tournaient l'une après l'autre, lectures comprises ;
# sous opencode, les quatre d'une écriture : 0,4 à 0,6 s par appel d'outil, sur un poste
# rapide (audit du 29/09/2026). Chaque garde coûte surtout son démarrage et la lecture de la
# charge, qu'elle ait ou non affaire à cet outil. Lancées ensemble, elles coûtent deux fois
# moins. Sous Claude Code, rien ne change : il lance déjà ses hooks ensemble.
#
# Aucune garde n'est sautée : chacune reconnaît elle-même les outils qui la regardent, avec
# ses replis prudents. Trier les gardes ici aurait recopié ces règles, et une erreur de
# recopie aurait laissé passer un appel.
#
# Le verdict ne dépend pas de l'ordre d'arrivée. Les sorties sont rendues dans l'ordre des
# arguments, comme si les gardes avaient tourné l'une après l'autre : la sortie d'erreur de
# chacune, sa sortie standard si elle laisse passer, et au premier refus, son message et son
# code, rien de ce qui suit. Les gardes ne font que lire : lancer celles d'après un refus ne
# change rien.
#
# Une garde qui n'a pas rendu son verdict en EPHOR_GARDES_DELAI secondes (20 par défaut) est
# arrêtée, et compte pour un refus. Avant, elle bloquait l'appel jusqu'au délai de l'outil,
# qui, lui, laisse passer (Copilot, Claude Code).
#
# EPHOR_GARDES_EN_SERIE=1 : l'une après l'autre, arrêt au premier refus, comme avant la 1.3.
# Même verdict ; à prendre si lancer plusieurs processus à la fois pose problème sur un poste
# (un antivirus qui les inspecte un par un, par exemple).
set -uo pipefail
ICI="$(cd -- "$(dirname -- "$0")" && pwd -P)"
CHARGE="$(cat)"
[ "$#" -gt 0 ] || exit 0

en_serie() {
  local GARDE SORTIE CODE
  for GARDE in "$@"; do
    [ -f "$ICI/$GARDE" ] || continue
    SORTIE="$(printf '%s' "$CHARGE" | bash "$ICI/$GARDE")"
    CODE=$?
    [ "$CODE" = "0" ] || exit "$CODE"
    [ -n "$SORTIE" ] && printf '%s\n' "$SORTIE"
  done
  exit 0
}

[ "${EPHOR_GARDES_EN_SERIE:-0}" = "1" ] && en_serie "$@"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ephor-gardes.XXXXXX" 2>/dev/null)" || en_serie "$@"
trap 'rm -rf "$TMP"' EXIT
printf '%s' "$CHARGE" >"$TMP/charge"
DELAI="${EPHOR_GARDES_DELAI:-20}"
case "$DELAI" in "" | *[!0-9]*) DELAI=20 ;; esac

N=0
PIDS=()
for GARDE in "$@"; do
  N=$((N + 1))
  PIDS[N]=""
  [ -f "$ICI/$GARDE" ] || continue
  bash "$ICI/$GARDE" <"$TMP/charge" >"$TMP/$N.sortie" 2>"$TMP/$N.erreur" &
  PIDS[N]=$!
done
# Le veilleur : au-delà du délai, il arrête les gardes qui tournent encore, et le note.
# Arrêté plus tôt, il arrête aussi son `sleep` : sans cela, chaque appel d'outil laissait un
# processus dormir vingt secondes.
(
  sleep "$DELAI" &
  SOMMEIL=$!
  trap 'kill "$SOMMEIL" 2>/dev/null; exit 0' TERM
  wait "$SOMMEIL" || exit 0
  touch "$TMP/delai"
  for PID in "${PIDS[@]}"; do [ -n "$PID" ] && kill "$PID" 2>/dev/null; done
) &
VEILLEUR=$!

N=0
CODES=()
for GARDE in "$@"; do
  N=$((N + 1))
  CODES[N]=""
  [ -n "${PIDS[N]}" ] || continue
  wait "${PIDS[N]}"
  CODES[N]=$?
done
kill "$VEILLEUR" 2>/dev/null
wait "$VEILLEUR" 2>/dev/null

N=0
for GARDE in "$@"; do
  N=$((N + 1))
  [ -n "${CODES[N]}" ] || continue
  cat "$TMP/$N.erreur" >&2 2>/dev/null
  CODE="${CODES[N]}"
  # Arrêtée par le veilleur : elle n'a rien dit, et ne rien dire n'est pas un « oui ».
  if [ "$CODE" -ge 128 ] && [ -f "$TMP/delai" ]; then
    echo "REFUSÉ : le garde-fou $GARDE n'a pas rendu son verdict en $DELAI s. Réessaie ; si ça se répète, dis-le à l'opérateur (make doctor)." >&2
    exit 2
  fi
  [ "$CODE" = "0" ] || exit "$CODE"
  SORTIE="$(cat "$TMP/$N.sortie" 2>/dev/null)"
  [ -n "$SORTIE" ] && printf '%s\n' "$SORTIE"
done
exit 0
