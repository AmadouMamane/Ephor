#!/usr/bin/env bash
# PreToolUse (Bash) — compacter les sorties bavardes SANS fausser le résultat.
#
# Règles tenues, quoi qu'il arrive :
#  1. On ne réécrit qu'une commande SIMPLE **déjà autorisée** dans .claude/settings.json.
#     Ce hook rend une décision « allow » : s'il réécrivait une commande que l'opérateur
#     n'a pas autorisée, il l'approuverait à sa place. Il ne le fait pas.
#  2. Un enchaînement (; & | < > $ ` # parenthèses, accolades, retour à la ligne) passe
#     tel quel par le circuit normal des permissions.
#  3. Le code de retour reste celui de la commande. Un échec reste un échec.
#  4. Le résumé garde toujours la ligne qui conclut (« N passed », « Found N errors »…).
#  5. Le journal complet va dans .verify/last-<genre>.txt, jamais dans la conversation.
set -uo pipefail

# RTK installé ? Il fait le même travail et pose son propre hook : on s'efface.
command -v rtk >/dev/null 2>&1 && exit 0

# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || exit 0   # confort : sans la bibliothèque, on s'efface
# Seul hook qui doit ÉCRIRE du JSON en réponse : celui-là garde jq comme condition, et
# s'efface sans lui. Ne rien compacter ne coûte que du contexte, jamais de la sécurité.
jq_ok || exit 0
CMD="$(field '.tool_input.command')"
[ -z "$CMD" ] && exit 0

case "$CMD" in
  *[\;\&\|\<\>\$\`\(\)\{\}\\\#]*|*$'\n'*) exit 0 ;;
esac

ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
REGLAGES="$ROOT/.claude/settings.json"
[ -f "$REGLAGES" ] || exit 0

# La commande est-elle DÉJÀ autorisée sans question ? Sinon, on ne touche à rien.
autorisee() {
  local motif
  while IFS= read -r motif; do
    [ -z "$motif" ] && continue
    case "$motif" in *:\*) motif="${motif%:\*} *" ;; esac   # `x:*` vaut `x *`
    # shellcheck disable=SC2254      # le motif est un glob, c'est voulu
    case "$CMD" in $motif) return 0 ;; esac
    # `x *` autorise aussi la commande nue `x`, comme chez Claude Code
    case "$motif" in *" *") case "$CMD" in "${motif% \*}") return 0 ;; esac ;; esac
  done < <(jq -r '.permissions.allow[]? | select(startswith("Bash(")) | .[5:-1]' "$REGLAGES" 2>/dev/null)
  return 1
}
autorisee || exit 0

# Genre de sortie, et ce qu'on en garde.
ERREURS='^(FAILED|ERROR|E  )|Traceback|error:|panicked'
case "$CMD" in
  *pytest*|"npm test"*|"go test"*|"cargo test"*) GENRE=test ;;
  *ruff*|*eslint*|*golangci-lint*) GENRE=lint ;;
  *mypy*|*tsc*|*lint-imports*) GENRE=types ;;
  "uv sync"*|"uv pip install"*|"npm install"*|"npm ci"*) GENRE=install ;;
  "git log"*|"git show --stat"*|"git blame"*) GENRE=log ;;
  *) exit 0 ;;
esac

DIR="$(printf '%q' "$ROOT/.verify")"
LOG="$(printf '%q' "$ROOT/.verify/last-$GENRE.txt")"
case "$GENRE" in
  test)          RESUME="grep -E -A4 '$ERREURS' $LOG | head -80; tail -n 1 $LOG" ;;
  lint|types)    RESUME="grep -E -v '^(Success|All checks)' $LOG | head -60; tail -n 1 $LOG" ;;
  install)       RESUME="tail -n 3 $LOG" ;;
  log)           RESUME="head -n 40 $LOG" ;;
esac

NEW="mkdir -p $DIR && { $CMD ; } > $LOG 2>&1; rc=\$?; $RESUME; echo '(journal complet : .verify/last-$GENRE.txt)'; exit \$rc"
printf '%s' "$INPUT" | jq --arg c "$NEW" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow",updatedInput:(.tool_input + {command:$c})}}'
exit 0
