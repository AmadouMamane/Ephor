#!/usr/bin/env bash
# `make task-worktree ID=<ID> [DIR=<dossier>]` : ouvre une tâche approuvée dans son propre
# dossier (un worktree git), sur sa branche task/<ID>, pour qu'une session d'agent y travaille
# seule. Deux sessions dans un même dossier partagent la branche et les fichiers, et se
# marchent dessus (vécu en session réelle, le 30/09/2026) : une tâche par worktree, une
# session par tâche. Réservé à l'opérateur : il écrit les réglages locaux de Claude Code.
#
# Mesuré le 30/09/2026 avec Claude Code 2.1.285, dans une vraie session : depuis un worktree,
# le bac à sable laisse commiter, et protège la configuration et les hooks du dépôt principal,
# mais pas son dossier `.git/info/` (exclusions, attributs), que la règle Edit(.git/info/**)
# protège dans le dossier principal. Le script l'ajoute aux réglages locaux du worktree.
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "impossible d'atteindre la racine du dépôt" >&2; exit 1; }
# Geste de l'opérateur : refusé quand un agent le lance, détours compris (.ephor/operateur.py).
python3 ".ephor/operateur.py" "make task-worktree" || exit 2
ID="${1:-}"
DIR="${2:-}"
if ! printf '%s' "$ID" | grep -Eq '^[A-Z][A-Z0-9-]{2,50}$'; then
  echo "usage : make task-worktree ID=FEAT-001 [DIR=../dossier]"
  exit 2
fi
PRINCIPALE=""
for branche in main master; do
  git rev-parse --verify -q "refs/heads/$branche" >/dev/null && { PRINCIPALE="$branche"; break; }
done
[ -n "$PRINCIPALE" ] || { echo "REFUS : branche principale introuvable (main, master)"; exit 2; }
if ! git cat-file -e "$PRINCIPALE:tasks/$ID/contract.json" 2>/dev/null; then
  echo "REFUS : pas de contrat approuvé pour $ID sur $PRINCIPALE (tasks/$ID/contract.json commité)."
  echo "L'approbation d'abord : make task-new, make task-check, puis le commit sur $PRINCIPALE."
  exit 2
fi
RACINE="$(pwd -P)"
[ -n "$DIR" ] || DIR="$(dirname "$RACINE")/$(basename "$RACINE")-$ID"
if [ -e "$DIR" ]; then
  echo "REFUS : $DIR existe déjà. Autre dossier : make task-worktree ID=$ID DIR=<dossier>"
  exit 2
fi
if git rev-parse --verify -q "refs/heads/task/$ID" >/dev/null; then
  # git refuse lui-même, et le dit, une branche déjà ouverte dans un autre dossier.
  git -c core.hooksPath=/dev/null worktree add -q "$DIR" "task/$ID" || exit 2
else
  git -c core.hooksPath=/dev/null worktree add -q -b "task/$ID" "$DIR" "$PRINCIPALE" || exit 2
fi
DIR="$(cd "$DIR" && pwd -P)"
COMMUN="$(git rev-parse --path-format=absolute --git-common-dir)"

# Le bac à sable de Claude Code, tel que le projet le règle, plus `.git/info/` du dépôt
# principal. Recopié en entier, pas seulement complété : on ne dépend pas de la façon dont
# Claude Code fusionne les réglages locaux avec ceux du projet.
if [ -f "$DIR/.claude/settings.json" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$DIR/.claude/settings.json" "$DIR/.claude/settings.local.json" "$COMMUN/info" <<'PY'
import json
import sys

projet, local, info = sys.argv[1:4]
with open(projet, encoding="utf-8") as fichier:
    bac = json.load(fichier).get("sandbox")
if isinstance(bac, dict) and bac.get("enabled"):
    systeme = dict(bac.get("filesystem") or {})
    systeme["denyWrite"] = [*systeme.get("denyWrite", []), info]
    with open(local, "w", encoding="utf-8") as fichier:
        json.dump({"sandbox": {**bac, "filesystem": systeme}}, fichier, indent=2)
        fichier.write("\n")
PY
  # Propres à ce poste : jamais commités. Sans quoi un `git add -A` de l'agent les emportait,
  # et le juge refusait la tâche (.claude/ est une zone).
  if [ -f "$DIR/.claude/settings.local.json" ] \
    && ! git -C "$DIR" check-ignore -q .claude/settings.local.json 2>/dev/null; then
    printf '%s\n' "/.claude/settings.local.json" >> "$COMMUN/info/exclude"
  fi
fi

echo "Tâche $ID ouverte dans $DIR, sur task/$ID."
if grep -q '^setup:' "$DIR/Makefile" 2>/dev/null; then
  if (cd "$DIR" && make setup >/dev/null 2>&1); then
    echo "environnement prêt (make setup)"
  else
    echo "ATTENTION : make setup a échoué dans ce dossier : relancez-le là-bas."
  fi
fi
echo "Lancez votre agent DANS ce dossier (cd \"$DIR\", puis claude ou opencode) : une session"
echo "par tâche. Après la fusion : git worktree remove \"$DIR\""
# Le niveau et l'effort que le contrat approuvé demande : la ligne de lancement qui va avec.
CONTRAT="$(git show "$PRINCIPALE:tasks/$ID/contract.json" 2>/dev/null)"
python3 - "$CONTRAT" <<'LANCEMENT' 2>/dev/null || true
import json
import os
import sys
from pathlib import Path

try:
    contrat = json.loads(sys.argv[1])
except ValueError:
    sys.exit(0)
niveau, effort = contrat.get("niveau"), contrat.get("effort")
if niveau not in ("rapide", "standard", "fort"):
    sys.exit(0)
alias = {"rapide": "haiku", "standard": "sonnet", "fort": "opus"}[niveau]
print(f"Le contrat demande le niveau {niveau}" + (f", effort {effort}" if effort else "") + " :")
print(f"  Claude Code : claude --model {alias}" + (f" --effort {effort}" if effort else ""))
dossier = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"
try:
    agents = json.loads((dossier / "opencode.json").read_text(encoding="utf-8")).get("agent", {})
except (OSError, ValueError):
    agents = {}
role = {"rapide": "scout", "standard": "reviewer", "fort": "planner"}[niveau]
modele = (agents.get(role) or {}).get("model")
if modele:
    variante = f" (effort : opencode run --variant {effort})" if effort else ""
    print(f"  opencode    : opencode --model {modele}{variante}")
else:
    print("  opencode    : réglez d'abord les modèles du poste (make opencode-modeles)")
print("  Copilot     : choisissez ce niveau de modèle dans le sélecteur de la discussion")
LANCEMENT
