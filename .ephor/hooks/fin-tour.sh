#!/usr/bin/env bash
# Quand l'agent rend la main (Stop), sous les trois outils : le point d'entrée de la fin de
# tour (ADR 0011). Budget de la tâche, contrôle de la sentinelle et point de reprise, alerte de
# contexte, lancés ensemble. Chacun dit ce qu'il a à dire : l'alerte de la sentinelle (code 1,
# pour que l'outil la montre) n'étouffe pas celle du contexte, redite alors en clair.
set -uo pipefail
ICI="$(cd -- "$(dirname -- "$0")" && pwd -P)"
CHARGE="$(cat)"
AGENT="${EPHOR_OUTIL:-claude}"
SCRIPTS=()
[ "$AGENT" = opencode ] || SCRIPTS+=(budget.sh) # opencode : pas de plafond par tâche
SCRIPTS+=(checkpoint.sh)
[ "$AGENT" = copilot ] || SCRIPTS+=(contexte.sh) # Copilot ne donne ses tokens qu'à la fin
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ephor-fin.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -rf "$TMP"' EXIT
PIDS=()
for S in "${SCRIPTS[@]}"; do
  printf '%s' "$CHARGE" | bash "$ICI/$S" >"$TMP/$S.sortie" 2>"$TMP/$S.erreur" &
  PIDS+=($!)
done
CODE=0
for I in "${!SCRIPTS[@]}"; do
  wait "${PIDS[I]}"
  C=$?
  [ "$C" -gt "$CODE" ] && CODE=$C
done
for S in "${SCRIPTS[@]}"; do cat "$TMP/$S.erreur" >&2 2>/dev/null; done
if [ "$CODE" -eq 0 ]; then
  for S in "${SCRIPTS[@]}"; do cat "$TMP/$S.sortie" 2>/dev/null; done
elif [ -s "$TMP/contexte.sh.sortie" ]; then
  # Un code non nul fait ignorer le JSON par l'outil : l'alerte de contexte passe en clair.
  python3 -c 'import json, sys; print(json.load(sys.stdin).get("systemMessage", ""))' \
    <"$TMP/contexte.sh.sortie" >&2 2>/dev/null || true
fi
exit "$CODE"
