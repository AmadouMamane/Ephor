#!/usr/bin/env bash
# Après chaque appel d'outil (PostToolUse) : le point d'entrée (ADR 0011). Aujourd'hui, la
# consigne de l'opérateur donnée en cours de tour (consigne.sh).
exec bash "$(dirname "$0")/consigne.sh" "$@"
