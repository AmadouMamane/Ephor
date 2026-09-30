#!/usr/bin/env bash
# PreToolUse (Write|Edit|MultiEdit|NotebookEdit) — rester dans le périmètre du contrat.
#
# Sur une branche task/<ID>, un fichier hors de `allowed_paths` est refusé tout de suite,
# au lieu d'être découvert par le juge dix minutes plus tard : ce qui est écrit hors
# périmètre est du travail perdu, donc des tokens perdus.
#
# Ouverture volontaire en cas de doute (pas de moteur JSON, pas de contrat, pas de branche) :
# ce hook fait gagner du temps, il ne garantit rien. La garantie, c'est le juge.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || exit 0   # confort : sans la bibliothèque, on s'efface
moteur_ok || exit 0
ROOT="$(racine)"

# Copilot lance chaque hook pour chaque outil, sans filtre : on ne se prononce que sur
# une écriture, telle que `ecriture` la reconnaît (voir _lib.sh). Ce hook fait gagner du
# temps, il ne garantit rien : le juge tranche.
ecriture || exit 0

BRANCHE="$(git -C "$ROOT" branch --show-current 2>/dev/null || true)"
case "$BRANCHE" in task/*) ;; *) exit 0 ;; esac
ID="${BRANCHE#task/}"; ID="${ID%%/*}"
CONTRAT="$ROOT/tasks/$ID/contract.json"
[ -f "$CONTRAT" ] || exit 0

AUTORISES="$(liste "$CONTRAT" allowed_paths)"
[ -z "$AUTORISES" ] && exit 0

# Un outil peut viser plusieurs fichiers d'un coup, et cacher leurs chemins dans un
# correctif : on les examine tous, sinon il suffit d'en grouper deux pour passer.
while IFS= read -r CHEMIN; do
  [ -n "$CHEMIN" ] || continue
  # Les chemins arrivent résolus : `src/../docs/x` est déjà devenu `docs/x`, et c'est
  # comme tel qu'il est comparé au périmètre. Plus rien à deviner ici.
  case "$CHEMIN" in
    "$ROOT"/*) CHEMIN="${CHEMIN#"$ROOT"/}" ;;
    *) continue ;;                     # hors du dépôt : le juge ne le verra pas
  esac
  # PROGRESS.md est l'état tenu par l'agent, jamais une preuve : toujours autorisé.
  [ "$CHEMIN" = "PROGRESS.md" ] && continue

  DEDANS=0
  while IFS= read -r REGLE; do
    [ -z "$REGLE" ] && continue
    case "$REGLE" in
      */) case "$CHEMIN" in "$REGLE"*) DEDANS=1 ;; esac ;;
      *)  [ "$CHEMIN" = "$REGLE" ] && DEDANS=1 ;;
    esac
  done <<REGLES
$AUTORISES
REGLES
  [ "$DEDANS" = "1" ] && continue
  deny "REFUSÉ : $CHEMIN est hors du périmètre du contrat $ID. Autorisé : $(printf '%s' "$AUTORISES" | tr '\n' ' '). Le juge refuserait ce patch. Reste dans le périmètre, ou demande à l'opérateur d'élargir le contrat sur la branche principale."
done <<CIBLES
$(cibles)
CIBLES
exit 0
