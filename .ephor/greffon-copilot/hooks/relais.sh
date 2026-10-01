#!/usr/bin/env bash
# Relais des garde-fous pour Copilot.
#
# Copilot ne lance pas partout les hooks déclarés dans un dossier de travail. Sous
# VS Code 1.139.0, le 23 septembre 2026, le fichier était trouvé, listé dans le journal de
# l'éditeur, et jamais appelé ; depuis le 24, la vue « agents » les lance, le panneau de
# discussion non. Ce que Copilot exécute dans les deux vues, c'est un greffon au format
# Claude installé dans `~/.copilot/installed-plugins/`. Le greffon vit donc HORS du dépôt,
# une fois par machine — c'est la seule entorse à « tout vit dans le dépôt », et elle est
# subie, pas choisie. (Il faut aussi une FENÊTRE NEUVE : une session ouverte avant
# l'installation ne voit pas le greffon, et rien ne le dit.)
#
# Ce fichier ne décide rien. Il reçoit la charge de Copilot, retrouve le dépôt déclaré,
# et rejoue cette charge vers les garde-fous DE CE DÉPÔT, dans l'ordre donné en arguments.
# Une seule logique, trois outils : tout ce qui juge vit dans `.ephor/hooks/`.
#
# Le greffon est actif pour TOUS les dossiers que Copilot ouvre. Il ne lance donc que les
# garde-fous des dépôts inscrits dans `depots.txt`, à côté de ce fichier : lancer les
# scripts d'un dépôt inconnu reviendrait à exécuter le code d'un inconnu, à chaque appel
# d'outil. `.ephor/scripts/install_copilot_plugin.sh` y inscrit le dépôt depuis lequel il tourne.
#
# Et quand il ne peut pas faire son travail dans un dépôt qui, lui, attend des garde-fous,
# il REFUSE au lieu de se taire. Un filet qui disparaît sans prévenir est pire que pas de
# filet : on continue à travailler en croyant être couvert.
set -uo pipefail
CHARGE="$(cat)"
ICI="$(cd -- "$(dirname -- "$0")" && pwd -P)"
APPELES="$*"

# Capteur du POSTE, éteint par défaut : `touch <greffon>/trace`, et chaque appel est noté
# dans `<greffon>/trace.jsonl` DÈS SA PREMIÈRE LIGNE — avant toute décision — avec son code
# de sortie et son message. Le capteur du dépôt, plus bas, ne voit que les appels qui ont
# trouvé leur dépôt. Un refus absent d'ici ne vient pas du greffon : dans la vue « agents »,
# ce sont les hooks du dépôt qui l'ont prononcé, avant lui (mesuré le 24/09/2026). Les
# charges contiennent les demandes de l'opérateur : à éteindre après usage (rm <greffon>/trace).
if [ -f "$ICI/../trace" ] && TRACE_ERR="$(mktemp "${TMPDIR:-/tmp}/ephor-relais.XXXXXX")"; then
  exec 3>&2 2>"$TRACE_ERR"
  noter() {
    local code=$? message
    exec 2>&3 3>&-
    message="$(cat "$TRACE_ERR" 2>/dev/null)"
    rm -f "$TRACE_ERR"
    [ -n "$message" ] && printf '%s\n' "$message" >&2
    EPHOR_T_CHARGE="$CHARGE" EPHOR_T_MESSAGE="$message" python3 -c 'import json, os, sys, time
print(json.dumps({"quand": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "dossier": sys.argv[2],
                  "gardes": sys.argv[3], "code": int(sys.argv[1]),
                  "message": os.environ["EPHOR_T_MESSAGE"], "charge": os.environ["EPHOR_T_CHARGE"]},
                 ensure_ascii=False))' "$code" "$PWD" "$APPELES" >> "$ICI/../trace.jsonl" 2>/dev/null
    exit "$code"
  }
  trap noter EXIT
fi

_plier() { # plie « . », « .. » et les doubles barres d'un chemin absolu
  local morceau pile="" IFS=/
  for morceau in $1; do
    case "$morceau" in
      ""|.) ;;
      ..) pile="${pile%/*}" ;;
      *) pile="$pile/$morceau" ;;
    esac
  done
  printf '%s' "${pile:-/}"
}

resoudre() { # même résolution que les garde-fous : ni « . », ni « .. », ni lien
  local chemin="$1" reste="" tete reel
  case "$chemin" in /*) ;; *) chemin="${2:-$PWD}/$chemin" ;; esac
  while [ "${chemin%/}" != "$chemin" ] && [ "$chemin" != "/" ]; do chemin="${chemin%/}"; done
  tete="$chemin"
  while [ -n "$tete" ]; do
    # `cd` fait le travail où il peut : il suit les liens et avale les détours.
    if reel="$(cd -- "$tete" 2>/dev/null && pwd -P)"; then
      # Ce qui reste n'existe pas encore — donc aucun lien à craindre : on le plie à la
      # main. Sans cela, `src/../ephor/x` restait tel quel quand `src/` n'existait pas,
      # et traversait toutes les listes de refus.
      printf '%s' "$(_plier "${reel%/}$reste")"
      return 0
    fi
    [ "$tete" = "/" ] && break
    reste="/${tete##*/}$reste"
    tete="${tete%/*}"
    [ -n "$tete" ] || tete="/"
  done
  printf '%s' "$(_plier "$chemin")"
}

# Où vivent les garde-fous d'un dépôt : `.ephor/hooks/` depuis Ephor 0.9, `.claude/hooks/`
# avant. Le greffon est posé UNE fois par poste et sert tous les dépôts déclarés : un même
# poste porte donc des projets équipés à des versions différentes. Ne connaître que la
# nouvelle adresse aurait fait refuser tout appel d'outil dans les projets pas encore
# mis à jour — et l'ancienne seule, dans les autres.
gardes_de() { # gardes_de <dossier> : écrit le dossier de ses garde-fous, ou échoue
  local dossier
  for dossier in "$1/.ephor/hooks" "$1/.claude/hooks"; do
    if [ -f "$dossier/_lib.sh" ]; then printf '%s' "$dossier"; return 0; fi
  done
  return 1
}

gardes_la() { # gardes_la <dossier> : ce dossier porte-t-il les garde-fous d'Ephor ?
  gardes_de "$1" >/dev/null
}

# Le dossier ouvert est DANS la charge : sans moteur pour la lire, le relais retombait sur
# son dossier courant, ne reconnaissait plus aucun dépôt déclaré, et ne lançait donc AUCUNE
# garde — en silence. C'est exactement le filet qui disparaît sans prévenir.
if command -v jq >/dev/null 2>&1; then
  OUVERT="$(printf '%s' "$CHARGE" | jq -r '.workingDirectory // .cwd // empty' 2>/dev/null)"
elif command -v python3 >/dev/null 2>&1; then
  OUVERT="$(printf '%s' "$CHARGE" | python3 -c 'import json,sys
try:
    charge = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(charge, dict):
    for cle in ("workingDirectory", "cwd"):
        valeur = charge.get(cle)
        if isinstance(valeur, str) and valeur:
            print(valeur)
            break' 2>/dev/null)"
else
  echo "REFUSÉ : le relais des garde-fous doit lire l'appel de Copilot, et ce poste n'a ni jq ni python3. Il refuse plutôt que de travailler sans filet. Installez l'un des deux (python3 suffit)." >&2
  exit 2
fi
[ -n "$OUVERT" ] || OUVERT="$PWD"
OUVERT="$(resoudre "$OUVERT")"

# Le dépôt DÉCLARÉ qui contient le dossier ouvert — c'est lui qui fournit les garde-fous,
# jamais un dépôt tiers cloné dessous, qui glisserait ses propres scripts dans la boucle.
DECLARES="$ICI/../depots.txt"
declare_pour() { # declare_pour <dossier> : le dépôt déclaré qui le contient, ou rien
  local ligne trouve=""
  [ -f "$DECLARES" ] || return 0
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    ligne="${ligne%%#*}"
    ligne="${ligne#"${ligne%%[![:space:]]*}"}"
    ligne="${ligne%"${ligne##*[![:space:]]}"}"
    [ -n "$ligne" ] || continue
    case "$ligne" in "~/"*) ligne="$HOME/${ligne#\~/}" ;; esac
    ligne="$(resoudre "$ligne")"
    # Le dossier déclaré lui-même, ou n'importe lequel dessous : VS Code ouvre parfois un
    # sous-dossier. Les deux chemins sont résolus, donc un « .. » ne rachète rien.
    case "$1/" in "$ligne"/*) trouve="$ligne" ;; esac
  done < "$DECLARES"
  printf '%s' "$trouve"
}
DEPOT="$(declare_pour "$OUVERT")"
# D'où viennent les garde-fous : le dépôt déclaré, et pour le dossier d'une tâche (un
# worktree, #32), sa copie principale déclarée. `make task-approve` dit de l'ouvrir sous
# Copilot ; non déclaré, le relais refusait tout, `read_file` compris (audit du 01/10/2026).
# Ce sont les gardes de l'opérateur qui jugent, pas celles de la branche de l'agent.
SOURCE=""
if [ -z "$DEPOT" ] && command -v git >/dev/null 2>&1; then
  TETE="$(git -C "$OUVERT" rev-parse --show-toplevel 2>/dev/null || true)"
  COMMUN="$(git -C "$OUVERT" rev-parse --git-common-dir 2>/dev/null || true)"
  if [ -n "$TETE" ] && [ -n "$COMMUN" ]; then
    case "$COMMUN" in /*) ;; *) COMMUN="$OUVERT/$COMMUN" ;; esac
    COMMUN="$(resoudre "$COMMUN")"
    case "$COMMUN" in
      */.git)
        PRINCIPALE="$(declare_pour "${COMMUN%/.git}")"
        if [ -n "$PRINCIPALE" ] && [ "$PRINCIPALE" = "${COMMUN%/.git}" ]; then
          DEPOT="$(resoudre "$TETE")"
          SOURCE="$PRINCIPALE"
        fi ;;
    esac
  fi
fi

if [ -z "$DEPOT" ]; then
  # Pas déclaré. Si ce dossier — ou l'un de ses parents — attend des garde-fous, se taire
  # serait mentir : on refuse, et on dit quoi faire. Sinon ce n'est pas notre affaire.
  SONDE="$OUVERT"
  while [ -n "$SONDE" ] && [ "$SONDE" != "/" ] && [ "$SONDE" != "$HOME" ]; do
    if gardes_la "$SONDE"; then
      echo "REFUSÉ : $SONDE attend les garde-fous d'Ephor, et ce poste ne les a pas branchés pour lui. Lancez « make copilot-install » dans ce dépôt, puis ouvrez une FENÊTRE NEUVE (un greffon n'est lu qu'au démarrage). Sans ça, rien n'arrête l'agent en direct." >&2
      exit 2
    fi
    SONDE="${SONDE%/*}"
  done
  exit 0
fi

if ! GARDES="$(gardes_de "${SOURCE:-$DEPOT}")"; then
  echo "REFUSÉ : $DEPOT est déclaré auprès du greffon Copilot, mais ses garde-fous ($DEPOT/.ephor/hooks/) sont introuvables. Soit le dépôt a changé, soit la déclaration est périmée : corrigez-la dans $DECLARES, ou relancez « make copilot-install »." >&2
  exit 2
fi

# Capteur, éteint par défaut. `touch .verify/trace-copilot` dans le dépôt, et chaque charge
# reçue est écrite dans `.verify/copilot-charges.jsonl`. C'est la seule façon de répondre à
# « Copilot appelle-t-il vraiment le greffon, et avec quoi ? » — la question revient à
# chaque version de l'éditeur, et on ne la tranche pas en devinant.
if [ -f "$DEPOT/.verify/trace-copilot" ]; then
  printf '%s\n' "$CHARGE" >> "$DEPOT/.verify/copilot-charges.jsonl" 2>/dev/null || true
fi

[ "$#" -gt 0 ] || exit 0
DEMANDEES=("$@")
# Un dépôt d'avant la 1.4 n'a pas les points d'entrée (ADR 0011) : ses gardes d'alors, une à
# une. Sans ce repli, un poste réinstallé laisserait un vieux dépôt sans aucun garde-fou.
if [ "$#" -eq 1 ] && [ ! -f "$GARDES/$1" ]; then
  case "$1" in
    avant-outil.sh) DEMANDEES=(kill-switch.sh block-secrets.sh write-guard.sh scope-guard.sh refs-guard.sh shell-guard.sh read-guard.sh) ;;
    debut-tour.sh) DEMANDEES=(steer.sh) ;;
    fin-tour.sh) DEMANDEES=(budget.sh checkpoint.sh) ;;
  esac
fi
juger() { # juger <charge> [ensemble] : la passe aux gardes, dans l'ordre ; sort au premier refus
  local GARDE SCRIPT SORTIE CODE
  # Un appel d'outil, dans un dépôt qui sait le faire (Ephor 1.3 et après) : les gardes
  # tournent en même temps, avec le verdict qu'elles rendraient l'une après l'autre (voir
  # .ephor/hooks/ensemble.sh). Deux fois moins d'attente à chaque appel d'outil.
  # EPHOR_GARDES_EN_SERIE=1, dans l'environnement de l'éditeur, remet la série.
  # Un point d'entrée seul (Ephor 1.4 et après) lance lui-même ses gardes ensemble.
  if [ "${2:-}" = "ensemble" ] && [ "${#DEMANDEES[@]}" -gt 1 ] && [ -f "$GARDES/ensemble.sh" ]; then
    SORTIE="$(printf '%s' "$1" | CLAUDE_PROJECT_DIR="$DEPOT" EPHOR_OUTIL=copilot bash "$GARDES/ensemble.sh" "${DEMANDEES[@]}")"
    CODE=$?
    [ "$CODE" = "0" ] || exit "$CODE"
    [ -n "$SORTIE" ] && printf '%s\n' "$SORTIE"
    return 0
  fi
  for GARDE in "${DEMANDEES[@]}"; do
    SCRIPT="$GARDES/$GARDE"
    [ -f "$SCRIPT" ] || continue
    # La sortie d'erreur passe telle quelle : c'est le message de refus que l'agent doit lire.
    # La sortie standard est reprise à notre compte (consigne opérateur de `steer.sh`).
    SORTIE="$(printf '%s' "$1" | CLAUDE_PROJECT_DIR="$DEPOT" EPHOR_OUTIL=copilot bash "$SCRIPT")"
    CODE=$?
    [ "$CODE" = "0" ] || exit "$CODE"
    [ -n "$SORTIE" ] && printf '%s\n' "$SORTIE"
  done
  return 0
}

# Un appel d'outil, ou autre chose (un message envoyé, la fin d'un tour) ? Seules les
# gardes d'appel d'outil ont besoin de savoir QUEL outil, et sur quoi.
APPEL_D_OUTIL=0
for GARDE in "${DEMANDEES[@]}"; do
  case "$GARDE" in
    avant-outil.sh|write-guard.sh|read-guard.sh|scope-guard.sh|block-secrets.sh|refs-guard.sh|shell-guard.sh) APPEL_D_OUTIL=1 ;;
  esac
done
if [ "$APPEL_D_OUTIL" != "1" ]; then
  juger "$CHARGE"
  exit 0
fi

# Le moteur de la vue « agents » de VS Code (« vscode-agent-host », VS Code 1.139.0) montre
# dans son journal des appels groupés en LOT :
#   {"sessionId": "…", "cwd": "…", "toolCalls": [{"id": "…", "name": "apply_patch", "args": …}]}
# Le greffon, lui, reçoit chaque appel SEUL, à la forme Copilot connue (mesuré le
# 24/09/2026) ; mais si un lot arrive un jour tel quel, il est éclaté en appels à la forme
# connue, un par outil, et chacun passe TOUTES les gardes ; un seul refus refuse le lot.
#
# Et une forme qu'on ne reconnaît pas est REFUSÉE. L'éditeur en changera encore ; ce
# jour-là, le filet doit se voir tomber, pas disparaître.
eclater() { # une charge par ligne, à la forme connue ; échoue sur une forme inconnue
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$CHARGE" | jq -c '
      if type != "object" then error("pas un objet")
      elif ((.toolCalls // .tool_calls) | type) == "array" then
        (.cwd // .workingDirectory // "") as $ouvert
        | (.toolCalls // .tool_calls)[]
        | if type != "object" then error("appel illisible") else . end
        | {toolName: (.name // .toolName // .tool_name // ""), toolArgs: (.args // .arguments // .toolArgs // .tool_input // {}), workingDirectory: $ouvert}
      elif ((.toolName | type) == "string") or ((.tool_name | type) == "string") then .
      else error("forme inconnue") end' 2>/dev/null
  else
    printf '%s' "$CHARGE" | python3 -c 'import json, sys
try:
    charge = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not isinstance(charge, dict):
    sys.exit(1)
lot = charge.get("toolCalls") if charge.get("toolCalls") is not None else charge.get("tool_calls")
if isinstance(lot, list):
    ouvert = charge.get("cwd") or charge.get("workingDirectory") or ""
    for appel in lot:
        if not isinstance(appel, dict):
            sys.exit(1)
        args = next((appel[c] for c in ("args", "arguments", "toolArgs", "tool_input") if appel.get(c) is not None), {})
        nom = appel.get("name") or appel.get("toolName") or appel.get("tool_name") or ""
        print(json.dumps({"toolName": nom, "toolArgs": args, "workingDirectory": ouvert}, ensure_ascii=False))
elif isinstance(charge.get("toolName"), str) or isinstance(charge.get("tool_name"), str):
    print(json.dumps(charge, ensure_ascii=False))
else:
    sys.exit(1)' 2>/dev/null
  fi
}
if ! APPELS="$(eclater)"; then
  echo "REFUSÉ : le greffon Copilot d'Ephor ne reconnaît pas la forme de cet appel d'outil (ni « toolName » ni « tool_name », ni un lot « toolCalls »). L'éditeur a sans doute changé de protocole. Plutôt que de laisser passer sans rien voir, il refuse. Mettez Ephor à jour ; pour voir la charge reçue : touch $DEPOT/.verify/trace-copilot, puis lisez $DEPOT/.verify/copilot-charges.jsonl." >&2
  exit 2
fi
while IFS= read -r APPEL; do
  [ -n "$APPEL" ] || continue
  juger "$APPEL" ensemble
done <<LOT
$APPELS
LOT
exit 0
