#!/usr/bin/env bash
# Score de mutation : mes tests VERRAIENT-ILS la différence si le comportement était
# réellement cassé ? Question bien plus dure que la couverture — un composant peut
# afficher 100 % de couverture de lignes et n'attraper que 61 % des mutants.
#
# Stratégie délibérément SÉLECTIVE : uniquement les fichiers changés depuis la base.
# La passe complète du dépôt appartient à l'étage post-merge de la CI (`--tout`). Un mutation
# testing lancé sur tout à chaque édition est un outil qu'on désactive en une semaine.
#   usage : .ephor/scripts/mutants.sh [base_git]
#           .ephor/scripts/mutants.sh --tout      passe complète, celle de la CI après fusion
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "impossible d'atteindre la racine du dépôt" >&2; exit 1; }
TOUT=""
if [ "${1:-}" = "--tout" ]; then TOUT=1; shift; fi
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
V=.verify; mkdir -p "$V"
R=""; command -v uv >/dev/null 2>&1 && R="uv run"

FILTERS=""
if [ -n "$TOUT" ]; then
  echo "mutation testing : passe complète du dépôt"
else
  BASE=$(pick_base "${1:-}")
  # On compare l'arbre de travail à la base : couvre le commité, l'indexé et le modifié.
  # (pas de glob `src/**/*.py` en pathspec git : il faudrait la magie :(glob) — on filtre après)
  CHANGED=$(git diff --name-only "$BASE" -- src 2>/dev/null | grep '\.py$' | tr '\n' ' ')
  if [ -z "$CHANGED" ]; then
    echo "aucun fichier source changé — rien à muter (c'est le cas nominal)"
    exit 0
  fi
  # src/app/service.py -> "app.service.*" : le nom du mutant part de la racine du paquet,
  # pas du dossier src/. mutmut 3 filtre par NOM de mutant, jamais par chemin.
  for f in $CHANGED; do
    m=${f#src/}; m=${m%.py}
    FILTERS="$FILTERS $(printf '%s' "$m" | tr '/' '.').*"
  done
  echo "mutation testing sur :$(printf '%s' " $CHANGED")"
fi

# Les tests de structure livrés par Ephor lisent les fichiers de src/ par leur chemin. Sous
# mutmut, ils lisent sa copie, où chaque fonction empile toutes ses variantes : dix fois plus
# longue, elle dépasse 400 lignes, et mutmut s'arrêtait avant le premier mutant (vu en session
# réelle : 289 lignes, copie de 4 771). Ils jugent la forme, pas le comportement : ils n'ont
# rien à faire ici. Un test du projet qui lit les sources par leur chemin a le même défaut :
# l'écarter dans pyproject.toml, `[tool.mutmut] pytest_add_cli_args = ["--ignore=…"]`.
if [ -f tests/test_architecture.py ]; then
  export PYTEST_ADDOPTS="--ignore=tests/test_architecture.py${PYTEST_ADDOPTS:+ $PYTEST_ADDOPTS}"
fi

# shellcheck disable=SC2086
$R mutmut run $FILTERS 2>&1 | tr '\r' '\n' > "$V/mutants-run.log"
CODE=${PIPESTATUS[0]}

# mutmut n'est allé au bout que s'il sort avec 0 ET a écrit son rapport de fin (« … mutations/
# second ») — il sort avec 0 même quand des mutants survivent. Arrêté en route, il n'a rien
# mesuré : c'est un échec, jamais un « aucun mutant » en vert (ce que ce script disait).
if [ "$CODE" -ne 0 ] || ! grep -q "mutations/second" "$V/mutants-run.log"; then
  if grep -q "Filtered for specific mutants, but nothing matches" "$V/mutants-run.log"; then
    echo "rien à muter : les fichiers changés n'ont pas de code que mutmut sache modifier"
    exit 0
  fi
  echo "ÉCHEC : mutmut s'est arrêté avant la fin (code $CODE) — aucun score de mutation."
  RAISON=$(grep -E '^(FAILED|ERROR) |failed to|Stopping early|Error: ' "$V/mutants-run.log" | tail -5)
  [ -n "$RAISON" ] || RAISON=$(grep -v '^[[:space:]]*$' "$V/mutants-run.log" | tail -5)
  printf '%s\n' "$RAISON" | sed 's/^/  /'
  echo "journal complet : $V/mutants-run.log"
  exit 1
fi

# Le compte fiable est dans le rapport final de `run`, section "Mutant results" :
# une ligne par mutant, préfixée 🎉 (tué) ou 🙁 (survivant), en colonne 0.
# `mutmut results` ne liste PAS les tués (seulement survivants et non vérifiés),
# et les lignes de la barre de progression commencent par un caractère braille —
# d'où l'ancrage en début de ligne.
sed -n '/^Mutant results/,$p' "$V/mutants-run.log" | grep -E '^(🎉|🙁|⏰|🤔) ' > "$V/mutants.txt" || true

KILLED=$(grep -c '^🎉 ' "$V/mutants.txt" 2>/dev/null; true)
SURVIVED=$(grep -c '^🙁 ' "$V/mutants.txt" 2>/dev/null; true)
# 🫥 : un mutant qu'aucun test n'exécute. Hors du score, qui ne dirait rien de ce code-là.
SANS_TEST=$(sed -n '/^Mutant results/,$p' "$V/mutants-run.log" | grep -c '^🫥 '; true)
TOTAL=$(( KILLED + SURVIVED ))
echo "détail -> $V/mutants.txt  (log brut : $V/mutants-run.log)"
if [ "$SANS_TEST" -gt 0 ]; then
  echo "${SANS_TEST} mutant(s) qu'aucun test n'exécute : ce code-là n'a pas de test du tout."
fi
if [ "$TOTAL" -eq 0 ]; then
  echo "ÉCHEC : aucun mutant n'a pu être jugé (délais dépassés, mutants écartés) — aucun score."
  exit 1
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
