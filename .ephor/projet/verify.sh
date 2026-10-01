#!/usr/bin/env bash
# CE FICHIER EST AU PROJET. Il dit ce que « vert » veut dire ICI : adaptez les étapes aux
# commandes du dépôt (format, lint, types, tests). Une mise à jour d'Ephor ne le remplace
# jamais — c'est le sens de `.ephor/projet/`. Le reste de `.ephor/` est à Ephor.
#
# LA commande de vérification. Conçue pour un LLM, pas pour un humain :
#   - sortie console minimale (le contexte est une ressource rare)
#   - tout le détail dans .verify/*.txt
#   - statistiques pré-calculées pour que l'agent n'ait pas à les recalculer
#
# Distinction essentielle en environnement contraint : un outil ABSENT n'est pas
# un test en ÉCHEC. Les confondre produit un mur de rouge qui ne dit rien et qu'on
# finit par ignorer. Ici un outil manquant donne SKIP, et le verdict le dit — un
# GREEN n'est jamais annoncé comme complet s'il reste des étapes non exécutées.
#   VERIFY_STRICT=1  -> les SKIP deviennent des échecs (à utiliser en CI)
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "racine du dépôt introuvable" >&2; exit 1; }
V=.verify; mkdir -p "$V"
FAIL=0; SKIP=0
STRICT="${VERIFY_STRICT:-0}"

if command -v uv >/dev/null 2>&1; then R="uv run"; else R=""; fi

have() {  # l'exécutable est-il réellement disponible ?
  if [ -n "$R" ]; then [ -x ".venv/bin/$1" ]; else command -v "$1" >/dev/null 2>&1; fi
}

skip() { # skip <nom> <ce qui manque> — une étape non exécutée n'est pas une étape verte
  printf '  SKIP %-8s  (%s absent)\n' "$1" "$2"
  echo "outil absent : $2" > "$V/$1.txt"
  SKIP=$((SKIP+1)); [ "$STRICT" = "1" ] && FAIL=1
}

step() { # step <nom> <exécutable> <commande...>
  local name="$1" exe="$2"; shift 2
  if ! have "$exe"; then
    skip "$name" "$exe"
    return 0
  fi
  # shellcheck disable=SC2086  # $R doit être découpé ("uv run" = deux arguments)
  if $R "$@" > "$V/$name.txt" 2>&1; then
    printf '  ok   %-8s\n' "$name"
  else
    printf '  FAIL %-8s  -> %s\n' "$name" "$V/$name.txt"
    FAIL=1
  fi
  return 0
}

echo "verify @ $(git rev-parse --short HEAD 2>/dev/null || echo 'no-git') $(date -u +%H:%M:%SZ)"

if [ -n "$R" ] && [ ! -d .venv ]; then
  # Sans pyproject.toml, `make setup` refuse : le conseil tournait en rond dans un projet Node
  # ou Go, la boucle que doctor avait déjà corrigée chez lui (audit du 01/10/2026).
  if [ -f pyproject.toml ]; then
    echo "  environnement absent — lancez 'make setup' (ou 'make doctor' si setup échoue)"
  else
    echo "  projet sans pyproject.toml — adaptez ce fichier (.ephor/projet/verify.sh) à ses commandes"
  fi
fi

step fmt    ruff         ruff format --check .
step lint   ruff         ruff check --output-format=concise .
step types  mypy         mypy src tests
step arch   lint-imports lint-imports
# Données personnelles dans les journaux : bibliothèque standard seule. La règle « aucune
# donnée client dans un log » était écrite sans être appliquée. Il ne lit que le Python :
# du code d'un autre langage, non lu, donne SKIP (code 3), jamais un faux « ok ».
if [ ! -f .ephor/personal_data.py ]; then
  skip fuites .ephor/personal_data.py
else
  python3 .ephor/personal_data.py --code-si-partiel 3 > "$V/fuites.txt" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '  ok   %-8s\n' fuites
  elif [ "$rc" -eq 3 ]; then
    printf '  SKIP %-8s  (code non Python non lu -> %s)\n' fuites "$V/fuites.txt"
    SKIP=$((SKIP+1)); [ "$STRICT" = "1" ] && FAIL=1
  else
    printf '  FAIL %-8s  -> %s\n' fuites "$V/fuites.txt"
    FAIL=1
  fi
fi
# Tests : PYTEST_MARK filtre par marqueur (la CI pré-fusion exclut les lents), et le
# code 5 de pytest (« aucun test sélectionné ») n'est pas un échec.
if ! have pytest; then
  skip test pytest
else
  # Le code de retour seul ne suffit pas : une ligne `os._exit(0)` dans le code testé fait
  # sortir pytest en 0 avant le premier test (reproduit le 24/09/2026). Une suite allée au
  # bout écrit son rapport ; une suite coupée n'en écrit pas.
  rm -f "$V/test-junit.xml"
  # shellcheck disable=SC2086  # $R doit être découpé ("uv run" = deux arguments)
  $R pytest -q --maxfail=1 ${PYTEST_MARK:+-m "$PYTEST_MARK"} --junitxml="$V/test-junit.xml" > "$V/test.txt" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ] && [ ! -s "$V/test-junit.xml" ]; then
    printf '  FAIL %-8s  -> tests arrêtés avant la fin, aucun rapport (%s)\n' test "$V/test.txt"
    FAIL=1
  elif [ "$rc" -eq 0 ] || [ "$rc" -eq 5 ]; then
    printf '  ok   %-8s\n' test
  else
    printf '  FAIL %-8s  -> %s\n' test "$V/test.txt"
    FAIL=1
  fi
fi

{
  echo "commit=$(git rev-parse --short HEAD 2>/dev/null || echo none)"
  echo "changed_files=$(git diff --name-only HEAD 2>/dev/null | wc -l | tr -d ' ')"
  # La forme concise d'un constat (`chemin:ligne:colonne: CODE …`) seule : un avertissement de
  # ruff sur sa configuration (`chemin:ligne: …`) était compté comme un constat.
  echo "lint_findings=$(grep -cE '^[^ ].*:[0-9]+:[0-9]+: [A-Z]+[0-9]+ ' "$V/lint.txt" 2>/dev/null; true)"
  echo "type_errors=$(grep -c 'error:' "$V/types.txt" 2>/dev/null; true)"
  echo "tests_line=$(tail -n 1 "$V/test.txt" 2>/dev/null)"
  echo "steps_skipped=$SKIP"
  echo "task_branch=$(git branch --show-current 2>/dev/null | grep '^task/' || echo none)"
} > "$V/stats.txt"
sed 's/^/  /' "$V/stats.txt"

if [ $FAIL -ne 0 ]; then
  echo "VERDICT: RED (lisez le fichier indiqué, pas tout le log)"
elif [ $SKIP -gt 0 ]; then
  echo "VERDICT: GREEN PARTIEL — $SKIP étape(s) NON exécutée(s), outillage incomplet"
  echo "         ce n'est pas une validation. Lancez 'make doctor' pour savoir quoi installer."
else
  echo "VERDICT: GREEN"
fi
exit $FAIL
