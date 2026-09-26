#!/usr/bin/env bash
# pytest pour la CI : tolère le code 5 (« aucun test collecté »).
#
# Pourquoi ce script existe : `pytest -m "integration"` sur un projet qui n'a pas
# encore de test d'intégration sort en 5, et GitHub Actions traite tout code non nul
# comme un échec. Sans ce garde-fou, la file de merge d'un projet neuf est bloquée
# dès le premier jour — et le réflexe sera de désactiver l'étage, pas de le réparer.
#   usage : .ephor/scripts/pytest_ci.sh [arguments pytest...]
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "racine du dépôt introuvable" >&2; exit 1; }
R=""; command -v uv >/dev/null 2>&1 && R="uv run"
# shellcheck disable=SC2086  # $R doit être découpé ("uv run" = deux arguments)
$R pytest "$@"
rc=$?
if [ "$rc" -eq 5 ]; then
  echo "note: aucun test ne correspond à la sélection — considéré comme succès en CI"
  exit 0
fi
exit "$rc"
