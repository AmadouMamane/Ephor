#!/usr/bin/env bash
# PreToolUse (commandes du terminal) — ne pas écrire par le terminal ce que les réglages
# refusent d'écrire par les outils d'édition.
#
# Mesuré le 24/09/2026 dans la vue « agents » de VS Code : le garde-fou refuse d'écrire
# .vscode/settings.json par `apply_patch`, l'agent l'annonce… puis propose un `python3 -c`
# qui réécrit le même fichier. Plus rien ne l'arrêtait, sauf le clic de l'opérateur — et un
# clic distrait, c'est exactement ce que les garde-fous doivent couvrir.
#
# Ce n'est PAS une frontière. Un shell peut toujours écrire n'importe où par un détour
# qu'aucune analyse ne voit : une variable, du base64, un script écrit ailleurs puis lancé.
# Cette garde attrape le cas FRANC — une redirection, `tee`, `sed -i`, `cp`, `mv`, `rm`,
# `git restore`, un script python, node ou perl en ligne qui écrit — et, depuis le 25/09/2026,
# les formes simples qui passaient encore : un `cd` avant l'écriture, un script donné en
# document en ligne (`python3 - <<EOF`), `find -delete`, `git apply` et `patch` (le
# correctif est lu, même à noms entre guillemets), un formateur qui réécrit en place
# (`uv run ruff check --fix`), et ce que le réseau ou une archive déposent (`curl -o`,
# `wget -O`, `tar -C`, `unzip -d`, `cp -t`) ; depuis le 27/09/2026, `git config` et
# `git remote` qui écrivent .git/config (un `core.hooksPath` ou un `core.fsmonitor` y fait
# lancer par git le code de l'agent, au prochain commit de l'opérateur). Les
# zones sont celles du moteur (.ephor/zones.txt), plus celles que les réglages ajoutent. La
# sentinelle signale le détour en fin de tour ; le juge, qui refuse tout correctif touchant
# ces zones, reste la barrière. `make doctor` le dit à l'opérateur.
#
# Depuis le 29/09/2026, aussi : écrire l'histoire de la branche principale (`git commit`,
# `merge`, `cherry-pick`…). Le commit d'un contrat sur cette branche EST l'approbation, et le
# juge la lit là : un agent qui y commitait un contrat glissé dans tasks/ par un détour
# s'approuvait lui-même, en local. L'agent ne commite que sur ses branches de tâche.
set -uo pipefail
# shellcheck source=/dev/null  # résolu à l'exécution, chemin relatif au hook
source "$(dirname "$0")/_lib.sh" || { echo "REFUSÉ : la bibliothèque des garde-fous (.ephor/hooks/_lib.sh) ne s'est pas chargée, donc cette garde ne peut rien juger. Elle refuse. Vérifie le dépôt : fichier manquant, droits, ou installation à moitié faite." >&2; exit 2; }
moteur_ok || deny "REFUSÉ : cette garde doit lire l'appel d'outil pour le juger, et ce poste n'a ni jq ni python3. Elle refuse plutôt que de laisser passer sans rien voir. Installe l'un des deux (python3 suffit), puis recommence."
ROOT="$(racine)"
REGLAGES="$ROOT/.claude/settings.json"

# Les terminaux des trois outils, et rien d'autre. Un nom absent n'est pas un terminal :
# Claude Code ne lance ce hook que sur `Bash`, et le nomme toujours.
OUTIL="$(outil)"
case "$OUTIL" in
  Bash|bash|shell|powershell|run_in_terminal|runInTerminal|*/run_in_terminal|*/runInTerminal) ;;
  *) exit 0 ;;
esac

COMMANDE="$(commande)"
[ -n "$COMMANDE" ] || exit 0
# Les zones du moteur, plus celles que les réglages du projet ajoutent (voir _lib.sh).
[ -f "$ZONES_MOTEUR" ] || deny "REFUSÉ : la liste des zones de contrôle ($ZONES_MOTEUR) est introuvable, donc cette garde ne sait pas ce qu'elle protège. Elle refuse. Installation à moitié faite : préviens l'opérateur."
MOTIFS="$(motifs_zones "$ROOT" "$REGLAGES")"
EXCEPTIONS="$(_entrees_zones exceptions)"
# La branche courante, et les principales : main, master, et celle que le dépôt distant
# désigne. Git durci (voir _lib.sh).
BRANCHE="$(git -C "$ROOT" branch --show-current 2>/dev/null || true)"
DISTANTE="$(git -C "$ROOT" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
PRINCIPALES="main master ${DISTANTE#*/}"
# Ce que les réglages refusent à la LECTURE (`Read(...)`) : secrets, clés, identifiants.
LECTURES="$(refus "$REGLAGES" Read 2>/dev/null || true)"

# Sans python3, pas d'analyse de la commande. Une commande qui ne nomme aucune zone de
# contrôle passe ; une qui en nomme une est refusée, faute de pouvoir dire si elle y écrit.
if ! command -v python3 >/dev/null 2>&1; then
  while IFS= read -r MOTIF; do
    LITTERAL="${MOTIF%%[*?[]*}"; LITTERAL="${LITTERAL#./}"
    [ -n "$LITTERAL" ] || continue
    case "$COMMANDE" in
      *"$LITTERAL"*) deny "REFUSÉ : cette commande nomme « $LITTERAL », zone de contrôle, et ce poste n'a pas python3 pour vérifier si elle y écrit. Elle est refusée. Si c'est une lecture, passe par l'outil de lecture ; sinon, c'est l'opérateur qui le fait." ;;
    esac
  done <<EOF
$MOTIFS
EOF
  exit 0
fi

# Le programme est lu d'abord, à part : bash 3.2 (celui de macOS) ne sait pas lire un
# document en ligne à l'intérieur d'un $(…) quand il contient des apostrophes.
read -r -d '' ANALYSE <<'PY' || true
import fnmatch
import os
import re
import shlex
import sys
import urllib.parse

racine, maison = sys.argv[1], sys.argv[2]
commande = os.environ["EPHOR_COMMANDE"]
# La branche où l'on est, suivie à travers les `git switch` de la commande : `git switch main
# && git commit` commite sur main alors que la garde voit la branche de tâche.
BRANCHE = {"courante": os.environ.get("EPHOR_BRANCHE", "")}
PRINCIPALES = set(os.environ.get("EPHOR_PRINCIPALES", "").split())
# Ce qui écrit l'histoire de la branche courante.
HISTOIRE = {"commit", "merge", "cherry-pick", "revert", "am", "rebase", "pull"}
PRINCIPALE = "::branche principale::"
# Les gestes de l'opérateur. Les règles de permissions ne les refusaient que dans un ordre de
# mots : `make ID=X task-check` passait par la règle générale `make *` (audit du 29/09/2026).
OPERATEUR = "::geste de l'opérateur::"
CIBLES_OPERATEUR = {
    "task-new",
    "task-check",
    "adapters",
    "fetch-docs",
    "copilot-install",
    "checkpoints-purge",
    "task-worktree",
    "task-approve",
    "task-ranger",
    "opencode-modeles",
}
SCRIPTS_OPERATEUR = {
    "judge.py": {"new", "rouge", "approuve", "ranger"},
    "adapters.py": {"write", "modeles"},
    "fetch_docs.py": None,  # toute invocation
    "install_copilot_plugin.sh": None,
    "bootstrap_existing_repo.sh": None,
    "worktree.sh": None,
}
# Ce qui lit un fichier et en montre le contenu : un secret lu entre dans le prompt.
SECRET = "::lecture d'un secret::"
# Les variables qui disent qu'un agent tourne : les gestes de l'opérateur les lisent pour se
# refuser à lui (.ephor/operateur.py). Les retirer, c'est les contourner.
AGENT = "::variable d'agent::"
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ.get("EPHOR_MOTEUR", ""))
try:
    from operateur import VARIABLES as AGENTS
except ImportError:
    AGENTS = ()
LECTEURS = {
    "cat", "less", "more", "head", "tail", "bat", "batcat", "nl", "tac", "rev", "strings",
    "xxd", "od", "hexdump", "base64", "rg", "grep", "egrep", "fgrep", "ag", "ack", "jq", "yq",
    "awk", "gawk", "sed", "cut", "sort", "uniq", "paste", "column", "diff", "source", ".",
}
# Les refus `Read(...)` des réglages, chacun rendu comparable à un chemin absolu.
LECTURES = [m.strip() for m in os.environ.get("EPHOR_LECTURES", "").splitlines() if m.strip()]


def lecture_refusee(absolu):
    """Le motif `Read(...)` des réglages que vise ce chemin absolu, ou None."""
    relatif = os.path.relpath(absolu, racine) if absolu.startswith(racine + os.sep) else None
    for motif in LECTURES:
        if motif.startswith("~/"):
            vise, forme = absolu, os.path.join(maison, motif[2:])
        elif motif.startswith("//"):
            vise, forme = absolu, motif[1:]
        elif relatif is None:
            continue
        else:
            vise, forme = relatif, motif[2:] if motif.startswith("./") else motif
        vise, forme = vise.lower(), forme.lower()
        # `**/x` vaut aussi à la racine ; `*` traverse les dossiers, comme chez fnmatch.
        if fnmatch.fnmatchcase(vise, forme) or (
            forme.startswith("**/") and fnmatch.fnmatchcase(vise, forme[3:])
        ):
            return motif
        if forme.endswith("/**") and vise == forme[:-3]:
            return motif
    return None
# Les zones, déjà en motifs (voir motifs_zones dans _lib.sh) : relatifs au dépôt, ou absolus
# pour le poste et le dossier git. Les exceptions (la mémoire de l'agent) passent.
motifs = [m.strip() for m in os.environ["EPHOR_MOTIFS"].splitlines() if m.strip()]
exceptions = [
    e.strip() + ("*" if e.strip().endswith("/") else "")
    for e in os.environ.get("EPHOR_EXCEPTIONS", "").splitlines()
    if e.strip()
]
# Ce qu'une zone donne à voir dans un texte : ce qui précède son premier joker, sans la barre
# finale. Un chemin du poste se lit aussi sous sa forme « ~/ » ou « $HOME/ ».
LITTERAUX = set()
for m in motifs:
    litteral = re.split(r"[*?\[]", m, maxsplit=1)[0].rstrip("/")
    if not litteral:
        continue
    LITTERAUX.add(litteral)
    if litteral.startswith(maison + "/"):
        reste = litteral[len(maison) + 1 :]
        LITTERAUX.update({"~/" + reste, "$HOME/" + reste, "${HOME}/" + reste})
LITTERAUX = sorted(LITTERAUX, key=len, reverse=True)

SEPARATEURS = {";", "&&", "||", "|", "&", "|&", "(", ")", "\n", ";;"}
# `>&` suivi d'un NOM de fichier écrit sortie et erreurs dedans (`ls >& x`) ; `>&2` ne
# vise qu'un descripteur, que la zone ne reconnaît pas.
REDIRIGE = {">", ">>", ">|", "&>", "&>>", ">&"}
TOUS_ECRITS = {"rm", "rmdir", "unlink", "truncate", "touch", "chmod", "chown", "shred", "tee"}
DERNIER_ECRIT = {"cp", "install", "ln", "rsync"}
INTERPRETES = {"python", "python3", "node", "nodejs", "bun", "deno", "perl", "ruby"}
COQUILLES = {"bash", "sh", "zsh", "dash", "ksh"}
PREFIXES = {"sudo", "env", "command", "exec", "nohup", "time", "xargs"}
# Ce qui lance un autre programme sans rien changer à ce qu'il écrit : `uv run ruff`, `npx x`.
LANCEURS = {"uv": "run", "poetry": "run", "pipx": "run", "pdm": "run", "hatch": "run", "rye": "run", "pnpm": "exec"}
LANCEURS_SEULS = {"uvx", "npx", "bunx", "pnpx"}
# Un script en ligne qui écrit, déplace ou supprime un fichier.
ECRIT = re.compile(
    r"write_text|write_bytes|\.write\(|open\([^)]*['\"][rwxa]?[wxa+]|writeFile|appendFile|"
    r"createWriteStream|os\.remove|os\.unlink|os\.rename|os\.replace|shutil\.|\.unlink\(|"
    r"\.rename\(|\.touch\(|unlinkSync|renameSync|rmSync|copyFile|['\"]\+?>{1,2}"
)
# `python3 - <<EOF … EOF` : la forme la plus courante chez un agent pour écrire un script.
HEREDOC = re.compile(
    r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S
)


def resolu(cible):
    cible = cible.replace("${HOME}", maison).replace("$HOME", maison)
    if cible.startswith("~/"):
        cible = os.path.join(maison, cible[2:])
    return os.path.normpath(os.path.join(racine, cible))


def zone(cible):
    """Le motif de zone de contrôle que vise cette cible, ou None."""
    if not cible or cible.startswith("/dev/") or cible.startswith("&"):
        return None
    absolu = resolu(cible)
    if any(fnmatch.fnmatchcase(absolu.lower(), e.lower()) for e in exceptions):
        return None
    relatif = os.path.relpath(absolu, racine) if absolu.startswith(racine + os.sep) else None
    for motif in motifs:
        vise = absolu if motif.startswith("/") else relatif
        if vise is None:
            continue
        if fnmatch.fnmatchcase(vise.lower(), motif.lower()):
            return motif
        # `a/**` couvre aussi le dossier `a` lui-même : `rm -rf .ephor` le détruit.
        if motif.endswith("/**") and vise.lower() == motif[:-3].lower():
            return motif
    # Une variable qu'on ne sait pas résoudre (`"$ROOT"/.ephor/x`) : on lit le texte.
    if "$" in cible:
        for litteral in LITTERAUX:
            if "/" + litteral in cible or cible.startswith(litteral):
                return litteral
    return None


def valeurs(args, courtes, longues):
    """Les valeurs d'une option : `-o x`, `-sLo x`, `-ox`, `--output x`, `--output=x`."""
    trouvees = []
    for j, a in enumerate(args):
        suivant = args[j + 1] if j + 1 < len(args) else None
        if a.startswith("--"):
            nom_option, egal, valeur = a.partition("=")
            if nom_option in longues and (egal or suivant is not None):
                trouvees.append(valeur if egal else suivant)
        elif a.startswith("-") and len(a) > 1:
            for k, lettre in enumerate(a[1:], 1):
                if lettre in courtes:
                    if a[k + 1 :] or suivant is not None:
                        trouvees.append(a[k + 1 :] or suivant)
                    break
    return trouvees


def tar_extrait(args):
    """`tar -xzf a.tgz`, `tar xf a.tar`, `tar --extract` : une extraction, qui écrit."""
    if {"--extract", "--get"} & set(args):
        return True
    if args and not args[0].startswith("-") and "x" in args[0]:
        return True
    return any(a.startswith("-") and not a.startswith("--") and "x" in a[1:] for a in args)


def scripts_en_ligne(mots):
    """Les textes passés à un interprète par -c / -e, avec leurs options groupées."""
    textes = []
    for i, mot in enumerate(mots[:-1]):
        if mot.startswith("-") and not mot.startswith("--") and ("c" in mot or "e" in mot):
            textes.append(mots[i + 1])
    return textes


def script_qui_ecrit(script, pourquoi="script en ligne qui écrit"):
    """Les zones qu'un script d'interprète nomme, s'il écrit."""
    if ECRIT.search(script):
        for litteral in LITTERAUX:
            if litteral in script:
                yield litteral, pourquoi


def sans_guillemets(brut):
    """`"b/\\056ephor/x"` -> `b/.ephor/x` : `git apply` lit ainsi N'IMPORTE QUEL nom, octal
    compris (même lecture que _sans_guillemets dans _lib.sh)."""
    if len(brut) < 2 or brut[0] != '"' or brut[-1] != '"':
        return brut
    corps, octets, i = brut[1:-1], bytearray(), 0
    echappes = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, '"': 34, "\\": 92}
    while i < len(corps):
        if corps[i] == "\\" and re.match(r"[0-7]{1,3}", corps[i + 1 : i + 4]):
            chiffres = re.match(r"[0-7]{1,3}", corps[i + 1 : i + 4]).group(0)
            octets.append(int(chiffres, 8) & 0xFF)
            i += 1 + len(chiffres)
        elif corps[i] == "\\" and corps[i + 1 : i + 2] in echappes:
            octets.append(echappes[corps[i + 1]])
            i += 2
        else:
            octets.extend(corps[i].encode("utf-8"))
            i += 1
    return octets.decode("utf-8", errors="replace")


def noms_du_correctif(texte):
    """Les fichiers qu'un correctif nomme, dans les deux formats (même lecture que _lib.sh)."""
    def nom(brut):
        brut = sans_guillemets(brut.split("\t", 1)[0].rstrip())
        if brut[:2] in ("a/", "b/"):
            brut = brut[2:]
        return [brut] if brut and brut != "/dev/null" else []
    noms, v4a, precedente = [], False, ""
    for ligne in texte.splitlines():
        if ligne.startswith("*** Begin Patch"):
            v4a = True
        trouve = re.match(r"^\*\*\* (?:Add|Update|Delete) File: (.*)$|^\*\*\* Move to:\s*(.*)$", ligne)
        if trouve:
            noms += nom(trouve.group(1) or trouve.group(2) or "")
        if not v4a:
            if ligne.startswith("+++ ") and precedente.startswith("--- "):
                noms += nom(precedente[4:]) + nom(ligne[4:])
            git_diff = re.match(r"^diff --git a/(.*?) b/(.*)$", ligne)
            if git_diff:
                noms += nom(git_diff.group(1)) + nom(git_diff.group(2))
            # Entre guillemets, l'en-tête est le seul à nommer le fichier d'un correctif
            # binaire ou d'un changement de droits.
            cite = re.match(r'^diff --git ("(?:[^"\\]|\\.)*")', ligne)
            if cite:
                noms += nom(cite.group(1))
            renomme = re.match(r"^(?:rename|copy) (?:from|to) (.*)$", ligne)
            if renomme:
                noms += nom(renomme.group(1))
        precedente = ligne
    return noms


def correctif_lu(chemin, base):
    """Le texte d'un fichier de correctif, s'il est lisible et raisonnable ; sinon None."""
    try:
        complet = os.path.join(base or racine, chemin)
        if os.path.getsize(complet) > 4_000_000:
            return None
        with open(complet, encoding="utf-8", errors="replace") as fichier:
            return fichier.read()
    except OSError:
        return None


CONFIG_ECRIT = {"--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section", "-e", "--edit"}
CONFIG_LIT = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "--get-color", "--get-colorbool", "--blob"}
CONFIG_VALEUR = {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value"}


def git_config(args):
    """Le fichier qu'un `git config` ÉCRIT, ou rien pour une lecture. Par défaut .git/config,
    où un hook ou un moniteur de fichiers se plante : git les lance ensuite lui-même."""
    cible, positionnels, ecrit, lit, j = os.path.join(racine, ".git", "config"), [], False, False, 0
    while j < len(args):
        a = args[j]
        nom_option = a.split("=", 1)[0]
        if nom_option in {"--global", "--system"}:
            return None  # hors du dépôt : ni zone, ni affaire de cette garde
        if nom_option == "--worktree":
            cible = os.path.join(racine, ".git", "config.worktree")
        ecrit = ecrit or nom_option in CONFIG_ECRIT
        lit = lit or nom_option in CONFIG_LIT
        if nom_option in CONFIG_VALEUR and "=" not in a:
            if a in {"-f", "--file"} and j + 1 < len(args):
                cible = args[j + 1]
            j += 2
            continue
        if nom_option in {"-f", "--file"}:
            cible = a.split("=", 1)[1]
        elif not a.startswith("-"):
            positionnels.append(a)
        j += 1
    # Depuis git 2.46, une sous-commande : `git config set x y`, `git config get x`.
    sous = positionnels[0] if positionnels else ""
    if sous in {"set", "unset", "rename-section", "remove-section", "edit"}:
        return cible
    if sous in {"get", "list"} or lit:
        return None
    return cible if ecrit or len(positionnels) >= 2 else None


def git_sous_commande(args):
    """(sous-commande, ses arguments), après les options globales : `git -C x commit`."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"} else 1
    return (args[i], args[i + 1 :]) if i < len(args) else ("", [])


def branche_apres(sous, args):
    """La branche où mène `git switch` ou `git checkout`, sinon None (elle ne change pas)."""
    if sous == "switch":
        creees = valeurs(args, "cC", {"--create", "--force-create"})
    elif sous == "checkout":
        creees = valeurs(args, "bB", set())
    else:
        return None
    if creees:
        return creees[0]
    positionnels = []
    for a in args:
        if a == "--":
            break
        if not a.startswith("-"):
            positionnels.append(a)
    if not positionnels:
        return None
    # `git checkout fichier` restaure un fichier : seul un nom de branche principale compte.
    if sous == "checkout" and positionnels[0] not in PRINCIPALES:
        return None
    return positionnels[0]


def principale_visee(nom):
    """La branche principale que nomme ce texte (`main`, `refs/heads/main`), ou None."""
    nom = nom.lstrip("+")
    nom = nom[len("refs/heads/") :] if nom.startswith("refs/heads/") else nom
    return nom if nom in PRINCIPALES else None


def principale_deplacee(sous, args):
    """La branche principale qu'une commande git crée, déplace, renomme ou supprime, sans
    être un commit : `switch -C main x`, `checkout -B main`, `branch -f main x`,
    `branch -M main`, `update-ref refs/heads/main x`, `push . x:main`, `fetch . x:main`,
    `reset <commit>` sur main, `rebase <amont> main`, `worktree add -B main`. Le commit d'un
    contrat sur cette branche EST l'approbation, et `git switch` passe sans question."""
    # La valeur d'une option n'est pas un nom de branche : `update-ref -m <raison>`,
    # `rebase --onto <x>`, `branch -u <amont>`. (`branch -m` renomme : pas de valeur.)
    avec_valeur = {
        "update-ref": {"-m"},
        "rebase": {"--onto", "-x", "--exec", "-s", "--strategy", "-X", "--strategy-option"},
        "branch": {"-u", "--set-upstream-to"},
    }.get(sous, set())
    positionnels, j = [], 0
    while j < len(args):
        if args[j] in avec_valeur:
            j += 2
            continue
        if not args[j].startswith("-"):
            positionnels.append(args[j])
        j += 1
    if sous == "switch":
        for valeur in valeurs(args, "cC", {"--create", "--force-create"}):
            if principale_visee(valeur):
                return principale_visee(valeur)
    elif sous == "checkout":
        for valeur in valeurs(args, "bB", set()):
            if principale_visee(valeur):
                return principale_visee(valeur)
    elif sous == "worktree" and positionnels[:1] == ["add"]:
        for valeur in valeurs(args, "bB", set()):
            if principale_visee(valeur):
                return principale_visee(valeur)
    elif sous == "branch":
        liste = {"-l", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
                 "--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
                 "--show-current", "--sort", "--format", "--column"}
        if not any(a.split("=", 1)[0] in liste for a in args):
            for a in positionnels:
                if principale_visee(a):
                    return principale_visee(a)
    elif sous == "update-ref":
        if "--stdin" in args:
            return "main"
        cible = positionnels[0] if positionnels else ""
        if principale_visee(cible) or (cible == "HEAD" and BRANCHE["courante"] in PRINCIPALES):
            return principale_visee(cible) or BRANCHE["courante"]
    elif sous in {"push", "fetch"} and positionnels:
        distant, refspecs = positionnels[0], positionnels[1:]
        if sous == "fetch" or distant in {".", "./", racine}:
            for refspec in refspecs:
                if ":" in refspec and principale_visee(refspec.split(":", 1)[1]):
                    return principale_visee(refspec.split(":", 1)[1])
    elif sous == "reset" and BRANCHE["courante"] in PRINCIPALES:
        avant_chemins = args[: args.index("--")] if "--" in args else args
        modes = {"--soft", "--mixed", "--hard", "--keep", "--merge"}
        if modes & set(avant_chemins):
            return BRANCHE["courante"]
        for a in avant_chemins:
            # Un nom qui n'est pas un fichier du dossier désigne un commit : `reset x` déplace.
            if not a.startswith("-") and not os.path.exists(os.path.join(racine, a)):
                return BRANCHE["courante"]
    elif sous == "rebase" and len(positionnels) >= 2 and principale_visee(positionnels[1]):
        return principale_visee(positionnels[1])
    return None


def cibles_de_make(args):
    """(les cibles d'une commande `make`, dans n'importe quel ordre ; imprime-t-elle
    l'environnement ?). `make -p` affiche toutes les variables, jetons compris."""
    avec_valeur = {"-C", "-f", "-I", "-o", "-W", "-E", "--directory", "--file", "--makefile",
                   "--include-dir", "--old-file", "--assume-old", "--what-if", "--new-file",
                   "--assume-new", "--eval"}
    cibles, imprime, j = [], False, 0
    while j < len(args):
        a = args[j]
        if a in avec_valeur:
            j += 2
            continue
        if a.startswith("--"):
            imprime = imprime or a == "--print-data-base"
        elif a.startswith("-") and len(a) > 1:
            for lettre in a[1:]:
                if lettre in "CfIoWEjl":
                    break  # la suite du mot est la valeur de l'option
                imprime = imprime or lettre == "p"
        elif "=" not in a:
            cibles.append(a)
        j += 1
    return cibles, imprime


def script_lance(nom, args):
    """(nom de fichier du script qu'un interprète lance, ses arguments), ou (None, [])."""
    if nom.rstrip("0123456789.") not in INTERPRETES | COQUILLES:
        return None, []
    j = 0
    while j < len(args):
        a = args[j]
        if a in {"-c", "-m", "-e"}:
            return None, []
        if a in {"-X", "-W", "-O"} and nom.startswith("python"):
            j += 2
            continue
        if not a.startswith("-"):
            return os.path.basename(a), args[j + 1 :]
        j += 1
    return None, []


def programme(mots):
    """(nom du programme, ses arguments), après affectations, préfixes et lanceurs."""
    while mots:
        tete = os.path.basename(mots[0])
        if re.match(r"^\w+=", mots[0]) or tete in PREFIXES:
            mots = mots[1:]
        elif tete in LANCEURS and len(mots) > 1 and mots[1] == LANCEURS[tete]:
            mots = [m for m in mots[2:]]
            while mots and mots[0].startswith("-"):  # `uv run --with x ruff` : on saute les options
                mots = mots[2:] if mots[0] in {"--with", "--python", "-p", "--group"} else mots[1:]
        elif tete in LANCEURS_SEULS:
            mots = mots[1:]
        elif tete.rstrip("0123456789.") in {"python", "python3"} and len(mots) > 2 and mots[1] == "-m":
            mots = mots[2:]
        else:
            break
    if not mots:
        return "", []
    return os.path.basename(mots[0]), mots[1:]


def formateur(nom, args):
    """Les fichiers qu'un formateur réécrit en place, s'il les réécrit."""
    options = [a for a in args if a.startswith("-")]
    positionnels = [a for a in args if not a.startswith("-")]
    if nom == "ruff":
        sous = positionnels[0] if positionnels else ""
        ecrit = "--fix" in options or (sous == "format" and not {"--check", "--diff"} & set(options))
        return positionnels[1:] if ecrit and sous in {"check", "format"} else []
    if nom in {"black", "isort"}:
        return [] if {"--check", "--check-only", "--diff"} & set(options) else positionnels
    if nom in {"autopep8", "autoflake"}:
        return positionnels if {"-i", "--in-place"} & set(options) else []
    if nom == "prettier":
        return positionnels if {"-w", "--write"} & set(options) else []
    if nom == "eslint":
        return positionnels if "--fix" in options else []
    if nom in {"gofmt", "shfmt"}:
        return positionnels if "-w" in options else []
    return []


# Le premier argument de ces programmes est un motif ou un programme, pas un fichier.
MOTIF_D_ABORD = {"rg", "grep", "egrep", "fgrep", "ag", "ack", "awk", "gawk", "sed", "jq", "yq"}
AVEC_VALEUR_LECTURE = {
    "-e", "--regexp", "-g", "--glob", "-t", "--type", "-m", "--max-count",
    "-A", "-B", "-C", "--include", "--exclude", "--exclude-dir",
}


def chemins_lus(nom, args):
    """Les fichiers dont la commande montre le contenu : `cat .env`, `rg x api/.env`,
    `grep -f .env`, `source .env`, ou un script en ligne qui les ouvre."""
    if nom.rstrip("0123456789.") in INTERPRETES:
        for script in scripts_en_ligne(args):
            yield from re.findall(r"""['"]([^'"\s]+)['"]""", script)
        return
    if nom not in LECTEURS:
        return
    motif_d_abord = nom in MOTIF_D_ABORD and not (
        {"-e", "--regexp", "-f", "--file"} & set(args)
        or any(a.startswith(("--regexp=", "--file=")) for a in args)
    )
    j = 0
    while j < len(args):
        a = args[j]
        if a in {"-f", "--file"} and j + 1 < len(args):
            yield args[j + 1]  # le motif est LU dans ce fichier
            j += 2
            continue
        if a.startswith("--file="):
            yield a.split("=", 1)[1]
        elif a in AVEC_VALEUR_LECTURE:
            j += 2
            continue
        elif not a.startswith("-"):
            if motif_d_abord:
                motif_d_abord = False
            else:
                yield a
        j += 1


def gestes_et_lectures(nom, args, ici):
    """Les gestes de l'opérateur, dans n'importe quel ordre de mots, et ce qui montre un
    secret : un fichier refusé à la lecture, ou tout l'environnement (jetons compris)."""
    if nom in {"make", "gmake"}:
        cibles_m, imprime = cibles_de_make(args)
        gestes = [c for c in cibles_m if c in CIBLES_OPERATEUR]
        if gestes:
            yield OPERATEUR + "make " + gestes[0], "make"
        if imprime:
            yield SECRET + "tout l'environnement", "make -p"
    script, args_script = script_lance(nom, args)
    if script is None and nom in SCRIPTS_OPERATEUR:
        script, args_script = nom, args
    if script in SCRIPTS_OPERATEUR:
        attendus = SCRIPTS_OPERATEUR[script]
        sous = next((a for a in args_script if not a.startswith("-")), "")
        if attendus is None or sous in attendus:
            yield OPERATEUR + script + (" " + sous if attendus else ""), script
    if nom == "printenv" and any(
        re.search(r"TOKEN|SECRET|PASSW|KEY|CREDENTIAL|AUTH", a, re.I) for a in args
    ):
        yield SECRET + "une variable secrète de l'environnement", "printenv"
    if (
        (nom == "export" and "-p" in args)
        or (nom == "declare" and {"-x", "-p"} & set(args))
        or (nom == "set" and not args)
    ):
        yield SECRET + "tout l'environnement", nom
    for chemin in chemins_lus(nom, args):
        if chemin.startswith("-") or "://" in chemin:
            continue
        if lecture_refusee(resolu(ici(chemin))):
            yield SECRET + chemin, nom


def agent_retire(mots):
    """La variable d'agent que cette commande retire, ou None : `X= commande`, `unset X`,
    `export X=`, `env -u X`, ou `env -i`, qui les retire toutes."""
    for mot in mots:
        affectation = re.match(r"^(\w+)=", mot)
        if not affectation:
            break
        if affectation.group(1) in AGENTS:
            return affectation.group(1)
    tete, args = (os.path.basename(mots[0]), mots[1:]) if mots else ("", [])
    if tete == "unset":
        return next((a for a in args if a in AGENTS), None)
    if tete == "export":
        return next((a.split("=", 1)[0] for a in args if a.split("=", 1)[0] in AGENTS), None)
    if tete == "env":
        for i, a in enumerate(args):
            if a in {"-i", "--ignore-environment", "-"}:
                return "tout l'environnement"
            retiree = args[i + 1] if a in {"-u", "--unset"} and i + 1 < len(args) else None
            retiree = retiree or (a[len("--unset="):] if a.startswith("--unset=") else None)
            retiree = retiree or (a[2:] if a.startswith("-u") and len(a) > 2 else None)
            if retiree in AGENTS or a.split("=", 1)[0] in AGENTS:
                return retiree if retiree in AGENTS else a.split("=", 1)[0]
            if not a.startswith("-") and not re.match(r"^\w+=", a):
                break
    return None


def heredocs(texte):
    """Retire les documents en ligne du texte, et dit ce que chacun écrit selon qui le reçoit."""
    trouves = []

    def retire(m):
        ligne = texte[texte.rfind("\n", 0, m.start()) + 1 : m.start()]
        segment = re.split(r"&&|\|\||[;|&(]", ligne)[-1]
        try:
            mots = shlex.split(segment)
        except ValueError:
            mots = segment.split()
        trouves.append((mots, m.group(3)))
        return ""

    reste = HEREDOC.sub(retire, texte)
    return reste, trouves


def cibles(texte, profondeur=0, base=None):
    """(cible, pourquoi) pour chaque écriture qu'on sait lire dans une commande.

    `base` est le dossier courant, suivi à travers les `cd` : `cd .ephor && sed -i x judge.py`
    visait judge.py à la racine, qui n'est pas une zone, et passait (mesuré le 25/09/2026).
    None : dossier inconnu (`cd "$X"`), on lit alors depuis la racine du dépôt.
    """
    base = base or racine

    def ici(cible):
        if base is None or cible.startswith(("/", "~", "$", "&")):
            return cible
        return os.path.join(base, cible)

    texte, documents = heredocs(texte)
    for mots_doc, corps in documents:
        nom_doc, args_doc = programme(mots_doc)
        if nom_doc.rstrip("0123456789.") in INTERPRETES:
            yield from script_qui_ecrit(corps, "script en document en ligne qui écrit")
        elif nom_doc in COQUILLES and profondeur < 3:
            yield from cibles(corps, profondeur + 1, base)
        elif (nom_doc == "git" and args_doc[:1] in (["apply"], ["am"])) or nom_doc == "patch":
            for nom in noms_du_correctif(corps):
                yield ici(nom), "correctif appliqué"
    try:
        lexeur = shlex.shlex(texte, posix=True, punctuation_chars=";&|()<>")
        lexeur.whitespace_split = True
        lexeur.commenters = ""
        mots = list(lexeur)
    except ValueError:
        # Guillemets déséquilibrés : on ne sait pas lire. Une zone nommée suffit alors.
        for litteral in LITTERAUX:
            if litteral in texte:
                yield litteral, "commande illisible qui nomme une zone de contrôle"
        return
    commandes, courante = [], []
    for mot in mots:
        if mot in SEPARATEURS:
            if courante:
                commandes.append(courante)
            courante = []
            continue
        courante.append(mot)
    if courante:
        commandes.append(courante)
    for mots in commandes:
        # Les redirections, où qu'elles soient ; l'entrée lue (`< x.diff`) est gardée à part.
        reste, entree = [], None
        i = 0
        while i < len(mots):
            if mots[i] in REDIRIGE and i + 1 < len(mots):
                yield ici(mots[i + 1]), "redirection"
                i += 2
                continue
            if mots[i] == "<" and i + 1 < len(mots):
                entree = mots[i + 1]
                i += 2
                continue
            reste.append(mots[i])
            i += 1
        # `env` seul (ou suivi de seules affectations) affiche tout l'environnement ; plus bas,
        # le lanceur `env x` est enlevé avant de regarder `x`, et ce cas-là passerait.
        tete = os.path.basename(reste[0]) if reste else ""
        if tete in {"env", "printenv"} and all(re.match(r"^\w+=", m) or m.startswith("-") for m in reste[1:]):
            yield SECRET + "tout l'environnement", tete
        retiree = agent_retire(reste)
        if retiree:
            yield AGENT + retiree, "variable d'agent"
        nom, args = programme(reste)
        if not nom:
            continue
        options = [a for a in args if a.startswith("-")]
        yield from gestes_et_lectures(nom, args, ici)
        positionnels = [a for a in args if not a.startswith("-")]
        if nom in {"cd", "pushd"}:
            cible = positionnels[0] if positionnels else maison
            if "$" in cible or cible == "-":
                base = None
            else:
                cible = maison + cible[1:] if cible.startswith("~") else cible
                base = os.path.normpath(os.path.join(base or racine, cible))
            continue
        if nom == "popd":
            base = None
            continue
        if nom in TOUS_ECRITS or nom == "mv":
            for a in positionnels:
                yield ici(a), nom
        elif nom in DERNIER_ECRIT and valeurs(args, "t", {"--target-directory"}):
            # `cp -t .ephor x` : la destination est l'option, pas le dernier argument.
            for a in valeurs(args, "t", {"--target-directory"}):
                yield ici(a), nom + " -t"
        elif nom in DERNIER_ECRIT and positionnels:
            yield ici(positionnels[-1]), nom
        elif nom in {"curl", "wget"}:
            # Ce que le réseau rapporte s'écrit quelque part : l'option de sortie, sinon, pour
            # `curl -O` et `wget`, le dernier morceau de l'adresse dans le dossier courant.
            if nom == "curl":
                sorties = valeurs(args, "o", {"--output"})
                par_nom = bool({"-O", "--remote-name", "--remote-name-all"} & set(args)) or any(
                    re.match(r"^-[A-Za-z]*O[A-Za-z]*$", a) for a in options
                )
            else:
                sorties = valeurs(args, "O", {"--output-document"})
                par_nom = not sorties
            prefixe = (valeurs(args, "P", {"--directory-prefix"}) or [""])[0] if nom == "wget" else ""
            for a in sorties:
                yield ici(a), nom
            if par_nom:
                for a in positionnels:
                    morceau = os.path.basename(urllib.parse.urlsplit(a).path) if "://" in a else ""
                    if morceau:
                        yield ici(os.path.join(prefixe, morceau)), nom
        elif nom == "tar" and tar_extrait(args):
            for a in valeurs(args, "C", {"--directory"}) or ["."]:
                yield ici(a), "tar (extraction)"
        elif nom == "unzip":
            for a in valeurs(args, "d", set()) or ["."]:
                yield ici(a), "unzip"
        elif nom == "dd":
            for a in args:
                if a.startswith("of="):
                    yield ici(a[3:]), "dd"
        elif nom in {"sed", "gsed"} and any(o.startswith("-i") or o == "--in-place" or o.startswith("--in-place=") for o in options):
            for a in positionnels:
                yield ici(a), "sed -i"
        elif nom == "perl" and any(o.startswith("-") and "i" in o[1:] and not o.startswith("--") for o in options):
            for a in positionnels:
                yield ici(a), "perl -i"
        elif nom == "find":
            racines = []
            for a in args:
                if a.startswith(("-", "(", "!")):
                    break
                racines.append(a)
            executes = [os.path.basename(args[j + 1]) for j, a in enumerate(args[:-1]) if a in {"-exec", "-execdir", "-ok", "-okdir"}]
            if "-delete" in args or any(e in TOUS_ECRITS | DERNIER_ECRIT | {"mv", "sed", "perl"} for e in executes):
                for a in racines or ["."]:
                    yield ici(a), "find qui écrit"
        elif nom == "git" and args:
            sous_g, reste_g = git_sous_commande(args)
            deplacee = principale_deplacee(sous_g, reste_g)
            if deplacee:
                yield PRINCIPALE + deplacee, "git " + sous_g
            nouvelle = branche_apres(sous_g, reste_g)
            if nouvelle is not None:
                BRANCHE["courante"] = nouvelle
            elif sous_g in HISTOIRE and BRANCHE["courante"] in PRINCIPALES:
                yield PRINCIPALE + BRANCHE["courante"], "git " + sous_g
            sous = args[0]
            if sous in {"checkout", "restore", "rm", "mv"}:
                for a in args[1:]:
                    if not a.startswith("-"):
                        yield ici(a), "git " + sous
            elif sous == "config":
                fichier = git_config(args[1:])
                if fichier:
                    yield ici(fichier), "git config"
            elif sous == "remote" and args[1:2] and args[1] in {"add", "set-url", "rename", "remove", "rm", "set-head", "set-branches"}:
                yield os.path.join(racine, ".git", "config"), "git remote " + args[1]
            elif sous in {"apply", "am"} and not {"--check", "--stat", "--numstat", "--summary"} & set(args):
                for fichier in [a for a in args[1:] if not a.startswith("-")] + ([entree] if entree else []):
                    texte_correctif = correctif_lu(fichier, base)
                    for nom_c in noms_du_correctif(texte_correctif or ""):
                        yield ici(nom_c), "git " + sous
        elif nom == "patch":
            fichiers = [entree] if entree else []
            for j, a in enumerate(args):
                if a in {"-i", "--input"} and j + 1 < len(args):
                    fichiers.append(args[j + 1])
                elif a.startswith("--input="):
                    fichiers.append(a.split("=", 1)[1])
            for fichier in fichiers:
                for nom_c in noms_du_correctif(correctif_lu(fichier, base) or ""):
                    yield ici(nom_c), "patch"
            if positionnels and not any(a.startswith(("-i", "--input")) for a in options):
                yield ici(positionnels[0]), "patch"
        else:
            for a in formateur(nom, args):
                yield ici(a), nom + " (réécrit en place)"
        if nom in COQUILLES and profondeur < 3:
            for texte_interne in scripts_en_ligne(args):
                yield from cibles(texte_interne, profondeur + 1, base)
        elif nom.rstrip("0123456789.") in INTERPRETES:
            for script in scripts_en_ligne(args):
                yield from script_qui_ecrit(script)


try:
    for cible, pourquoi in cibles(commande):
        speciale = next(
            (m for m in (PRINCIPALE, OPERATEUR, SECRET, AGENT) if cible.startswith(m)), None
        )
        if speciale:
            print(f"{cible[len(speciale):]}\t{speciale}\t{pourquoi}")
            break
        motif = zone(cible)
        if motif:
            print(f"{cible}\t{motif}\t{pourquoi}")
            break
except Exception:  # un défaut de cette analyse ne doit pas tout laisser passer
    for litteral in LITTERAUX:
        if litteral in commande:
            print(f"{litteral}\t{litteral}\tcommande que cette garde n'a pas su analyser")
            break
PY
VERDICT="$(EPHOR_COMMANDE="$COMMANDE" EPHOR_MOTIFS="$MOTIFS" EPHOR_EXCEPTIONS="$EXCEPTIONS" \
  EPHOR_MOTEUR="$(dirname "$ZONES_MOTEUR")" \
  EPHOR_BRANCHE="$BRANCHE" EPHOR_PRINCIPALES="$PRINCIPALES" EPHOR_LECTURES="$LECTURES" \
  python3 -c "$ANALYSE" "$ROOT" "$HOME")"
if [ -n "$VERDICT" ]; then
  IFS="$(printf '\t')" read -r CIBLE MOTIF POURQUOI <<EOF
$VERDICT
EOF
  case "$MOTIF" in
    "::branche principale::")
      deny "REFUSÉ : $POURQUOI écrit ou déplace $CIBLE, la branche principale. Un commit sur cette branche, c'est l'approbation de l'opérateur (le juge la lit là) : l'agent n'y écrit pas l'histoire et ne la déplace pas. Travaille sur ta branche de tâche (git switch task/<ID>). Si c'est vraiment voulu, c'est l'opérateur qui le fait, dans son terminal." ;;
    "::geste de l'opérateur::")
      deny "REFUSÉ : $CIBLE est un geste de l'opérateur (approuver ou ranger une tâche, régénérer les réglages, faire entrer une page du web, installer le greffon, effacer les points de reprise) : l'agent ne le lance pas, dans aucun ordre de mots. Demande-le à l'opérateur." ;;
    "::variable d'agent::")
      deny "REFUSÉ : cette commande retire $CIBLE de l'environnement, ce qui dit qu'un agent tourne. Les gestes de l'opérateur le lisent pour se refuser à l'agent : le retirer, c'est les contourner. Demande le geste à l'opérateur." ;;
    "::lecture d'un secret::")
      deny "REFUSÉ : cette commande ($POURQUOI) montrerait $CIBLE, refusé à la lecture par les réglages du dépôt : un secret lu entre dans le prompt, donc chez le fournisseur du modèle. Utilise une variable d'environnement sans l'afficher, ou demande à l'opérateur." ;;
  esac
  deny "REFUSÉ : cette commande écrit dans $CIBLE ($POURQUOI), zone de contrôle « $MOTIF ». Écrire par le terminal ce que l'édition refuse, c'est contourner le garde-fou — et le juge refuserait de toute façon le correctif. Si le changement est vraiment nécessaire, c'est l'opérateur qui le fait, à la main."
fi
exit 0
