#!/usr/bin/env bash
# PreToolUse (tous outils) — interrupteur d'arrêt opérateur.
# `make stop` crée AGENT_STOP ; le hook de budget aussi, en écrivant sa raison dedans.
# Tout appel d'outil est alors refusé, et l'agent lit pourquoi.
# Coût : trois lignes. Valeur : vous reprenez la main sans tuer le process.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée, donc cette garde ne peut rien juger. Elle refuse. Vérifie le dépôt : fichier manquant, droits, ou installation à moitié faite." >&2; exit 2; }
ROOT="$(racine)"
ARRET="$ROOT/AGENT_STOP"
# Présent sous n'importe quelle forme : un dossier ou un lien posé sous ce nom à l'avance
# n'était pas vu (`[ -f ]`), et empêchait `make stop`, le plafond de tokens et la sentinelle
# d'écrire le bouton (relecture du 01/10/2026). Un bouton altéré arrête aussi.
present() { [ -e "$1" ] || [ -L "$1" ]; }
# `make stop` pose AGENT_STOP là où l'opérateur le lance : dans SA copie. L'agent, lui,
# travaille dans le dossier de la tâche, où ce bouton ne regardait pas (audit du 01/10/2026).
if ! present "$ARRET"; then
  PRINCIPALE="$(copie_principale "$ROOT")"
  if [ -n "$PRINCIPALE" ] && present "$PRINCIPALE/AGENT_STOP"; then ARRET="$PRINCIPALE/AGENT_STOP"; fi
fi
if present "$ARRET"; then
  # Une exception, et une seule : écrire PROGRESS.md. C'est ce qu'on lui demande de faire
  # avant de s'arrêter ; le lui interdire ferait tourner l'agent dans le vide.
  # Un lot mixte ne passe pas : il suffirait sinon de joindre PROGRESS.md à autre chose.
  # Et seulement par un outil d'édition du dossier : la charge d'un outil MCP (GitHub…), d'un
  # terminal ou d'un sous-agent qui porte `"path": "PROGRESS.md"` passait (audit du
  # 01/10/2026), alors qu'il écrit ailleurs ou lance n'importe quoi.
  case "$(outil)" in
    Write | Edit | MultiEdit | write | edit | patch | apply_patch | multiedit) EDITEUR=1 ;;
    create_file | replace_string_in_file | multi_replace_string_in_file | insert_edit_into_file) EDITEUR=1 ;;
    createFile | editFiles | */editFiles | */createFile | create | str_replace*) EDITEUR=1 ;;
    *) EDITEUR=0 ;;
  esac
  if [ "$EDITEUR" = 1 ] && moteur_ok; then
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
  if [ -f "$ARRET" ] && [ ! -L "$ARRET" ]; then
    RAISON="$(head -c 400 "$ARRET" 2>/dev/null | tr '\n' ' ')"
    [ -n "$RAISON" ] || RAISON="l'opérateur a demandé l'arrêt."
  else
    RAISON="AGENT_STOP n'est pas un fichier ordinaire (un dossier ou un lien) : le bouton d'arrêt a été altéré, et l'agent est arrêté. L'opérateur le retire lui-même."
  fi
  echo "AGENT_STOP est présent : $RAISON Résume ton état dans PROGRESS.md puis arrête-toi. Ne supprime pas AGENT_STOP." >&2
  exit 2
fi
exit 0
