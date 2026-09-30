#!/usr/bin/env bash
# opencode dans le bac à sable du système : `bash .ephor/scripts/opencode_bac_a_sable.sh
# [arguments d'opencode]`, à la place de `opencode`. Tout ce qu'opencode lance (son terminal,
# un script écrit puis lancé) ne peut plus écrire une zone de contrôle ni lire un `.env` :
# la frontière que Claude Code a déjà. Réglages et mesure : .ephor/scripts/srt_reglages.py.
#
# Il faut `srt` (npm install -g @anthropic-ai/sandbox-runtime). Réseau : fermé, sauf les
# domaines de EPHOR_SRT_DOMAINES (par défaut, ceux de Copilot et des dépendances) ; derrière
# un proxy, srt passe par celui de HTTP_PROXY / HTTPS_PROXY.
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "impossible d'atteindre la racine du dépôt" >&2; exit 1; }
if ! command -v srt >/dev/null 2>&1; then
  echo "srt absent de ce poste : npm install -g @anthropic-ai/sandbox-runtime"
  exit 2
fi
if ! command -v opencode >/dev/null 2>&1; then
  echo "opencode absent de ce poste"
  exit 2
fi
REGLAGES="$(mktemp "${TMPDIR:-/tmp}/ephor-srt.XXXXXX")" || exit 2
trap 'rm -f "$REGLAGES"' EXIT
python3 .ephor/scripts/srt_reglages.py > "$REGLAGES" || exit 2
DOMAINES="$(python3 -c 'import json, sys; print(" ".join(json.load(open(sys.argv[1]))["network"]["allowedDomains"]))' "$REGLAGES")"
echo "opencode dans le bac à sable : zones de contrôle et .env hors d'atteinte ; réseau : $DOMAINES" >&2
srt --settings "$REGLAGES" -- opencode "$@"
