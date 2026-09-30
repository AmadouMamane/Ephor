#!/usr/bin/env bash
# `make task-clore ID=<ID> [FORCE=1]` : ferme une tâche fusionnée (#32). Retire son dossier (le
# worktree ouvert par make task-worktree ou make task-approve) et sa branche locale, après avoir
# gardé ici ses jugements et sa mesure de tokens (make score, make usage). Une tâche pas encore
# fusionnée garde les siens, sauf FORCE=1 ; un dossier qui a du travail pas commité n'est
# jamais retiré.
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "impossible d'atteindre la racine du dépôt" >&2; exit 1; }
# Rien de compilé dans le moteur, une zone de contrôle : `-B`, comme le Makefile.
PY=(python3 -B)
# Geste de l'opérateur : refusé quand un agent le lance, détours compris (.ephor/operateur.py).
"${PY[@]}" ".ephor/operateur.py" "make task-clore" || exit 2
ID="${1:-}"
FORCE="${2:-}"
if ! printf '%s' "$ID" | grep -Eq '^[A-Z][A-Z0-9-]{2,50}$'; then
  echo "usage : make task-clore ID=FEAT-001 [FORCE=1]"
  exit 2
fi
PRINCIPALE=""
for branche in main master; do
  git rev-parse --verify -q "refs/heads/$branche" >/dev/null && { PRINCIPALE="$branche"; break; }
done
[ -n "$PRINCIPALE" ] || { echo "REFUS : branche principale introuvable (main, master)"; exit 2; }
BRANCHE="task/$ID"
DIR="$(git worktree list --porcelain | awk -v b="branch refs/heads/$BRANCHE" '/^worktree /{d=substr($0, 10)} $0 == b {print d}')"
# Fusionnée, au sens de make taches : un commit de la branche principale nomme task/<ID> (la
# fusion d'une merge request le fait), ou la branche, jugée VÉRIFIÉE, y est entrée. Le verdict
# se lit dans tous les dossiers du dépôt, celui de la tâche compris.
ETAT="$("${PY[@]}" .ephor/judge.py etat --task-id "$ID" 2>/dev/null)"
if [ "$ETAT" != "fusionnée" ] && [ "$FORCE" != 1 ]; then
  echo "REFUS : $ID n'est pas fusionnée sur $PRINCIPALE (${ETAT:-état inconnu}) : son dossier et sa branche restent."
  echo "Pour la fermer quand même (travail abandonné) : make task-clore ID=$ID FORCE=1"
  exit 2
fi
if [ -n "$DIR" ]; then
  if [ -n "$(git -C "$DIR" status --porcelain 2>/dev/null)" ]; then
    echo "REFUS : $DIR a du travail pas commité : commitez-le ou retirez-le, puis relancez."
    exit 1
  fi
  # Ses jugements et sa mesure de tokens restent ici : parti avec le dossier, tout cela
  # manquait ensuite à make score et à make usage (mesuré le 30/09/2026).
  "${PY[@]}" .ephor/usage.py rapatrier "$DIR" --repo "$PWD" \
    || echo "ATTENTION : jugements et mesure de $DIR pas rapatriés"
  if ! ERREUR="$(git worktree remove "$DIR" 2>&1)"; then
    echo "REFUS : $DIR ne s'est pas retiré : $ERREUR"
    exit 1
  fi
  echo "dossier retiré : $DIR"
fi
if git rev-parse --verify -q "refs/heads/$BRANCHE" >/dev/null; then
  git branch -q -D "$BRANCHE" && echo "branche locale retirée : $BRANCHE"
fi
echo "tâche $ID close."
