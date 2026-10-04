#!/usr/bin/env bash
# Ephor (privé) : Ephor sur un dépôt partagé que l'équipe n'a pas (encore) adopté.
#
# Le moteur, les garde-fous et l'interface de travail sont posés dans VOTRE clone et commités
# sur VOTRE branche principale locale, derrière un marqueur ; rien n'en part sur le serveur.
# Ce que l'équipe reçoit, par merge request : les commits de la tâche, rejoués tels quels,
# précédés du contrat et de ses tests d'acceptation (la spécification avant le code, et la
# preuve), avec le verdict local du juge en déclaration. Ephor ne sait pas qu'il est
# « privé » : juge, diagnostic et installation ne changent pas ; seule la disposition git
# diffère. Détail et limites : .ephor/docs/13-ephor-prive.md.
#
#   bash <dépôt d'Ephor>/.ephor/scripts/prive.sh poser /chemin/du/clone
#   bash .ephor/scripts/prive.sh publier <ID> [--branche <nom>] [--sans-verdict] [--sans-pousser]
#   bash .ephor/scripts/prive.sh publier --sans-contrat <branche> [--sans-pousser]
#   bash .ephor/scripts/prive.sh adopter
#   bash .ephor/scripts/prive.sh retirer [--force]
#   bash .ephor/scripts/prive.sh etat
#
#   poser     installe Ephor dans le clone (ou le met à jour, lancé depuis une nouvelle
#             version) : le script d'installation fait son travail ; puis ce que celui-ci
#             aurait ajouté à .gitignore va dans .git/info/exclude, PROGRESS.md et STEER.md
#             restent hors de git, les réglages de Claude Code vont dans
#             .claude/settings.local.json quand l'équipe a les siens, le Makefile de l'équipe
#             ne gagne qu'une ligne d'inclusion (les cibles d'Ephor vivent dans
#             .ephor/projet/prive.mk, sans celles que l'équipe définit déjà) ; le tout dans UN
#             commit sur la branche principale locale, marqué « Ephor-prive: », et une garde
#             pre-push refuse de pousser le moteur, le marqueur et rouge.json.
#   publier   rejoue les commits de task/<ID> sur origin/<principale>, dans une copie à part
#             (jamais sur la branche de l'agent) : d'abord le contrat et ses tests (sans
#             rouge.json, trace interne), avec le verdict local dans tasks/<ID>/rapport.md ;
#             puis chaque commit de la tâche, dans le périmètre du contrat. Les commits
#             d'Ephor sont reconnus à leur marqueur ou à leurs fichiers, jamais à « au-dessus
#             de main » : un git pull --rebase les déplace. Poussé avec --force-with-lease ;
#             les jugements et la mesure de tokens sont sauvegardés hors du dépôt avant.
#             --sans-contrat <branche> : une petite correction sans contrat, faite sur une
#             branche partie de la principale locale (donc avec Ephor dedans) : ses commits
#             sont rejoués de même, sans contrat ni rapport, vers la branche du même nom.
#   adopter   prépare la branche « adoption-ephor » : Ephor tel qu'il est ici, exclusions
#             remises dans .gitignore, PROGRESS.md et STEER.md ; à pousser avec --no-verify.
#   retirer   remet la branche principale locale à celle du serveur, enlève exclusions,
#             garde et fichiers locaux ; sauvegarde les jugements d'abord.
#   etat      où en est ce clone.
#
# Gestes de l'opérateur : refusés sous un agent (.ephor/operateur.py). Toujours
# `git pull --rebase`, jamais `git pull` qui fusionne : le commit d'Ephor se replace au-dessus.
set -uo pipefail
ICI="$(cd "$(dirname "$0")/../.." && pwd -P)"
MARQUE="Ephor-prive"
BLOC_DEBUT="# >>> ephor-prive : posé par .ephor/scripts/prive.sh poser, retiré par retirer"
BLOC_FIN="# <<< ephor-prive"
INCLUSION='-include .ephor/projet/prive.mk  # Ephor (privé) : jamais poussé'
# Ce qui reste hors de git sur ce poste. Les trois fichiers ancrés (/PROGRESS.md…) sont ceux
# qu'Ephor commite d'ordinaire ; ici, l'état de l'agent et la consigne ne regardent que vous.
EXCLUSIONS_FIXES=".verify/
AGENT_STOP
mutants/
.ephor/**/__pycache__/
.claude/settings.local.json
/PROGRESS.md
/STEER.md
/CLAUDE.local.md
.opencode/node_modules/
.opencode/package.json
.opencode/package-lock.json
.opencode/bun.lock"
ID_MOTIF='^[A-Z][A-Z0-9-]{2,50}$'
ZERO="0000000000000000000000000000000000000000"

refus() { echo "REFUS : $*" >&2; exit 2; }
echec() { echo "ÉCHEC : $*" >&2; exit 1; }
usage() {
  cat >&2 <<'U'
usage : bash <dépôt d'Ephor>/.ephor/scripts/prive.sh poser /chemin/du/clone
        bash .ephor/scripts/prive.sh publier <ID> [--branche <nom>] [--sans-verdict] [--sans-pousser]
        bash .ephor/scripts/prive.sh publier --sans-contrat <branche> [--sans-pousser]
        bash .ephor/scripts/prive.sh adopter
        bash .ephor/scripts/prive.sh retirer [--force]
        bash .ephor/scripts/prive.sh etat
U
  exit 2
}

# ----------------------------------------------------------------------------- outils git

principale() { # la branche principale : celle que désigne le serveur, sinon main, master
  local designee b
  designee="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  designee="${designee#origin/}"
  for b in ${designee:+"$designee"} main master; do
    if git rev-parse --verify -q "refs/remotes/origin/$b" >/dev/null; then echo "$b"; return 0; fi
  done
  for b in main master; do
    if git rev-parse --verify -q "refs/heads/$b" >/dev/null; then echo "$b"; return 0; fi
  done
  return 1
}
racine_principale() { # le dossier principal du dépôt, d'où qu'on lance (un worktree de tâche compris)
  git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p'
}
etat_dir() { echo "$(git rev-parse --path-format=absolute --git-common-dir)/ephor-prive"; }
commits_marques() { git rev-list --grep="^$MARQUE: " "$@"; }
en_mode_prive() { [ -n "$(commits_marques -n 1 "$1" 2>/dev/null)" ]; }
dossier_de_la_tache() { # le dossier où task/<ID> est ouverte, s'il y en a un
  git worktree list --porcelain 2>/dev/null \
    | awk -v b="branch refs/heads/task/$1" 'index($0, "worktree ") == 1 { d = substr($0, 10) } $0 == b { print d; exit }'
}
empreinte_de() { # le sha256 d'un fichier
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$1"
  fi
}
# Les fichiers que poser a écrits HORS de git (propres à ce poste) : <sha256> <chemin>, pour
# les remplacer à la mise à jour s'ils n'ont pas été retouchés, et les retirer à la fin.
noter_local() {
  local liste; liste="$(etat_dir)/locaux.txt"
  { [ -f "$liste" ] && grep -v " $1\$" "$liste"; printf '%s %s\n' "$(empreinte_de "$1")" "$1"; } > "$liste.tmp"
  mv "$liste.tmp" "$liste"
}
empreinte_notee() { awk -v p="$1" '$2 == p { print $1; exit }' "$(etat_dir)/locaux.txt" 2>/dev/null; }
est_local_note() { [ -n "$(empreinte_notee "$1")" ]; }

# ------------------------------------------------------------------ ce qui demande Python
# JSON, Makefile, tri des commits : un seul programme, plusieurs commandes. Fonction à part :
# bash 3.2, celui de macOS, lit mal un document en ligne dans un $(…). Python 3.9 suffit.
py() {
  python3 -B - "$@" <<'PY'
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MARQUE = "Ephor-prive"
TRAILER = re.compile(r"^Ephor-prive: ", re.M)
# Ce qu'Ephor écrit toujours hors de ses commits, ou qui ne doit jamais partir.
FIXES = {
    "PROGRESS.md",
    "STEER.md",
    "AGENT_STOP",
    "CLAUDE.local.md",
    ".claude/settings.local.json",
    "tasks/README.md",
}


def git(*args, cwd=None):
    sortie = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if sortie.returncode != 0:
        raise SystemExit("git " + " ".join(args) + " : " + sortie.stderr.strip())
    return sortie.stdout


def empreinte(principale):
    """Les fichiers d'Ephor : ceux que ses commits marqués ont AJOUTÉS. Un fichier de
    l'équipe qu'Ephor a seulement modifié (le Makefile, une ligne d'inclusion) reste à
    l'équipe : c'est la ligne ajoutée qui est traquée, pas le fichier."""
    fichiers = set()
    for sha in git("rev-list", "--grep=^" + MARQUE + ": ", principale).split():
        for ligne in git("diff-tree", "--no-commit-id", "--name-status", "-r", "--root", sha).splitlines():
            statut, _, chemin = ligne.partition("\t")
            if statut[:1] == "A" and chemin:
                fichiers.add(chemin)
    return fichiers


def d_ephor(chemin, fichiers):
    return (
        chemin.startswith(".ephor/")
        or chemin in fichiers
        or chemin in FIXES
        or chemin.endswith("/rouge.json")
        or chemin.endswith(".ephor-new")
    )


def matches(chemin, regle):  # la règle du juge pour allowed_paths
    return chemin == regle.rstrip("/") or (regle.endswith("/") and chemin.startswith(regle))


def commits(plage):
    """(sha, parents, sujet, marqué), du plus ancien au plus récent."""
    brut = git("log", "--reverse", "--format=%H%x00%P%x00%s%x00%B%x1e", *plage)
    for bloc in brut.split("\x1e"):
        if not bloc.strip():
            continue
        sha, parents, sujet, corps = bloc.lstrip("\n").split("\x00", 3)
        yield sha, parents.split(), sujet, bool(TRAILER.search(corps))


def fichiers_de(sha):
    sortie = git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
    return [c for c in sortie.splitlines() if c]


def lignes_ephor(sha):
    """Les fichiers où ce commit AJOUTE une ligne qui nomme .ephor/ : une inclusion de
    Makefile, une exclusion, un réglage. Hors tasks/ : le contrat y cite verify.sh, et
    c'est voulu, c'est ainsi que le juge local a vérifié."""
    fautifs = []
    fichier = None
    for ligne in git("diff-tree", "--no-commit-id", "-p", "-r", "--root", sha).splitlines():
        if ligne.startswith("+++ "):
            fichier = ligne[4:]
            fichier = fichier[2:] if fichier.startswith("b/") else fichier
        elif ligne.startswith("+") and ".ephor/" in ligne and fichier:
            if not fichier.startswith("tasks/") and fichier not in fautifs:
                fautifs.append(fichier)
    return fautifs


def classer(sha, parents, sujet, marque, foot, permis):
    """ephor | contrat | fusion | tache | hors, et la raison."""
    if len(parents) > 1:
        return "fusion", "commit de fusion, sans changement propre"
    if marque:
        return "ephor", "marqueur " + MARQUE
    fichiers = fichiers_de(sha)
    if fichiers and all(c.startswith("tasks/") for c in fichiers) and not all(c == "tasks/README.md" for c in fichiers):
        return "contrat", "ne touche que tasks/"
    if fichiers and all(d_ephor(c, foot) for c in fichiers):
        return "ephor", "ne touche que des fichiers d'Ephor"
    if permis is None:
        return "autre", ""
    dehors = [c for c in fichiers if c != "PROGRESS.md" and not any(matches(c, r) for r in permis)]
    if dehors:
        return "hors", "hors du périmètre du contrat : " + " ".join(dehors[:5])
    return "tache", ""


def cmd_garde(args):
    """La garde pre-push : rien d'Ephor ne part. Entrée : <principale> <ref poussée> <plage…>."""
    principale, rref = args[0], args[1]
    foot = empreinte(principale)
    refus = []
    for sha, parents, sujet, marque in commits(args[2:]):
        court = sha[:7]
        if marque:
            refus.append(court + " « " + sujet + " » : commit d'Ephor (marqueur " + MARQUE + ")")
            continue
        if len(parents) > 1:
            continue
        interdits = [c for c in fichiers_de(sha) if d_ephor(c, foot)]
        if interdits:
            refus.append(court + " « " + sujet + " » : fichier(s) d'Ephor ou trace locale : " + " ".join(interdits[:5]))
            continue
        fuites = lignes_ephor(sha)
        if fuites:
            refus.append(court + " « " + sujet + " » : ajoute une ligne qui nomme .ephor/ dans " + " ".join(fuites[:5]))
    if refus:
        print("ephor-prive : " + rref + " emporterait Ephor hors de ce clone :", file=sys.stderr)
        for ligne in refus:
            print("  " + ligne, file=sys.stderr)
        return 1
    return 0


def cmd_trier(args):
    """publier : une ligne par commit, <sha>\t<classe>\t<sujet>\t<raison>."""
    principale, chemin_contrat = args[0], args[1]
    foot = empreinte(principale)
    with open(chemin_contrat, encoding="utf-8") as fichier:
        permis = json.load(fichier).get("allowed_paths") or []
    for sha, parents, sujet, marque in commits(args[2:]):
        classe, raison = classer(sha, parents, sujet, marque, foot, permis)
        print(sha + "\t" + classe + "\t" + sujet + "\t" + raison)
    return 0


def contrat_publie(sha, distante):
    """Un commit de contrat dont chaque fichier, rouge.json à part, est sur la branche du
    serveur à l'identique : publié, l'équipe l'a. Après la fusion et un git pull --rebase, le
    commit d'approbation ne porte plus que rouge.json ; sans cela, retirer le disait perdu."""
    if not distante:
        return False
    for chemin in fichiers_de(sha):
        if chemin.endswith("/rouge.json"):
            continue
        ici = git("ls-tree", sha, "--", chemin).split()
        la = git("ls-tree", distante, "--", chemin).split()
        if len(ici) < 3 or len(la) < 3 or ici[2] != la[2]:
            return False
    return True


def cmd_lister(args):
    """Les commits d'une classe (ephor, contrat, autre ; adoption = ephor et contrats), sans
    contrat : <principale> <classe> <plage…>. Un contrat déjà sur origin/<principale> n'est pas
    listé comme « contrat » : il est publié."""
    principale, voulue = args[0], args[1]
    foot = empreinte(principale)
    distante = "refs/remotes/origin/" + principale
    if subprocess.run(["git", "rev-parse", "-q", "--verify", distante], capture_output=True).returncode != 0:
        distante = ""
    for sha, parents, sujet, marque in commits(args[2:]):
        classe, _ = classer(sha, parents, sujet, marque, foot, None)
        if classe == "contrat" and contrat_publie(sha, distante):
            classe = "contrat-publie"
        if voulue == "adoption" and classe in ("ephor", "contrat", "contrat-publie"):
            print(sha + "\t" + sujet)
        elif classe == voulue or (voulue == "autre" and classe not in ("ephor", "contrat", "contrat-publie", "fusion")):
            print(sha + "\t" + sujet)
    return 0


def cmd_verifier(args):
    """Avant de pousser : tout ce qui change entre la base et la copie est dans le périmètre
    du contrat ou dans le dossier de la tâche. <contrat> <dossier> <base> <copie>"""
    chemin_contrat, dossier, base, copie = args
    with open(chemin_contrat, encoding="utf-8") as fichier:
        permis = json.load(fichier).get("allowed_paths") or []
    dehors = []
    for chemin in git("diff", "--name-only", base, copie).splitlines():
        if not chemin or chemin.startswith(dossier + "/") or chemin == "PROGRESS.md":
            continue
        if not any(matches(chemin, r) for r in permis):
            dehors.append(chemin)
    if dehors:
        print("écart entre la copie à publier et le contrat : " + " ".join(dehors[:8]), file=sys.stderr)
        return 1
    return 0


def cmd_statut(args):
    with open(args[0], encoding="utf-8") as fichier:
        print(json.load(fichier).get("status") or "")
    return 0


def unites(texte):
    """Un Makefile en unités : règle (commentaires attachés, ligne de cible, recette),
    variable, .PHONY, .DEFAULT_GOAL, autre."""
    lignes = texte.splitlines()
    sortie, tampon, i = [], [], 0
    while i < len(lignes):
        ligne = lignes[i]
        if ligne.startswith("#") or not ligne.strip():
            tampon.append(ligne)
            i += 1
            continue
        if ligne.startswith(".PHONY"):
            sortie.append(("phony", None, tampon, [ligne]))
            tampon, i = [], i + 1
            continue
        if ligne.startswith(".DEFAULT_GOAL"):
            sortie.append(("defaut", None, tampon, [ligne]))
            tampon, i = [], i + 1
            continue
        variable = re.match(r"^(?:export\s+|override\s+)?([A-Za-z_][\w]*)\s*(?::::|::|:|\?|\+)?=", ligne)
        if variable:
            bloc = [ligne]
            i += 1
            while bloc[-1].endswith("\\") and i < len(lignes):
                bloc.append(lignes[i])
                i += 1
            sortie.append(("variable", variable.group(1), tampon, bloc))
            tampon = []
            continue
        regle = re.match(r"^([^\s:=#][^:=#]*?)\s*:(?!=)", ligne)
        if regle and not ligne.startswith("\t"):
            bloc = [ligne]
            i += 1
            while i < len(lignes) and (
                lignes[i].startswith("\t")
                or (not lignes[i].strip() and i + 1 < len(lignes) and lignes[i + 1].startswith("\t"))
            ):
                bloc.append(lignes[i])
                i += 1
            sortie.append(("regle", regle.group(1).split(), tampon, bloc))
            tampon = []
            continue
        sortie.append(("autre", None, tampon, [ligne]))
        tampon, i = [], i + 1
    if tampon:
        sortie.append(("autre", None, tampon, []))
    return sortie


def cmd_makefile(args):
    """Les cibles d'Ephor pour un dépôt qui a déjà son Makefile : le Makefile d'Ephor, moins
    les cibles et les variables que celui de l'équipe définit déjà (les siennes gagnent, sans
    avertissement de make), moins .DEFAULT_GOAL (le sien reste). <ephor> <équipe> <sortie>"""
    chemin_ephor, chemin_equipe, chemin_sortie = args
    equipe = Path(chemin_equipe).read_text(encoding="utf-8")
    cibles_equipe = set()
    for trouve in re.finditer(r"^([^\s:=#\t][^:=#]*?)\s*:(?!=)", equipe, re.M):
        cibles_equipe.update(trouve.group(1).split())
    variables_equipe = set(
        re.findall(r"^(?:export\s+|override\s+)?([A-Za-z_][\w]*)\s*(?::::|::|:|\?|\+)?=", equipe, re.M)
    )
    gardees, ecartees, variables_ecartees, lignes = [], [], [], []
    for genre, nom, commentaires, bloc in unites(Path(chemin_ephor).read_text(encoding="utf-8")):
        if genre == "regle":
            if any(c in cibles_equipe for c in nom):
                ecartees.extend(c for c in nom if c in cibles_equipe)
                continue
            gardees.extend(c for c in nom if not c.startswith("$"))
            lignes.extend(commentaires + bloc)
        elif genre == "variable":
            if nom in variables_equipe:
                variables_ecartees.append(nom)
                continue
            lignes.extend(commentaires + bloc)
        elif genre == "phony":
            phony = [c for c in bloc[0].split(":", 1)[1].split() if c in gardees]
            lignes.extend(commentaires + [".PHONY: " + " ".join(phony)])
        elif genre == "defaut":
            lignes.extend(commentaires)
        else:
            lignes.extend(commentaires + bloc)
    entete = [
        "# Les cibles d'Ephor, incluses par le Makefile de l'équipe (dernière ligne). Généré par",
        "# .ephor/scripts/prive.sh poser, et refait à chaque poser : ne pas retoucher ici.",
        "# Écartées, parce que l'équipe les définit déjà et que les siennes gagnent : "
        + (" ".join(sorted(set(ecartees))) or "aucune cible")
        + (" ; variables : " + " ".join(sorted(set(variables_ecartees))) if variables_ecartees else "")
        + ".",
        "",
    ]
    Path(chemin_sortie).write_text("\n".join(entete + lignes).rstrip("\n") + "\n", encoding="utf-8")
    if ecartees:
        print("cibles d'Ephor écartées, l'équipe a les siennes : " + " ".join(sorted(set(ecartees))))
    return 0


VERDICTS = {
    "verified_needs_review": "VÉRIFIÉ, reste la revue humaine",
    "verification_failed": "ÉCHEC",
    "budget_exhausted": "budget de temps épuisé",
    "stale": "périmé (le code a bougé pendant la vérification)",
    "no_change": "rien à juger",
    "rejected": "REFUSÉ",
}


def milliers(n):
    try:
        return "{:,}".format(int(n)).replace(",", " ")
    except (TypeError, ValueError):
        return "?"


def lire_lignes(chemin):
    try:
        return [l for l in Path(chemin).read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def cmd_rapport(args):
    """tasks/<ID>/rapport.md (pour l'équipe) et le texte de la merge request.
    <ID> <dossier> <contrat> <report.json|-> <rapport.md> <mr.md> <branche> <principale> <rejoués> <laissés>"""
    tache, dossier, chemin_contrat, chemin_rapport, sortie_rapport, sortie_mr, branche, principale = args[:8]
    rejoues, laisses = lire_lignes(args[8]), lire_lignes(args[9])
    with open(chemin_contrat, encoding="utf-8") as fichier:
        contrat = json.load(fichier)
    rapport = None
    if chemin_rapport != "-" and os.path.exists(chemin_rapport):
        with open(chemin_rapport, encoding="utf-8") as fichier:
            rapport = json.load(fichier)
    corps = [
        "Cette tâche a été menée avec Ephor sur le poste de son auteur : contrat approuvé avant",
        "le code, tests d'acceptation vus échouer d'abord, verdict rendu par un juge qui ne dépend",
        "pas de l'agent. Ce rapport est une **déclaration** : la CI de ce dépôt ne rejoue pas ce",
        "juge. Les tests d'acceptation de ce dossier, eux, se rejouent (commandes en bas).",
        "",
        "| | |",
        "|---|---|",
        "| Objectif | " + str(contrat.get("objective", "?")) + " |",
    ]
    if rapport is None:
        corps.append("| Verdict local | aucun : publiée sans verdict, comme brouillon |")
        corps.append(
            "| Niveau / effort / risque | "
            + " / ".join(str(contrat.get(c) or "–") for c in ("niveau", "effort", "risk"))
            + " |"
        )
    else:
        statut = str(rapport.get("status", "?"))
        quand = str(rapport.get("finished_at") or rapport.get("started_at") or "?")[:16].replace("T", " ")
        corps.append("| Verdict local | " + VERDICTS.get(statut, statut) + " (`" + statut + "`), le " + quand + " UTC |")
        corps.append("| Base jugée | `" + str(rapport.get("base_commit") or "?")[:12] + "` (branche principale locale) |")
        rouge = rapport.get("rouge") or {}
        if rouge:
            corps.append(
                "| Contrôle rouge | chaque critère vu échouer avant le code, le "
                + str(rouge.get("date") or "?")[:10] + " (`" + str(rouge.get("commit") or "?")[:12] + "`) |"
            )
        criteres = rapport.get("criteria") or []
        if criteres:
            corps.append(
                "| Critères | "
                + " ; ".join(
                    str(c.get("id")) + " : " + ("ok" if c.get("passed") else "ÉCHEC") + " (" + str(c.get("proof", "?")) + ")"
                    for c in criteres
                )
                + " |"
            )
        corps.append(
            "| Niveau / effort / risque | "
            + " / ".join(str(rapport.get(c) or contrat.get(c) or "–") for c in ("niveau", "effort", "risk"))
            + " |"
        )
        usage = rapport.get("usage")
        if isinstance(usage, dict) and usage.get("facturables") is not None:
            corps.append("| Tokens | " + milliers(usage.get("facturables")) + " facturables (" + str(usage.get("source", "?")) + ") |")
        else:
            corps.append("| Tokens | non mesurés |")
    corps += ["", "Rejouer les tests d'acceptation :", "", "```"]
    for critere in contrat.get("criteria", []):
        commande = " ".join(str(m) for m in critere.get("check", []))
        commande = commande.replace("{python}", "python3").replace("{rapport}", "<dossier des rapports>")
        corps.append(commande + "   # " + str(critere.get("id")) + " : " + str(critere.get("description", "")))
    corps += ["```"]
    Path(sortie_rapport).write_text("# " + tache + " — verdict local d'Ephor\n\n" + "\n".join(corps) + "\n", encoding="utf-8")

    mr = ["## " + tache + " : " + str(contrat.get("objective", "")), ""]
    mr.append(
        "Le contrat et ses tests d'acceptation d'abord (`" + dossier + "/`), le code ensuite : chaque"
        " commit est rejoué tel que le juge local l'a vu, depuis `" + branche + "` vers `" + principale + "`."
    )
    mr += [""] + corps + ["", "### Commits", ""]
    for ligne in rejoues:
        sha, _, sujet = ligne.partition("\t")
        mr.append("- `" + sha[:7] + "` " + sujet.split("\t")[0])
    if laisses:
        mr += ["", "Laissés sur le poste (Ephor, contrats, fusions) : " + str(len(laisses)) + " commit(s)."]
    mr += [
        "",
        "Volontairement absents de cette demande : le moteur d'Ephor, qui reste sur le poste de"
        " l'auteur, et la trace locale du contrôle rouge.",
    ]
    Path(sortie_mr).write_text("\n".join(mr) + "\n", encoding="utf-8")
    return 0


def cmd_tests_ci(args):
    """La CI de l'équipe lancera-t-elle les tests de la tâche ? <racine> <dossier> <contrat>"""
    racine, dossier, chemin_contrat = Path(args[0]), args[1], args[2]
    with open(chemin_contrat, encoding="utf-8") as fichier:
        criteres = json.load(fichier).get("criteria") or []
    commandes = [" ".join(str(m) for m in c.get("check", [])).replace("{python}", "python3") for c in criteres]
    if not any("pytest" in c for c in commandes):
        print(
            "  Vérifiez que la CI de l'équipe lance les tests de " + dossier + "/ :\n    "
            + "\n    ".join(commandes)
        )
        return 0
    chemins = None
    pyproject = racine / "pyproject.toml"
    if pyproject.exists():
        dedans = False
        for ligne in pyproject.read_text(encoding="utf-8", errors="replace").splitlines():
            if ligne.strip().startswith("["):
                dedans = ligne.strip() == "[tool.pytest.ini_options]"
            elif dedans and re.match(r"^\s*testpaths\s*=", ligne):
                chemins = re.findall(r'"([^"]+)"|\'([^\']+)\'', ligne.split("=", 1)[1])
                chemins = [a or b for a, b in chemins]
    for nom, section in (("pytest.ini", "[pytest]"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        fichier = racine / nom
        if chemins is not None or not fichier.exists():
            continue
        dedans = False
        for ligne in fichier.read_text(encoding="utf-8", errors="replace").splitlines():
            if ligne.strip().startswith("["):
                dedans = ligne.strip() == section
            elif dedans and re.match(r"^\s*testpaths\s*=", ligne):
                chemins = ligne.split("=", 1)[1].split()
    if chemins is not None and not any(c == "tasks" or c.startswith("tasks/") or c == "." for c in chemins):
        print(
            "  ATTENTION : la configuration pytest de l'équipe limite les tests à "
            + " ".join(chemins) + " : la CI ne lancera pas ceux de " + dossier + "/."
            " Ajoutez tasks à testpaths dans la merge request, ou dites-le dans sa description."
        )
    return 0


COMMANDES = {
    "garde": cmd_garde,
    "trier": cmd_trier,
    "lister": cmd_lister,
    "verifier": cmd_verifier,
    "statut": cmd_statut,
    "makefile": cmd_makefile,
    "rapport": cmd_rapport,
    "tests-ci": cmd_tests_ci,
}
sys.exit(COMMANDES[sys.argv[1]](sys.argv[2:]) or 0)
PY
}

# ------------------------------------------------------------- exclusions et garde pre-push

retirer_bloc() { # retirer_bloc <fichier> : enlève le bloc d'Ephor, s'il y est
  [ -f "$1" ] || return 0
  grep -q '^# >>> ephor-prive' "$1" || return 0
  awk '/^# >>> ephor-prive/ { dedans = 1; next } /^# <<< ephor-prive/ { dedans = 0; next } !dedans' "$1" > "$1.tmp" \
    && mv "$1.tmp" "$1"
}
ecrire_exclusions() { # les lignes sur l'entrée standard -> le bloc d'Ephor dans .git/info/exclude
  local info exclude
  info="$(git rev-parse --path-format=absolute --git-path info)"
  mkdir -p "$info"
  exclude="$info/exclude"
  retirer_bloc "$exclude"
  {
    if [ -s "$exclude" ] && [ -n "$(tail -c 1 "$exclude")" ]; then printf '\n'; fi
    printf '%s\n' "$BLOC_DEBUT"
    awk 'NF && !vu[$0]++'
    printf '%s\n' "$BLOC_FIN"
  } >> "$exclude"
}
lignes_exclues() { # le bloc d'Ephor de .git/info/exclude, ligne par ligne
  awk '/^# >>> ephor-prive/ { dedans = 1; next } /^# <<< ephor-prive/ { dedans = 0 } dedans && NF' \
    "$(git rev-parse --path-format=absolute --git-path info)/exclude" 2>/dev/null
}

poser_garde() {
  local hooks cible
  hooks="$(git rev-parse --path-format=absolute --git-path hooks)"
  mkdir -p "$hooks"
  cible="$hooks/pre-push"
  if [ -e "$cible" ] && ! grep -q 'ephor-prive' "$cible" 2>/dev/null; then
    if [ -n "$(git ls-files --full-name -- "$cible" 2>/dev/null)" ]; then
      echo "  ATTENTION : $cible est suivi par git (core.hooksPath) : la garde pre-push n'est pas posée."
      echo "  publier vérifie lui-même ce qu'il pousse ; un git push fait à la main ne l'est pas."
      return 0
    fi
    mv "$cible" "$cible.avant-ephor-prive"
    echo "  garde pre-push posée ; votre hook pre-push précédent est chaîné derrière elle"
  fi
  cat > "$cible" <<'HOOK'
#!/usr/bin/env bash
# ephor-prive : garde pre-push posée par « bash .ephor/scripts/prive.sh poser », retirée par
# « retirer ». Refuse de pousser le moteur d'Ephor, son marqueur et les traces locales, puis
# chaîne le hook qui était là avant (<ce fichier>.avant-ephor-prive).
ENTREE="$(cat)"
PRINCIPAL="$(git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
SCRIPT="$PRINCIPAL/.ephor/scripts/prive.sh"
COPIE=""
# Le dossier principal peut être sur une branche sans Ephor (partie de origin/main) : le script
# se lit alors dans la branche principale locale, où il est commité.
if [ ! -f "$SCRIPT" ]; then
  for B in "$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')" main master; do
    [ -n "$B" ] || continue
    if git cat-file -e "refs/heads/$B:.ephor/scripts/prive.sh" 2>/dev/null; then
      COPIE="$(mktemp "${TMPDIR:-/tmp}/ephor-garde.XXXXXX")"
      git show "refs/heads/$B:.ephor/scripts/prive.sh" > "$COPIE" && SCRIPT="$COPIE"
      break
    fi
  done
fi
if [ ! -f "$SCRIPT" ]; then
  echo "ephor-prive : .ephor/scripts/prive.sh introuvable, la garde ne peut rien vérifier. Retirez-la (bash .ephor/scripts/prive.sh retirer) ou, si c'est voulu, git push --no-verify." >&2
  exit 1
fi
printf '%s\n' "$ENTREE" | bash "$SCRIPT" garde-push "$@"
CODE=$?
[ -z "$COPIE" ] || rm -f "$COPIE"
[ "$CODE" = 0 ] || exit 1
PRECEDENT="$(cd "$(dirname "$0")" && pwd)/pre-push.avant-ephor-prive"
if [ -x "$PRECEDENT" ]; then printf '%s\n' "$ENTREE" | "$PRECEDENT" "$@" || exit 1; fi
exit 0
HOOK
  chmod +x "$cible"
  printf '%s\n' "$cible" > "$(etat_dir)/garde"
}
retirer_garde() {
  local cible
  cible="$(cat "$(etat_dir)/garde" 2>/dev/null || true)"
  [ -n "$cible" ] || cible="$(git rev-parse --path-format=absolute --git-path hooks)/pre-push"
  if [ -f "$cible" ] && grep -q 'ephor-prive' "$cible" 2>/dev/null; then
    rm -f "$cible"
    [ -f "$cible.avant-ephor-prive" ] && mv "$cible.avant-ephor-prive" "$cible"
    echo "garde pre-push retirée"
  fi
}

# La garde elle-même : lue par le hook, une ligne par référence poussée (<ref locale> <sha>
# <ref distante> <sha distant>). Un sha distant inconnu ici (branche neuve, serveur en
# avance) : tout ce qui n'est sur aucune branche du serveur.
garde_push() {
  local lref lsha rref rsha p refusee=0
  cd "$(racine_principale)" || exit 1
  p="$(principale)" || p="main"
  while read -r lref lsha rref rsha; do
    [ -n "${lsha:-}" ] || continue
    [ "$lsha" != "$ZERO" ] || continue
    if [ "${rsha:-$ZERO}" = "$ZERO" ] || ! git cat-file -e "$rsha" 2>/dev/null; then
      py garde "$p" "$rref" "$lsha" --not --remotes || refusee=1
    else
      py garde "$p" "$rref" "$rsha..$lsha" || refusee=1
    fi
  done
  if [ "$refusee" != 0 ]; then
    echo "ephor-prive : poussée refusée. Pour envoyer une tâche à l'équipe : bash .ephor/scripts/prive.sh publier <ID>." >&2
    echo "ephor-prive : si c'est voulu (adoption d'Ephor par l'équipe) : git push --no-verify." >&2
    exit 1
  fi
}

# ------------------------------------------------------------------------- sauvegardes

copier_preuves() { # copier_preuves <dossier source> <destination> <ID|tout>
  local src="$1" dst="$2" quoi="$3"
  [ -d "$src/.verify" ] || return 0
  mkdir -p "$dst"
  if [ "$quoi" = tout ]; then cp -R "$src/.verify" "$dst/"; return 0; fi
  [ -f "$src/.verify/journal.jsonl" ] && cp "$src/.verify/journal.jsonl" "$dst/"
  [ -d "$src/.verify/usage" ] && cp -R "$src/.verify/usage" "$dst/"
  if [ -d "$src/.verify/judge/$quoi" ]; then mkdir -p "$dst/judge"; cp -R "$src/.verify/judge/$quoi" "$dst/judge/"; fi
  return 0
}
sauvegarder() { # sauvegarder <ID|tout> [dossier de la tâche] : les jugements et la mesure, hors du dépôt
  local quoi="$1" ailleurs="${2:-}" dest
  dest="${XDG_CACHE_HOME:-$HOME/.cache}/ephor/prive/$(basename "$(pwd -P)")/$(date -u +%Y%m%dT%H%M%SZ)-$quoi"
  mkdir -p "$dest"
  copier_preuves . "$dest/principal" "$quoi"
  if [ -n "$ailleurs" ] && [ "$(cd "$ailleurs" && pwd -P)" != "$(pwd -P)" ]; then
    copier_preuves "$ailleurs" "$dest/tache" "$quoi"
  fi
  echo "$dest"
}

# ------------------------------------------------------------------------------- poser

poser_local() { # poser_local <source .ephor-new> <cible locale> <ce que c'est>
  local source="$1" cible="$2" quoi="$3"
  if [ ! -e "$cible" ]; then
    mv "$source" "$cible"; noter_local "$cible"
    echo "  + $cible : $quoi, propre à ce poste, jamais commité"
  elif est_local_note "$cible" && [ "$(empreinte_de "$cible")" = "$(empreinte_notee "$cible")" ]; then
    mv "$source" "$cible"; noter_local "$cible"
    echo "  ↻ $cible : $quoi, remplacé (jamais retouché ici)"
  else
    echo "  ~ $cible existe et n'est pas celui d'Ephor : sa version reste à côté, $source — reprenez ce qui manque, puis supprimez-la"
  fi
}

poser() {
  local dst="${1:-}" racine meme=0 p etat maj=0 gi_avant gi_existe=0 vscode_suivi=0 settings_equipe=0
  local claude_equipe=0 ajouts="" garde restes court
  [ -n "$dst" ] || usage
  [ -f "$ICI/.ephor/scripts/bootstrap_existing_repo.sh" ] \
    || refus "ce geste se lance depuis un dépôt d'Ephor complet (le vôtre, ou son archive) : $ICI n'a pas le script d'installation"
  [ -d "$dst" ] || refus "dépôt introuvable : $dst"
  cd "$dst" || exit 1
  racine="$(git rev-parse --show-toplevel 2>/dev/null)" || refus "$dst n'est pas un dépôt git"
  cd "$racine" || exit 1
  [ "$(pwd -P)" != "$ICI" ] || meme=1
  git remote get-url origin >/dev/null 2>&1 \
    || refus "pas de dépôt distant « origin » : le mode privé n'a de sens que sur le clone d'un dépôt partagé"
  p="$(principale)" || refus "branche principale introuvable (origin/HEAD, main, master)"
  [ "$(git branch --show-current 2>/dev/null)" = "$p" ] \
    || refus "placez-vous sur la branche principale ($p) : le commit d'Ephor se pose là, et seulement là"
  [ -z "$(git status --porcelain 2>/dev/null)" ] \
    || refus "des changements en cours : commitez-les ou rangez-les (git stash) d'abord, le commit d'Ephor doit être seul"
  court="$(find . -maxdepth 1 \( -name GNUmakefile -o -name makefile \) -print 2>/dev/null | head -n 1)"
  [ -z "$court" ] \
    || refus "${court#./} à la racine : GNU make le lit avant Makefile, et trois défenses d'Ephor le tiennent pour hostile ; renommez-le d'abord"
  etat="$(etat_dir)"
  mkdir -p "$etat/modeles"
  en_mode_prive "$p" && maj=1

  gi_avant="$(mktemp "${TMPDIR:-/tmp}/ephor-gitignore.XXXXXX")"
  if [ -f .gitignore ]; then cp .gitignore "$gi_avant"; gi_existe=1; fi
  git cat-file -e "HEAD:.vscode/settings.json" 2>/dev/null && vscode_suivi=1
  [ -f .claude/settings.json ] && settings_equipe=1
  [ -f CLAUDE.md ] && claude_equipe=1

  if [ "$meme" = 1 ]; then
    echo "le moteur est déjà celui de ce dépôt : rien à copier, la disposition privée est (re)posée"
  else
    echo "== installation d'Ephor (le script d'installation, tel quel) =="
    court=""; [ "$maj" = 1 ] && court="--mettre-a-jour"
    if ! bash "$ICI/.ephor/scripts/bootstrap_existing_repo.sh" $court "$racine"; then
      rm -f "$gi_avant"
      echec "l'installation a échoué (voir ci-dessus) : rien n'est commité. Pour revenir en arrière : git restore . && git clean -fd"
    fi
    for court in PROGRESS.md STEER.md; do
      [ -f "$ICI/$court" ] && cp "$ICI/$court" "$etat/modeles/$court"
    done
  fi
  echo
  echo "== disposition privée =="

  # .gitignore : rendu à l'équipe ; ce qu'Ephor y aurait mis va dans .git/info/exclude.
  if [ -f .gitignore ]; then
    if [ "$gi_existe" = 1 ]; then
      ajouts="$(LC_ALL=C comm -13 <(LC_ALL=C sort -u "$gi_avant") <(LC_ALL=C sort -u .gitignore))"
      cp "$gi_avant" .gitignore
    else
      ajouts="$(cat .gitignore)"
      rm -f .gitignore
    fi
  fi
  rm -f "$gi_avant"
  printf '%s\n%s\n' "$EXCLUSIONS_FIXES" "$ajouts" | ecrire_exclusions
  echo "  .gitignore inchangé ; exclusions dans .git/info/exclude (bloc ephor-prive)"
  # Les fichiers que l'installation a posés hors de git : notés, pour les retirer un jour.
  for court in PROGRESS.md STEER.md .verify/detection.md; do
    if [ -f "$court" ] && ! git ls-files --error-unmatch -- "$court" >/dev/null 2>&1 && ! est_local_note "$court"; then
      noter_local "$court"
    fi
  done

  # Makefile : celui de l'équipe reste le sien ; les cibles d'Ephor sont incluses.
  if [ -f Makefile.ephor-new ]; then
    py makefile Makefile.ephor-new Makefile .ephor/projet/prive.mk || echec "la dérivation des cibles d'Ephor a échoué"
    rm -f Makefile.ephor-new
    if ! grep -qF -- '-include .ephor/projet/prive.mk' Makefile; then
      if [ -s Makefile ] && [ -n "$(tail -c 1 Makefile)" ]; then printf '\n' >> Makefile; fi
      printf '\n%s\n' "$INCLUSION" >> Makefile
    fi
    echo "  Makefile : celui de l'équipe, plus une ligne d'inclusion ; les cibles d'Ephor sont dans .ephor/projet/prive.mk"
  fi

  # Réglages de Claude Code : les vôtres si l'équipe a les siens.
  if [ -f .claude/settings.json.ephor-new ]; then
    poser_local .claude/settings.json.ephor-new .claude/settings.local.json "réglages de Claude Code (garde-fous, permissions, bac à sable)"
  fi
  if [ "$claude_equipe" = 1 ] && ! grep -q 'AGENTS.md' CLAUDE.md 2>/dev/null; then
    if [ ! -e CLAUDE.local.md ]; then
      printf '@AGENTS.md\n' > CLAUDE.local.md; noter_local CLAUDE.local.md
      echo "  + CLAUDE.local.md : le pointeur vers AGENTS.md, propre à ce poste (le CLAUDE.md de l'équipe ne le cite pas)"
    elif ! grep -q 'AGENTS.md' CLAUDE.local.md; then
      echo "  note : ni CLAUDE.md ni CLAUDE.local.md ne citent AGENTS.md — ajoutez « @AGENTS.md » à CLAUDE.local.md"
    fi
  fi
  [ -e AGENTS.md ] || echo "  note : AGENTS.md absent — le contrat de travail des agents, à écrire ; il entre dans le commit d'Ephor au prochain poser, ou : git commit AGENTS.md --trailer '$MARQUE: AGENTS.md'"

  # Les adaptateurs ont dérivé opencode et Copilot des réglages de l'ÉQUIPE : refaits depuis
  # ceux d'Ephor, le temps de la dérivation.
  if [ "$settings_equipe" = 1 ] && [ -f .claude/settings.local.json ] && est_local_note .claude/settings.local.json; then
    garde="$(mktemp "${TMPDIR:-/tmp}/ephor-reglages.XXXXXX")"
    cp .claude/settings.json "$garde"
    cp .claude/settings.local.json .claude/settings.json
    if python3 -B .ephor/adapters.py write >/dev/null 2>&1; then
      echo "  opencode et Copilot : réglages dérivés de ceux d'Ephor (.claude/settings.local.json), pas de ceux de l'équipe"
    else
      echo "  ATTENTION : les adaptateurs opencode et Copilot ont échoué : python3 -B .ephor/adapters.py write"
    fi
    cp "$garde" .claude/settings.json
    rm -f "$garde"
  fi
  # Les réglages VS Code de l'équipe restent les siens : Copilot y perd ses approbations.
  if [ "$vscode_suivi" = 1 ] && ! git diff --quiet -- .vscode/settings.json 2>/dev/null; then
    git checkout -q -- .vscode/settings.json
    echo "  .vscode/settings.json : celui de l'équipe, rendu tel quel ; sous Copilot, reprenez les clés chat.* dans vos réglages utilisateur (.ephor/docs/11-trois-agents.md)"
  fi

  poser_garde

  git add -A -- . ':(exclude)*.ephor-new' 2>/dev/null || git add -A
  if git diff --cached --quiet; then
    echo "  rien de nouveau à commiter : Ephor était déjà en place"
  else
    git commit -q \
      -m "Ephor (privé) : moteur, garde-fous et interface de travail, sur ce clone seulement" \
      -m "Ce commit ne part jamais sur le serveur : publier rejoue les commits de tâche sans lui, et la garde pre-push le refuse. Son marqueur le fait reconnaître après chaque git pull --rebase." \
      -m "$MARQUE: $(sed -n '1s/ —.*//p' .ephor/VERSION 2>/dev/null || echo moteur)" \
      || echec "le commit a échoué (identité git ? voir ci-dessus) : tout est en place sur le disque, commitez vous-même avec le marqueur « $MARQUE: moteur » en dernière ligne"
    echo "  commit « Ephor (privé) » sur $p : $(git rev-parse --short HEAD), local seulement"
  fi

  echo
  echo "Ephor (privé) est en place dans $racine, sur $p ($(commits_marques "$p" | wc -l | tr -d ' ') commit(s) marqué(s) $MARQUE)."
  echo "  Les bannières « GESTE 0 » de l'installation, s'il y en a eu, ne s'appliquent pas ici : les"
  echo "  réglages sont dans .claude/settings.local.json, les cibles dans .ephor/projet/prive.mk."
  restes="$(find . -name '*.ephor-new' -not -path './.git/*' 2>/dev/null | sed 's#^\./##' | tr '\n' ' ')"
  [ -z "$restes" ] || echo "  À fusionner à la main (.ephor-new) : $restes"
  cat <<SUITE

Ensuite :
  1. Adaptez .ephor/projet/verify.sh aux commandes du dépôt, écrivez AGENTS.md s'il manque,
     puis commitez-les sur $p avec le marqueur : git commit -a --trailer '$MARQUE: réglages'
     (sans marqueur, un commit qui ne touche qu'Ephor est reconnu quand même ; pas un mixte).
  2. make doctor, puis make verify.
  3. La boucle habituelle (contrat, tâche, make judge), et pour envoyer une tâche à l'équipe :
     bash .ephor/scripts/prive.sh publier <ID>
  Toujours git pull --rebase, jamais git pull qui fusionne. Dites à l'équipe qu'Ephor est à
  l'essai ici, et fixez une date de décision. Le reste : .ephor/docs/13-ephor-prive.md
SUITE
}

# ----------------------------------------------------------------------------- publier

ouvrir_copie() { # ouvrir_copie <ref> : une copie détachée, à part, sans hook ; son chemin dans COPIE
  COPIE="$(mktemp -d "${TMPDIR:-/tmp}/ephor-copie.XXXXXX")"
  if ! git -c core.hooksPath=/dev/null worktree add -q --detach "$COPIE" "$1" 2>/dev/null; then
    rm -rf "$COPIE"; COPIE=""
    return 1
  fi
}
fermer_copie() {
  [ -n "${COPIE:-}" ] || return 0
  git worktree remove --force "$COPIE" >/dev/null 2>&1 || rm -rf "$COPIE"
  git worktree prune 2>/dev/null
  COPIE=""
}
# rejouer <sha…> : chaque commit dans la copie, dans l'ordre. Un commit déjà sur la base est
# sauté (DEJA) ; un conflit arrête tout (CONFLIT, les fichiers dans EN_CONFLIT).
rejouer() {
  local sha
  DEJA=""; CONFLIT=0; EN_CONFLIT=""
  for sha in "$@"; do
    if git -C "$COPIE" -c core.hooksPath=/dev/null cherry-pick "$sha" >/dev/null 2>&1; then continue; fi
    if [ -n "$(git -C "$COPIE" diff --name-only --diff-filter=U 2>/dev/null)" ]; then
      EN_CONFLIT="$(git -C "$COPIE" diff --name-only --diff-filter=U | tr '\n' ' ')"
      git -C "$COPIE" cherry-pick --abort >/dev/null 2>&1
      CONFLIT=1
      return 1
    fi
    git -C "$COPIE" -c core.hooksPath=/dev/null cherry-pick --skip >/dev/null 2>&1 || true
    DEJA="$DEJA $(git rev-parse --short "$sha")"
  done
}
pousser() { # pousser <ref locale> <branche distante> : --force-with-lease sur ce qu'on connaît du serveur
  local attendu
  attendu="$(git rev-parse -q --verify "refs/remotes/origin/$2" 2>/dev/null || true)"
  git push --force-with-lease="refs/heads/$2:$attendu" origin "$1:refs/heads/$2" \
    || echec "la poussée a échoué (voir ci-dessus). Ce qui était prêt reste sous $1 ; un refus de --force-with-lease veut dire que $2 a bougé sur le serveur : git fetch, regardez, relancez"
  echo "  poussé : origin/$2"
}

publier() {
  local id="" branche="" sans_verdict=0 sans_pousser=0 sans_contrat=0 p dossier_tache dossier contrat="" rapport_json=""
  local base statut="" tri hors a_rejouer laisses rejoues_f laisses_f sauvegarde cible
  while [ $# -gt 0 ]; do
    case "$1" in
      --branche) branche="${2:-}"; shift ;;
      --sans-verdict) sans_verdict=1 ;;
      --sans-pousser) sans_pousser=1 ;;
      --sans-contrat) sans_contrat=1 ;;
      -*) refus "option inconnue : $1 — publier <ID> [--branche <nom>] [--sans-verdict] [--sans-pousser] | publier --sans-contrat <branche>" ;;
      *) [ -z "$id" ] || refus "un seul identifiant (ou une seule branche) à la fois"; id="$1" ;;
    esac
    shift
  done
  cd "$ICI" && cd "$(racine_principale)" || exit 1
  p="$(principale)" || refus "branche principale introuvable"
  en_mode_prive "$p" || refus "aucun commit d'Ephor marqué sur $p : ce clone n'est pas en mode privé (poser d'abord)"
  if [ "$sans_contrat" = 1 ]; then
    [ -n "$id" ] && git check-ref-format --branch "$id" >/dev/null 2>&1 \
      || refus "usage : bash .ephor/scripts/prive.sh publier --sans-contrat <branche> [--sans-pousser]"
    [ "$id" != "$p" ] || refus "$p ne se publie pas : c'est la branche principale, et elle porte Ephor"
    [ -n "$branche" ] || branche="$id"
    cible="$id"
  else
    printf '%s' "$id" | grep -Eq "$ID_MOTIF" \
      || refus "usage : bash .ephor/scripts/prive.sh publier <ID> [--branche <nom>] [--sans-verdict] [--sans-pousser] — l'identifiant est la clé du ticket (PAY-123) ; sans contrat : --sans-contrat <branche>"
    [ -n "$branche" ] || branche="task/$id"
    cible="task/$id"
  fi
  git rev-parse --verify -q "refs/heads/$cible" >/dev/null || refus "pas de branche $cible : rien à publier"
  dossier_tache="$(git worktree list --porcelain 2>/dev/null \
    | awk -v b="branch refs/heads/$cible" 'index($0, "worktree ") == 1 { d = substr($0, 10) } $0 == b { print d; exit }')"
  if [ -n "$dossier_tache" ] && [ -n "$(git -C "$dossier_tache" status --porcelain 2>/dev/null)" ]; then
    refus "$dossier_tache a du travail pas commité : ce qui part est la branche, commitez-le d'abord (ou retirez-le)"
  fi
  git fetch -q origin || refus "origin injoignable (git fetch origin) : rien ne se publie sans la branche principale du serveur à jour"
  git rev-parse --verify -q "refs/remotes/origin/$p" >/dev/null || refus "origin/$p introuvable"

  if [ "$sans_contrat" = 1 ]; then
    # Une correction sans contrat : ses commits, moins Ephor et les contrats, rien d'autre.
    a_rejouer="$(py lister "$p" autre "origin/$p..$cible" | cut -f1)"
    [ -n "$a_rejouer" ] || refus "aucun commit à publier sur $cible (seulement Ephor, des contrats ou des fusions)"
    ouvrir_copie "origin/$p" || echec "impossible d'ouvrir une copie de origin/$p"
    # shellcheck disable=SC2086  # des sha, séparés par des sauts de ligne
    if ! rejouer $a_rejouer; then
      fermer_copie
      refus "$cible ne se rejoue pas sur origin/$p (conflit : $EN_CONFLIT). Mettez $p à jour (git pull --rebase), rebasez $cible dessus, puis publiez."
    fi
    if ! (cd "$COPIE" && py garde "$p" "refs/heads/$branche" "origin/$p..HEAD"); then
      fermer_copie; echec "ce qui allait partir contient Ephor : rien n'est poussé"
    fi
    git update-ref "refs/ephor/publier/$cible" "$(git -C "$COPIE" rev-parse HEAD)"
    fermer_copie
    echo "prêt : refs/ephor/publier/$cible = origin/$p + $(printf '%s\n' "$a_rejouer" | wc -l | tr -d ' ') commit(s)${DEJA:+ (déjà sur $p, sautés :$DEJA)}"
    echo "  à relire : git log --stat origin/$p..refs/ephor/publier/$cible"
    if [ "$sans_pousser" = 1 ]; then
      echo "  pas poussé (--sans-pousser). Pour pousser : git push --force-with-lease origin refs/ephor/publier/$cible:refs/heads/$branche"
    else
      pousser "refs/ephor/publier/$cible" "$branche"
      echo "  merge request : glab mr create --source-branch \"$branche\" --target-branch \"$p\" --fill"
    fi
    return 0
  fi

  dossier="$(python3 -B .ephor/judge.py dossier --repo . --task-id "$id" --ref "$p" 2>/dev/null)" \
    || refus "pas de contrat approuvé pour $id sur $p (tasks/$id/contract.json commité) : l'approbation d'abord"
  CONTRAT_TMP="$(mktemp "${TMPDIR:-/tmp}/ephor-contrat.XXXXXX")"
  contrat="$CONTRAT_TMP"
  trap 'rm -f "$CONTRAT_TMP"' EXIT
  git show "$p:$dossier/contract.json" > "$contrat" 2>/dev/null || refus "$dossier/contract.json illisible sur $p"

  for base in "$dossier_tache" "."; do
    if [ -n "$base" ] && [ -f "$base/.verify/judge/$id/latest/report.json" ]; then
      rapport_json="$base/.verify/judge/$id/latest/report.json"; break
    fi
  done
  [ -z "$rapport_json" ] || statut="$(py statut "$rapport_json")"
  if [ "$statut" != "verified_needs_review" ] && [ "$sans_verdict" != 1 ]; then
    refus "pas de verdict VÉRIFIÉ pour $id (${statut:-aucun rapport du juge}) : make judge TASK=$id d'abord. Pour publier quand même, en brouillon : --sans-verdict"
  fi

  # Les commits de la branche, triés : Ephor et les contrats restent ici, la tâche part.
  tri="$(py trier "$p" "$contrat" "origin/$p..$cible")" || echec "le tri des commits a échoué"
  hors="$(printf '%s\n' "$tri" | awk -F'\t' '$2 == "hors"')"
  if [ -n "$hors" ]; then
    echo "REFUS : des commits de $cible touchent des fichiers qui ne sont ni d'Ephor, ni du contrat, ni du périmètre : l'équipe ne doit pas les recevoir sans qu'on sache pourquoi." >&2
    printf '%s\n' "$hors" | awk -F'\t' '{ print "  " substr($1, 1, 7) " « " $3 " » : " $4 }' >&2
    exit 2
  fi
  a_rejouer="$(printf '%s\n' "$tri" | awk -F'\t' '$2 == "tache" { print $1 }')"
  laisses="$(printf '%s\n' "$tri" | awk -F'\t' '$2 != "tache" { print $1 "\t" $3 "\t" $4 }')"
  [ -n "$a_rejouer" ] || refus "aucun commit de tâche à publier sur $cible (seulement Ephor, des contrats ou des fusions)"

  # La copie : la branche principale du serveur, le contrat, puis la tâche. Jamais la branche
  # de l'agent. Git y tourne sans hook : ceux du dépôt ont déjà eu leur mot au commit.
  ouvrir_copie "origin/$p" || echec "impossible d'ouvrir une copie de origin/$p"
  git -C "$COPIE" checkout -q "$p" -- "$dossier" 2>/dev/null || { fermer_copie; echec "$dossier introuvable sur $p"; }
  rm -f "$COPIE/$dossier/rouge.json"
  git -C "$COPIE" rm -q --cached --ignore-unmatch -- "$dossier/rouge.json" >/dev/null 2>&1
  rejoues_f="$(mktemp "${TMPDIR:-/tmp}/ephor-rejoues.XXXXXX")"
  laisses_f="$(mktemp "${TMPDIR:-/tmp}/ephor-laisses.XXXXXX")"
  printf '%s\n' "$tri" | awk -F'\t' '$2 == "tache" { print $1 "\t" $3 }' > "$rejoues_f"
  printf '%s\n' "$laisses" > "$laisses_f"
  mkdir -p ".verify/prive/$id"
  if ! py rapport "$id" "$dossier" "$contrat" "${rapport_json:--}" "$COPIE/$dossier/rapport.md" ".verify/prive/$id/mr.md" \
      "$branche" "$p" "$rejoues_f" "$laisses_f"; then
    rm -f "$rejoues_f" "$laisses_f"; fermer_copie; echec "le rapport n'a pas pu être écrit"
  fi
  rm -f "$rejoues_f" "$laisses_f"
  git -C "$COPIE" add -A -- "$dossier"
  if ! git -C "$COPIE" diff --cached --quiet; then
    git -C "$COPIE" -c core.hooksPath=/dev/null commit -q \
      -m "$id : contrat de tâche et tests d'acceptation" \
      -m "Approuvés avant le code, et vus échouer d'abord. Le verdict local du juge est dans $dossier/rapport.md." \
      || { fermer_copie; echec "le commit du contrat a échoué (identité git ?)"; }
  fi
  # shellcheck disable=SC2086  # des sha, séparés par des sauts de ligne
  if ! rejouer $a_rejouer; then
    fermer_copie
    refus "$cible ne se rejoue pas sur origin/$p (conflit : $EN_CONFLIT). Mettez $p à jour (git pull --rebase), rebasez $cible dessus (depuis son dossier : git rebase $p), rejugez, puis publiez."
  fi
  # Deux vérifications avant de pousser : rien hors du contrat, rien d'Ephor. La garde pre-push
  # refera la seconde ; on ne compte pas sur elle seule (elle peut avoir été retirée).
  if ! (cd "$COPIE" && py verifier "$contrat" "$dossier" "origin/$p" HEAD); then
    fermer_copie; echec "ce qui allait partir ne correspond pas au contrat : rien n'est poussé"
  fi
  if ! (cd "$COPIE" && py garde "$p" "refs/heads/$branche" "origin/$p..HEAD"); then
    fermer_copie; echec "ce qui allait partir contient Ephor : rien n'est poussé"
  fi
  git update-ref "refs/ephor/publier/$id" "$(git -C "$COPIE" rev-parse HEAD)"
  fermer_copie
  echo "prêt : refs/ephor/publier/$id = origin/$p + contrat + $(printf '%s\n' "$a_rejouer" | wc -l | tr -d ' ') commit(s) de tâche${DEJA:+ (déjà sur $p, sautés :$DEJA)}"
  echo "  à relire : git log --stat origin/$p..refs/ephor/publier/$id"

  sauvegarde="$(sauvegarder "$id" "$dossier_tache")"
  echo "  jugements et mesure sauvegardés : $sauvegarde"

  if [ "$sans_pousser" = 1 ]; then
    echo "  pas poussé (--sans-pousser). Pour pousser : git push --force-with-lease origin refs/ephor/publier/$id:refs/heads/$branche"
  else
    pousser "refs/ephor/publier/$id" "$branche"
  fi
  cat <<FIN

Merge request : $branche -> $p. Description prête dans .verify/prive/$id/mr.md (verdict local, critères, tokens).
  glab mr create --source-branch "$branche" --target-branch "$p" --title "$id" --description "\$(cat .verify/prive/$id/mr.md)"
FIN
  py tests-ci . "$dossier" "$contrat" 2>/dev/null || true
  cat <<FIN
Après la fusion : git pull --rebase sur $p (le commit d'Ephor se replace au-dessus) ; make taches
la verra fusionnée, par le nom de la branche ou par son contenu (squash) ; puis make task-clore ID=$id.
FIN
}

# ----------------------------------------------------------------------------- adopter

adopter() {
  local p tmp shas sha n ligne gitignore_lignes etat
  cd "$ICI" && cd "$(racine_principale)" || exit 1
  p="$(principale)" || refus "branche principale introuvable"
  en_mode_prive "$p" || refus "aucun commit d'Ephor marqué sur $p : rien à proposer à l'équipe"
  git fetch -q origin || refus "origin injoignable (git fetch origin)"
  etat="$(etat_dir)"
  # Ephor, et les contrats : approuvés ici, ils le sont pour l'équipe (rouge.json compris).
  shas="$(py lister "$p" adoption "origin/$p..$p" | cut -f1)"
  [ -n "$shas" ] || refus "les commits d'Ephor sont déjà sur origin/$p : rien à adopter"
  ouvrir_copie "origin/$p" || echec "impossible d'ouvrir une copie de origin/$p"
  tmp="$COPIE"
  nettoyer() { fermer_copie; }
  # shellcheck disable=SC2086  # des sha, séparés par des sauts de ligne
  if ! rejouer $shas; then
    nettoyer
    refus "un commit d'Ephor ne se rejoue pas sur origin/$p (conflit : $EN_CONFLIT) : git pull --rebase d'abord"
  fi
  n=$(( $(printf '%s\n' "$shas" | wc -l) - $(printf '%s' "$DEJA" | wc -w) ))
  # Ce qui ne tenait qu'à ce poste redevient ce qu'Ephor pose d'ordinaire.
  gitignore_lignes="$(lignes_exclues | grep -v -e '^/PROGRESS.md$' -e '^/STEER.md$' -e '^/CLAUDE.local.md$' || true)"
  if [ -n "$gitignore_lignes" ]; then
    touch "$tmp/.gitignore"
    if [ -s "$tmp/.gitignore" ] && [ -n "$(tail -c 1 "$tmp/.gitignore")" ]; then printf '\n' >> "$tmp/.gitignore"; fi
    while IFS= read -r ligne; do
      [ -n "$ligne" ] || continue
      grep -qxF -- "$ligne" "$tmp/.gitignore" || printf '%s\n' "$ligne" >> "$tmp/.gitignore"
    done <<< "$gitignore_lignes"
  fi
  # Exclus ici (.git/info/exclude vaut aussi dans la copie) : ajoutés de force.
  for ligne in PROGRESS.md STEER.md; do
    if [ ! -e "$tmp/$ligne" ] && [ -f "$etat/modeles/$ligne" ]; then
      cp "$etat/modeles/$ligne" "$tmp/$ligne" && git -C "$tmp" add -f -- "$ligne"
    fi
  done
  if [ -f CLAUDE.md ] && [ -f "$tmp/CLAUDE.md" ] && ! grep -q 'AGENTS.md' "$tmp/CLAUDE.md"; then
    printf '\n@AGENTS.md\n' >> "$tmp/CLAUDE.md"
  fi
  if est_local_note .claude/settings.local.json && [ -f "$tmp/.claude/settings.json" ] \
    && ! grep -q '\.ephor/hooks/' "$tmp/.claude/settings.json" 2>/dev/null; then
    cp .claude/settings.local.json "$tmp/.claude/settings.json.ephor-new"
    echo "  GESTE 0 pour l'équipe : .claude/settings.json est à elle ; les garde-fous d'Ephor sont à côté, en .claude/settings.json.ephor-new, à fusionner dans la merge request (blocs hooks et permissions), puis make adapters"
  fi
  git -C "$tmp" add -A
  if ! git -C "$tmp" diff --cached --quiet; then
    git -C "$tmp" -c core.hooksPath=/dev/null commit -q \
      -m "Ephor : adoption par l'équipe — exclusions dans .gitignore, état et consigne" \
      -m "Ce qui ne tenait qu'au poste d'un essai privé redevient ce qu'Ephor pose d'ordinaire." \
      || { nettoyer; echec "le commit d'adoption a échoué"; }
  fi
  git branch -f adoption-ephor "$(git -C "$tmp" rev-parse HEAD)" || { nettoyer; echec "la branche adoption-ephor n'a pas pu être posée"; }
  nettoyer
  cat <<FIN
Branche adoption-ephor prête : origin/$p + $n commit(s) d'Ephor + l'adoption.
  à relire : git log --stat origin/$p..adoption-ephor
  à pousser : git push --no-verify -u origin adoption-ephor
     (la garde pre-push refuse tout ce qui est Ephor : --no-verify est voulu, cette fois seulement)
  puis la merge request vers $p. Les contrats déjà sur le serveur comptent comme approuvés :
  rien à rejouer. Après la fusion : git pull --rebase, puis bash .ephor/scripts/prive.sh retirer
  (la garde et les exclusions locales s'en vont ; Ephor reste, il est à l'équipe).
FIN
}

# ----------------------------------------------------------------------------- retirer

retirer() {
  local force=0 p etat contrats autres sauvegarde h chemin
  [ "${1:-}" != "--force" ] || force=1
  cd "$ICI" && cd "$(racine_principale)" || exit 1
  p="$(principale)" || refus "branche principale introuvable"
  etat="$(etat_dir)"
  { [ -d "$etat" ] || en_mode_prive "$p"; } || refus "rien d'Ephor (privé) dans ce clone"
  [ "$(git branch --show-current 2>/dev/null)" = "$p" ] || refus "placez-vous sur $p : retirer remet cette branche à l'état du serveur"
  [ -z "$(git status --porcelain 2>/dev/null)" ] || refus "des changements en cours : commitez-les ou rangez-les d'abord"
  git fetch -q origin || refus "origin injoignable (git fetch origin)"
  contrats="$(py lister "$p" contrat "origin/$p..$p" || true)"
  autres="$(py lister "$p" autre "origin/$p..$p" || true)"
  if [ -n "$contrats$autres" ] && [ "$force" != 1 ]; then
    echo "REFUS : des commits de $p ne sont pas sur le serveur et ne sont pas d'Ephor ; retirer les abandonnerait." >&2
    [ -z "$contrats" ] || printf '%s\n' "$contrats" | awk -F'\t' '{ print "  contrat pas publié : " substr($1, 1, 7) " « " $2 " »" }' >&2
    [ -z "$autres" ] || printf '%s\n' "$autres" | awk -F'\t' '{ print "  autre : " substr($1, 1, 7) " « " $2 " »" }' >&2
    echo "  Publiez-les (publier <ID>), ou --force pour les abandonner (git reflog les garde un temps)." >&2
    exit 2
  fi
  sauvegarde="$(sauvegarder tout)"
  echo "jugements, mesure et plans sauvegardés : $sauvegarde"
  git reset -q --hard "origin/$p" || echec "git reset --hard origin/$p a échoué"
  retirer_bloc "$(git rev-parse --path-format=absolute --git-path info)/exclude"
  retirer_garde
  if [ -f "$etat/locaux.txt" ]; then
    while read -r h chemin; do
      [ -n "${chemin:-}" ] && [ -f "$chemin" ] && rm -f "$chemin"
    done < "$etat/locaux.txt"
  fi
  rm -f AGENT_STOP
  git ls-files --error-unmatch -- .verify >/dev/null 2>&1 || rm -rf .verify
  rm -rf .opencode/node_modules .opencode/package.json .opencode/package-lock.json .opencode/bun.lock 2>/dev/null
  rm -rf "$etat"
  git worktree prune 2>/dev/null
  echo "Ephor (privé) retiré : $p = origin/$p ($(git rev-parse --short HEAD)). Restent, à vous : les branches task/* (git branch -D),"
  echo "les dossiers de tâche ouverts, et la copie du juge sous ~/.cache/ephor/judge."
}

# -------------------------------------------------------------------------------- etat

etat() {
  local p etat_d garde="absente" exclusions="absentes" ligne sha
  cd "$ICI" && cd "$(racine_principale)" || exit 1
  p="$(principale)" || refus "branche principale introuvable"
  etat_d="$(etat_dir)"
  echo "Ephor (privé) dans $(pwd -P) — branche principale : $p"
  if ! en_mode_prive "$p"; then echo "  aucun commit marqué $MARQUE sur $p : pas en mode privé"; fi
  git rev-parse --verify -q "refs/remotes/origin/$p" >/dev/null || { echo "  origin/$p inconnu ici : git fetch origin"; return 0; }
  echo "  commits d'Ephor sur $p, pas sur le serveur :"
  py lister "$p" ephor "origin/$p..$p" | awk -F'\t' '{ print "    " substr($1, 1, 7) "  " $2 }'
  echo "  contrats pas encore publiés (commits qui ne touchent que tasks/) :"
  py lister "$p" contrat "origin/$p..$p" | awk -F'\t' '{ print "    " substr($1, 1, 7) "  " $2 }'
  echo "  autres commits locaux :"
  py lister "$p" autre "origin/$p..$p" | awk -F'\t' '{ print "    " substr($1, 1, 7) "  " $2 }'
  echo "  branches de tâche :"
  for ligne in $(git for-each-ref --format='%(refname:short)' refs/heads/task/); do
    sha="$(git rev-parse -q --verify "refs/ephor/publier/${ligne#task/}" 2>/dev/null || true)"
    echo "    $ligne${sha:+  (préparée : ${sha:0:7}$(git rev-parse -q --verify "refs/remotes/origin/$ligne" >/dev/null 2>&1 && echo ', poussée'))}"
  done
  [ -f "$(cat "$etat_d/garde" 2>/dev/null || echo /nulle/part)" ] && garde="en place"
  [ -n "$(lignes_exclues)" ] && exclusions="en place ($(lignes_exclues | wc -l | tr -d ' ') lignes)"
  echo "  garde pre-push : $garde ; exclusions locales : $exclusions"
  [ -f .ephor/projet/prive.mk ] && echo "  Makefile : celui de l'équipe + inclusion de .ephor/projet/prive.mk"
  [ -f "$etat_d/locaux.txt" ] && echo "  fichiers propres à ce poste : $(cut -d' ' -f2 "$etat_d/locaux.txt" | tr '\n' ' ')"
  return 0
}

# ------------------------------------------------------------------------------- entrée

GESTE="${1:-}"
[ $# -gt 0 ] && shift
case "$GESTE" in
  garde-push) garde_push "$@" ;;  # lancé par le hook, sous votre identité : pas un geste d'agent
  poser|publier|adopter|retirer|etat)
    python3 -B "$ICI/.ephor/operateur.py" "prive.sh $GESTE" || exit 2
    "$GESTE" "$@" ;;
  *) usage ;;
esac
