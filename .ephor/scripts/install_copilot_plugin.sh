#!/usr/bin/env bash
# Installe le greffon Copilot pour CETTE machine, et y déclare ce dépôt.
#   usage : bash .ephor/scripts/install_copilot_plugin.sh      (ou : make copilot-install)
#
# Pourquoi hors du dépôt : Copilot ne lance les hooks d'un dossier de travail que dans sa
# vue « agents » ; partout, il lance les greffons installés dans `~/.copilot/installed-plugins/`. C'est la
# seule entorse à « tout vit dans le dépôt » — le greffon posé là ne contient AUCUNE
# règle : il appelle les garde-fous du dépôt, qui, eux, restent versionnés.
#
# Idempotent : relancez-le après chaque `make adapters`.
set -euo pipefail
RACINE="$(cd -- "$(dirname -- "$0")/../.." && pwd -P)"
# Geste de l'opérateur : refusé quand un agent le lance, détours compris (.ephor/operateur.py).
python3 "$RACINE/.ephor/operateur.py" "make copilot-install" || exit 2
SOURCE="$RACINE/.ephor/greffon-copilot"
GREFFONS="${COPILOT_PLUGINS:-$HOME/.copilot/installed-plugins}"
CIBLE="$GREFFONS/ephor"
JETON="__RACINE_DU_GREFFON__"

[ -d "$SOURCE" ] || { echo "greffon absent : lancez d'abord make adapters" >&2; exit 1; }

mkdir -p "$CIBLE/.claude-plugin" "$CIBLE/hooks"
cp "$SOURCE/.claude-plugin/plugin.json" "$CIBLE/.claude-plugin/plugin.json"
cp "$SOURCE/hooks/relais.sh" "$CIBLE/hooks/relais.sh"
chmod +x "$CIBLE/hooks/relais.sh"
# Le chemin du greffon n'existe qu'ici : c'est le seul endroit où le jeton est remplacé.
sed "s|$JETON|$CIBLE|g" "$SOURCE/hooks/hooks.json" > "$CIBLE/hooks/hooks.json"
# VS Code garde sa PROPRE copie des hooks du greffon, et ne la refait que si la date du
# DOSSIER change (son repère, dans ~/Library/Application Support/Code/agentPlugins/, est
# cette date — mesuré le 24/09/2026). Réécrire les fichiers en place ne la change pas :
# les réinstallations passaient inaperçues, et la vue « agents » enchaînait toujours
# l'ancienne liste de gardes. On date donc le dossier à nouveau.
touch "$CIBLE/hooks" "$CIBLE/.claude-plugin" "$CIBLE"

# Le greffon tourne pour TOUS les dossiers que Copilot ouvre. Il ne lance les scripts
# que des dépôts inscrits ici : lancer ceux d'un dépôt inconnu, ce serait exécuter le
# code d'un inconnu à chaque appel d'outil.
DECLARES="$CIBLE/depots.txt"
[ -f "$DECLARES" ] || printf '# Dépôts dont le greffon a le droit de lancer les garde-fous.\n' > "$DECLARES"
if grep -qxF "$RACINE" "$DECLARES"; then
  echo "  = $RACINE déjà déclaré"
else
  printf '%s\n' "$RACINE" >> "$DECLARES"
  echo "  + $RACINE déclaré"
fi

# Essai à blanc, ici et maintenant : un refus doit sortir en 2, un travail normal en 0.
# Cela prouve que le relais marche. Cela ne prouve PAS que Copilot l'appelle — ça, seul
# le protocole en quatre demandes de .ephor/docs/10-installation.md peut le dire.
#
# La sonde vise PROGRESS.md, et le refus est un faux secret : c'est le seul couple qui ne
# dépende d'AUCUN réglage du dépôt. Viser une zone de contrôle marcherait ici, et ferait
# échouer l'installation chez qui a une autre liste de refus.
essai() { # essai <ligne ajoutée> -> code de sortie du relais
  printf '{"toolName":"apply_patch","toolArgs":"*** Begin Patch\\n*** Update File: %s/PROGRESS.md\\n+%s\\n*** End Patch","workingDirectory":"%s"}' \
    "$RACINE" "$1" "$RACINE" \
    | bash "$CIBLE/hooks/relais.sh" avant-outil.sh >/dev/null 2>&1 || echo $?
}
REFUS="$(essai 'CLE = \"AKIAIOSFODNN7EXAMPLE\"')"
PASSE="$(essai 'avancement du jour')"
if [ "$REFUS" != "2" ] || [ -n "$PASSE" ]; then
  echo "greffon installé MAIS l'essai à blanc a échoué (refus=$REFUS, normal=${PASSE:-0})." >&2
  echo "jq est-il installé ? les garde-fous de $RACINE/.ephor/hooks/ sont-ils lisibles ?" >&2
  exit 1
fi

cat <<TEXTE

greffon installé : $CIBLE
essai à blanc : un faux secret est refusé, une ligne normale passe.

Il reste deux gestes, et ils ne peuvent pas être faits d'ici :
  1. Ouvrir une FENÊTRE VS Code NEUVE. Un rechargement ne suffit pas : une session
     ouverte avant l'installation ne voit jamais le greffon, et rien ne le dit
     (mesuré — deux écritures en zone de contrôle sont passées comme ça).
     Un greffon n'est lu qu'au démarrage.
  2. Dérouler le protocole en quatre demandes de .ephor/docs/10-installation.md :
     c'est la seule façon de savoir si Copilot appelle vraiment le greffon.
TEXTE
