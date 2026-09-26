#!/usr/bin/env bash
# Carte structurelle : donne à l'agent la forme du dépôt pour ~300 tokens
# au lieu de lui faire lire des dizaines de fichiers.
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "impossible d'atteindre la racine du dépôt" >&2; exit 1; }

# Détection des répertoires de code. Ne JAMAIS coder `src` en dur : la moitié des
# dépôts Python posent leur paquet à la racine (`moncode/__init__.py`). Un script qui
# suppose `src/` rend une carte silencieusement incomplète — et un agent y croit.
detect_src_dirs() {
  local dirs=""
  if [ -d src ]; then
    dirs="src"
  else
    for d in */; do
      d=${d%/}
      case "$d" in .*|tests|test|docs|doc|build|dist|node_modules|venv|htmlcov) continue ;; esac
      [ -f "$d/__init__.py" ] && dirs="$dirs $d"
    done
  fi
  [ -z "$dirs" ] && dirs="."
  printf '%s' "$dirs"
}
detect_test_dirs() {
  local dirs=""
  for d in tests test; do [ -d "$d" ] && dirs="$dirs $d"; done
  printf '%s' "$dirs"
}

SRC_DIRS=$(detect_src_dirs)
TEST_DIRS=$(detect_test_dirs)
ALL_DIRS="$SRC_DIRS $TEST_DIRS"
echo "== arbre (profondeur 3, code seulement) =="
# Sans les dossiers cachés : sur un petit projet équipé, ceux d'Ephor et des agents (.ephor,
# .claude, .opencode, .github, .vscode) faisaient vingt-six lignes sur quarante-deux, payées en
# tokens à chaque carte (mesuré le 25/09/2026). La forme du projet, c'est son code.
if command -v fd >/dev/null 2>&1; then
  fd -t d -d 3 -E node_modules -E __pycache__ -E mutants | head -60
else
  find . -mindepth 1 -maxdepth 3 -type d \( -name '.*' -o -name 'node_modules' -o -name '__pycache__' -o -name 'mutants' \) -prune -o -type d -print | head -60
fi
echo
echo "== code détecté : $SRC_DIRS =="
echo
echo "== symboles de haut niveau =="
if [ -f .ephor/repo_map.py ] && command -v python3 >/dev/null 2>&1; then
  # Lecture de la syntaxe : la vraie imbrication, les `async def` compris, et un cache.
  python3 .ephor/repo_map.py --max-lines 120
elif command -v ast-grep >/dev/null 2>&1; then
  # shellcheck disable=SC2016,SC2086  # $ du motif ast-grep non expansés ; $SRC_DIRS découpé volontairement
  ast-grep --pattern 'def $NAME($$$)' --lang python -l $SRC_DIRS 2>/dev/null | head -40
else
  # shellcheck disable=SC2086  # découpage voulu : liste de répertoires
  grep -rn --include='*.py' -E '^(class|def|async def) ' $SRC_DIRS 2>/dev/null | head -40
fi
echo
echo "== volumétrie =="
if command -v tokei >/dev/null 2>&1 && tokei --output compact 2>/dev/null; then
  :
else
  # `find -exec` plutôt que `cat $(find ...)` : un chemin contenant un espace
  # casserait le découpage de mots et le compte serait faux sans le dire.
  # shellcheck disable=SC2086
  echo "fichiers py: $(find $ALL_DIRS -name '*.py' 2>/dev/null | wc -l | tr -d ' ')"
  # shellcheck disable=SC2086
  echo "lignes py:   $(find $ALL_DIRS -name '*.py' -exec cat {} + 2>/dev/null | wc -l | tr -d ' ')"
fi
echo
echo "== points d'entrée =="
# shellcheck disable=SC2086
grep -rln --include='*.py' -E 'if __name__|FastAPI\(|Flow\(|@flow|def main' $SRC_DIRS 2>/dev/null | head -10 || true
# Sortie explicite : ce script LIT, il n'échoue jamais. Un code de retour non nul
# ferait croire à l'agent que l'outil est cassé et lui ferait perdre des tours.
exit 0
