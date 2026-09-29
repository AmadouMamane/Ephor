#!/usr/bin/env bash
# La sentinelle des zones de contrôle : `sentinelle.sh photo`, puis `sentinelle.sh controle`.
#
# Les garde-fous jugent un appel d'outil, et le terminal offre des détours qu'aucun ne voit :
# un script écrit ailleurs puis lancé, une variable, un encodage. Le 24/09/2026, un agent
# refusé a écrit « comme le terminal restait utilisable, j'ai basculé sur des commandes
# shell ». La sentinelle regarde le RÉSULTAT, pas le moyen : au début du tour, elle
# photographie les zones de contrôle ; à la fin, elle compare. Une zone qui a changé pendant
# le tour, par quelque chemin que ce soit, pose AGENT_STOP avec la liste des fichiers.
#
# Ce qu'elle regarde (chaque point a d'abord été mesuré en défaut) :
#   - les zones du MOTEUR (.ephor/zones.txt), plus celles que les réglages ajoutent — au
#     début ET à la fin du tour. Elle lisait sa liste dans .claude/settings.json : un agent
#     qui en retirait les règles la rendait aveugle ;
#   - les fichiers ignorés par git dans une zone : .git/info/exclude ou un fichier ignoré
#     (.claude/settings.local.json) passaient sans être vus. Ce fichier-là, Claude Code
#     l'écrit lui-même quand l'opérateur clique « ne plus demander » : il est lu, pas
#     comparé — ce qui compte, c'est qu'il coupe les hooks ou les permissions ;
#   - le dossier git : sa configuration (hors branches), ses hooks, ses exclusions ;
#   - le tour INTERROMPU : Claude Code ne lance pas le hook de fin de tour quand on
#     interrompt l'agent (2.1.282). Un tour jamais contrôlé l'est au début du suivant.
# Et c'est la sentinelle du dernier commit qui tourne, pas celle du disque (voir steer.sh et
# checkpoint.sh) : réécrite pendant le tour, elle ne pourrait pas se juger elle-même.
#
# Ce qu'elle ne fait pas : empêcher. Elle constate, et prévient tout de suite ; `git restore`
# défait. Un agent qui VEUT la faire taire y arrive encore (réécrire les deux hooks qui
# l'appellent, par exemple) : c'est une alarme contre le détour, pas une frontière. Le juge,
# qui refuse tout correctif touchant ces zones, reste la barrière.
#
# Une retouche de l'opérateur ENTRE deux tours ne la déclenche pas, puisque la photo est
# reprise à chaque demande — sauf après un tour interrompu, où elle ne peut pas savoir qui a
# écrit : le message le dit, et `make resume` lève l'arrêt.
#
# Appelée par steer.sh (début du tour) et checkpoint.sh (fin du tour), déjà branchés sous
# Claude Code, dans la vue « agents » de VS Code et par le greffon Copilot ; sous opencode,
# par son greffon, au message de l'opérateur et quand la session redevient inactive (1.1.5).
# Il passait pour n'avoir pas de fin de tour : il en a une.
#
# Sort en 1 quand elle alerte, pour que l'outil MONTRE le message ; en 0 sinon, y compris
# quand elle ne peut rien dire (pas de dépôt, pas de moteur de lecture).
set -uo pipefail
MODE="${1:-}"
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || exit 0
moteur_ok || exit 0
ROOT="$(racine)"
cd "$ROOT" 2>/dev/null || exit 0
git rev-parse --verify -q HEAD >/dev/null 2>&1 || exit 0
REGLAGES="$ROOT/.claude/settings.json"
LOCAL="$ROOT/.claude/settings.local.json"

SESSION="$(_charge session)"
SESSION="$(printf '%s' "$SESSION" | tr -cd 'A-Za-z0-9._-' | cut -c1-64)"
[ -n "$SESSION" ] || SESSION=inconnue
# Dans le dossier de git, pas dans .verify/ : `make clean` y efface tout en cours de tour.
PHOTOS="$(git rev-parse --git-path ephor-sentinelle 2>/dev/null)" || exit 0
case "$PHOTOS" in /*) ;; *) PHOTOS="$ROOT/$PHOTOS" ;; esac
PHOTO="$PHOTOS/$SESSION"
mkdir -p "$PHOTOS" 2>/dev/null || exit 0

specs_moteur() { # les zones du moteur, en pathspecs git ; le dossier git se regarde à part
  local zone
  zones_moteur | while IFS= read -r zone; do
    case "$zone" in
      /*|.git|.git/) continue ;;
      */) printf ':(glob)%s**\n' "$zone" ;;
      *) printf ':(glob)%s\n' "$zone" ;;
    esac
  done
}

specs_reglages() { # les zones que les réglages du projet ajoutent
  [ -f "$REGLAGES" ] || return 0
  local motif
  refus "$REGLAGES" Edit | while IFS= read -r motif; do
    motif="${motif#./}"
    case "$motif" in ""|/*|"~"*|.git|.git/*) continue ;; esac
    printf ':(glob)%s\n' "$motif"
  done
}

lue() { # lue <clé> : la valeur de la ligne « clé valeur » de la photo
  sed -n "s/^$1 //p" "$PHOTO" 2>/dev/null
}

# Ce que des outils posent d'eux-mêmes dans une zone, sans que personne l'écrive : caches de
# Python et des outils de vérification, dépendances du greffon d'opencode, arbres de travail
# des agents. Depuis que la sentinelle voit les fichiers ignorés, un cache de ruff dans
# .ephor/ la faisait crier à chaque `make verify` (vu en mesurant le bac à sable, 25/09/2026). Les compter ferait
# crier la sentinelle pour rien. `settings.local.json` est lu à part (voir `drapeaux`).
EXCLUS=(
  ":(exclude,glob)**/__pycache__/**"
  ":(exclude,glob)**/.ruff_cache/**"
  ":(exclude,glob)**/.mypy_cache/**"
  ":(exclude,glob)**/.pytest_cache/**"
  ":(exclude,glob)**/.hypothesis/**"
  ":(exclude,glob)**/node_modules/**"
  ":(exclude).opencode/package.json"
  ":(exclude).opencode/package-lock.json"
  ":(exclude).opencode/bun.lock"
  ":(exclude).claude/settings.local.json"
  ":(exclude,glob).claude/worktrees/**"
)

arbre() { # arbre <pathspec>... : HEAD, plus les zones telles qu'elles sont sur le disque
  local tmp resultat=""
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/ephor-sentinelle.XXXXXX")" || return 1
  # Ce qui diffère de HEAD dans les zones, plus TOUT ce qui est nouveau, ignoré compris.
  # `--remove` retire de l'arbre ce qui n'est plus sur le disque.
  if GIT_INDEX_FILE="$tmp/index" git read-tree HEAD 2>/dev/null \
    && { git diff --name-only -z HEAD -- "$@" "${EXCLUS[@]}" 2>/dev/null
         git ls-files -z -o -- "$@" "${EXCLUS[@]}" 2>/dev/null; } \
       | GIT_INDEX_FILE="$tmp/index" git update-index -z --add --remove --stdin 2>/dev/null; then
    resultat="$(GIT_INDEX_FILE="$tmp/index" git write-tree 2>/dev/null)"
  fi
  rm -rf "$tmp"
  [ -n "$resultat" ] && printf '%s' "$resultat"
}

etat_git() { # empreinte de ce que git exécute : configuration hors branches, hooks, exclusions
  local commun fichier
  commun="$(git rev-parse --git-common-dir 2>/dev/null)" || return 0
  case "$commun" in /*) ;; *) commun="$ROOT/$commun" ;; esac
  {
    git config --file "$commun/config" --list 2>/dev/null | grep -v '^branch\.'
    for fichier in "$commun"/hooks/* "$commun/info/attributes" "$commun/info/exclude"; do
      [ -f "$fichier" ] || continue
      case "$fichier" in *.sample) continue ;; esac
      printf '%s %s\n' "${fichier#"$commun"/}" "$(git hash-object -- "$fichier" 2>/dev/null)"
    done
  } | git hash-object --stdin 2>/dev/null
}

drapeaux() { # ce que .claude/settings.local.json coupe : hooks, permissions, MCP, bac à sable
  [ -f "$LOCAL" ] || return 0
  if jq_ok; then
    jq -r '[ (if .disableAllHooks == true then "disableAllHooks" else empty end),
             (if .permissions.defaultMode == "bypassPermissions" then "bypassPermissions" else empty end),
             (if .enableAllProjectMcpServers == true then "enableAllProjectMcpServers" else empty end),
             (if .sandbox.enabled == false then "sandbox.enabled=false" else empty end),
             (if .sandbox.allowUnsandboxedCommands == true then "allowUnsandboxedCommands" else empty end)
           ] | join(" ")' "$LOCAL" 2>/dev/null || printf 'illisible'
  else
    python3 -c 'import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fichier:
        r = json.load(fichier)
except Exception:
    r = None
if not isinstance(r, dict):
    print("illisible")
    sys.exit(0)
d = []
if r.get("disableAllHooks") is True:
    d.append("disableAllHooks")
if (r.get("permissions") or {}).get("defaultMode") == "bypassPermissions":
    d.append("bypassPermissions")
if r.get("enableAllProjectMcpServers") is True:
    d.append("enableAllProjectMcpServers")
bac = r.get("sandbox") if isinstance(r.get("sandbox"), dict) else {}
if bac.get("enabled") is False:
    d.append("sandbox.enabled=false")
if bac.get("allowUnsandboxedCommands") is True:
    d.append("allowUnsandboxedCommands")
print(" ".join(d))' "$LOCAL" 2>/dev/null
  fi
}

# Les zones de ce contrôle : celles du moteur, celles de la photo, celles des réglages
# d'aujourd'hui. Une zone retirée pendant le tour reste regardée, et son retrait se voit.
SPECS=()
while IFS= read -r SPEC; do
  [ -n "$SPEC" ] && SPECS+=("$SPEC")
done <<SPECS_LISTE
$( { specs_moteur; lue spec; specs_reglages; } | sort -u )
SPECS_LISTE
[ "${#SPECS[@]}" -gt 0 ] || exit 0

photographier() { # photographier <état> : la référence du prochain contrôle
  local tree
  tree="$(arbre "${SPECS[@]}")" || return 0
  [ -n "$tree" ] || return 0
  {
    printf 'arbre %s\netat %s\ngit %s\nlocal %s\n' "$tree" "$1" "$(etat_git)" "$(drapeaux)"
    printf 'spec %s\n' "${SPECS[@]}"
  } > "$PHOTO"
}

controler() { # controler <quand> : compare à la photo ; alerte et rend 1 si une zone a changé
  local avant maintenant changes liste total raison nouveaux drapeau
  avant="$(lue arbre)"
  if [ -z "$avant" ] || ! git cat-file -e "$avant^{tree}" 2>/dev/null; then
    return 0  # pas de photo pour ce tour (hook branché en cours de session) : on commence ici
  fi
  maintenant="$(arbre "${SPECS[@]}")" || return 0
  [ -n "$maintenant" ] || return 0
  changes=""
  if [ "$avant" != "$maintenant" ]; then
    changes="$(git -c core.quotepath=off diff-tree -r --name-status "$avant" "$maintenant" -- "${SPECS[@]}" 2>/dev/null)"
  fi
  if [ "$(lue git)" != "$(etat_git)" ]; then
    changes="${changes:+$changes$'\n'}M"$'\t'"dossier git (configuration, hooks ou exclusions)"
  fi
  nouveaux=""
  for drapeau in $(drapeaux); do
    case " $(lue local) " in *" $drapeau "*) ;; *) nouveaux="$nouveaux $drapeau" ;; esac
  done
  if [ -n "$nouveaux" ]; then
    changes="${changes:+$changes$'\n'}M"$'\t'".claude/settings.local.json :$nouveaux"
  fi
  [ -n "$changes" ] || return 0
  liste="$(printf '%s\n' "$changes" | head -n 8 | awk -F '\t' '
    { etat = $1 == "A" ? "ajouté" : $1 == "D" ? "supprimé" : "modifié"
      printf "%s%s (%s)", (NR > 1 ? ", " : ""), $2, etat }')"
  total="$(printf '%s\n' "$changes" | wc -l | tr -d ' ')"
  [ "$total" -gt 8 ] && liste="$liste, et $((total - 8)) autre(s)"
  raison="sentinelle : zone de contrôle changée $1 — $liste. À vérifier (git diff ; git status pour un fichier nouveau), à défaire au besoin (git restore -- <fichier>). Si c'est l'opérateur qui l'a changée : make resume."
  printf '%s\n' "$raison" >> "$ROOT/AGENT_STOP"
  echo "ATTENTION — $raison AGENT_STOP posé : l'agent est arrêté." >&2
  return 1
}

CODE=0
case "$MODE" in
  photo)
    # Un tour jamais contrôlé : sa fin n'a pas tourné (tour interrompu, erreur). On le
    # contrôle maintenant, avant de reprendre la photo. Sans session connue (un appel
    # sans charge), rien ne dit que c'est le même agent : on s'abstient.
    if [ "$SESSION" != inconnue ] && [ "$(lue etat)" = photo ]; then
      controler "pendant le tour précédent, qui n'est pas allé jusqu'à sa fin (interrompu ?), ou depuis" || CODE=1
    fi
    photographier photo
    ;;
  controle)
    controler "pendant le tour de l'agent" || CODE=1
    # La nouvelle photo devient la référence : l'alerte n'est donnée qu'une fois. Dans la vue
    # « agents », hooks du dépôt et greffon tournent tous deux ; le second n'a rien à redire.
    photographier controle
    ;;
esac
exit "$CODE"
