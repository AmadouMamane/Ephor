#!/usr/bin/env bash
# PreToolUse (écritures) — un dépôt de référence se lit, il ne s'écrit jamais.
#
# Le garde-périmètre laisse passer une écriture hors du dépôt de travail, et il a raison :
# le juge ne verra jamais ce fichier, donc il ne peut rien en dire. C'est précisément le
# problème quand on déclare des dépôts à lire (schémas amont, référentiels partagés) :
# une écriture qui y atterrit ne serait jugée par personne. Elle est refusée ici.
#
# Vaut sur toutes les branches, avec ou sans contrat : ce n'est pas une question de
# périmètre de tâche, c'est une question de propriété.
#
# Portée honnête, comme les autres hooks : ce filet attrape l'écriture par un outil de
# fichier, quel que soit son nom, quel que soit le champ où le chemin est passé. Il ne
# voit PAS une redirection shell (`echo x > ...`) : la barrière, côté Bash, reste la
# liste de commandes autorisées. Ce hook fait gagner du temps, il ne fait pas de bac à sable.
#
# Fermé en cas de doute, contrairement au garde-périmètre : si la déclaration est
# illisible ou l'outillage absent, on refuse. Un contrôle qui s'ouvre quand il ne sait
# pas ne protège rien — et ici, personne ne repassera derrière.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée, donc cette garde ne peut rien juger. Elle refuse. Vérifie le dépôt : fichier manquant, droits, ou installation à moitié faite." >&2; exit 2; }
ROOT="$(racine)"
# Rien de déclaré : rien à protéger.
[ -f "$ROOT/.ephor/projet/reference-repos.json" ] || exit 0
[ -f "$ROOT/.ephor/refs.py" ] || exit 0

moteur_ok || deny "REFUSÉ : contrôler les dépôts de référence demande de lire l'appel d'outil, et ce poste n'a ni jq ni python3. Installe l'un des deux (python3 suffit)."
charge_ok || deny "REFUSÉ : l'appel d'outil est illisible (ce n'est pas un objet JSON), donc on ne peut pas savoir où cette écriture atterrit. Préviens l'opérateur."
command -v python3 >/dev/null 2>&1 || deny "REFUSÉ : python3 est requis pour contrôler les dépôts de référence."

# On ne se fie pas au nom de l'outil : n'importe lequel qui porte un chemin est examiné.
# Les lectures passent d'elles-mêmes, puisqu'un dépôt de référence est fait pour être lu.
# `read_file`, `list_dir`, `file_search`, `grep_search` : les noms du panneau de Copilot.
# Absents, une lecture y était jugée comme une écriture (vu le 24/09/2026).
OUTIL="$(outil)"
case "$OUTIL" in
  Read|read|readFile|NotebookRead|Grep|Glob|view|glob|rg|grep|*readFile*|*/readFile) exit 0 ;;
  read_file|list_dir|file_search|grep_search|semantic_search) exit 0 ;;
esac

CHEMINS="$(cibles)"
[ -z "$CHEMINS" ] && exit 0

while IFS= read -r CIBLE; do
  [ -n "$CIBLE" ] || continue
  # Une écriture DANS le dépôt de travail passe sans lire la déclaration (voir `dans_le_depot`
  # dans refs.py) : une déclaration cassée ne bloque plus que ce qui sort du dépôt.
  DEPOT="$(python3 "$ROOT/.ephor/refs.py" --repo "$ROOT" vise --chemin "$CIBLE" 2>&1)"
  case $? in
    0) ;;
    3) deny "REFUSÉ : $DEPOT est un dépôt de référence, en lecture seule. On le lit (make refs, make map REPO=<nom>), on n'y écrit jamais. Besoin d'y changer quelque chose ? C'est une autre tâche, dans cet autre dépôt, avec son propre contrat." ;;
    *) deny "REFUSÉ : cette écriture sort du dépôt de travail, et la déclaration des dépôts de référence est inutilisable (${DEPOT#erreur : }) : on ne peut pas savoir si elle y atterrit. C'est une erreur de configuration, que seul l'opérateur corrige : arrête-toi et préviens-le." ;;
  esac
done <<EOF
$CHEMINS
EOF
exit 0
