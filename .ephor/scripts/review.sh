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

# La copie de l'opérateur, d'où la revue est lancée : son juge et son Makefile sont les siens.
PRINCIPAL="$(pwd -P)"
# Une tâche vit dans son dossier (#32) : la revue s'y fait. Lancée du dossier principal, elle
# vérifiait la branche principale, et le juge n'y voyait « rien à juger » (01/10/2026).
AILLEURS="$(git worktree list --porcelain 2>/dev/null \
  | awk -v b="branch refs/heads/task/$TASK" 'index($0, "worktree ") == 1 { d = substr($0, 10) } $0 == b { print d; exit }')"
if [ -n "$AILLEURS" ] && [ -d "$AILLEURS" ] && [ "$(cd "$AILLEURS" && pwd -P)" != "$(pwd -P)" ]; then
  echo "-> $TASK est ouverte dans son dossier : la revue s'y fait ($AILLEURS)"
  cd "$AILLEURS" || exit 1
fi

# Rangée ou non dans un lot (tasks/<lot>/<ID>/, #35) : le juge sait où est la tâche.
# Le juge de la copie de l'opérateur, jamais celui du dossier de la tâche (voir plus bas).
JUGE="$PRINCIPAL/.ephor/judge.py"
DOSSIER="$(python3 -B "$JUGE" dossier --repo . --task-id "$TASK" 2>/dev/null)" || DOSSIER="tasks/${TASK}"
CONTRACT="$DOSSIER/contract.json"
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
  DESIGNEE="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  DESIGNEE="${DESIGNEE#origin/}"
  for REF in ${DESIGNEE:+"$DESIGNEE" "origin/$DESIGNEE"} main master origin/main origin/master; do
    if git rev-parse --verify -q "$REF^{commit}" >/dev/null 2>&1; then BASE="$REF"; break; fi
  done
fi
BASE="${BASE:-main}"
DOSSIER_BASE="$(python3 -B "$JUGE" dossier --repo . --task-id "$TASK" --ref "$BASE" 2>/dev/null)" || DOSSIER_BASE="$DOSSIER"
APPROUVE="$(mktemp)"
trap 'rm -f "$APPROUVE"' EXIT
if ! git show "${BASE}:${DOSSIER_BASE}/contract.json" > "$APPROUVE" 2>/dev/null; then
  echo "Erreur : $CONTRACT n'existe pas sur $BASE — c'est l'approbation qui manque." >&2
  exit 1
fi

# La revue LANCE du code du dépôt : verify.sh, le Makefile, le juge. Sur la branche de la tâche,
# l'agent a pu les retoucher, et la revue les lançait avec les droits de l'opérateur : « 2
# étape(s) sur 2 vertes » avec un juge réécrit, là où `make judge` refusait (audit du
# 01/10/2026). Une zone de contrôle qui diffère du point de départ de la branche, commitée ou
# non : rien n'est lancé. Lu par le juge de la copie de l'opérateur, pas par celui de la tâche.
if [ "$(git branch --show-current 2>/dev/null)" = "task/$TASK" ]; then
  DEPART="$(git merge-base HEAD "$BASE" 2>/dev/null || true)"
  # Et ce que `git update-index --skip-worktree|--assume-unchanged` cache à `git diff`.
  RETOUCHEES="$( { [ -z "$DEPART" ] || git diff --name-only "$DEPART" --; git ls-files --others --exclude-standard; git ls-files -v | sed -n 's/^[Sh] //p'; } 2>/dev/null \
    | python3 -B -c 'import sys
sys.path.insert(0, sys.argv[1])
import judge
for ligne in sys.stdin:
    if ligne.strip() and judge.is_protected(ligne.strip()):
        print(ligne.strip())' "$PRINCIPAL/.ephor" | LC_ALL=C sort -u | tr '\n' ' ')"
  if [ -n "$RETOUCHEES" ]; then
    echo "REFUS : la branche de la tâche retouche des zones de contrôle ($RETOUCHEES) ; la revue ne" >&2
    echo "lance pas ce code avec vos droits, et le juge refusera le correctif. git diff $BASE -- <fichier>." >&2
    exit 1
  fi
fi
# GNU make lit GNUmakefile puis makefile AVANT Makefile : posé dans le dossier de la tâche,
# même hors de git (.git/info/exclude), l'un ou l'autre détournait les étapes de la revue
# (« ok » en 0 s, relecture du 01/10/2026). Le disque, pas git ; et le Makefile de VOTRE copie.
for DETOUR in GNUmakefile makefile; do
  if [ -e "$DETOUR" ]; then
    echo "REFUS : $PWD/$DETOUR existe : GNU make le lirait à la place du Makefile, et la revue" >&2
    echo "lancerait ce que l'agent y a mis. Retirez-le (git status --ignored) avant de relancer." >&2
    exit 1
  fi
done
MAKE_SUR() { make --no-print-directory -f "$PRINCIPAL/Makefile" "$@"; }

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
  local nom="$1" sortie
  shift
  TOTAL=$((TOTAL + 1))
  echo "-> $nom"
  sortie="$(mktemp)"
  if "$@" 2>&1 | tee "$sortie"; [ "${PIPESTATUS[0]}" -eq 0 ]; then
    # « GREEN PARTIEL… ce n'est pas une validation », dit verify.sh lui-même : la revue la
    # comptait verte (audit du 01/10/2026). Et le « PARTIEL — pas contrôlé : gitleaks » de
    # `make sec`, en risque élevé, l'était encore (relecture du 01/10/2026).
    if grep -qE 'GREEN PARTIEL|^  PARTIEL — pas contrôlé' "$sortie"; then
      ECHECS="${ECHECS:+$ECHECS, }$nom (partielle)"
    else
      VERTES=$((VERTES + 1))
    fi
  else
    ECHECS="${ECHECS:+$ECHECS, }$nom"
  fi
  rm -f "$sortie"
}

CONSEIL=""
case "$RISK" in
  faible)
    echo "-> Risque faible : vérification locale puis jugement."
    etape "vérification" bash .ephor/projet/verify.sh
    etape "juge" make --no-print-directory -C "$PRINCIPAL" judge TASK="$TASK"
    ;;
  modere)
    echo "-> Risque modéré : vérification complète + échantillon de régression."
    etape "vérification" bash .ephor/projet/verify.sh
    etape "échantillon de régression (test-fast)" MAKE_SUR test-fast
    etape "juge" make --no-print-directory -C "$PRINCIPAL" judge TASK="$TASK"
    CONSEIL="CONSEIL : sur un risque modéré, faites relire le patch par le sous-agent evaluator."
    ;;
  eleve)
    echo "-> Risque élevé : vérification stricte + sécurité + mutations."
    etape "vérification stricte" env VERIFY_STRICT=1 bash .ephor/projet/verify.sh
    etape "secrets et fuites de données (make sec)" MAKE_SUR sec
    etape "score de mutation (make mutants)" MAKE_SUR mutants
    etape "juge" make --no-print-directory -C "$PRINCIPAL" judge TASK="$TASK"
    CONSEIL="CONSEIL : sur un risque élevé, la revue humaine est obligatoire avant la fusion."
    ;;
  *)
    echo "Niveau de risque inconnu : $RISK. Exécution standard."
    etape "vérification" bash .ephor/projet/verify.sh
    etape "juge" make --no-print-directory -C "$PRINCIPAL" judge TASK="$TASK"
    ;;
esac

echo ""
echo "============================================================"
echo " revue : $VERTES étape(s) sur $TOTAL vertes${ECHECS:+ — en échec : $ECHECS}"
echo "============================================================"
[ -z "$CONSEIL" ] || echo "$CONSEIL"
[ -z "$ECHECS" ]
