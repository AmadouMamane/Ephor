#!/usr/bin/env bash
# Avant chaque appel d'outil (PreToolUse), sous les trois outils : LE point d'entrée des
# garde-fous (ADR 0011). Les réglages de Claude Code, le greffon de Copilot et celui
# d'opencode n'appellent que lui ; quelles gardes tournent, et pour quel outil, se décide ici,
# dans le moteur, que la mise à jour remplace. Ajouter une garde ne touche plus aucun fichier
# du projet, ni le greffon de Copilot installé sur chaque poste.
#
# Les gardes d'un appel tournent ensemble (ensemble.sh) : même verdict qu'en série. Elles sont
# choisies par famille d'appel (terminal, lecture, le reste), avec la règle de la bibliothèque
# qui dit si un appel écrit ; un appel qu'on ne sait pas classer passe toutes les gardes, et
# chacune reconnaît elle-même les outils qui la regardent. EPHOR_OUTIL (claude, copilot,
# opencode : posé par le relais de Copilot et le greffon d'opencode) dit ce qui change d'un
# outil à l'autre.
set -uo pipefail
ICI="$(cd -- "$(dirname -- "$0")" && pwd -P)"
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$ICI/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée : rien ne peut être jugé, donc rien ne passe. Préviens l'opérateur." >&2; exit 2; }
AGENT="${EPHOR_OUTIL:-claude}"
ECRIT=(block-secrets.sh write-guard.sh scope-guard.sh refs-guard.sh)
# Le filtre de sortie réécrit une commande de test : seul Claude Code lit sa réponse.
TERMINAL=(shell-guard.sh)
[ "$AGENT" = claude ] && TERMINAL+=(filter-output.sh)
# La lecture d'un secret : Claude Code et opencode la refusent par leurs réglages, Copilot non.
# Un outil qui lit ET écrit (str_replace_editor) passe aussi par elle : elle ne juge que les
# lectures.
LIT=()
[ "$AGENT" = copilot ] && LIT+=(read-guard.sh)
case "$(outil)" in
  Bash | bash | shell | powershell | run_in_terminal | runInTerminal | */run_in_terminal | */runInTerminal)
    GARDES=("${TERMINAL[@]}") ;;
  Read | read | readFile | read_file | NotebookRead | view | Glob | glob | Grep | grep | LS | list | list_dir | file_search | grep_search | */readFile)
    GARDES=(${LIT[@]+"${LIT[@]}"}) ;;
  *)
    if ecriture; then
      GARDES=("${ECRIT[@]}" ${LIT[@]+"${LIT[@]}"})
    else
      GARDES=("${ECRIT[@]}" "${TERMINAL[@]}" ${LIT[@]+"${LIT[@]}"})
    fi ;;
esac
printf '%s' "$INPUT" | bash "$ICI/ensemble.sh" kill-switch.sh ${GARDES[@]+"${GARDES[@]}"}
