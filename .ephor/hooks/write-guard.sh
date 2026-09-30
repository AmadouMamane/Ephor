#!/usr/bin/env bash
# PreToolUse (écritures) — ne pas écrire une zone de contrôle.
#
# Les zones viennent du MOTEUR (`.ephor/zones.txt`), plus celles que les réglages du projet
# ajoutent par leurs refus `Edit(...)`. Mesuré le 25/09/2026 : l'outil d'édition écrivait
# `.git/config`, un `GNUmakefile` et la copie du greffon que VS Code exécute, parce qu'aucune
# liste ne les nommait ; et la liste lue dans les réglages était à portée de l'agent.
#
# Branché pour les trois outils : Claude Code (qui a aussi ses propres refus, mais seulement
# ceux des réglages du projet), opencode (son greffon) et Copilot (le relais), chez qui
# `false` fait DEMANDER et ne bloque pas. Le juge refuserait de toute façon un patch qui touche
# une zone du dépôt — mais dix minutes plus tard, et après avoir laissé l'agent croire qu'il
# avançait. Hors du dépôt, personne ne repasserait derrière.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée, donc cette garde ne peut rien juger. Elle refuse. Vérifie le dépôt : fichier manquant, droits, ou installation à moitié faite." >&2; exit 2; }
# Sans moteur JSON, cette garde ne voit RIEN : elle sortait en « autorisé », et l'agent
# pouvait écrire dans ses propres garde-fous sans que personne le sache.
moteur_ok || deny "REFUSÉ : cette garde doit lire l'appel d'outil pour le juger, et ce poste n'a ni jq ni python3. Elle refuse plutôt que de laisser passer sans rien voir. Installe l'un des deux (python3 suffit), puis recommence."
# Un disque Mac ne distingue pas la casse : `.EPHOR/judge.py` écrit bien dans `.ephor/`.
# Comparer en respectant la casse laissait donc passer la zone de contrôle.
shopt -s nocasematch
ROOT="$(racine)"
REGLAGES="$ROOT/.claude/settings.json"

# Copilot lance chaque hook pour chaque outil, sans filtre : on ne se prononce que sur
# une écriture, telle que `ecriture` la reconnaît (voir _lib.sh).
ecriture || exit 0

CIBLES="$(cibles)"
[ -z "$CIBLES" ] && exit 0

[ -f "$ZONES_MOTEUR" ] || deny "REFUSÉ : la liste des zones de contrôle ($ZONES_MOTEUR) est introuvable, donc cette garde ne sait pas ce qu'elle protège. Elle refuse. Installation à moitié faite : préviens l'opérateur."
MOTIFS="$(motifs_zones "$ROOT" "$REGLAGES")"

while IFS= read -r CIBLE; do
  [ -n "$CIBLE" ] || continue
  hors_zone "$CIBLE" && continue
  RELATIF=""
  case "$CIBLE" in "$ROOT"/*) RELATIF="${CIBLE#"$ROOT"/}" ;; esac
  while IFS= read -r MOTIF; do
    [ -z "$MOTIF" ] && continue
    case "$MOTIF" in
      /*)
        # shellcheck disable=SC2254  # le motif est un glob, c'est voulu
        case "$CIBLE" in $MOTIF) ;; *) continue ;; esac
        case "$CIBLE" in
          "$ROOT"/*) deny "REFUSÉ : $RELATIF est dans le dossier git du dépôt. Un agent n'a rien à y écrire : c'est là que vivent les hooks de git et sa configuration, que git exécute lui-même. Si c'est vraiment nécessaire, c'est l'opérateur qui le fait." ;;
          *) deny "REFUSÉ : $CIBLE est un réglage d'agent du POSTE, hors de tout dépôt. C'est là que vivent les garde-fous eux-mêmes : y écrire reviendrait à les désarmer. Si c'est vraiment nécessaire, c'est l'opérateur qui le fait, à la main." ;;
        esac
        ;;
      *)
        [ -n "$RELATIF" ] || continue
        # shellcheck disable=SC2254  # le motif est un glob, c'est voulu
        case "$RELATIF" in
          $MOTIF) deny "REFUSÉ : $RELATIF correspond à « $MOTIF », zone de contrôle refusée à l'écriture. Le juge refuse de toute façon tout patch qui y touche. Si le changement est vraiment nécessaire, c'est l'opérateur qui le fait." ;;
        esac
        ;;
    esac
  done <<MOTIFS_LISTE
$MOTIFS
MOTIFS_LISTE
done <<CIBLES_LISTE
$CIBLES
CIBLES_LISTE
exit 0
