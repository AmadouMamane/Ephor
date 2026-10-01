#!/usr/bin/env bash
# Diagnostic de l'environnement, à lancer AVANT `make setup` et chaque fois que
# `make verify` échoue pour une raison qui ne ressemble pas à du code.
#
# Raison d'être : sur une machine réelle, ce qui casse n'est presque jamais
# Ephor — c'est un PATH, un conda actif qui masque un binaire, un bit
# d'exécution perdu à la décompression, ou une étape d'installation sautée.
# Ce script nomme la cause et donne la commande exacte. Il ne corrige rien lui-même.
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "racine du dépôt introuvable" >&2; exit 1; }

FAIL=0
ok()   { printf '  [ OK ] %s\n' "$1"; }
warn() { printf '  [WARN] %s\n         → %s\n' "$1" "$2"; }
bad()  { printf '  [FAIL] %s\n         → %s\n' "$1" "$2"; FAIL=1; }

echo "== Emplacement =="
PWD_REAL=$(pwd -P)
case "$PWD_REAL" in
  *"Mobile Documents"*|*iCloud*|*Dropbox*|*"Google Drive"*|*OneDrive*)
    warn "le projet est dans un dossier synchronisé ($PWD_REAL)" \
         "déplacez-le : les fichiers évincés par la synchro cassent les lectures de l'agent" ;;
  *) ok "hors dossier synchronisé" ;;
esac
if git rev-parse --git-dir >/dev/null 2>&1; then
  ok "dépôt git initialisé"
else
  bad "pas un dépôt git" "git init && git add -A && git commit -m 'chore: base Ephor'"
fi
if command -v xattr >/dev/null 2>&1 && xattr -r . 2>/dev/null | grep -q com.apple.quarantine; then
  warn "attribut de quarantaine macOS présent" "xattr -dr com.apple.quarantine ."
fi

echo
echo "== Outils obligatoires =="
for t in git make; do
  if command -v "$t" >/dev/null 2>&1; then
    ok "$t ($(command -v "$t"))"
  else
    case "$t" in
      make) bad "make absent" "xcode-select --install" ;;
      *)    bad "$t absent" "xcode-select --install" ;;
    esac
  fi
done
# jq n'est plus une dépendance dure : les garde-fous savent lire leur charge avec python3.
# Ce qui bloque, c'est de n'avoir NI l'un NI l'autre — une garde qui ne peut pas lire
# l'appel d'outil refuse, donc plus rien ne passe.
if command -v jq >/dev/null 2>&1; then
  ok "jq ($(command -v jq)) — moteur JSON des garde-fous"
elif command -v python3 >/dev/null 2>&1; then
  warn "jq absent : les garde-fous lisent leur charge avec python3" \
       "confort seulement — brew install jq, ou demander son ajout à l'image du poste"
else
  bad "ni jq ni python3 : les garde-fous refuseront TOUT appel d'outil" \
      "installez python3 (ou jq) — sans l'un des deux, aucune garde ne peut juger"
fi

echo
echo "== uv et environnement Python =="
if [ -n "${CONDA_DEFAULT_ENV:-}" ]; then
  warn "environnement conda actif (${CONDA_DEFAULT_ENV}) — il masque souvent uv et python" \
       "conda deactivate   (durable : conda config --set auto_activate_base false)"
fi
if command -v uv >/dev/null 2>&1; then
  UV_PATH=$(command -v uv)
  N=$(command -v -a uv 2>/dev/null | wc -l | tr -d ' ')
  if [ "${N:-1}" -gt 1 ]; then
    warn "plusieurs uv dans le PATH, c'est «$UV_PATH» qui gagne" \
         "export PATH=\"\$HOME/.local/bin:\$PATH\"  puis  which -a uv"
  else
    ok "uv ($UV_PATH, $(uv --version 2>/dev/null | head -1))"
  fi
else
  bad "uv absent du PATH" "export PATH=\"\$HOME/.local/bin:\$PATH\"  (installez-le : curl -LsSf https://astral.sh/uv/install.sh | sh)"
fi
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "?")
# `3.[2-9]*` disait OUI à 3.9 : le motif attrapait 3.2 à 3.9, c'est-à-dire exactement
# le python3 système d'un Mac — et le point que .ephor/docs/10 désigne comme blocage numéro un
# passait au vert. Deux chiffres sont exigés après le point, sauf pour 3.11 à 3.19.
case "$PYV" in
  3.1[1-9]|3.[2-9][0-9]|[4-9].*) ok "python3 système $PYV (≥ 3.11)" ;;
  "?")  warn "pas de python3 système" "sans réseau, uv ne pourra pas en télécharger un — point à remonter" ;;
  *)    warn "python3 système $PYV, le projet exige ≥ 3.11" "uv télécharge sa propre version SI le réseau le permet" ;;
esac
if [ -d .venv ]; then
  ok "environnement du projet présent (.venv)"
elif [ ! -f pyproject.toml ]; then
  # Renvoyer vers `make setup` sur un dépôt Node ou Go était une boucle fermée : setup
  # refuse (pas de pyproject), le diagnostic renvoie vers setup, et on tourne en rond.
  warn "pas d'environnement Python, et ce dépôt n'est pas un projet Python" \
       "normal : le juge et les garde-fous marchent sans. Adaptez .ephor/projet/verify.sh aux commandes du projet (.ephor/docs/10)"
else
  # Un avertissement, pas un échec : ce diagnostic se lance AVANT `make setup`. En échec, le
  # premier geste de la recette « nouveau projet » finissait sur `make: *** [doctor] Error 1`.
  warn "environnement pas encore installé (.venv absent) — l'étape la plus souvent sautée" "make setup"
fi

echo
echo "== Réseau et dépôt de paquets (poste contraint) =="
PROXY=""
for v in HTTPS_PROXY https_proxy HTTP_PROXY http_proxy; do
  # Sans l'identifiant ni le mot de passe : `make doctor` est permis à l'agent, et sa sortie
  # entre dans le prompt. Le mot de passe s'y affichait en clair (audit du 01/10/2026).
  [ -n "${!v:-}" ] && PROXY="$v=$(printf '%s' "${!v}" | sed -E 's#(://)[^/@]*@#\1…@#')"
done
[ -n "$PROXY" ] && ok "proxy configuré ($PROXY)" || printf '  [ -- ] aucun proxy dans l'"'"'environnement\n'
IDX=""
for v in UV_INDEX_URL UV_DEFAULT_INDEX PIP_INDEX_URL UV_INDEX; do
  [ -n "${!v:-}" ] && IDX="$v"
done
if [ -n "$IDX" ]; then
  ok "dépôt de paquets interne configuré ($IDX)"
else
  printf '  [ -- ] aucun dépôt interne configuré — PyPI public sera tenté\n'
  printf '         si le réseau est filtré : export UV_DEFAULT_INDEX=<votre dépôt PyPI JFrog>\n'
fi
# Un jeton écrit DANS l'adresse (`https://moi:jeton@…`) : le juge l'ôte hors installation,
# mais il reste en clair partout où l'adresse s'affiche. La valeur n'est jamais imprimée ici.
AVEC_ID=""
for v in UV_INDEX_URL UV_DEFAULT_INDEX UV_EXTRA_INDEX_URL UV_INDEX PIP_INDEX_URL; do
  case "${!v:-}" in *://*@*) AVEC_ID="${AVEC_ID:+$AVEC_ID }$v" ;; esac
done
if [ -n "$AVEC_ID" ]; then
  warn "identifiant écrit dans l'adresse de l'index ($AVEC_ID) : le juge l'ôte hors installation, mais il reste en clair partout où l'adresse s'affiche" \
       "index nommé ([[tool.uv.index]] name = \"jfrog\") avec UV_INDEX_JFROG_USERNAME et UV_INDEX_JFROG_PASSWORD, ou ~/.netrc"
fi
if command -v uv >/dev/null 2>&1; then
  # Pièges évités ici :
  #   1. `timeout` est GNU et absent de macOS -> on passe par le délai interne d'uv.
  #   2. `--no-cache` force un vrai aller-retour réseau.
  #   3. `uv pip install --dry-run` répond « déjà satisfait » sans toucher au dépôt quand
  #      le paquet est dans .venv. `uv pip compile` résout toujours contre l'index.
  #   4. JAMAIS `-o /dev/null` : uv écrit d'abord un fichier temporaire à côté de la
  #      sortie, et /dev n'est pas inscriptible sur macOS. La version d'origine annonçait
  #      donc « réseau bloqué » sur tous les Mac, même quand tout fonctionnait.
  NET_TMP="$(mktemp -d "${TMPDIR:-/tmp}/doctor.XXXXXX")"
  if printf 'idna\n' | UV_HTTP_TIMEOUT=15 uv pip compile --no-cache - -o "$NET_TMP/req.txt" >"$NET_TMP/log" 2>&1; then
    ok "uv atteint un dépôt de paquets"
  elif grep -qE 'invalid peer certificate|certificate verify failed' "$NET_TMP/log"; then
    # Le dépôt répond, mais uv ne reconnaît pas qui l'a signé : un proxy qui déchiffre le
    # trafic, ou un JFrog signé par l'autorité de l'entreprise. uv garde ses propres racines
    # et ne lit pas le trousseau du poste (mesuré le 26/09/2026 : « UnknownIssuer »).
    # La cause exacte, dite : « UnknownIssuer » appelle l'autorité de l'entreprise, un
    # certificat « Expired » appelle l'équipe qui tient le dépôt, pas un réglage du poste.
    CAUSE="$(grep -oE 'invalid peer certificate: [A-Za-z]+|certificate verify failed[^)]*' "$NET_TMP/log" | head -n 1)"
    warn "uv joint le dépôt de paquets, mais refuse son certificat ($CAUSE : proxy qui déchiffre, ou autorité de l'entreprise)" \
         "export UV_SYSTEM_CERTS=true   (uv lit alors le magasin du poste, où l'IT a posé l'autorité ; .ephor/docs/10, « Certificat de l'entreprise »)"
  else
    # La dernière ligne du journal d'uv est souvent vide, ou un conseil : on prend la
    # dernière qui dit quelque chose.
    warn "uv ne parvient pas à résoudre un paquet depuis le réseau ($(grep -v '^[[:space:]]*$' "$NET_TMP/log" | grep -v '^hint:' | tail -n 1))" \
         "dépôt injoignable ou non configuré — c'est LE point bloquant, à remonter AVANT tout le reste"
  fi
  rm -rf "$NET_TMP"
fi

echo
echo "== Poste : architecture, conteneurs, passerelle de modèles =="
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64) ok "processeur $ARCH (Apple Silicon) — prenez des images de conteneur arm64 ou multi-architecture" ;;
  *) ok "processeur $ARCH" ;;
esac
if command -v podman >/dev/null 2>&1; then
  if podman info >/dev/null 2>&1; then
    ok "podman prêt — le juge peut vérifier dans un conteneur sans réseau (RUNTIME=podman)"
  else
    warn "podman installé mais pas démarré" "podman machine start"
  fi
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "docker prêt — utilisable par le juge (RUNTIME=docker)"
else
  printf '  [ -- ] aucun moteur de conteneur prêt — le juge vérifiera en local, sans bac à sable\n'
fi
# Sous Linux, Podman sans droits root a besoin d'une plage de sous-identifiants pour
# l'utilisateur. Un compte d'annuaire n'en a souvent pas, et alors Podman ne peut même pas
# installer une image ordinaire (« insufficient UIDs or GIDs », mesuré le 25/09/2026 avec un
# uid de 1 234 567 890). Avec une plage, même un uid bien plus grand qu'elle marche. Sur Mac,
# la machine virtuelle de Podman s'en charge : rien à vérifier.
if [ "$(uname -s)" = "Linux" ] && command -v podman >/dev/null 2>&1 && [ "$(id -u)" != "0" ]; then
  MOI="$(id -un)"
  if { command -v getsubids >/dev/null 2>&1 && getsubids "$MOI" >/dev/null 2>&1; } \
     || grep -qE "^($MOI|$(id -u)):" /etc/subuid 2>/dev/null; then
    ok "plage de sous-identifiants pour $MOI (Podman sans droits root)"
  else
    warn "aucune plage de sous-identifiants pour $MOI : Podman sans droits root ne pourra pas installer l'image du juge" \
         "demandez à l'IT une plage dans /etc/subuid et /etc/subgid (ou par l'annuaire), puis : podman system migrate"
  fi
fi
IMAGE_JUGE="${EPHOR_IMAGE:-ghcr.io/astral-sh/uv:python3.12-bookworm-slim}"
if [ -n "${EPHOR_IMAGE:-}" ]; then
  ok "image du juge : $EPHOR_IMAGE"
else
  printf '  [ -- ] image du juge par défaut (ghcr.io/astral-sh/uv) — en entreprise, exportez EPHOR_IMAGE=<image JFrog>\n'
fi
# Le juge télécharge l'image une fois, avant la première étape. En entreprise, le registre est
# privé : mieux vaut apprendre au diagnostic qu'il faut `podman login` que le découvrir en jugeant.
for MOTEUR in podman docker; do
  command -v "$MOTEUR" >/dev/null 2>&1 && "$MOTEUR" info >/dev/null 2>&1 || continue
  if "$MOTEUR" image inspect "$IMAGE_JUGE" >/dev/null 2>&1; then
    ok "image du juge présente dans $MOTEUR"
  else
    REGISTRE="${IMAGE_JUGE%%/*}"
    printf '  [ -- ] image du juge absente de %s — téléchargée au premier jugement ; registre privé : %s login %s\n' \
      "$MOTEUR" "$MOTEUR" "$REGISTRE"
  fi
  break
done
if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
  ok "Claude Code passe par une passerelle ($ANTHROPIC_BASE_URL)"
else
  printf '  [ -- ] Claude Code parle directement à Anthropic (pas de ANTHROPIC_BASE_URL)\n'
fi

echo
echo "== Droits d'exécution =="
NX=$(find .ephor -name '*.sh' ! -perm -u+x 2>/dev/null | wc -l | tr -d ' ')
if [ "${NX:-0}" -eq 0 ]; then
  ok "tous les scripts et hooks sont exécutables"
else
  bad "$NX script(s) sans bit d'exécution — perdu à la décompression" "find .ephor -name '*.sh' -exec chmod +x {} +"
fi
# Les hooks que déclarent les réglages de Claude Code. Un script introuvable est une erreur
# que Claude Code montre, puis il laisse passer l'appel : ce n'est pas un refus. Arrive quand
# les réglages sont plus récents que le moteur (ADR 0011 : ils appellent ses points d'entrée).
ABSENTS="$(python3 -c 'import json, os, re
try:
    with open(".claude/settings.json", encoding="utf-8") as fichier:
        hooks = json.load(fichier).get("hooks") or {}
except (OSError, ValueError, AttributeError):
    hooks = {}
noms = sorted(set(re.findall(r"\.ephor/hooks/([\w.-]+\.sh)", json.dumps(hooks))))
print(" ".join(n for n in noms if not os.access(os.path.join(".ephor", "hooks", n), os.X_OK)))' 2>/dev/null)"
if [ -n "$ABSENTS" ]; then
  bad "hooks déclarés par .claude/settings.json introuvables : $ABSENTS (Claude Code le signale, puis laisse passer l'appel)" \
      "mettez le moteur à jour : bootstrap_existing_repo.sh --mettre-a-jour, depuis le dépôt d'Ephor"
fi
# Aucune garde déclarée : Ephor est posé, et rien ne tourne. Le diagnostic concluait « Rien ne
# bloque » sur un dépôt dont le geste 0 n'était pas fait (audit du 01/10/2026).
if ! grep -q '\.ephor/hooks/' .claude/settings.json 2>/dev/null; then
  bad "aucun garde-fou d'Ephor n'est déclaré dans .claude/settings.json : Ephor est posé, et INERTE" \
      "geste 0 : fusionnez les blocs « hooks » et « permissions » de .claude/settings.json.ephor-new dans le vôtre, puis make adapters"
fi

echo
echo "== Zones de contrôle =="
if [ -f .ephor/zones.txt ]; then
  ok "liste des zones du moteur ($(grep -cvE '^[[:space:]]*(#|$)' .ephor/zones.txt) entrées, .ephor/zones.txt)"
else
  bad "liste des zones de contrôle absente (.ephor/zones.txt) : le juge et les gardes refusent tout" \
      "réinstallez le moteur : bootstrap_existing_repo.sh --mettre-a-jour, depuis le dépôt d'Ephor"
fi
# GNU make lit GNUmakefile puis makefile AVANT Makefile : l'un ou l'autre détourne `make judge`
# (mesuré le 25/09/2026 : faux « VÉRIFIÉ »). `find -name` compare le nom exact, même sur un
# disque insensible à la casse, où `[ -f makefile ]` trouverait Makefile.
DETOUR="$(find . -maxdepth 1 \( -name GNUmakefile -o -name makefile \) 2>/dev/null | head -1)"
if [ -n "$DETOUR" ]; then
  bad "${DETOUR#./} présent : make le lit avant Makefile, et « make judge » ne lance plus le juge" \
      "lisez-le (git log -- ${DETOUR#./}), puis retirez-le : git rm ${DETOUR#./}"
fi
for LOCAL_R in .claude/settings.local.json "$HOME/.claude/settings.json"; do
  [ -f "$LOCAL_R" ] || continue
  COUPE="$(grep -oE '"(disableAllHooks|enableAllProjectMcpServers)"[[:space:]]*:[[:space:]]*true|"defaultMode"[[:space:]]*:[[:space:]]*"bypassPermissions"' "$LOCAL_R" 2>/dev/null | cut -d'"' -f2 | tr '\n' ' ')"
  if [ -n "$COUPE" ]; then
    bad "$LOCAL_R coupe des garde-fous : $COUPE" "retirez ces réglages : sous Claude Code, ils désarment les hooks ou les permissions d'Ephor"
  fi
done
# Le bac à sable de Claude Code : la seule frontière mesurée pour le terminal (ADR 0008).
# Les réglages locaux passent devant ceux du projet : c'est là qu'un poste le coupe.
BAC="$(python3 -c 'import json, sys
def lu(chemin):
    try:
        with open(chemin, encoding="utf-8") as fichier:
            return (json.load(fichier).get("sandbox") or {}).get("enabled")
    except Exception:
        return None
local, projet = lu(".claude/settings.local.json"), lu(".claude/settings.json")
print("coupe" if local is False else "actif" if (local or projet) else "absent")' 2>/dev/null)"
# Sous Linux, le bac à sable repose sur bubblewrap et socat. S'il ne peut pas démarrer
# (paquet absent, espaces de noms refusés par AppArmor sur Ubuntu 24.04, conteneur sans
# droits, WSL1), Claude Code l'annonce d'un avertissement et lance le terminal SANS lui.
# Le programme présent ne suffit donc pas : on le lance. Mesuré le 26/09/2026 : cet essai
# passe sur une machine Fedora, et échoue dans un conteneur sans droits (« Can't mount
# devpts on /newroot/dev/pts: Permission denied »).
SAUTE=""
if [ "$BAC" = actif ] && [ "$(uname -s)" = "Linux" ]; then
  MANQUE=""
  command -v bwrap >/dev/null 2>&1 || MANQUE="bubblewrap"
  command -v socat >/dev/null 2>&1 || MANQUE="${MANQUE:+$MANQUE et }socat"
  if [ -n "$MANQUE" ]; then
    SAUTE="il manque $MANQUE"
    REMEDE="sudo apt-get install bubblewrap socat (dnf sous Fedora), puis relancez Claude Code"
  elif ! ESSAI="$(bwrap --ro-bind / / --dev /dev --proc /proc --unshare-net \
                    --new-session --die-with-parent true 2>&1)"; then
    SAUTE="bubblewrap ne démarre pas : $(printf '%s\n' "$ESSAI" | tail -n 1)"
    REMEDE="Ubuntu 24.04 : profil AppArmor pour bwrap ; dans un conteneur : sandbox.enableWeakerNestedSandbox ; WSL1 : passez en WSL2 (doc « sandboxing » de Claude Code)"
  fi
fi
[ -n "$SAUTE" ] && BAC=saute
case "$BAC" in
  actif) ok "bac à sable de Claude Code : le terminal ne peut pas écrire les zones, détour compris (ADR 0008)" ;;
  saute) warn "bac à sable de Claude Code réglé, mais SAUTÉ sur ce poste ($SAUTE) : Claude Code avertit, puis lance le terminal sans frontière" \
              "$REMEDE. Pour qu'il refuse de démarrer plutôt que de le sauter : \"failIfUnavailable\": true dans le bloc sandbox (ADR 0008)" ;;
  coupe) warn "bac à sable de Claude Code coupé sur ce poste (.claude/settings.local.json)" \
              "le terminal peut de nouveau écrire les zones par un détour ; retirez ce réglage (ADR 0008)" ;;
  absent) warn "bac à sable de Claude Code absent des réglages : le terminal n'a pas de frontière" \
               "reprenez le bloc « sandbox » de .claude/settings.json.ephor-new (ADR 0008)" ;;
esac
# La limite, dite là où l'opérateur regarde : un filet, pas une frontière.
printf '  [ -- ] terminal : la garde attrape l'"'"'écriture franche et les formes simples (cd, heredoc, find,\n'
printf '         git apply, formateurs, curl, tar, blocs, plusieurs lignes), pas un détour (script écrit ailleurs,\n'
printf '         variable calculée). La sentinelle\n'
printf '         le signale en fin de tour, le juge refuse le correctif — ce n'"'"'est pas une frontière.\n'
printf '         La frontière, sous Claude Code, c'"'"'est le bac à sable ; opencode peut l'"'"'avoir par srt (.ephor/docs/11), Copilot non.\n'

echo
echo "== Rôles des sous-agents =="
# L'évaluateur relit chaque tâche : c'est le seul regard sur ce que les critères ne mesurent
# pas. Réglé sous le niveau fort (son en-tête, `model:`), sa relecture ne vaut plus celle d'un
# modèle fort (#30). Un choix que le projet peut faire, mais qui doit se voir.
if [ -f .claude/agents/evaluator.md ]; then
  EVAL_MODELE="$(awk 'NR == 1 && $0 != "---" { exit } NR > 1 && $0 == "---" { exit } /^model:/ { sub(/^model:[[:space:]]*/, ""); print; exit }' .claude/agents/evaluator.md)"
  EVAL_EFFORT="$(awk 'NR == 1 && $0 != "---" { exit } NR > 1 && $0 == "---" { exit } /^effort:/ { sub(/^effort:[[:space:]]*/, ""); print; exit }' .claude/agents/evaluator.md)"
  case "$EVAL_MODELE" in
    *opus* | "") ok "évaluateur : modèle fort, effort ${EVAL_EFFORT:-celui de la session} (.claude/agents/evaluator.md)" ;;
    *) warn "évaluateur réglé sur « $EVAL_MODELE » : sa relecture ne vaut plus celle d'un modèle fort" \
            "remettez model: opus dans .claude/agents/evaluator.md, sauf choix assumé par l'équipe ; puis make adapters" ;;
  esac
fi

echo
echo "== Mise à jour d'Ephor =="
# Un .ephor-new, c'est une version d'Ephor posée à côté d'un fichier que la mise à jour n'a pas
# pu remplacer : tant qu'il reste, c'est l'ANCIENNE version qui tourne. Mesuré le 29/09/2026 :
# un projet « en 1.1.5 » chargeait le greffon d'opencode de la 1.1.3, et rien ne le disait.
empreinte() { # empreinte <fichier> : le sha256 de son contenu
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
  fi
}
EN_ATTENTE="$(find . \( -name .git -o -name node_modules -o -name .venv -o -name .verify \) -prune \
  -o -type f -name '*.ephor-new' -print 2>/dev/null | sed 's|^\./||' | LC_ALL=C sort)"
if [ -n "$EN_ATTENTE" ]; then
  N_ATTENTE="$(printf '%s\n' "$EN_ATTENTE" | wc -l | tr -d ' ')"
  warn "$N_ATTENTE fichier(s) .ephor-new en attente : tant qu'ils restent, c'est l'ANCIENNE version qui tourne ($(printf '%s\n' "$EN_ATTENTE" | head -n 5 | sed 's/\.ephor-new$//' | tr '\n' ' ' | sed 's/ $//'))" \
       "pour chacun : diff <fichier> <fichier>.ephor-new, reprenez ce qui manque (ou mv <fichier>.ephor-new <fichier>), puis make adapters"
else
  ok "aucun .ephor-new en attente"
fi
if [ -f .opencode/plugins/ephor.js ] && [ -f .ephor/empreintes.txt ]; then
  if grep -qxF "$(empreinte .opencode/plugins/ephor.js) .opencode/plugins/ephor.js actuelle" .ephor/empreintes.txt; then
    ok "greffon opencode à la version du moteur ($(sed -n '1s/ —.*//p' .ephor/VERSION 2>/dev/null))"
  else
    warn "greffon opencode (.opencode/plugins/ephor.js) différent de celui de cette version d'Ephor : opencode tourne avec un autre" \
         "s'il n'a pas été retouché exprès : relancez bootstrap_existing_repo.sh --mettre-a-jour depuis cette version (ou reprenez son .ephor-new), puis relancez opencode"
  fi
fi

echo
echo "== opencode sur ce poste =="
# Un sous-agent sans modèle prend celui de l'agent qui l'appelle (doc d'opencode) : sans
# réglage sur le poste, scout comme planner tournent sur le modèle de la session, à son
# niveau d'effort. Les adaptateurs ne recopient aucun modèle, exprès (audit du 29/09/2026).
if ! command -v opencode >/dev/null 2>&1; then
  printf '  [ -- ] opencode absent : rien à régler\n'
else
  CONF_OC="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
  # Lu d'abord, cherché ensuite, sans tuyau : sous pipefail, un des trois fichiers absent
  # faisait échouer le `cat`, donc la recherche, même trouvée.
  TEXTE_OC="$(cat "$CONF_OC/opencode.json" "$CONF_OC/opencode.jsonc" "$CONF_OC/config.json" 2>/dev/null | tr -d '\n')"
  SANS_MODELE=""
  for role in scout planner evaluator; do
    if grep -qE "\"$role\"[[:space:]]*:[[:space:]]*[{][^{}]*\"model\"[[:space:]]*:" <<< "$TEXTE_OC"; then
      continue
    fi
    grep -qE '^model:' "$CONF_OC/agents/$role.md" "$CONF_OC/agent/$role.md" 2>/dev/null && continue
    SANS_MODELE="${SANS_MODELE:+$SANS_MODELE, }$role"
  done
  if [ -z "$SANS_MODELE" ]; then
    ok "opencode : un modèle réglé pour scout, planner et evaluator"
  else
    warn "opencode : aucun modèle réglé sur ce poste pour $SANS_MODELE — ils tournent sur le modèle de la session" \
         "make opencode-modeles FORT=<fournisseur/modèle> RAPIDE=<…> [STANDARD=<…>], une fois par poste (.ephor/docs/07-token-budget.md, 2.6)"
  fi
  # La frontière du terminal que Claude Code a déjà : srt, le moteur de son bac à sable.
  if command -v srt >/dev/null 2>&1; then
    ok "opencode peut tourner dans le bac à sable du système : bash .ephor/scripts/opencode_bac_a_sable.sh"
  else
    printf '  [ -- ] srt absent : sous opencode, rien ne tient un détour par le terminal (npm install -g @anthropic-ai/sandbox-runtime, puis bash .ephor/scripts/opencode_bac_a_sable.sh ; .ephor/docs/11)\n'
  fi
  if [ "${EPHOR_GARDES_EN_SERIE:-0}" = "1" ]; then
    printf '  [ -- ] gardes lancées l'"'"'une après l'"'"'autre (EPHOR_GARDES_EN_SERIE=1) : même verdict, deux fois plus lent\n'
  fi
  if [ -n "${OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX:-}" ]; then
    warn "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=$OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX sur ce poste : le greffon garde ce plafond de réponse" \
         "au plus haut effort, la réflexion du planner peut le remplir et la réponse sortir vide ; si ce n'est pas voulu : unset OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX (.ephor/docs/11)"
  fi
fi

echo
echo "== Dépôts de référence =="
# Une déclaration cassée (le plus souvent : une variable d'environnement pas exportée sur
# ce poste) fait refuser toute écriture hors du dépôt, et `make refs` échoue. Vu le
# 24/09/2026 : personne ne l'avait remarqué avant que l'agent se heurte au refus.
if [ ! -f .ephor/projet/reference-repos.json ]; then
  ok "aucun dépôt de référence déclaré"
elif ! command -v python3 >/dev/null 2>&1; then
  warn "dépôts de référence déclarés, mais python3 absent pour lire la déclaration" "installez python3"
elif ERREUR="$(python3 .ephor/refs.py --repo . chemins 2>&1 >/dev/null)"; then
  ok "déclaration des dépôts de référence lisible (make refs : leur état)"
else
  bad "déclaration des dépôts de référence inutilisable : ${ERREUR#erreur : }" \
      "corrigez .ephor/projet/reference-repos.json, ou exportez la variable qu'il cite ; d'ici là, toute écriture hors du dépôt est refusée"
fi

echo
echo "== Garde-fous sous Copilot (poste, hors dépôt) =="
# Copilot ne lance les hooks d'un dossier de travail que dans sa vue « agents » : seul un
# greffon installé sur la machine tourne partout. Rien ne le dit quand il manque — c'est
# l'angle mort le plus coûteux d'Ephor, alors il est nommé ici.
GREFFON="${COPILOT_PLUGINS:-$HOME/.copilot/installed-plugins}/ephor"
if [ ! -d "$GREFFON" ]; then
  printf '  [ -- ] greffon Copilot non installé sur ce poste\n         → si vous utilisez Copilot : make copilot-install, puis une FENÊTRE VS Code neuve\n         → tant qu'"'"'il manque, VS Code ne demande RIEN avant d'"'"'écrire une zone de contrôle (choix assumé : .ephor/docs/11) ; hors de la vue « agents », seuls le juge et la CI refusent encore\n'
elif ! cmp -s .ephor/greffon-copilot/hooks/relais.sh "$GREFFON/hooks/relais.sh"; then
  # Testé AVANT la déclaration : un relais d'avant la 0.9 ne connaît que l'ancienne
  # adresse des garde-fous. Dans un dépôt non déclaré, il ne les reconnaît donc pas, et
  # laisse passer au lieu de refuser — l'avertissement ci-dessous mentait alors.
  bad "le greffon installé sur ce poste ne correspond plus à celui du dépôt" \
      "make copilot-install, puis une FENÊTRE VS Code neuve (le relais a changé depuis la dernière installation)"
elif ! sed "s|__RACINE_DU_GREFFON__|$GREFFON|g" .ephor/greffon-copilot/hooks/hooks.json 2>/dev/null \
     | cmp -s - "$GREFFON/hooks/hooks.json"; then
  # Le relais peut être à jour et la liste des gardes qu'il enchaîne, non : une garde
  # ajoutée depuis l'installation ne tournait pas sous Copilot, et rien ne le disait.
  bad "le greffon installé n'enchaîne pas la liste de garde-fous du dépôt (hooks.json)" \
      "make copilot-install, puis une FENÊTRE VS Code neuve"
elif ! grep -qxF "$PWD_REAL" "$GREFFON/depots.txt" 2>/dev/null; then
  # Un avertissement, pas un blocage : le relais REFUSE tout appel d'outil dans un dépôt
  # non déclaré qui porte des garde-fous, au lieu de se taire. Rien ne passe en silence.
  # Et un poste qui n'utilise pas Copilot ne doit pas voir un [FAIL] pour autant.
  warn "greffon Copilot installé, mais ce dépôt n'y est pas déclaré" \
       "si vous utilisez Copilot ici : make copilot-install, puis une FENÊTRE VS Code neuve (sinon Copilot refusera chaque appel d'outil dans ce dépôt)"
else
  ok "greffon Copilot installé et ce dépôt déclaré (fenêtre VS Code neuve requise après toute réinstallation)"
fi

echo
echo "== Outils optionnels =="
for t in rg fd shellcheck gitleaks glab opencode; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t"; else printf '  [ -- ] %s absent (confort, non bloquant)\n' "$t"; fi
done

echo
if [ $FAIL -eq 0 ]; then
  echo "  Rien ne bloque. Lancez : make verify"
else
  echo "  Corrigez les [FAIL] ci-dessus dans l'ordre, puis relancez : make doctor"
fi
# Un diagnostic qui a trouvé un blocage ne sort pas en succès : sinon `make doctor` est
# vert à l'écran comme au code de retour, et tout ce qui l'enchaîne croit la voie libre.
exit $FAIL
