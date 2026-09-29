#!/usr/bin/env bash
# Stop — la conversation dépasse 60 % de la mémoire du modèle : on le dit à l'opérateur.
#
# La règle du README (`/handoff`, puis une nouvelle session) n'était appliquée par personne :
# l'agent ne voit pas son propre remplissage, seul l'opérateur le voit, en bas de l'écran, et
# il ne le regarde pas. Ce hook ne vide rien lui-même : vider en pleine tâche, avant que
# l'état soit écrit dans PROGRESS.md, perdrait du travail. Il prévient, à chaque fin de tour.
#
# Claude Code : la charge porte le journal, on y mesure le remplissage. opencode : le greffon
# passe `{"contexte": {"occupe": …, "taille": …}}`, qu'il est seul à connaître, et affiche le
# message. Copilot ne donne ses tokens qu'à la fermeture de la session : rien à mesurer.
# Le calcul et le seuil vivent dans usage.py (`alerte_contexte`), testés là.
set -uo pipefail
command -v python3 >/dev/null 2>&1 || exit 0
python3 "$(dirname "$0")/../usage.py" contexte 2>/dev/null || true
exit 0
