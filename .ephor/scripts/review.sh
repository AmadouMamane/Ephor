#!/usr/bin/env bash
# Commande de revue proportionnée au risque du contrat.
#   Usage : bash .ephor/scripts/review.sh <TASK_ID>
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

TASK="${1:-}"
if [ -z "$TASK" ]; then
  echo "usage : make review TASK=<ID>" >&2
  exit 2
fi

CONTRACT="tasks/${TASK}/contract.json"
if [ ! -f "$CONTRACT" ]; then
  echo "Erreur : contrat $CONTRACT introuvable." >&2
  exit 1
fi

# Le risque est lu dans la version APPROUVÉE du contrat, celle de la branche principale,
# jamais dans le fichier de l'arbre de travail. Sinon il suffisait de passer « eleve » à
# « faible » pour faire sauter le contrôle des secrets et le score de mutation — le juge
# refuserait le contrat modifié ensuite, mais les contrôles chers auraient déjà été sautés
# et l'affichage aurait menti à l'opérateur.
# Sans BASE, les mêmes candidats que le juge, dans le même ordre : `main` seul faisait dire
# « l'approbation manque » sur un dépôt en `master`, où le juge trouvait le contrat.
if [ -z "${BASE:-}" ]; then
  for REF in main master origin/main origin/master; do
    if git rev-parse --verify -q "$REF^{commit}" >/dev/null 2>&1; then BASE="$REF"; break; fi
  done
fi
BASE="${BASE:-main}"
APPROUVE="$(mktemp)"
trap 'rm -f "$APPROUVE"' EXIT
if ! git show "${BASE}:${CONTRACT}" > "$APPROUVE" 2>/dev/null; then
  echo "Erreur : $CONTRACT n'existe pas sur $BASE — c'est l'approbation qui manque." >&2
  exit 1
fi

# Le chemin passe en ARGUMENT, jamais dans le texte du programme : il contient
# l'identifiant de tâche, c'est-à-dire un nom de branche que l'agent choisit. Collé dans
# une chaîne Python, il faisait exécuter ce qu'il voulait, avec les droits de l'opérateur.
RISK=$(python3 - "$APPROUVE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fichier:
    print(json.load(fichier).get("risk", "faible"))
PY
)
echo "============================================================"
echo " Revue de la tâche : $TASK"
echo " Niveau de risque   : $RISK"
echo "============================================================"

case "$RISK" in
  faible)
    echo "-> Risque faible : vérification locale puis jugement."
    bash .ephor/projet/verify.sh
    echo "-> Exécution du juge..."
    make judge TASK="$TASK"
    ;;
  modere)
    echo "-> Risque modéré : vérification complète + échantillon de régression."
    bash .ephor/projet/verify.sh
    echo "-> Exécution de l'échantillon de régression (test-fast)..."
    make test-fast
    echo "-> Exécution du juge..."
    make judge TASK="$TASK"
    echo ""
    echo "CONSEIL : sur un risque modéré, faites relire le patch par le sous-agent evaluator."
    ;;
  eleve)
    echo "-> Risque élevé : vérification stricte + sécurité + mutations."
    VERIFY_STRICT=1 bash .ephor/projet/verify.sh
    echo "-> Vérification des secrets et fuites de données (make sec)..."
    make sec
    echo "-> Score de mutation sur les fichiers changés (make mutants)..."
    make mutants
    echo "-> Exécution du juge..."
    make judge TASK="$TASK"
    echo ""
    echo "CONSEIL : sur un risque élevé, la revue humaine est obligatoire avant la fusion."
    ;;
  *)
    echo "Niveau de risque inconnu : $RISK. Exécution standard."
    bash .ephor/projet/verify.sh
    make judge TASK="$TASK"
    ;;
esac
