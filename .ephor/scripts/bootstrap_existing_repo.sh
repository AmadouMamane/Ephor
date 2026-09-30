#!/usr/bin/env bash
# Installe Ephor sur un dépôt EXISTANT, sans rien casser.
#   usage : bash .ephor/scripts/bootstrap_existing_repo.sh [--docs] [--mettre-a-jour] /chemin/du/depot
#     --docs   pose aussi les squelettes de documentation du projet (docs/00 à 06, modèle
#              d'ADR, fiches de composant). Sans lui, le docs/ du dépôt n'est pas touché.
#     --mettre-a-jour
#              fait passer un dépôt déjà équipé à CETTE version d'Ephor (ADR 0007 et 0010).
#              Un fichier du moteur (.ephor/ hors projet/) qui a changé est REMPLACÉ si son
#              contenu est exactement celui du dernier commit : l'ancien reste dans
#              l'historique, et « git diff -- .ephor/ » montre ce qui a changé. Retouché,
#              pas suivi, lien symbolique ou en lecture seule, il reçoit son .ephor-new
#              comme avant. Hors de .ephor/ (greffon d'opencode, réglages et sous-agents de
#              Claude Code, skills, Makefile), un fichier n'est remplacé que s'il est, en
#              plus, une version qu'Ephor a livrée, à l'octet près (.ephor/empreintes.txt) :
#              le projet ne l'a jamais retouché. Sinon, .ephor-new, et le bilan le donne
#              « à reprendre » : tant qu'il reste, c'est l'ancienne version qui tourne.
#
# Ce qui arrive dans le dépôt, et c'est tout :
#   .ephor/           le moteur : juge, garde-fous, scripts, greffon Copilot, doc d'Ephor
#   .ephor/projet/    ce que CE projet règle — posé une fois, jamais remplacé ensuite
#   .claude/ .opencode/ .github/ .vscode/   les réglages des trois agents, là où ils les cherchent
#   tasks/  Makefile  PROGRESS.md  STEER.md  l'interface de travail, visible exprès
#
# Idempotent : un fichier identique est laissé tel quel ; un fichier existant et différent
# n'est jamais écrasé, la version d'Ephor est déposée à côté en .ephor-new — sauf, avec
# --mettre-a-jour, un fichier d'Ephor dont git garde le contenu exact (et, hors du moteur,
# qui est une version livrée par Ephor).
# (La version d'origine copiait deux fois hooks et sous-agents : 13 doublons dès le
# premier passage, puis des dizaines au second.)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
# Geste de l'opérateur : refusé quand un agent le lance, détours compris (.ephor/operateur.py).
python3 "$SRC/.ephor/operateur.py" "bootstrap_existing_repo.sh" || exit 2
USAGE="usage : bootstrap_existing_repo.sh [--docs] [--mettre-a-jour] /chemin/du/depot"
DOCS=0; MAJ=0; DST=""
for ARG in "$@"; do
  case "$ARG" in
    --docs) DOCS=1 ;;
    --mettre-a-jour) MAJ=1 ;;
    -*) echo "option inconnue : $ARG — $USAGE" >&2; exit 2 ;;
    *) [ -z "$DST" ] || { echo "un seul dépôt à la fois — $USAGE" >&2; exit 2; }; DST="$ARG" ;;
  esac
done
[ -n "$DST" ] || { echo "$USAGE" >&2; exit 2; }
[ -d "$DST" ] || { echo "dépôt introuvable: $DST" >&2; exit 1; }
cd "$DST"

ADDED=0; SAME=0; SIDE=0; KEPT=0; COTE=""; REPRIS=""
REMPLACES=""; BLOQUES=""; RETIRES=0; HORS_MOTEUR=""; A_REPRENDRE=""
# Les fichiers suivis déjà modifiés AVANT nous : sans cette photo, un dépôt au travail
# nous ferait porter le chapeau de ses propres modifications. `--relative` : des chemins
# lus depuis le projet, qui n'est pas forcément à la racine de son dépôt.
AVANT="$(git diff --name-only --relative 2>/dev/null || true)"
# Racine du dépôt avant installation : ce qu'Ephor y ajoute doit pouvoir se compter.
RACINE_AVANT="$(ls -A 2>/dev/null | LC_ALL=C sort)"
# La version en place, lue AVANT de poser la nouvelle : une mise à jour dit d'où elle part.
version_de() { sed -n '1s/ —.*//p' "$1" 2>/dev/null || true; }
VERSION_AVANT="$(version_de .ephor/VERSION)"
A_UN_COMMIT=0
if git rev-parse --verify -q HEAD >/dev/null 2>&1; then A_UN_COMMIT=1; fi

# Un fichier d'Ephor peut être remplacé — en mise à jour seulement — si son contenu est
# EXACTEMENT celui du dernier commit : l'ancienne version reste alors dans l'historique, et
# rien de ce que le projet n'a pas commité ne se perd. On compare les contenus, pas l'avis
# de git : un fichier marqué skip-worktree paraît intact à `git diff`, et sa retouche
# disparaissait (trouvé en relecture). Jamais un lien symbolique — c'est sa cible, hors de
# git, qu'on aurait écrasée — ni un fichier en lecture seule, qui arrêtait la mise à jour
# à mi-chemin. `HEAD:./` lit le chemin depuis le dossier courant, pas depuis la racine.
#
# Hors de .ephor/, une condition de plus : le fichier doit être, à l'octet près, une version
# qu'Ephor a livrée (.ephor/empreintes.txt de la NOUVELLE version, qui les liste toutes). Le
# Makefile, les réglages des agents, les sous-agents peuvent avoir été retouchés et commités
# par le projet : une retouche n'est aucune de ces versions, et garde son .ephor-new. Avant
# cette liste, rien n'était remplacé hors de .ephor/ — et un projet mis à jour en 1.1.5
# gardait le greffon d'opencode de la 1.1.3, sans plafond relevé ni sentinelle, avec un
# .ephor-new que rien ne disait de reprendre (mesuré le 29/09/2026).
moteur_du_projet() { case "$1" in .ephor/projet/*) return 1 ;; .ephor/*) return 0 ;; esac; return 1; }
suivi() { git cat-file -e "HEAD:./$1" 2>/dev/null; }
EMPREINTES="$SRC/.ephor/empreintes.txt"
empreinte() { # empreinte <fichier> : le sha256 de son contenu
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
  fi
}
d_ephor() { # d_ephor <relatif> : un fichier qu'Ephor pose hors du moteur ?
  [ -f "$EMPREINTES" ] && awk -v p="$1" '$1 !~ /^#/ && $2 == p { vu = 1 } END { exit !vu }' "$EMPREINTES"
}
version_livree() { # version_livree <relatif> : ce contenu est-il une version livrée par Ephor ?
  local h
  [ -f "$EMPREINTES" ] || return 1
  h="$(empreinte "$1" 2>/dev/null)" || return 1
  [ -n "$h" ] || return 1
  grep -qxF "$h $1" "$EMPREINTES" || grep -qxF "$h $1 actuelle" "$EMPREINTES"
}
intact_depuis_le_commit() { # le contenu exact du dernier commit ?
  local ancien
  ancien="$(git rev-parse -q --verify "HEAD:./$1" 2>/dev/null)" || return 1
  [ "$(git hash-object -- "$1" 2>/dev/null)" = "$ancien" ]
}
remplacable() { # remplacable <relatif>
  [ "$MAJ" = "1" ] || return 1
  case "$1" in .ephor/projet/*) return 1 ;; esac
  if [ -L "$1" ] || [ ! -f "$1" ] || [ ! -w "$1" ]; then return 1; fi
  intact_depuis_le_commit "$1" || return 1
  moteur_du_projet "$1" || version_livree "$1"
}
pourquoi_pas() { # pourquoi_pas <relatif> : pourquoi un fichier d'Ephor n'a pas été remplacé
  if [ -L "$1" ]; then echo "lien symbolique"
  elif [ ! -w "$1" ]; then echo "en lecture seule"
  elif ! suivi "$1"; then echo "pas suivi par git"
  elif ! intact_depuis_le_commit "$1"; then echo "modifié depuis le dernier commit"
  else echo "pas une version connue d'Ephor (retouché dans le projet, ou d'avant la 1.0)"
  fi
}
copy() { # copy <relatif>
  local rel="$1" src="$SRC/$1"
  mkdir -p "$(dirname "$rel")"
  if [ ! -e "$rel" ] && [ ! -L "$rel" ]; then
    cp "$src" "$rel"; echo "  + $rel"; ADDED=$((ADDED+1))
  elif [ ! -L "$rel" ] && cmp -s "$src" "$rel"; then
    SAME=$((SAME+1))
  elif remplacable "$rel"; then
    cp "$src" "$rel"; echo "  ↻ $rel"
    if moteur_du_projet "$rel"; then REMPLACES="$REMPLACES $rel"; else HORS_MOTEUR="$HORS_MOTEUR $rel"; fi
  else
    cp "$src" "$rel.ephor-new"; SIDE=$((SIDE+1)); COTE="$COTE $rel"
    if [ "$MAJ" = "1" ] && moteur_du_projet "$rel"; then
      BLOQUES="$BLOQUES $rel"
      echo "  ~ $rel $(pourquoi_pas "$rel") : pas remplacé -> $rel.ephor-new"
    elif [ "$MAJ" = "1" ] && d_ephor "$rel"; then
      A_REPRENDRE="$A_REPRENDRE $rel"
      echo "  ~ $rel $(pourquoi_pas "$rel") : pas remplacé -> $rel.ephor-new"
    else
      echo "  ~ $rel existe et diffère -> $rel.ephor-new"
    fi
  fi
  # Un .ephor-new laissé par un passage précédent ne dit plus rien quand le fichier est à
  # jour : c'est le nôtre, et le laisser traîner ferait croire qu'il reste à fusionner.
  if [ "$MAJ" = "1" ] && [ -f "$rel.ephor-new" ] && [ ! -L "$rel" ] && cmp -s "$src" "$rel"; then
    rm -f "$rel.ephor-new"; RETIRES=$((RETIRES+1))
  fi
}
seed() { # seed <relatif> : un réglage du PROJET, posé s'il manque, jamais remplacé ensuite
  local rel="$1"
  if [ -e "$rel" ]; then
    KEPT=$((KEPT+1))
  else
    mkdir -p "$(dirname "$rel")"; cp "$SRC/$rel" "$rel"; echo "  + $rel"; ADDED=$((ADDED+1))
  fi
}

VERSION_APRES="$(version_de "$SRC/.ephor/VERSION")"
if [ "$MAJ" = "1" ]; then
  echo "Mise à jour d'Ephor dans $DST : ${VERSION_AVANT:-version inconnue} -> ${VERSION_APRES:-?}"
  if [ "$A_UN_COMMIT" != "1" ]; then
    echo "  ce dossier n'a aucun commit git : rien ne sera remplacé, tout ce qui diffère"
    echo "  arrivera en .ephor-new. Commitez d'abord l'état actuel, puis relancez."
  fi
else
  echo "Installation d'Ephor dans $DST"
fi
echo
echo "[1/6] le moteur : .ephor/ (juge, garde-fous, scripts, livraison, greffon, doc)"
# Tout le dossier, jamais une liste à la main : quatre modules avaient été oubliés, et
# les commandes correspondantes échouaient sur « fichier introuvable ».
#
# La liste vient de git quand la source est un dépôt : seuls les fichiers SUIVIS partent.
# Un `find` emportait aussi ce qui traînait dans la copie de travail — une politique de
# livraison locale, un `.orig`, le cache que `ruff` range dans `.ephor/`. Sans git (une
# archive), le `find` reste, avec ses exclusions.
#
# Restent dehors : les réglages du projet (étape 2) ; les tests d'Ephor, qui se testent
# dans SON dépôt ; les trois scripts qui n'ont de sens que là-bas (l'installation, la
# démonstration, et le calcul des empreintes, qui lit l'histoire d'Ephor) ; et les deux
# fichiers du greffon que les adaptateurs DÉRIVENT des réglages de ce dépôt (étape 6) —
# copiés ici, ils auraient « différé » au passage suivant, et fabriqué un .ephor-new à
# chaque relance.
moteur() {
  if [ -n "$(git -C "$SRC" ls-files .ephor/judge.py 2>/dev/null)" ]; then
    git -C "$SRC" ls-files .ephor
  else
    (cd "$SRC" && find .ephor \( -name __pycache__ -o -name .ruff_cache -o -name .mypy_cache \
      -o -name .pytest_cache -o -name .hypothesis \) -prune \
      -o -type f ! -name '*.pyc' ! -name .DS_Store ! -name '*.ephor-new' -print)
  fi | LC_ALL=C sort | while IFS= read -r f; do
    case "$f" in
      .ephor/projet/*|.ephor/tests/*) continue ;;
      .ephor/scripts/bootstrap_existing_repo.sh|.ephor/scripts/demo_judge.sh) continue ;;
      .ephor/scripts/empreintes.py) continue ;;
      .ephor/greffon-copilot/hooks/hooks.json) continue ;;
      .ephor/greffon-copilot/.claude-plugin/plugin.json) continue ;;
    esac
    if [ -f "$SRC/$f" ]; then printf '%s\n' "$f"; fi
  done
}
MOTEUR="$(moteur)"
while IFS= read -r f; do if [ -n "$f" ]; then copy "$f"; fi; done <<< "$MOTEUR"
find .ephor -name '*.sh' -exec chmod +x {} + 2>/dev/null || true
# Ce que la version en place avait et que celle-ci n'a plus. Rien n'est retiré : on le
# nomme, avec la commande. Seuls les fichiers suivis comptent — le reste n'est pas à nous.
OBSOLETES=""
if [ "$MAJ" = "1" ]; then
  while IFS= read -r f; do
    case "$f" in
      ""|.ephor/projet/*|.ephor/tests/*) continue ;;
      .ephor/greffon-copilot/hooks/hooks.json|.ephor/greffon-copilot/.claude-plugin/plugin.json) continue ;;
    esac
    printf '%s\n' "$MOTEUR" | grep -qxF -- "$f" || OBSOLETES="$OBSOLETES $f"
  done < <(git ls-files -- .ephor 2>/dev/null || true)
fi

echo "[2/6] les réglages du projet : .ephor/projet/ (posés une fois, à vous ensuite)"
# Un dépôt équipé AVANT la 0.9 range ses réglages ailleurs. Ils passent dans .ephor/projet/
# AVANT qu'on y pose les valeurs par défaut : sinon une mise à jour perdait en silence les
# dépôts de référence, les exceptions et le scanner maison — et deux garde-fous cessaient
# de protéger (reproduit par une relecture le 24/09/2026). Rien n'est retiré : copié.
# >>> ancienne disposition — les chemins d'avant la 0.9, nommés exprès
ANCIENNE=0
if [ -f ephor/judge.py ] || [ -f .claude/hooks/_lib.sh ] || [ -d .copilot/greffon ]; then
  ANCIENNE=1
fi
reprend() { # reprend <ancien> <nouveau> : un réglage du projet, à sa nouvelle adresse
  if [ "$ANCIENNE" != "1" ] || [ ! -f "$1" ] || [ -e "$2" ]; then return 0; fi
  mkdir -p "$(dirname "$2")"; cp -p "$1" "$2"
  echo "  > $1 -> $2 (réglage du projet repris)"; REPRIS="$REPRIS $1"
}
reprend reference-repos.json .ephor/projet/reference-repos.json
reprend ephor/fuites-autorisees.txt .ephor/projet/fuites-autorisees.txt
reprend ephor/sast.sh .ephor/projet/sast.sh
reprend deploy/release-policy.json .ephor/projet/release-policy.json
if [ "$ANCIENNE" = "1" ] && [ -f scripts/verify.sh ] && [ ! -e .ephor/projet/verify.sh ]; then
  # Les mêmes étapes, deux chemins à suivre : la racine est un cran plus haut, et le
  # contrôle des données personnelles a déménagé.
  mkdir -p .ephor/projet
  sed -e 's#"$(dirname "$0")/\.\."#"$(dirname "$0")/../.."#' \
      -e 's#\([^.a-zA-Z_/]\)ephor/personal_data\.py#\1.ephor/personal_data.py#g' \
      scripts/verify.sh > .ephor/projet/verify.sh
  echo "  > scripts/verify.sh -> .ephor/projet/verify.sh (chemins adaptés : relisez-le)"
  REPRIS="$REPRIS scripts/verify.sh"
fi
# <<< ancienne disposition
for f in "$SRC"/.ephor/projet/*; do seed ".ephor/projet/$(basename "$f")"; done
chmod +x .ephor/projet/*.sh 2>/dev/null || true

echo "[3/6] Claude Code : réglages, sous-agents, skills"
copy .claude/settings.json
for f in "$SRC"/.claude/agents/*.md; do copy ".claude/agents/$(basename "$f")"; done
for d in "$SRC"/.claude/skills/*/; do copy ".claude/skills/$(basename "$d")/SKILL.md"; done

echo "[4/6] interface de travail : Makefile, contrats, état, consigne"
copy Makefile
copy tasks/README.md
# L'état du travail et la consigne sont au projet dès le premier jour. Une mise à jour qui y
# déposait un .ephor-new demandait de « fusionner » le journal du projet avec un modèle vide.
for f in PROGRESS.md STEER.md; do
  if [ "$MAJ" = "1" ] && [ -e "$f" ]; then continue; fi
  copy "$f"
done
touch .gitignore
# Le cache compilé de Python, sous .ephor/ seulement : non ignoré, le juge le prenait pour
# une retouche de ses propres fichiers et refusait toute tâche (dépôt Node, typiquement).
# Les réglages locaux de Claude Code sont propres au poste : non ignorés, un `git add -A` de
# l'agent les commitait, et le juge refusait la tâche (zone). Claude Code les ignore pour
# qui l'a installé ; pas sur un poste qui n'a qu'opencode ou Copilot (vu en CI, 30/09/2026).
for line in ".verify/" "AGENT_STOP" "mutants/" ".ephor/**/__pycache__/" ".claude/settings.local.json"; do
  grep -qxF "$line" .gitignore || { printf '%s\n' "$line" >> .gitignore; echo "  + .gitignore : $line"; }
done

echo "[5/6] documentation du projet"
if [ "$DOCS" = "1" ]; then
  for f in "$SRC"/docs/*.md; do copy "docs/$(basename "$f")"; done
  copy docs/adr/0001-record-architecture-decisions.md
  for f in "$SRC"/docs/composants/*.md; do copy "docs/composants/$(basename "$f")"; done
elif [ "$MAJ" != "1" ]; then
  echo "  rien de posé : votre docs/ reste le vôtre. Les squelettes (croyances, architecture,"
  echo "  spécification, plan, dette, qualité, runbook) : relancez avec --docs."
fi

echo "[6/6] adaptateurs opencode et Copilot, dérivés du .claude/ de CE dépôt"
copy .opencode/plugins/ephor.js
python3 .ephor/adapters.py write || echo "  (à relancer à la main : python3 .ephor/adapters.py write)"
chmod +x .ephor/greffon-copilot/hooks/relais.sh 2>/dev/null || true

echo
echo "Détection de la pile (à valider à la main) :"
mkdir -p .verify
{
  echo "# Détection automatique — à valider à la main"
  [ -f package.json ]      && echo "- node : scripts = $(jq -r '.scripts | keys | @csv' package.json 2>/dev/null || echo '?')"
  [ -f pyproject.toml ]    && echo "- python (pyproject)"
  [ -f requirements.txt ]  && echo "- python (requirements.txt) -> migrer vers uv recommandé"
  [ -f go.mod ]            && echo "- go"
  [ -f Cargo.toml ]        && echo "- rust"
  { [ -f pom.xml ] || [ -f build.gradle ]; } && echo "- jvm"
  [ -f .gitlab-ci.yml ]    && echo "- CI GitLab existante : .gitlab-ci.yml"
  # shellcheck disable=SC2012  # simple inventaire lisible, pas de traitement des noms
  [ -d .github/workflows ] && echo "- CI GitHub Actions : $(ls .github/workflows | tr '\n' ' ')"
  { [ -f Containerfile ] || [ -f Dockerfile ]; } && echo "- image de conteneur (Containerfile/Dockerfile)"
  echo "- tests détectés : $(find . \( -path ./node_modules -o -path ./.ephor \) -prune -o \( -name 'test_*.py' -o -name '*_test.go' -o -name '*.test.ts' -o -name '*Test.java' \) -print 2>/dev/null | wc -l | tr -d ' ') fichier(s)"
} > .verify/detection.md
sed 's/^/  /' .verify/detection.md

echo
REMPL=""
[ "$MAJ" = "1" ] && REMPL="$(printf '%s' "$REMPLACES$HORS_MOTEUR" | wc -w | tr -d ' ') remplacé(s), "
echo "Bilan : $ADDED ajouté(s), $REMPL$SAME identique(s), $KEPT réglage(s) du projet gardé(s), $SIDE à fusionner à la main (.ephor-new)."
[ -n "$COTE" ] && echo "  à fusionner :$COTE"
# Ce que la racine du dépôt a gagné : c'était l'encombrement le plus visible (dix-sept
# entrées avant la 0.9). On le montre au lieu de le laisser découvrir.
NOUVEAUX="$(LC_ALL=C comm -13 <(printf '%s\n' "$RACINE_AVANT") <(ls -A | LC_ALL=C sort) | { grep -vxF .verify || true; } | tr '\n' ' ' | sed 's/ *$//')"
[ -n "$NOUVEAUX" ] && echo "  nouveau à la racine : $NOUVEAUX"
# « 0 à fusionner » était annoncé alors que deux fichiers suivis avaient bougé, sans être
# nommés : le .gitignore et les réglages VS Code fusionnés par les adaptateurs.
APRES="$(git diff --name-only --relative 2>/dev/null || true)"
TOUCHES="$(printf '%s\n' "$APRES" | { [ -n "$AVANT" ] && grep -vxF "$AVANT" || cat; } | tr '\n' ' ')"
# En mise à jour, ce qu'Ephor a remplacé a sa propre ligne, plus bas : pas deux fois.
if [ "$MAJ" = "1" ]; then
  RESTE=""
  set -f  # des noms de fichiers, pas des motifs
  for f in $TOUCHES; do
    case "$f" in .ephor/*) continue ;; esac
    case " $HORS_MOTEUR " in *" $f "*) continue ;; esac
    RESTE="$RESTE $f"
  done
  set +f
  TOUCHES="$RESTE"
fi
TOUCHES="$(printf '%s' "$TOUCHES" | sed -e 's/^ *//' -e 's/ *$//')"
[ -n "$TOUCHES" ] && echo "  fichiers suivis modifiés par l'installation : $TOUCHES"

# Les deux fichiers qui font MARCHER Ephor sont aussi les deux qu'un dépôt vivant a
# déjà : son Makefile et ses réglages Claude. Déposés à côté sans un mot, Ephor
# restait sur le disque et INERTE — 68 fichiers, aucune commande, aucune garde déclarée.
# D'où ce geste 0, avant les autres, et pas noyé dans un compte de fichiers.
#
# Mais un .ephor-new ne veut pas toujours dire « inerte ». Un Makefile qui lance déjà le
# juge, des réglages qui déclarent déjà ses garde-fous dans leurs hooks : le geste 0 a été
# fait, et le .ephor-new dit seulement que la version d'Ephor a changé. Crier « INERTE » à
# chaque mise à jour, c'était apprendre à ignorer le seul message qui compte le jour de
# l'installation. Le test est étroit exprès : une simple mention de « .ephor/ » (une
# exclusion de flake8, un refus dans les permissions) ne prouve rien, et faisait taire le
# geste 0 sur un dépôt inerte (trouvé en relecture).
makefile_arme() { grep -q '\.ephor/judge\.py' Makefile 2>/dev/null; }
reglages_armes() {
  python3 - .claude/settings.json <<'PY' 2>/dev/null
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fichier:
        hooks = json.load(fichier).get("hooks", {})
except (OSError, ValueError, AttributeError):
    sys.exit(1)
sys.exit(0 if ".ephor/hooks/" in json.dumps(hooks) else 1)
PY
}
# Les garde-fous que la nouvelle version déclare et que les réglages du projet, retouchés,
# ne déclarent pas : « Stop : contexte.sh ». Sans eux, la fonction est posée et ne tourne pas.
# Fonction à part : bash 3.2, celui de macOS, lit mal un document en ligne dans un $(…).
hooks_manquants() {
  python3 - "$SRC/.claude/settings.json" .claude/settings.json <<'PY' 2>/dev/null || true
import json
import re
import sys


def gardes(chemin):
    try:
        with open(chemin, encoding="utf-8") as fichier:
            hooks = json.load(fichier).get("hooks") or {}
    except (OSError, ValueError, AttributeError):
        return None
    vus = set()
    for evenement, blocs in hooks.items():
        for bloc in blocs if isinstance(blocs, list) else []:
            for hook in (bloc.get("hooks") or []) if isinstance(bloc, dict) else []:
                commande = hook.get("command", "") if isinstance(hook, dict) else ""
                for nom in re.findall(r"\.ephor/hooks/([\w.-]+\.sh)", str(commande)):
                    vus.add((evenement, nom))
    return vus


neuf, projet = gardes(sys.argv[1]), gardes(sys.argv[2])
if neuf is not None and projet is not None:
    print(", ".join(f"{evenement} : {nom}" for evenement, nom in sorted(neuf - projet)))
PY
}
MAKE_INERTE=0; REGLAGES_INERTES=0; MAKE_EN_RETARD=0; REGLAGES_EN_RETARD=0
if [ -f Makefile.ephor-new ]; then
  if makefile_arme; then MAKE_EN_RETARD=1; else MAKE_INERTE=1; fi
fi
if [ -f .claude/settings.json.ephor-new ]; then
  if reglages_armes; then REGLAGES_EN_RETARD=1; else REGLAGES_INERTES=1; fi
fi
if [ "$MAKE_INERTE" = "1" ] || [ "$REGLAGES_INERTES" = "1" ]; then
  echo
  echo "=================================================================="
  echo " GESTE 0 — OBLIGATOIRE. Sans lui, Ephor est POSÉ mais INERTE."
  echo "=================================================================="
  if [ "$MAKE_INERTE" = "1" ]; then
    cat <<'M0'

  Makefile : ce dépôt a déjà le sien, donc « make verify », « make doctor », « make sec »
  et « make copilot-install » répondent « No rule to make target ». Rien ne marche.
  Ne collez pas les deux bout à bout : les cibles en double (test, lint, fmt) passeraient
  à la version d'Ephor et votre CI casserait sans un mot. Voyez d'abord les doublons :

      grep -hoE '^[a-zA-Z0-9_.-]+:' Makefile Makefile.ephor-new | sort | uniq -d

  Puis reprenez les cibles d'Ephor une par une, en renommant celles qui se heurtent.
  Vérifiez : « make doctor » doit répondre.
M0
  fi
  if [ "$REGLAGES_INERTES" = "1" ]; then
    cat <<'S0'

  .claude/settings.json : ce dépôt a déjà le sien, donc les garde-fous sont sur le disque
  et AUCUN n'est déclaré. Seul votre hook à vous tourne, et rien ne vous le dit : on se croit
  couvert. Fusionnez les blocs « hooks » et « permissions » du fichier .ephor-new dans le
  vôtre — gardez vos règles, ajoutez les siennes. Vérifiez que les gardes sont DÉCLARÉES,
  et attendez-vous à un nombre non nul (0 : aucun outil ne les lancera) :

      grep -c '.ephor/hooks/write-guard.sh' .claude/settings.json

  Puis régénérez ce qui en dérive : les réglages d'opencode, de Copilot et de VS Code ont
  été écrits d'après VOS réglages, donc sans les garde-fous d'Ephor.

      make adapters          # et make copilot-install si vous travaillez sous Copilot
S0
  fi
fi
if [ "$MAKE_EN_RETARD" = "1" ] || [ "$REGLAGES_EN_RETARD" = "1" ]; then
  echo
  echo "À reprendre : vos fichiers appellent déjà Ephor, mais sa version de ces fichiers a changé."
  if [ "$MAKE_EN_RETARD" = "1" ]; then
    cibles() { grep -hoE '^[a-zA-Z0-9_.-]+:' "$1" | tr -d ':' | { grep -v '^\.' || true; } | LC_ALL=C sort -u; }
    MANQUANTES="$(LC_ALL=C comm -13 <(cibles Makefile) <(cibles Makefile.ephor-new) | tr '\n' ' ' | sed 's/ *$//')"
    # Une cible qui existe déjà n'est pas « absente », même quand sa recette a changé : en
    # 1.1, « make task-check » d'un Makefile resté en 1.0 ne vérifiait que la forme, et le
    # juge réclamait une trace que cette recette n'écrit jamais. Seules les recettes qui
    # appellent le moteur sont comparées : « test » ou « lint » sont celles du projet.
    recette() { awk -v t="$2:" 'index($0, t) == 1 { dedans = 1; next } dedans && /^\t/ { print; next } dedans { exit }' "$1"; }
    CHANGEES=""
    for c in $(LC_ALL=C comm -12 <(cibles Makefile) <(cibles Makefile.ephor-new)); do
      NEUVE="$(recette Makefile.ephor-new "$c")"
      case "$NEUVE" in *.ephor/*) ;; *) continue ;; esac
      [ "$NEUVE" = "$(recette Makefile "$c")" ] || CHANGEES="$CHANGEES $c"
    done
    echo "  Makefile : diff Makefile Makefile.ephor-new"
    if [ -n "$MANQUANTES" ]; then echo "    cibles d'Ephor absentes du vôtre : $MANQUANTES"; fi
    if [ -n "$CHANGEES" ]; then echo "    cibles d'Ephor dont la recette a changé :$CHANGEES — reprenez-les"; fi
  fi
  if [ "$REGLAGES_EN_RETARD" = "1" ]; then
    echo "  .claude/settings.json : diff .claude/settings.json .claude/settings.json.ephor-new"
    echo "    reprenez ce qui manque dans « hooks » et « permissions », puis : make adapters"
  fi
fi

# L'ancienne disposition ne sert plus, mais rien n'a été retiré : on dit quoi retirer,
# et seulement ce qui existe. Tant qu'ils restent, ces fichiers ne sont plus des zones de
# contrôle — un agent pourrait les modifier, sans effet puisque plus rien ne les lit.
# >>> ancienne disposition — les chemins d'avant la 0.9, nommés exprès
if [ "$ANCIENNE" = "1" ]; then
  RESTES=""
  for f in ephor .claude/hooks .copilot/greffon reference-repos.json \
           docs/07-token-budget.md docs/08-tooling.md docs/09-evaluer-ephor.md \
           docs/10-installation.md docs/11-trois-agents.md docs/CODEOWNERS.example \
           docs/adr/0003-adaptateurs-agents.md docs/adr/0004-le-nom-ephor.md; do
    [ -e "$f" ] && RESTES="$RESTES $f"
  done
  for f in bootstrap_existing_repo demo_judge doctor install_copilot_plugin mutants \
           pytest_ci repo_map review test_sample verify; do
    [ -f "scripts/$f.sh" ] && RESTES="$RESTES scripts/$f.sh"
  done
  for f in manifest.py Containerfile.ci release-policy.example.json; do
    [ -f "deploy/$f" ] && RESTES="$RESTES deploy/$f"
  done
  grep -q '^Ephor ' VERSION 2>/dev/null && RESTES="$RESTES VERSION"
  # `git rm` refuse tout si un seul chemin n'est pas suivi : on sépare les deux cas.
  SUIVIS=""; LIBRES=""
  for f in $RESTES; do
    if [ -n "$(git ls-files -- "$f" 2>/dev/null)" ]; then SUIVIS="$SUIVIS $f"; else LIBRES="$LIBRES $f"; fi
  done
  [ -f .ephor-detected.md ] && LIBRES="$LIBRES .ephor-detected.md"
  echo
  echo "=================================================================="
  echo " ANCIENNE DISPOSITION D'EPHOR (0.8 ou avant) — mise à jour vers .ephor/"
  echo "=================================================================="
  [ -n "$REPRIS" ] && echo "  Réglages du projet repris dans .ephor/projet/ :$REPRIS"
  cat <<A0

  Une fois le geste 0 fait et « make verify » vert, retire les fichiers d'Ephor restés à
  l'ancienne adresse. Plus rien ne les lit, et ils ne sont plus des zones de contrôle.
  Regardez d'abord ceux de scripts/ et deploy/ : ce sont les noms d'Ephor, pas forcément
  ses fichiers.
A0
  echo
  [ -n "$SUIVIS" ] && echo "      git rm -r$SUIVIS"
  [ -n "$LIBRES" ] && echo "      rm -rf$LIBRES      # pas suivis par git"
fi
# <<< ancienne disposition

# Une mise à jour s'arrête là : les gestes de première installation sont faits, et les
# répéter noierait les trois choses qui comptent — ce qui a changé, ce qui n'a pas pu
# l'être, ce qui ne sert plus.
if [ "$MAJ" = "1" ]; then
  echo
  echo "=================================================================="
  echo " MISE À JOUR : ${VERSION_AVANT:-version inconnue} -> ${VERSION_APRES:-?}"
  echo "=================================================================="
  if [ -n "$REMPLACES" ]; then
    echo "  $(printf '%s' "$REMPLACES" | wc -w | tr -d ' ') fichier(s) du moteur remplacé(s). L'ancienne version reste dans git :"
    echo "      git diff --stat -- .ephor/"
  else
    echo "  aucun fichier du moteur remplacé."
  fi
  if [ -n "$HORS_MOTEUR" ]; then
    echo "  $(printf '%s' "$HORS_MOTEUR" | wc -w | tr -d ' ') autre(s) fichier(s) d'Ephor remplacé(s), hors du moteur : c'était une version"
    echo "  livrée par Ephor, jamais retouchée ici (l'ancienne reste dans git) :"
    echo "     $HORS_MOTEUR"
  fi
  if [ "$RETIRES" -gt 0 ]; then
    echo "  $RETIRES .ephor-new d'un passage précédent retiré(s) : leur fichier est maintenant à jour."
  fi
  # Ce qu'Ephor pose hors du moteur et que le projet a retouché : le laisser en .ephor-new
  # sans le dire, c'était une mise à jour qui s'annonçait faite et laissait tourner
  # l'ancienne version (le greffon d'opencode, mesuré le 29/09/2026).
  if [ -n "$A_REPRENDRE" ]; then
    echo
    echo "  À REPRENDRE — la mise à jour n'est pas finie sans eux : ils n'ont pas été remplacés,"
    echo "  et c'est encore leur ANCIENNE version qui tourne."
    for f in $A_REPRENDRE; do
      echo "     $f : $(pourquoi_pas "$f")"
      echo "        diff $f $f.ephor-new"
      case "$f" in
        .opencode/plugins/*)
          echo "        opencode charge l'ancien greffon tant que celui-ci reste en place." ;;
        .claude/settings.json)
          MANQUANTS="$(hooks_manquants)"
          if [ -n "$MANQUANTS" ]; then
            echo "        hooks d'Ephor que vos réglages ne déclarent pas : $MANQUANTS"
          fi
          echo "        puis make adapters : les réglages d'opencode, de VS Code et de Copilot en dérivent." ;;
      esac
    done
    echo "  Pour chacun : reprenez dans le vôtre ce qui manque ; ou, si rien n'y est propre au"
    echo "  projet : mv <fichier>.ephor-new <fichier>."
  fi
  if [ -n "$BLOQUES" ]; then
    echo "  Pas remplacés (la raison est donnée plus haut, fichier par fichier) :"
    echo "     $BLOQUES"
    echo "  .ephor/ ne se modifie pas dans un projet : reprenez chaque .ephor-new tel quel"
    echo "  (mv <fichier>.ephor-new <fichier>), ou commitez l'état actuel et relancez."
  fi
  if [ -n "$OBSOLETES" ]; then
    echo "  Plus dans cette version d'Ephor, et plus rien ne les lit :"
    echo "      git rm$OBSOLETES"
  fi
  # Le greffon posé sur chaque poste est une COPIE : le relais et la liste des garde-fous
  # qu'il appelle ne suivent pas le dépôt tout seuls.
  GREFFON_CHANGE=0
  case " $REMPLACES " in *" .ephor/greffon-copilot/hooks/relais.sh "*) GREFFON_CHANGE=1 ;; esac
  if [ "$A_UN_COMMIT" = "1" ] && ! git diff --quiet HEAD -- .ephor/greffon-copilot/hooks/hooks.json 2>/dev/null; then
    GREFFON_CHANGE=1
  fi
  if [ "$GREFFON_CHANGE" = "1" ]; then
    echo "  Le greffon Copilot a changé : sur chaque poste qui travaille sous Copilot,"
    echo "  make copilot-install, puis une fenêtre VS Code NEUVE."
  fi
  echo
  echo "Puis : make verify. S'il est vert, commitez la mise à jour seule, dans son propre commit."
  # opencode et Claude Code ne relisent leur greffon, leurs réglages et leurs sous-agents
  # qu'au démarrage : une session ouverte garde l'ancienne version.
  if [ -n "$HORS_MOTEUR" ]; then
    echo "Et relancez opencode ou Claude Code : ils ne relisent leur greffon et leurs réglages qu'au démarrage."
  fi
  exit 0
fi

# Sans pyproject.toml, `make setup` refuse — et le diagnostic renvoyait vers `make setup`.
# Boucle fermée, sur un dépôt Node ou Go, c'est-à-dire le cas le plus courant en entreprise.
if [ ! -f pyproject.toml ]; then
  cat <<'PY0'

Ce dépôt n'est pas un projet Python (pas de pyproject.toml) : « make setup » refusera, et
c'est normal. Le juge, les garde-fous et le contrat marchent sans lui. Deux routes :
  - ajouter un pyproject.toml minimal, pour l'outillage d'Ephor (ruff, mypy, pytest) ;
  - ou sauter « make setup » et adapter .ephor/projet/verify.sh aux commandes du projet
    (geste 1 ci-dessous). « make verify » restera partiel, et il le dira lui-même.
PY0
fi
cat <<'NEXT'

Les trois gestes suivants, dans cet ordre, et PAS un de plus :

  1. Adaptez .ephor/projet/verify.sh à CE dépôt : format, lint, types, tests ; chaque
     sortie dans .verify/<étape>.txt ; une ligne par étape ; code de retour 0 ou 1.
     Ce fichier est au projet : une mise à jour d'Ephor n'y touchera pas.

  2. Écrivez AGENTS.md À LA MAIN (150 lignes max) : ce que fait le projet, la commande
     unique, la règle de dépendances RÉELLE, les trois pièges du dépôt, où sont les secrets.

  3. Ouvrez Claude Code et lancez /brownfield-onboard pour la suite.

Si vous travaillez aussi sous Copilot : `make copilot-install`, une fois par poste, puis
ouvrez une fenêtre VS Code NEUVE (recharger ne suffit pas). Sans ce geste, seule la vue
« agents » lance des garde-fous, et rien ne vous le dira (.ephor/docs/11-trois-agents.md).

Le lint du projet ne remonte pas le code d'Ephor : ruff y lit .ephor/ruff.toml, les règles
d'Ephor, et pytest comme mypy sautent les dossiers cachés. Un autre outil Python (flake8,
pylint, black) ne sait pas faire ça : ajoutez « .ephor/ » à ses exclusions.

Non installé volontairement : la CI et pyproject.toml. Ils dépendent trop de l'existant :
reprenez les étapes de .gitlab-ci.yml d'Ephor dans la CI du dépôt, une par une.
NEXT
