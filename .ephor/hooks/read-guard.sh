#!/usr/bin/env bash
# PreToolUse (lectures) — ne pas lire ce que les réglages refusent.
#
# Claude Code applique lui-même ses règles `Read(...)`, et opencode a les siennes, dérivées.
# Copilot n'a pas d'équivalent : sans ce hook, le même dépôt laisserait un agent lire
# `.env`, une clé privée ou `~/.aws`. On applique donc la même liste, lue à la source.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée, donc cette garde ne peut rien juger. Elle refuse. Vérifie le dépôt : fichier manquant, droits, ou installation à moitié faite." >&2; exit 2; }
# Sans moteur JSON, cette garde ne voit RIEN : elle sortait en « autorisé », et une lecture
# de `.env` passait sans un mot. Un contrôle aveugle n'est pas un contrôle.
moteur_ok || deny "REFUSÉ : cette garde doit lire l'appel d'outil pour le juger, et ce poste n'a ni jq ni python3. Elle refuse plutôt que de laisser passer sans rien voir. Installe l'un des deux (python3 suffit), puis recommence."
# Voir write-guard.sh : un disque insensible à la casse rendait la liste contournable.
shopt -s nocasematch
ROOT="$(racine)"
REGLAGES="$ROOT/.claude/settings.json"
[ -f "$REGLAGES" ] || exit 0

# `view`, `glob`, `rg` et `grep` sont les outils de lecture de Copilot ; un nom absent est
# celui de Claude Code, qui filtre déjà lui-même. `str_replace_editor` lit AUSSI (sa
# commande `view`) : il passe ici, quitte à refuser d'écrire un fichier qu'on n'a pas le
# droit de lire. Le reste ne concerne pas ce hook.
OUTIL="$(outil)"
case "$OUTIL" in
  ""|Read|read|readFile|NotebookRead|view|glob|rg|grep|str_replace_editor|*/readFile|*readFile*) ;;
  read_file|list_dir) ;;  # noms du panneau de Copilot : sans eux, `.env` s'y lisait librement
  *) exit 0 ;;
esac

MOTIFS="$(refus "$REGLAGES" Read)"
[ -z "$MOTIFS" ] && exit 0

# Une lecture peut viser plusieurs fichiers d'un coup (`rg` prend une liste) : on les
# examine tous, sinon il suffit d'en grouper deux pour passer.
while IFS= read -r CIBLE; do
  [ -n "$CIBLE" ] || continue
  RELATIF="$CIBLE"
  case "$RELATIF" in "$ROOT"/*) RELATIF="${RELATIF#"$ROOT"/}" ;; esac
  while IFS= read -r MOTIF; do
    [ -z "$MOTIF" ] && continue
    case "$MOTIF" in ./*) MOTIF="${MOTIF#./}" ;; "~/"*) MOTIF="$HOME/${MOTIF#\~/}" ;; esac
    # shellcheck disable=SC2254  # le motif est un glob, c'est voulu
    case "$RELATIF" in $MOTIF) SOURCE="$MOTIF" ;; *) case "$CIBLE" in $MOTIF) SOURCE="$MOTIF" ;; *) continue ;; esac ;; esac
    deny "REFUSÉ : $CIBLE correspond à « $SOURCE », refusé à la lecture par les réglages du dépôt. Secrets, clés et identifiants ne doivent pas entrer dans un prompt. Utilise une variable d'environnement, ou demande à l'opérateur."
  done <<MOTIFS_LISTE
$MOTIFS
MOTIFS_LISTE
done <<CIBLES_LISTE
$(cibles)
CIBLES_LISTE
exit 0
