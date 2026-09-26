#!/usr/bin/env bash
# PreToolUse (Write|Edit|MultiEdit|NotebookEdit) — aucun secret n'entre dans le dépôt.
# Échec fermé volontaire : sans moteur pour lire la charge (jq ou python3), on bloque.
# Un contrôle de sécurité qui s'ouvre quand son outillage manque n'est pas un contrôle.
# Filet de premier niveau seulement : gitleaks (pré-commit et CI) reste la vraie barrière.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée, donc cette garde ne peut rien juger. Elle refuse. Vérifie le dépôt : fichier manquant, droits, ou installation à moitié faite." >&2; exit 2; }
if ! moteur_ok; then
  echo "REFUSÉ : le contrôle anti-secret doit lire l'appel d'outil, et ce poste n'a ni jq ni python3. Installe l'un des deux (python3 suffit)." >&2
  exit 2
fi
# Une charge illisible rendrait tous les champs vides, et ce contrôle
# laisserait passer sans rien voir. Un contrôle aveugle n'est pas un contrôle.
charge_ok || {
  echo "REFUSÉ : l'appel d'outil est illisible (ce n'est pas un objet JSON), donc le contrôle anti-secret ne peut rien voir. Préviens l'opérateur." >&2
  exit 2
}
# Un fichier d'exemple est fait pour contenir de fausses valeurs : c'est même ce que le
# message de refus recommande. On ne lui cherche pas de secrets.
CIBLES="$(cibles)"
# Un fichier d'exemple est fait pour contenir de fausses valeurs : c'est même ce que le
# message de refus recommande. On ne lui cherche pas de secrets — mais un seul vrai
# fichier dans le lot suffit à remettre le contrôle.
EXEMPLES_SEULEMENT=0
if [ -n "$CIBLES" ]; then
  EXEMPLES_SEULEMENT=1
  while IFS= read -r CIBLE; do
    [ -n "$CIBLE" ] || continue
    case "$CIBLE" in *.example|*.sample|*.dist|*.template) ;; *) EXEMPLES_SEULEMENT=0 ;; esac
  done <<CIBLES_LISTE
$CIBLES
CIBLES_LISTE
fi
[ "$EXEMPLES_SEULEMENT" = "1" ] && exit 0

BODY="$(corps)"
[ -z "$BODY" ] && exit 0

PATTERNS='(AKIA[0-9A-Z]{16})|(sk-[A-Za-z0-9_-]{20,})|(gh[pousr]_[A-Za-z0-9]{36})|(glpat-[A-Za-z0-9_-]{20,})|(-----BEGIN [A-Z ]*PRIVATE KEY-----)|(xox[baprs]-[A-Za-z0-9-]{10,})|(AIza[0-9A-Za-z_-]{35})|("?(api[_-]?key|secret|password|passwd|token)"?[[:space:]]*[:=][[:space:]]*"[^"]{20,}")'
# Forme « variable d'environnement » sans guillemets : DB_PASSWORD=valeur (noms en majuscules).
# La valeur doit ressembler à un secret (lettres, chiffres, + / = _ -) ET faire au moins
# vingt caractères : une URL, un appel de fonction, un attribut (`settings.x`) ou une
# constante comme `PASSWORD_HASH_ALGORITHM=pbkdf2_sha256` ne déclenchent pas l'alerte.
ENV_STYLE='(^|[^A-Za-z0-9_])[A-Z0-9_]*(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY)[A-Z0-9_]*[[:space:]]*=[[:space:]]*[A-Za-z0-9_+/=-]{20,}([[:space:]]|$)'
if printf '%s' "$BODY" | grep -qiE "$PATTERNS" || printf '%s' "$BODY" | grep -qE "$ENV_STYLE"; then
  echo "REFUSÉ : le contenu ressemble à un secret en clair (clé API, jeton, clé privée ou mot de passe). Utilise une variable d'environnement et .env.example, jamais une valeur littérale. Faux positif ? Dis-le à l'opérateur au lieu de contourner." >&2
  exit 2
fi
exit 0
