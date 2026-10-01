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
python3 -B ".ephor/operateur.py" "make task-worktree" || exit 2
ID="${1:-}"
DIR="${2:-}"
if ! printf '%s' "$ID" | grep -Eq '^[A-Z][A-Z0-9-]{2,50}$'; then
  echo "usage : make task-worktree ID=FEAT-001 [DIR=../dossier]"
  exit 2
fi
PRINCIPALE=""
# La branche que désigne le serveur d'abord (develop…), comme le juge.
DESIGNEE="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
for branche in ${DESIGNEE#origin/} main master; do
  git rev-parse --verify -q "refs/heads/$branche" >/dev/null && { PRINCIPALE="$branche"; break; }
done
[ -n "$PRINCIPALE" ] || { echo "REFUS : branche principale introuvable (main, master)"; exit 2; }
# La tâche peut être rangée dans un lot (tasks/<lot>/<ID>/, #35) : le juge sait la trouver.
DOSSIER="$(python3 -B .ephor/judge.py dossier --task-id "$ID" --ref "$PRINCIPALE" 2>/dev/null)" || DOSSIER="tasks/$ID"
if ! git cat-file -e "$PRINCIPALE:$DOSSIER/contract.json" 2>/dev/null; then
  echo "REFUS : pas de contrat approuvé pour $ID sur $PRINCIPALE ($DOSSIER/contract.json commité)."
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
echo "Une session d'agent par tâche, lancée DANS ce dossier. Après la fusion : make task-clore ID=$ID"
# La ligne à coller : le dossier, le modèle et l'effort que le contrat demande, et /feature,
# qui enchaîne jusqu'au verdict.
CONTRAT="$(git show "$PRINCIPALE:$DOSSIER/contract.json" 2>/dev/null)"
python3 - "$CONTRAT" "$DIR" "$ID" <<'LANCEMENT' 2>/dev/null || echo "  Claude Code : cd \"$DIR\" && claude \"/feature $ID\""
import json
import os
import shlex
import sys
from pathlib import Path

try:
    contrat = json.loads(sys.argv[1])
except ValueError:
    contrat = {}
dossier_tache, tache = sys.argv[2], sys.argv[3]
# Entre guillemets doubles, un `$`, un accent grave ou un guillemet du chemin s'exécutaient
# au collage de la ligne (audit du 01/10/2026) : ces chemins-là sont cités à la façon du shell.
cite = shlex.quote(dossier_tache) if any(c in dossier_tache for c in '$`"\\!') else f'"{dossier_tache}"'
aller = f"cd {cite} && "
niveau, effort = contrat.get("niveau"), contrat.get("effort")
niveau = niveau if niveau in ("rapide", "standard", "fort") else None
effort = effort if effort in ("low", "medium", "high", "xhigh", "max") else None
demande = [f"le niveau {niveau}"] if niveau else []
demande += [f"l'effort {effort}"] if effort else []
if demande:
    print(f"Le contrat demande {' et '.join(demande)} :")
alias = {"rapide": "haiku", "standard": "sonnet", "fort": "opus"}.get(niveau or "")
reglage = (f" --model {alias}" if alias else "") + (f" --effort {effort}" if effort else "")
print(f'  Claude Code : {aller}claude{reglage} "/feature {tache}"')
# L'écran d'opencode n'a pas d'option d'effort : `--variant` n'existe que pour `opencode run`,
# et l'écran la refuse (il affiche son aide et ne démarre pas, opencode 1.18.33). L'effort s'y
# choisit dans la session, par /variants (ou ctrl+t).
modele = None
if niveau:
    dossier = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"
    try:
        lu = json.loads((dossier / "opencode.json").read_text(encoding="utf-8"))
        agents = lu.get("agent", {}) if isinstance(lu, dict) else {}
    except (OSError, ValueError):
        agents = {}
    role = {"rapide": "scout", "standard": "reviewer", "fort": "planner"}[niveau]
    modele = (agents.get(role) or {}).get("model")
if niveau and not modele:
    print("  opencode    : réglez d'abord les modèles du poste (make opencode-modeles)")
else:
    ouvrir = f"opencode --model {modele}" if modele else "opencode"
    choix = f", puis /variants : {effort}" if effort else ""
    print(f"  opencode    : {aller}{ouvrir}{choix}, puis /feature {tache}")
if niveau:
    print("  Copilot     : ouvrez ce dossier, choisissez ce niveau de modèle dans le sélecteur")
LANCEMENT
