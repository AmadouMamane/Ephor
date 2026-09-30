#!/usr/bin/env bash
# Commande de revue proportionnée au risque du contrat.
#   Usage : bash .ephor/scripts/review.sh <TASK_ID>
# Chaque étape tourne, même après un échec, et la revue finit par son bilan. En `set -e`, un
# `verify` rouge arrêtait tout : le juge ne tournait pas, et rien ne résumait la revue.
set -uo pipefail
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

VERTES=0
TOTAL=0
ECHECS=""
etape() { # etape <nom> <commande...> : lancée quoi qu'il soit arrivé avant, et comptée
  local nom="$1"
  shift
  TOTAL=$((TOTAL + 1))
  echo "-> $nom"
  if "$@"; then VERTES=$((VERTES + 1)); else ECHECS="${ECHECS:+$ECHECS, }$nom"; fi
}

CONSEIL=""
case "$RISK" in
  faible)
    echo "-> Risque faible : vérification locale puis jugement."
    etape "vérification" bash .ephor/projet/verify.sh
    etape "juge" make judge TASK="$TASK"
    ;;
  modere)
    echo "-> Risque modéré : vérification complète + échantillon de régression."
    etape "vérification" bash .ephor/projet/verify.sh
    etape "échantillon de régression (test-fast)" make test-fast
    etape "juge" make judge TASK="$TASK"
    CONSEIL="CONSEIL : sur un risque modéré, faites relire le patch par le sous-agent evaluator."
    ;;
  eleve)
    echo "-> Risque élevé : vérification stricte + sécurité + mutations."
    etape "vérification stricte" env VERIFY_STRICT=1 bash .ephor/projet/verify.sh
    etape "secrets et fuites de données (make sec)" make sec
    etape "score de mutation (make mutants)" make mutants
    etape "juge" make judge TASK="$TASK"
    CONSEIL="CONSEIL : sur un risque élevé, la revue humaine est obligatoire avant la fusion."
    ;;
  *)
    echo "Niveau de risque inconnu : $RISK. Exécution standard."
    etape "vérification" bash .ephor/projet/verify.sh
    etape "juge" make judge TASK="$TASK"
    ;;
esac

echo ""
echo "============================================================"
echo " revue : $VERTES étape(s) sur $TOTAL vertes${ECHECS:+ — en échec : $ECHECS}"
echo "============================================================"
[ -z "$CONSEIL" ] || echo "$CONSEIL"
[ -z "$ECHECS" ]
