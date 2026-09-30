#!/usr/bin/env bash
# Au message de l'opérateur (UserPromptSubmit), sous les trois outils : le point d'entrée du
# début de tour (ADR 0011). Photo de la sentinelle, autre session dans le même dossier,
# consigne de l'opérateur : le détail vit dans steer.sh (modes `photo` et `consigne` pour le
# greffon d'opencode).
exec bash "$(dirname "$0")/steer.sh" "$@"
