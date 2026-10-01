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
# Là où la commande tourne : Claude Code garde le `cd` d'un appel à l'autre, et le donne dans
# `cwd` ; opencode le reçoit en `workdir`. Lus depuis la racine, `cd .ephor` puis, à l'appel
# suivant, `sed -i x zones.txt` passait (audit du 01/10/2026).
COURANT="$(_charge courant 2>/dev/null || true)"
[ -z "$COURANT" ] || COURANT="$(resoudre "$COURANT" "$ROOT")"

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
import subprocess
import sys
import urllib.parse

racine, maison = sys.argv[1], sys.argv[2]
# Par l'entrée standard, pas par l'environnement : au-delà de 128 Kio, une variable empêchait
# python3 de démarrer (« Argument list too long »), et tout passait (audit du 01/10/2026).
commande = sys.stdin.read()
# Le dossier où la commande tourne (voir COURANT plus haut) ; la racine à défaut.
COURANT = os.environ.get("EPHOR_COURANT") or racine
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
    "task-clore",
    "opencode-modeles",
}
SCRIPTS_OPERATEUR = {
    "judge.py": {"new", "rouge", "approuve", "ranger"},
    "adapters.py": {"write", "modeles"},
    "fetch_docs.py": None,  # toute invocation
    "install_copilot_plugin.sh": None,
    "bootstrap_existing_repo.sh": None,
    "worktree.sh": None,
    "clore.sh": None,
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
# Un littéral tiré d'un chemin exact (`Makefile`, `tasks/`, `tasks/*`) ne compte que comme
# composant ENTIER d'un chemin : `$TMPDIR/tasks.log`, `.gitignore` ou `asyncio.all_tasks`
# faisaient refuser des commandes qui n'écrivaient nulle part ailleurs (audit du 01/10/2026).
# Tiré d'un préfixe (`docs/adr/0*`), il compte tel quel.
BORNE = {}
for m in motifs:
    debut = re.split(r"[*?\[]", m, maxsplit=1)[0]
    litteral = debut.rstrip("/")
    if not litteral:
        continue
    LITTERAUX.add(litteral)
    BORNE[litteral] = BORNE.get(litteral, True) and (debut.endswith("/") or debut == m)
    if litteral.startswith(maison + "/"):
        reste = litteral[len(maison) + 1 :]
        LITTERAUX.update({"~/" + reste, "$HOME/" + reste, "${HOME}/" + reste})
LITTERAUX = sorted(LITTERAUX, key=len, reverse=True)

SEPARATEURS = {";", "&&", "||", "|", "&", "|&", "(", ")", "\n", ";;"}
# Un jeton fait seulement de ces signes sépare deux commandes : `;\n`, `)\n`, `&&`, `((`.
SIGNES_SEPARATEURS = set(";&|()\n")
# `>&` suivi d'un NOM de fichier écrit sortie et erreurs dedans (`ls >& x`). `<>` ouvre en
# lecture et en écriture, et crée le fichier : `exec 3<> Makefile` passait.
REDIRIGE = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}
# Ce qui suit un nombre collé à sa redirection : `2>/dev/null`, `3<> x`, `0<&3`.
REDIRECTIONS = REDIRIGE | {"<", "<&", "<<<"}
# Un descripteur dupliqué ou fermé n'est pas un fichier : `2>&1`, `>&-`, `0<&3`.
DESCRIPTEUR = re.compile(r"[0-9]+|-")
TOUS_ECRITS = {"rm", "rmdir", "unlink", "truncate", "touch", "chmod", "chown", "shred", "tee"}
DERNIER_ECRIT = {"cp", "install", "ln", "rsync"}
INTERPRETES = {"python", "python3", "node", "nodejs", "bun", "deno", "perl", "ruby"}
COQUILLES = {"bash", "sh", "zsh", "dash", "ksh"}
# Ce qui lance la commande qui suit, et les options qui y prennent une valeur. Sans elles,
# `sudo -n rm -rf .ephor` ou `timeout 30 rm …` faisaient de `-n` ou de `30` le programme.
PREFIXES = {
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T", "--user", "--group",
             "--host", "--prompt", "--chdir", "--role", "--type", "--other-user"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir"},
    "command": set(),
    "exec": {"-a"},
    "nohup": set(),
    "time": {"-f", "-o", "--format", "--output"},
    "xargs": {"-I", "-n", "-P", "-d", "-E", "-L", "-s", "-a", "--max-args", "--max-procs",
              "--delimiter", "--eof", "--max-lines", "--max-chars", "--arg-file", "--replace"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"},
    "stdbuf": {"-i", "-o", "-e"},
    "setsid": set(),
    "caffeinate": {"-t", "-w"},
    "chronic": set(),
}
# Ce qui ouvre ou ferme un bloc du shell : `if x; then rm y; fi` lance `rm`, pas `then`.
MOTS_CLES = {"if", "then", "else", "elif", "do", "while", "until", "{", "}", "!", "fi", "done", "esac"}
# Ce qui lance un autre programme sans rien changer à ce qu'il écrit : `uv run ruff`, `npx x`.
LANCEURS = {"uv": "run", "poetry": "run", "pipx": "run", "pdm": "run", "hatch": "run", "rye": "run", "pnpm": "exec"}
LANCEURS_SEULS = {"uvx", "npx", "bunx", "pnpx"}
# Un script en ligne qui écrit, déplace ou supprime un fichier.
ECRIT = re.compile(
    r"write_text|write_bytes|\.write\(|open\((?:[^()]|\([^()]*\))*?['\"][rwxa]?[wxa+]|writeFile|appendFile|"
    r"createWriteStream|os\.remove|os\.unlink|os\.rename|os\.replace|shutil\.|\.unlink\(|"
    r"\.rename\(|\.touch\(|unlinkSync|renameSync|rmSync|copyFile|['\"]\+?>{1,2}"
)
# `python3 - <<EOF … EOF` : la forme la plus courante chez un agent pour écrire un script.
# Le reste de la ligne est GARDÉ (groupe 3) : `cat <<EOF > tasks/x/contract.json`, `| tee
# Makefile` ou `| git commit -F -` y écrivent, et passaient, retirés avec le document (audit
# du 01/10/2026). Un délimiteur peut porter un tiret ou un point : `PY-FIN`.
HEREDOC = re.compile(
    r"<<-?[ \t]*\\?(['\"]?)([^\s'\"\\;&|<>()]+)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.S
)
# Une substitution de commande, même entre guillemets, où shlex n'en voit qu'un mot :
# `echo "$(rm -f Makefile)"`, `` echo `rm -f Makefile` ``. Un niveau d'imbrication suffit.
SUBSTITUTION = re.compile(r"\$\(((?:[^()]|\([^()]*\))*)\)|`([^`]*)`")


def resolu(cible):
    cible = cible.replace("${HOME}", maison).replace("$HOME", maison)
    if cible.startswith("~/"):
        cible = os.path.join(maison, cible[2:])
    return os.path.normpath(os.path.join(racine, cible))


def dans_le_depot(dossier):
    """Ce dossier est-il dans le dépôt ? Inconnu (None) : on le suppose, par prudence."""
    if dossier is None:
        return True
    reel = os.path.realpath(dossier)
    return reel == racine or reel.startswith(racine + os.sep)


DEPOTS = {}


def depot_commun(dossier):
    """Le dossier git commun du dépôt où est ce dossier (le même pour tous ses worktrees), ou
    None hors de tout dépôt. Un appel à git, seulement pour une commande git hors du dépôt."""
    if dossier not in DEPOTS:
        try:
            fait = subprocess.run(
                ["git", "-c", "core.fsmonitor=false", "-C", dossier, "rev-parse",
                 "--path-format=absolute", "--git-common-dir"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            sortie = fait.stdout.strip() if fait.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            sortie = ""
        DEPOTS[dossier] = os.path.realpath(sortie) if sortie else None
    return DEPOTS[dossier]


def zone(cible):
    """Le motif de zone de contrôle que vise cette cible, ou None."""
    if not cible or cible.startswith("/dev/") or cible.startswith("&"):
        return None
    # Le chemin tel qu'écrit, puis tel que le système le suit : par un lien vers le dépôt
    # (`/tmp` est un lien sur Mac) ou vers une zone (`src/lien -> ../.ephor`), l'écriture
    # passait (audit du 01/10/2026). La racine, elle, est déjà sans lien (`pwd -P`).
    for absolu in dict.fromkeys((resolu(cible), os.path.realpath(resolu(cible)))):
        motif = _zone_de(absolu)
        if motif:
            return motif
    return _zone_par_le_texte(cible)


def _zone_de(absolu):
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
    return None


def _zone_par_le_texte(cible):
    # Une variable qu'on ne sait pas résoudre (`"$ROOT"/.ephor/x`) : on lit le texte.
    if "$" in cible:
        for litteral in LITTERAUX:
            if nomme(cible, litteral):
                return litteral
    return None


def nomme(texte, litteral, debut=r"(?:^|/)"):
    """Le texte nomme-t-il ce chemin de zone, comme composant entier d'un chemin ?"""
    if litteral.startswith(("/", "~", "$")):
        debut = ""
    fin = r"(?=/|$)" if BORNE.get(litteral, True) else ""
    return re.search(debut + re.escape(litteral) + fin, texte, re.M) is not None


# Les chaînes d'un script : c'est là qu'il nomme les fichiers qu'il ouvre. Un mot du code
# (`tasks = []`, `asyncio.all_tasks`) n'est pas un chemin.
CHAINE = re.compile(r"""\"(?:\\.|[^"\\\n])*\"|'(?:\\.|[^'\\\n])*'|`[^`]*`""")


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


def script_qui_ecrit(script, pourquoi="script en ligne qui écrit", base=None):
    """Les zones qu'un script d'interprète nomme, s'il écrit. Lancé hors du dépôt (`cd
    /ailleurs && python3 - <<EOF`), un nom relatif y désigne un fichier d'ailleurs : seul un
    chemin qui nomme le dépôt, ou une zone du poste, compte alors."""
    if not ECRIT.search(script):
        return
    dehors = not dans_le_depot(base)
    chaines = [m.group(0)[1:-1] for m in CHAINE.finditer(script)]
    for litteral in LITTERAUX:
        # `open(F, ">Makefile")` : le mode de perl, collé au nom, en tête de chaîne.
        if any(nomme(c, litteral, r"(?:^[+<>|]*\s*|/)") for c in chaines) and (
            not dehors or litteral.startswith("/") or racine in script
        ):
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


def config_de(dossier):
    """Le .git/config qu'écrit un `git config` lancé dans ce dossier, ou None hors de tout
    dépôt. Dans le dépôt, ou dossier inconnu : celui du dépôt. Ailleurs, celui du dépôt où
    l'on est (`cd /tmp/x && git config …` n'écrit pas le nôtre ; vu en session le 01/10/2026),
    qui EST le nôtre depuis le dossier d'une tâche : les worktrees partagent la configuration."""
    if dans_le_depot(dossier):
        return os.path.join(racine, ".git", "config")
    commun = depot_commun(dossier)
    return os.path.join(commun, "config") if commun else None


def git_config(args, defaut):
    """Le fichier qu'un `git config` ÉCRIT, ou rien pour une lecture. Par défaut .git/config,
    où un hook ou un moniteur de fichiers se plante : git les lance ensuite lui-même."""
    cible, positionnels, ecrit, lit, j = defaut, [], False, False, 0
    while j < len(args):
        a = args[j]
        nom_option = a.split("=", 1)[0]
        if nom_option in {"--global", "--system"}:
            return None  # hors du dépôt : ni zone, ni affaire de cette garde
        if nom_option == "--worktree" and defaut:
            cible = defaut + ".worktree"
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
    return git_options(args)[1:]


def git_options(args):
    """(les dossiers des `-C`, dans l'ordre ; sous-commande ; ses arguments)."""
    dossiers, i = [], 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            dossiers.append(args[i + 1])
        i += 2 if args[i] in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"} else 1
    return (dossiers, args[i], args[i + 1 :]) if i < len(args) else (dossiers, "", [])


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
    # `git checkout main -- x` ou `git checkout main x` restaure x tel que sur main, sans
    # changer de branche : la suite (`&& git commit`) commite sur la branche de tâche.
    chemins = args[args.index("--") + 1 :] if "--" in args else []
    if sous == "checkout" and (len(positionnels) > 1 or chemins):
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
        # `bash -n x.sh` lit la syntaxe sans rien lancer : ce n'est pas lancer le script.
        if nom in COQUILLES and re.match(r"^-[a-z]*n[a-z]*$", a):
            return None, []
        if a in {"-X", "-W", "-O"} and nom.startswith("python"):
            j += 2
            continue
        if not a.startswith("-"):
            return os.path.basename(a), args[j + 1 :]
        j += 1
    return None, []


def sans_prefixe(tete, mots):
    """Ce qui suit un préfixe (`sudo -n`, `timeout 30`, `nice -n 5`, `env -u X A=1`)."""
    avec_valeur = PREFIXES[tete]
    j = 1
    while j < len(mots):
        mot = mots[j]
        if mot == "--":
            j += 1
            break
        if mot in avec_valeur:
            j += 2
        elif mot.startswith("-") and len(mot) > 1:
            j += 1  # `-n5`, `--signal=KILL`, `-10` : l'option et sa valeur d'un seul mot
        elif tete == "env" and re.match(r"^\w+=", mot):
            j += 1
        else:
            break
    if tete == "timeout" and j < len(mots):
        j += 1  # la durée
    return mots[j:]


def programme(mots):
    """(nom du programme, ses arguments), après affectations, mots-clés, préfixes et
    lanceurs."""
    while mots:
        tete = os.path.basename(mots[0])
        if re.match(r"^\w+=", mots[0]) or mots[0] in MOTS_CLES:
            mots = mots[1:]
        elif tete in PREFIXES:
            mots = sans_prefixe(tete, mots)
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


# Ce qui tient la place d'un document en ligne dans la commande : un mot de sa commande, lu
# avec elle, au bon moment (après un `cd`), par son programme ou celui du tube qui suit.
MARQUE = "EPHORDOCUMENT"
EST_MARQUE = re.compile(MARQUE + r"(\d+)")
# Les corps, pour toute l'analyse : une marque peut finir dans une substitution analysée à
# part (`git commit -m "$(cat <<'EOF' … EOF)"`, la forme de commit de Claude Code).
DOCUMENTS = []


def heredocs(texte):
    """Remplace chaque document en ligne par une marque, en gardant le reste de sa ligne
    (`cat <<EOF > tasks/x/contract.json`) ; les corps vont dans DOCUMENTS."""

    def retire(m):
        DOCUMENTS.append(m.group(4))
        return f" {MARQUE}{len(DOCUMENTS) - 1} {m.group(3)}"

    return HEREDOC.sub(retire, texte)


def document(nom, args, corps, base, ici, profondeur):
    """Ce qu'écrit un texte donné à un programme sur son entrée (`<<EOF`, `<<<`, un tube)."""
    if nom.rstrip("0123456789.") in INTERPRETES:
        yield from script_qui_ecrit(corps, "script en document en ligne qui écrit", base)
    elif nom in COQUILLES and profondeur < 3:
        yield from cibles(corps, profondeur + 1, base)
    elif (nom == "git" and git_sous_commande(args)[0] in ("apply", "am")) or nom == "patch":
        for nom_c in noms_du_correctif(corps):
            yield ici(nom_c), "correctif appliqué"


VARIABLE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")


def developpe(mot, variables):
    """Un mot, ses variables connues remplacées : `for f in tasks/*.json; do rm "$f"`,
    `X=.ephor; rm -rf $X`. Une variable inconnue reste telle quelle."""
    noms = [n for n in dict.fromkeys(a or b for a, b in VARIABLE.findall(mot)) if n in variables]
    resultats = [mot]
    for n in noms:
        motif = re.compile(r"\$(?:\{%s\}|%s(?!\w))" % (re.escape(n), re.escape(n)))
        resultats = [motif.sub(lambda _t, v=v: v, r) for r in resultats for v in variables[n]]
    return resultats[:64]


def sed_en_place(option):
    """`-i`, `-i.bak`, `--in-place`, et groupées : `-ri`, `-Ei` (pas `-e i`, un script)."""
    if option == "--in-place" or option.startswith("--in-place="):
        return True
    if option.startswith("--") or not option.startswith("-"):
        return False
    for lettre in option[1:]:
        if lettre == "i":
            return True
        if lettre in "efl":
            return False
    return False


def branche_du_dossier(dossier):
    """La branche d'un autre dossier du même dépôt (la copie principale, vue d'une tâche)."""
    try:
        fait = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", dossier, "branch", "--show-current"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return fait.stdout.strip()


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

    texte = heredocs(texte)
    # Une ligne continuée (`\` en fin de ligne) est une seule commande.
    texte = texte.replace("\\\n", " ")
    # Une substitution de commande tourne, même entre guillemets.
    if profondeur < 3:
        for trouvee in SUBSTITUTION.finditer(texte):
            interne = trouvee.group(1) if trouvee.group(1) is not None else trouvee.group(2)
            if interne and interne.strip():
                yield from cibles(interne, profondeur + 1, base)
    try:
        # Le saut de ligne SÉPARE deux commandes, comme `;` : lu comme un blanc, `echo x` puis
        # `rm -rf .ephor` sur la ligne suivante faisaient une seule commande, `echo`, et la
        # seconde passait (audit du 01/10/2026). Entre guillemets, il reste dans le mot.
        lexeur = shlex.shlex(texte, posix=True, punctuation_chars=";&|()<>\n")
        lexeur.whitespace = " \t\r"
        lexeur.whitespace_split = True
        lexeur.commenters = ""
        mots = list(lexeur)
    except ValueError:
        # Guillemets déséquilibrés : on ne sait pas lire. Une zone nommée suffit alors.
        for litteral in LITTERAUX:
            if litteral in texte:
                yield litteral, "commande illisible qui nomme une zone de contrôle"
        return
    # Chaque commande, et le séparateur qui la suit : un tube passe son entrée à la suivante.
    # Une parenthèse se garde même sans commande devant : elle ouvre ou ferme un sous-shell.
    commandes, courante = [], []
    for mot in mots:
        if mot in SEPARATEURS or (mot and set(mot) <= SIGNES_SEPARATEURS):
            if courante or "(" in mot or ")" in mot:
                commandes.append((courante, mot))
            courante = []
            continue
        courante.append(mot)
    if courante:
        commandes.append((courante, ""))
    variables, recus = {}, []
    # Le `cd` d'un sous-shell s'arrête à sa parenthèse : `(cd tasks && ls) && echo x > n.txt`
    # écrivait tasks/n.txt, et `(cd src && make) && rm -f Makefile` visait src/Makefile
    # (audit du 01/10/2026). Le séparateur qui PRÉCÈDE chaque commande ouvre ou ferme.
    pile, avant = [], ""
    for mots, apres in commandes:
        for signe in avant:
            if signe == "(":
                pile.append(base)
            elif signe == ")" and pile:
                base = pile.pop()
        avant = apres
        if not mots:
            continue
        # Les documents en ligne de cette commande, et ceux qu'un tube lui passe.
        textes = recus + [
            DOCUMENTS[int(m.group(1))]
            for m in map(EST_MARQUE.fullmatch, mots)
            if m and int(m.group(1)) < len(DOCUMENTS)
        ]
        mots = [m for m in mots if not EST_MARQUE.fullmatch(m)]
        # `for f in …` et `X=…` : de quoi lire `"$f"` et `$X` dans les commandes suivantes.
        if len(mots) >= 3 and mots[0] == "for" and mots[2] == "in":
            variables[mots[1]] = [v for m in mots[3:] for v in developpe(m, variables)] or [""]
            recus = []
            continue
        if variables:
            mots = [v for m in mots for v in developpe(m, variables)]
        if mots and all(re.match(r"^\w+=", m) for m in mots):
            variables.update((m.split("=", 1)[0], [m.split("=", 1)[1]]) for m in mots)
        # Les redirections, où qu'elles soient ; l'entrée lue (`< x.diff`) est gardée à part,
        # comme le texte donné en ligne (`python3 <<< "…"`).
        reste, entree = [], None
        i = 0
        while i < len(mots):
            # shlex détache le `2` de `2>&1` : lu dans .ephor/, « 1 » y devenait un fichier
            # écrit, et le `2` de `touch /tmp/x 2>/dev/null` un fichier touché (vu en session
            # le 01/10/2026). Un nombre collé à sa redirection désigne un descripteur.
            if re.fullmatch(r"[0-9]+", mots[i]) and i + 1 < len(mots) and mots[i + 1] in REDIRECTIONS:
                i += 1
                continue
            if mots[i] in {">&", "<&"} and i + 1 < len(mots) and DESCRIPTEUR.fullmatch(mots[i + 1]):
                i += 2
                continue
            if mots[i] in REDIRIGE and i + 1 < len(mots):
                yield ici(mots[i + 1]), "redirection"
                i += 2
                continue
            if mots[i] == "<" and i + 1 < len(mots):
                entree = mots[i + 1]
                i += 2
                continue
            if mots[i] == "<<<" and i + 1 < len(mots):
                textes.append(mots[i + 1])
                i += 2
                continue
            reste.append(mots[i])
            i += 1
        recus = textes if apres in {"|", "|&"} else []
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
        for texte_recu in textes:
            yield from document(nom, args, texte_recu, base, ici, profondeur)
        if nom in {"cd", "pushd"}:
            # `cd -` revient au dossier précédent, inconnu ici : pas au dossier personnel.
            cible = next((a for a in args if a == "-" or not a.startswith("-")), maison)
            if "$" in cible or "`" in cible or cible == "-":
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
        elif nom in {"sed", "gsed"} and any(sed_en_place(o) for o in options):
            for a in positionnels:
                yield ici(a), "sed -i"
        elif nom == "perl" and any(o.startswith("-") and "i" in o[1:] and not o.startswith("--") for o in options):
            for a in positionnels:
                yield ici(a), "perl -i"
        elif nom == "find":
            # Les options qui précèdent les racines : `find -L tasks -delete`.
            k = 0
            while k < len(args) and (args[k] in {"-H", "-L", "-P", "-D"} or args[k].startswith("-O")):
                k += 2 if args[k] == "-D" else 1
            racines = []
            for a in args[k:]:
                if a.startswith(("-", "(", "!")):
                    break
                racines.append(a)
            executes = [os.path.basename(args[j + 1]) for j, a in enumerate(args[:-1]) if a in {"-exec", "-execdir", "-ok", "-okdir"}]
            if "-delete" in args or any(e in TOUS_ECRITS | DERNIER_ECRIT | {"mv", "sed", "perl"} for e in executes):
                for a in racines or ["."]:
                    yield ici(a), "find qui écrit"
        elif nom == "git" and args:
            # Les options globales d'abord : `git -C <dépôt> checkout -- Makefile` ou `git
            # --no-pager restore …` perdaient leur sous-commande (audit du 01/10/2026). `-C`
            # change aussi le dossier où se lisent les chemins.
            dossiers_g, sous_g, reste_g = git_options(args)
            base_g = base
            for dossier_g in dossiers_g:
                if base_g is None or "$" in dossier_g:
                    base_g = None
                    break
                dossier_g = maison + dossier_g[1:] if dossier_g.startswith("~") else dossier_g
                base_g = os.path.normpath(os.path.join(base_g, dossier_g))

            def ici_g(cible, base_g=base_g):
                if base_g is None or cible.startswith(("/", "~", "$", "&")):
                    return cible
                return os.path.join(base_g, cible)

            # L'histoire de NOTRE dépôt seulement, copie principale comprise vue du dossier
            # d'une tâche ; un autre dépôt sur `main` n'est pas une approbation.
            chez_nous = dans_le_depot(base_g)
            voisin = not chez_nous and depot_commun(base_g) is not None and depot_commun(base_g) == depot_commun(racine)
            if chez_nous or voisin:
                ici_branche = BRANCHE["courante"]
                if voisin:
                    BRANCHE["courante"] = branche_du_dossier(base_g)
                deplacee = principale_deplacee(sous_g, reste_g)
                if deplacee:
                    yield PRINCIPALE + deplacee, "git " + sous_g
                nouvelle = branche_apres(sous_g, reste_g)
                if nouvelle is not None:
                    BRANCHE["courante"] = nouvelle
                elif sous_g in HISTOIRE and BRANCHE["courante"] in PRINCIPALES:
                    yield PRINCIPALE + BRANCHE["courante"], "git " + sous_g
                if voisin:
                    BRANCHE["courante"] = ici_branche
            if sous_g in {"checkout", "restore", "rm", "mv"}:
                for a in reste_g:
                    if not a.startswith("-"):
                        yield ici_g(a), "git " + sous_g
            elif sous_g == "config":
                fichier = git_config(reste_g, config_de(base_g))
                if fichier:
                    yield ici_g(fichier), "git config"
            elif sous_g == "remote" and reste_g[:1] and reste_g[0] in {"add", "set-url", "rename", "remove", "rm", "set-head", "set-branches"}:
                fichier = config_de(base_g)
                if fichier:
                    yield fichier, "git remote " + reste_g[0]
            elif sous_g in {"apply", "am"} and not {"--check", "--stat", "--numstat", "--summary"} & set(reste_g):
                for fichier in [a for a in reste_g if not a.startswith("-")] + ([entree] if entree else []):
                    texte_correctif = correctif_lu(fichier, base_g)
                    for nom_c in noms_du_correctif(texte_correctif or ""):
                        yield ici_g(nom_c), "git " + sous_g
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
                yield from script_qui_ecrit(script, base=base)


try:
    for cible, pourquoi in cibles(commande, base=COURANT):
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
MOTEUR_DIR="$(dirname "$ZONES_MOTEUR")"
VERDICT="$(printf '%s' "$COMMANDE" | EPHOR_MOTIFS="$MOTIFS" EPHOR_EXCEPTIONS="$EXCEPTIONS" \
  EPHOR_MOTEUR="$MOTEUR_DIR" EPHOR_COURANT="$COURANT" \
  EPHOR_BRANCHE="$BRANCHE" EPHOR_PRINCIPALES="$PRINCIPALES" EPHOR_LECTURES="$LECTURES" \
  python3 -c "$ANALYSE" "$ROOT" "$HOME")" \
  || deny "REFUSÉ : la garde du terminal n'a pas pu analyser cette commande (python3 n'a pas abouti). Elle refuse plutôt que de laisser passer sans rien voir. Découpe la commande, ou écris le script dans un fichier hors des zones de contrôle, puis lance-le."
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
