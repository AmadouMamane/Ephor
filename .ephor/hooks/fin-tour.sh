#!/usr/bin/env bash
# Quand l'agent rend la main (Stop), sous les trois outils : le point d'entrée de la fin de
# tour (ADR 0011). Budget de la tâche, contrôle de la sentinelle et point de reprise, alerte de
# contexte, rappel de PROGRESS.md, lancés ensemble. Chacun dit ce qu'il a à dire : l'alerte de
# la sentinelle (code 1, pour que l'outil la montre) n'étouffe pas celle du contexte, redite
# alors en clair ; et leurs réponses JSON sortent en un seul objet, que l'outil sait lire.
set -uo pipefail
ICI="$(cd -- "$(dirname -- "$0")" && pwd -P)"
CHARGE="$(cat)"
AGENT="${EPHOR_OUTIL:-claude}"
SCRIPTS=()
[ "$AGENT" = opencode ] || SCRIPTS+=(budget.sh) # opencode : pas de plafond par tâche
SCRIPTS+=(checkpoint.sh)
[ "$AGENT" = copilot ] || SCRIPTS+=(contexte.sh) # Copilot ne donne ses tokens qu'à la fin
SCRIPTS+=(progres.sh)
PRESENTS=()
for S in "${SCRIPTS[@]}"; do [ -f "$ICI/$S" ] && PRESENTS+=("$S"); done
SCRIPTS=(${PRESENTS[@]+"${PRESENTS[@]}"})
[ "${#SCRIPTS[@]}" -gt 0 ] || exit 0
TMP="$(mktemp -d "${TMPDIR:-/tmp}/ephor-fin.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -rf "$TMP"' EXIT
PIDS=()
for S in "${SCRIPTS[@]}"; do
  printf '%s' "$CHARGE" | bash "$ICI/$S" >"$TMP/$S.sortie" 2>"$TMP/$S.erreur" &
  PIDS+=($!)
done
CODE=0
for I in "${!SCRIPTS[@]}"; do
  wait "${PIDS[I]}"
  C=$?
  [ "$C" -gt "$CODE" ] && CODE=$C
done
for S in "${SCRIPTS[@]}"; do cat "$TMP/$S.erreur" >&2 2>/dev/null; done
# Plusieurs réponses JSON (l'alerte de contexte, le rappel de PROGRESS.md) : un seul objet, les
# messages mis bout à bout. Deux objets à la suite, et l'outil n'en lisait aucun.
fusionne() { # fusionne <sortie>... : les objets JSON en un seul, le reste tel quel
  python3 -c 'import json, sys
fusion, messages = {}, []
for chemin in sys.argv[1:]:
    try:
        texte = open(chemin, encoding="utf-8").read().strip()
    except OSError:
        continue
    if not texte:
        continue
    try:
        objet = json.loads(texte)
    except ValueError:
        objet = None
    if not isinstance(objet, dict):
        print(texte)
        continue
    if objet.get("systemMessage"):
        messages.append(str(objet.pop("systemMessage")))
    for cle, valeur in objet.items():
        fusion.setdefault(cle, valeur)
if messages:
    fusion["systemMessage"] = "\n".join(messages)
if fusion:
    print(json.dumps(fusion, ensure_ascii=False))' "$@"
}
if [ "$CODE" -eq 0 ]; then
  SORTIES=()
  for S in "${SCRIPTS[@]}"; do SORTIES+=("$TMP/$S.sortie"); done
  fusionne "${SORTIES[@]}" 2>/dev/null || for S in "${SCRIPTS[@]}"; do cat "$TMP/$S.sortie" 2>/dev/null; done
elif [ -s "$TMP/contexte.sh.sortie" ]; then
  # Un code non nul fait ignorer le JSON par l'outil : l'alerte de contexte passe en clair.
  python3 -c 'import json, sys; print(json.load(sys.stdin).get("systemMessage", ""))' \
    <"$TMP/contexte.sh.sortie" >&2 2>/dev/null || true
fi
exit "$CODE"
