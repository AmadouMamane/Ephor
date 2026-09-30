#!/usr/bin/env bash
# Stop — plafond de tokens par tâche.
#
# À la fin de chaque tour, on mesure ce que la session a consommé (journal de Claude Code
# ou de Copilot, lu en repartant d'où on s'était arrêté) et on l'écrit dans .verify/usage/<ID>.json :
# c'est ce que le juge rapportera. Si le contrat fixe `budget_tokens` et qu'il est dépassé,
# on pose AGENT_STOP avec la raison dedans — l'agent sera refusé au prochain appel d'outil.
#
# Ouverture volontaire en cas de doute : pas de branche de tâche, pas de contrat, pas de
# journal, et ce hook ne fait rien. Il mesure et alerte ; il ne garantit pas.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || exit 0   # confort : sans la bibliothèque, on s'efface
moteur_ok || exit 0
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
BRANCHE="$(git -C "$ROOT" branch --show-current 2>/dev/null || true)"
case "$BRANCHE" in task/*) ;; *) exit 0 ;; esac
ID="${BRANCHE#task/}"; ID="${ID%%/*}"
CONTRAT="$(contrat_de "$ROOT" "$ID")" || exit 0
JOURNAL="$(_charge journal)"
[ -f "$JOURNAL" ] || exit 0
# La vue « agents » de VS Code appelle aussi ce hook, et nomme SON journal (mesuré le
# 24/09/2026). Lu comme celui de Claude Code, il donnait « 0 token » : il a son lecteur.
# Copilot n'y écrit les tokens qu'à la fermeture de la session ; d'ici là, la mesure est
# un minimum et le dit. Dépassé, ce minimum suffit à arrêter.
LECTEUR=claude
case "$JOURNAL" in */.copilot/session-state/*) LECTEUR=copilot ;; esac

ETAT="$ROOT/.verify/usage/$ID.json"
mkdir -p "$(dirname "$ETAT")" 2>/dev/null || exit 0
MESURE="$(python3 "$ROOT/.ephor/usage.py" "$LECTEUR" "$JOURNAL" --etat "$ETAT" 2>/dev/null)" || exit 0
printf '%s\n' "$MESURE" > "$ETAT"

BUDGET="$(scalaire "$CONTRAT" budget_tokens)"
[ -z "$BUDGET" ] && exit 0
FACTURABLES="$(printf '%s' "$MESURE" | scalaire - facturables)"
# Deux entiers, sinon on ne fait rien : un contrat mal écrit ne doit pas arrêter l'agent
# par accident — c'est le juge qui le refusera.
case "$BUDGET" in *[!0-9]*) exit 0 ;; esac
case "$FACTURABLES" in ''|*[!0-9]*) exit 0 ;; esac
[ "$FACTURABLES" -le "$BUDGET" ] && exit 0
[ -f "$ROOT/AGENT_STOP" ] && exit 0
printf 'budget de la tâche %s dépassé (%s tokens facturables pour %s autorisés). Il faut relever le budget dans le contrat, sur la branche principale, ou découper la tâche ; puis `make resume`.\n' \
  "$ID" "$FACTURABLES" "$BUDGET" > "$ROOT/AGENT_STOP"
echo "Budget dépassé : $FACTURABLES > $BUDGET. AGENT_STOP posé." >&2
exit 0
