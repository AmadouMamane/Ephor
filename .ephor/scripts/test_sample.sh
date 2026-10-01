#!/usr/bin/env bash
# Échantillon déterministe-mais-aléatoire de la suite de tests.
# Motif emprunté au compilateur C d'Anthropic : chaque agent parallèle exécute un
# sous-ensemble différent mais reproductible -> les régressions sortent vite, pour une
# fraction du temps CPU et des tokens.
#
# Portabilité : l'échantillonnage se fait en Python, pas avec `shuf`, qui est GNU-only
# et absent de macOS par défaut. Un script de harnais qui ne tourne que sur Linux est
# un script qui ne tourne pas sur la machine de la moitié de l'équipe.
#   usage : .ephor/scripts/test_sample.sh [POURCENTAGE] [SEED]
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "impossible d'atteindre la racine du dépôt" >&2; exit 1; }
PCT="${1:-10}"
# Un pourcentage qui n'en est pas un (`FAST_PCT=abc`) faisait tomber le tirage en Python,
# puis lançait la suite ENTIÈRE sous l'annonce « 0/N tests » (relecture du 01/10/2026).
case "$PCT" in
  ''|*[!0-9]*) echo "FAST_PCT : un entier de 1 à 100 attendu, pas « $PCT »" >&2; exit 2 ;;
esac
[ "$PCT" -gt 100 ] && PCT=100
[ "$PCT" -lt 1 ] && PCT=1
SEED="${2:-$(git rev-parse HEAD 2>/dev/null | tr -cd '0-9' | cut -c1-8)}"
SEED="${SEED:-42}"
V=.verify; mkdir -p "$V"
R=""; command -v uv >/dev/null 2>&1 && R="uv run"

# `-p no:randomly` est indispensable : pytest-randomly réordonne la collecte à chaque
# exécution. Sans ordre canonique, le même seed ne donne pas le même échantillon —
# et un échantillonnage non reproductible ne sert à rien.
$R pytest --collect-only -q -p no:randomly 2>/dev/null | sed '/^$/,$d' | LC_ALL=C sort > "$V/all-tests.txt"
TOTAL=$(grep -c . "$V/all-tests.txt" 2>/dev/null; true)
if [ "${TOTAL:-0}" -eq 0 ]; then
  # Sans Python, pytest n'a rien à collecter : ce n'est pas « aucun test » (F5).
  # Hors du moteur : ses `.ephor/*.py` faisaient passer un projet Node pour du Python.
  if git ls-files -- '*.py' ':(exclude).ephor' | grep -q .; then
    echo "aucun test collecté"
  else
    echo "SKIP : l'échantillon ne sait tirer que des tests pytest ; lancez la suite du projet"
  fi
  exit 0
fi

python3 - "$V/all-tests.txt" "$PCT" "$SEED" > "$V/sample.txt" <<'PY'
import random, sys
lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
pct, seed = int(sys.argv[2]), sys.argv[3]
n = max(1, len(lines) * pct // 100)
random.Random(seed).shuffle(lines)          # tirage reproductible pour un seed donné
print("\n".join(lines[:n]))
PY

N=$(grep -c . "$V/sample.txt" 2>/dev/null; true)
# Un échantillon vide, c'est pytest sans argument : toute la suite, sous une fausse annonce.
[ "${N:-0}" -gt 0 ] || { echo "échantillon vide : le tirage a échoué (voir $V/sample.txt)" >&2; exit 1; }
echo "échantillon: $N/$TOTAL tests (seed=$SEED)"
# shellcheck disable=SC2046
$R pytest -q -p no:randomly $(tr '\n' ' ' < "$V/sample.txt") > "$V/test-fast.txt" 2>&1
rc=$?; tail -n 1 "$V/test-fast.txt"; exit $rc
