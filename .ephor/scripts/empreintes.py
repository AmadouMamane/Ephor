#!/usr/bin/env python3
"""Empreintes de ce qu'Ephor pose HORS de `.ephor/`, dans toutes ses versions.

Une mise à jour remplace le moteur (`.ephor/`). Le reste — le greffon d'opencode, les
réglages de Claude Code, les sous-agents, les skills, le Makefile — vit là où chaque outil
le cherche, et le projet a pu le retoucher. Tant que rien ne permettait de le savoir, la
mise à jour déposait un `.ephor-new` à côté, et l'ancienne version restait active : un
projet « en 1.1.5 » tournait avec le greffon d'opencode de la 1.1.3 (mesuré le 29/09/2026).

`bootstrap_existing_repo.sh --mettre-a-jour` compare maintenant chacun de ces fichiers à la
liste écrite ici : l'empreinte de chaque version qu'Ephor en a livrée. Identique à l'une
d'elles, il n'a jamais été retouché : il est remplacé. Sinon, il garde son `.ephor-new`.

Ne tourne que dans le dépôt d'Ephor, dont il lit l'histoire : l'installation ne le copie pas
dans les projets. Un test refuse une liste en retard sur les fichiers : à relancer à chaque
livraison qui change l'un d'eux.

    python3 .ephor/scripts/empreintes.py          # réécrit .ephor/empreintes.txt
    python3 .ephor/scripts/empreintes.py check    # échoue si une version actuelle manque

Bibliothèque standard, Python 3.9.
"""

from __future__ import annotations

import fnmatch
import hashlib
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
SORTIE = RACINE / ".ephor" / "empreintes.txt"
# Ce que l'installation pose hors du moteur, et qu'une mise à jour peut remplacer. Les
# réglages du projet (.ephor/projet/), PROGRESS.md, STEER.md et les squelettes de doc sont
# au projet dès le premier jour ; les fichiers dérivés, les adaptateurs les régénèrent.
MOTIFS = (
    "Makefile",
    "tasks/README.md",
    # Les deux pointeurs vers AGENTS.md, posés s'ils manquent (jamais remplacés : un pointeur
    # d'une ligne n'a pas de version à suivre, et un fichier du projet reste le sien).
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    ".claude/settings.json",
    ".claude/agents/*.md",
    # Toute la skill, pas seulement son SKILL.md (#37) : `*` de fnmatch traverse les dossiers.
    ".claude/skills/*",
    ".opencode/plugins/*.js",
)
ENTETE = """\
# Empreintes (sha256) de ce qu'Ephor pose hors de .ephor/, dans toutes ses versions.
# Généré par .ephor/scripts/empreintes.py, dans le dépôt d'Ephor : ne pas modifier à la main.
# Une mise à jour ne remplace un de ces fichiers que si son contenu est l'une de ces
# versions, donc s'il n'a jamais été retouché dans le projet. « actuelle » : celle-ci.
"""
VIDE = {"0" * 40, "0" * 64}


def git(*args: str, entree: bytes | None = None) -> bytes:
    """Git durci, comme dans le juge : aucun hook du dépôt, aucun moniteur de fichiers."""
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
        cwd=RACINE,
        input=entree,
        capture_output=True,
        check=True,
    ).stdout


def chemins() -> list[str]:
    """Les fichiers suivis qu'Ephor pose hors du moteur."""
    suivis = git("ls-files", "-z").decode("utf-8").split("\0")
    return sorted(c for c in suivis if c and any(fnmatch.fnmatchcase(c, m) for m in MOTIFS))


def objets_passes(chemin: str) -> set[str]:
    """Les objets git qu'a été ce fichier, dans l'histoire de HEAD."""
    sortie = git(
        "log", "--root", "--format=", "--raw", "--no-abbrev", "--no-renames", "HEAD", "--", chemin
    ).decode("utf-8", "replace")
    objets: set[str] = set()
    for ligne in sortie.splitlines():
        if ligne.startswith(":"):
            _, _, avant, apres, _ = ligne.split("\t", 1)[0].split(maxsplit=4)
            objets.update({avant, apres} - VIDE)
    return objets


def contenus(objets: list[str]) -> dict[str, bytes]:
    """Le contenu de plusieurs objets git, en une seule lecture."""
    if not objets:
        return {}
    sortie = git("cat-file", "--batch", entree=("\n".join(objets) + "\n").encode())
    rendus: dict[str, bytes] = {}
    position = 0
    for objet in objets:
        fin = sortie.index(b"\n", position)
        taille = int(sortie[position:fin].split()[2])
        rendus[objet] = sortie[fin + 1 : fin + 1 + taille]
        position = fin + 1 + taille + 1
    return rendus


def sans_reglages(texte: bytes) -> bytes:
    """Celle des adaptateurs : un seul calcul, pour l'écriture et pour la mise à jour."""
    sys.path.insert(0, str(RACINE / ".ephor"))
    from adapters import sans_reglages as calcul

    return calcul(texte)


def ecrites() -> dict[tuple[str, str], set[str]]:
    """Les empreintes déjà écrites, par (chemin, genre) : « » ou « sans-reglages ». La marque
    « actuelle » est retirée : c'est une version passée pour la liste suivante."""
    lues: dict[tuple[str, str], set[str]] = {}
    texte = SORTIE.read_text(encoding="utf-8") if SORTIE.exists() else ""
    for ligne in texte.splitlines():
        morceaux = ligne.split()
        if ligne.startswith("#") or len(morceaux) not in (2, 3):
            continue
        genre = morceaux[2] if len(morceaux) == 3 and morceaux[2] == "sans-reglages" else ""
        lues.setdefault((morceaux[1], genre), set()).add(morceaux[0])
    return lues


def histoire_complete() -> bool:
    """Un clone superficiel n'a pas les versions passées : relues dedans, la liste perdait
    toutes les versions livrées, et `check` la disait « à jour » (relecture du 01/10/2026)."""
    return git("rev-parse", "--is-shallow-repository").strip() != b"true"


def liste() -> str:
    lignes = [ENTETE.rstrip("\n")]
    # Les versions déjà écrites restent : une histoire tronquée, un fichier renommé ou un
    # commit perdu ne peuvent plus faire oublier une version qu'Ephor a livrée.
    deja = ecrites()
    for chemin in chemins():
        passes = list(contenus(sorted(objets_passes(chemin))).values())
        empreintes = {hashlib.sha256(c).hexdigest() for c in passes} | deja.get((chemin, ""), set())
        # La version du disque, commitée ou non : c'est elle que la livraison emporte.
        disque = (RACINE / chemin).read_bytes()
        actuelle = hashlib.sha256(disque).hexdigest()
        for empreinte in sorted(empreintes | {actuelle}):
            suffixe = " actuelle" if empreinte == actuelle else ""
            lignes.append(f"{empreinte} {chemin}{suffixe}")
        # Un sous-agent dont le projet n'a changé que le modèle ou l'effort reste remplaçable :
        # la mise à jour garde ces deux lignes (#30).
        if fnmatch.fnmatchcase(chemin, ".claude/agents/*.md"):
            allegees = {hashlib.sha256(sans_reglages(c)).hexdigest() for c in [*passes, disque]}
            for empreinte in sorted(allegees | deja.get((chemin, "sans-reglages"), set())):
                lignes.append(f"{empreinte} {chemin} sans-reglages")
    return "\n".join(lignes) + "\n"


def ecarts() -> list[str]:
    """Ce qui ne va pas dans la liste écrite : une version actuelle absente, fausse, en trop.

    Seules les versions actuelles se vérifient partout : un projet né d'une archive d'Ephor
    n'a pas son histoire, donc pas ses versions passées. Celles-ci, `write` les relit dans
    l'histoire du dépôt d'Ephor, à chaque livraison."""
    actuelles: dict[str, set[str]] = {}
    ecrit = SORTIE.read_text(encoding="utf-8") if SORTIE.exists() else ""
    for ligne in ecrit.splitlines():
        morceaux = ligne.split()
        if not ligne.startswith("#") and len(morceaux) == 3 and morceaux[2] == "actuelle":
            actuelles.setdefault(morceaux[1], set()).add(morceaux[0])
    attendues = {c: hashlib.sha256((RACINE / c).read_bytes()).hexdigest() for c in chemins()}
    trouves = [
        f"{chemin} : sa version actuelle manque, ou n'est pas la bonne"
        for chemin, empreinte in attendues.items()
        if actuelles.get(chemin) != {empreinte}
    ]
    en_trop = sorted(set(actuelles) - set(attendues))
    trouves += [f"{chemin} : n'est plus posé par Ephor" for chemin in en_trop]
    # Une liste réécrite depuis une histoire tronquée perdait les versions passées, et passait
    # ce contrôle : la liste commitée en est le témoin, chaque version qu'elle porte doit rester.
    if histoire_complete():
        try:
            commitee = git("show", "HEAD:" + SORTIE.relative_to(RACINE).as_posix()).decode("utf-8")
        except subprocess.CalledProcessError:
            commitee = ""
        maintenant = {
            ligne.split()[:2][-1] + " " + ligne.split()[0]
            for ligne in ecrit.splitlines()
            if ligne and not ligne.startswith("#") and len(ligne.split()) >= 2
        }
        for ligne in commitee.splitlines():
            morceaux = ligne.split()
            if ligne.startswith("#") or len(morceaux) < 2 or morceaux[1] not in attendues:
                continue
            if morceaux[1] + " " + morceaux[0] not in maintenant:
                courte = morceaux[0][:12]
                trouves.append(
                    f"{morceaux[1]} : une version livrée ({courte}…) a disparu de la liste"
                )
    return trouves


def main(argv: list[str]) -> int:
    if argv[1:] == ["check"]:
        trouves = ecarts()
        for ecart in trouves:
            print(f"  {ecart}")
        if trouves:
            print(
                "empreintes en retard sur les fichiers qu'Ephor pose hors de .ephor/ :\n"
                "  python3 .ephor/scripts/empreintes.py   (puis commitez .ephor/empreintes.txt)"
            )
            return 1
        print("empreintes à jour")
        return 0
    if argv[1:]:
        print("usage : python3 .ephor/scripts/empreintes.py [check]")
        return 2
    if not histoire_complete():
        print(
            "dépôt superficiel : l'histoire d'Ephor manque, les versions passées ne peuvent pas "
            "être relues. D'abord : git fetch --unshallow"
        )
        return 2
    SORTIE.write_text(liste(), encoding="utf-8")
    print(f"écrit : {SORTIE.relative_to(RACINE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
