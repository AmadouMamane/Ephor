#!/usr/bin/env bash
# Score de mutation : mes tests VERRAIENT-ILS la différence si le comportement était
# réellement cassé ? Question bien plus dure que la couverture — un composant peut
# afficher 100 % de couverture de lignes et n'attraper que 61 % des mutants.
#
# Stratégie délibérément SÉLECTIVE : uniquement les fichiers changés depuis la base.
# La passe complète du dépôt appartient à l'étage post-merge de la CI. Un mutation
# testing lancé sur tout à chaque édition est un outil qu'on désactive en une semaine.
#   usage : .ephor/scripts/mutants.sh [base_git]
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "impossible d'atteindre la racine du dépôt" >&2; exit 1; }
# Base de comparaison. Attention : `git rev-parse HEAD~1` sur un dépôt à un seul
# commit écrit "HEAD~1" sur stdout ET échoue — un `||` naïf concatène deux valeurs.
# `--verify --quiet` est la forme qui se tait vraiment.
pick_base() {
  [ -n "${1:-}" ] && { printf '%s' "$1"; return; }
  for ref in main master; do
    git rev-parse --verify --quiet "$ref" >/dev/null 2>&1 || continue
    b=$(git merge-base HEAD "$ref" 2>/dev/null) && [ -n "$b" ] && { printf '%s' "$b"; return; }
  done
  b=$(git rev-parse --verify --quiet HEAD~1 2>/dev/null) && [ -n "$b" ] && { printf '%s' "$b"; return; }
  printf 'HEAD'
}
BASE=$(pick_base "${1:-}")
V=.verify; mkdir -p "$V"
R=""; command -v uv >/dev/null 2>&1 && R="uv run"

# On compare l'arbre de travail à la base : couvre le commité, l'indexé et le modifié.
# (pas de glob `src/**/*.py` en pathspec git : il faudrait la magie :(glob) — on filtre après)
CHANGED=$(git diff --name-only "$BASE" -- src 2>/dev/null | grep '\.py$' | tr '\n' ' ')
if [ -z "$CHANGED" ]; then
  echo "aucun fichier source changé — rien à muter (c'est le cas nominal)"
  exit 0
fi

# src/app/service.py -> "app.service.*" : le nom du mutant part de la racine du paquet,
# pas du dossier src/. mutmut 3 filtre par NOM de mutant, jamais par chemin.
FILTERS=""
for f in $CHANGED; do
  m=${f#src/}; m=${m%.py}
  FILTERS="$FILTERS $(printf '%s' "$m" | tr '/' '.').*"
done
echo "mutation testing sur :$(printf '%s' " $CHANGED")"

# shellcheck disable=SC2086
$R mutmut run $FILTERS 2>&1 | tr '\r' '\n' > "$V/mutants-run.log"

# Le compte fiable est dans le rapport final de `run`, section "Mutant results" :
# une ligne par mutant, préfixée 🎉 (tué) ou 🙁 (survivant), en colonne 0.
# `mutmut results` ne liste PAS les tués (seulement survivants et non vérifiés),
# et les lignes de la barre de progression commencent par un caractère braille —
# d'où l'ancrage en début de ligne.
sed -n '/^Mutant results/,$p' "$V/mutants-run.log" | grep -E '^(🎉|🙁|⏰|🤔) ' > "$V/mutants.txt" || true

KILLED=$(grep -c '^🎉 ' "$V/mutants.txt" 2>/dev/null; true)
SURVIVED=$(grep -c '^🙁 ' "$V/mutants.txt" 2>/dev/null; true)
TOTAL=$(( KILLED + SURVIVED ))
echo "détail -> $V/mutants.txt  (log brut : $V/mutants-run.log)"
if [ "$TOTAL" -eq 0 ]; then
  echo "aucun mutant exécuté — vérifiez que les fichiers changés contiennent du code testable"
  exit 0
fi
SCORE=$(( KILLED * 100 / TOTAL ))
echo "score de mutation : ${SCORE}%  (${KILLED} tués, ${SURVIVED} survivants sur ${TOTAL})"
echo
echo "Les survivants sont des modifications de votre code que vos tests n'ont PAS vues :"
grep '^🙁 ' "$V/mutants.txt" | head -10 | sed 's/^🙁 /  /'
echo
echo "Inspecter un survivant : uv run mutmut show <nom_du_mutant>"
echo "Un score bas sur une zone à risque (auth, règles métier, calculs) est un bloquant."
echo "Un score bas sur du code sans enjeu ne veut rien dire — ne courez pas après la moyenne."
# Volontairement non bloquant : c'est un signal de revue, pas une porte de CI.
exit 0
