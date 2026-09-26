#!/usr/bin/env bash
# PreToolUse (tous outils) — interrupteur d'arrêt opérateur.
# `make stop` crée AGENT_STOP ; le hook de budget aussi, en écrivant sa raison dedans.
# Tout appel d'outil est alors refusé, et l'agent lit pourquoi.
# Coût : trois lignes. Valeur : vous reprenez la main sans tuer le process.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée, donc cette garde ne peut rien juger. Elle refuse. Vérifie le dépôt : fichier manquant, droits, ou installation à moitié faite." >&2; exit 2; }
ROOT="$(racine)"
if [ -f "$ROOT/AGENT_STOP" ]; then
  # Une exception, et une seule : écrire PROGRESS.md. C'est ce qu'on lui demande de faire
  # avant de s'arrêter ; le lui interdire ferait tourner l'agent dans le vide.
  # Un lot mixte ne passe pas : il suffirait sinon de joindre PROGRESS.md à autre chose.
  if moteur_ok; then
    CIBLES="$(cibles)"
    if [ -n "$CIBLES" ]; then
      SEULEMENT=1
      while IFS= read -r CIBLE; do
        [ -n "$CIBLE" ] || continue
        [ "$CIBLE" = "$ROOT/PROGRESS.md" ] || SEULEMENT=0
      done <<CIBLES_LISTE
$CIBLES
CIBLES_LISTE
      [ "$SEULEMENT" = "1" ] && exit 0
    fi
  fi
  RAISON="$(head -c 400 "$ROOT/AGENT_STOP" 2>/dev/null | tr '\n' ' ')"
  [ -n "$RAISON" ] || RAISON="l'opérateur a demandé l'arrêt."
  echo "AGENT_STOP est présent : $RAISON Résume ton état dans PROGRESS.md puis arrête-toi. Ne supprime pas AGENT_STOP." >&2
  exit 2
fi
exit 0
